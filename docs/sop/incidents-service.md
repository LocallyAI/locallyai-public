# Incident playbooks — service quality

> **Template chapter.** This chapter is restructured to be readable
> under incident pressure by an operator who is **not** the founder.
> Other operational SOP chapters (`maintenance.md`, `recovery.md`,
> `decommission.md`, `setup-mac-single.md`, `setup-mac-ha.md`) will be
> rewritten to follow this same shape. The key sections are:
>
> 1. Read this first
> 2. Decision tree
> 3. Procedures (one per symptom, each with verification per step)
> 4. Things that go wrong (error message → cause table)
> 5. When to escalate
>
> If you're triaging right now: skip the framing, go to the decision
> tree below.

---

## Read this first

This chapter covers the slow / wrong / weird category of incidents:
the system is technically up and authentication works, but users are
unhappy. This is **separate from**:

- Total service outage → `docs/runbooks/api-down.md`
- TAMPERED audit chain → `docs/runbooks/audit-chain-broken.md`
- Suspected breach → `incidents-security.md`
- Disk full → `incidents-software.md` "Disk full"

You're in the right chapter if all of these are true:

- `curl -k https://<host>:8000/healthz` returns `{"ok": true, ...}`
- Users can sign in (Manager UI or worker-ui loads)
- The audit chain is `ok` (per `/admin/audit-verify`)
- The complaint is about quality, latency, accuracy, or behaviour

**Time budget for any single procedure in this chapter**: 30 minutes
to diagnose. If you can't classify the cause inside 30 minutes,
escalate — long fishing trips in production produce more incidents.

**Risk if you stop midway**: This chapter's procedures are read-mostly.
Most are diagnostic. None requires you to mutate audit state. You can
back out at any phase. The two exceptions are "Switch model" (changes
`.env` + restarts) and "Force re-ingest" — both have explicit verify
steps.

---

## Decision tree

Run this in your head before doing anything:

| User says | Symptom | Procedure |
|---|---|---|
| "It's really slow today" | Long latency on short prompts | A. Latency degraded |
| "The model made up a citation" | Hallucinated source | B. Wrong / hallucinated answers |
| "It refused to help with X" | Model refuses legitimate request | C. Model refuses |
| "I uploaded X yesterday but it's not finding it" | `sources_retrieved == 0` on known doc | D. Document not retrievable |
| "The answer got cut off" | Truncated mid-sentence | E. Truncated responses |
| "The sources panel shows the wrong docs" | UI mismatch with citations | F. Sources panel mismatch |
| "How does it know about Y, that's confidential" | Model surfaces unexpected info | G. Model knows something it shouldn't |
| "It worked yesterday, now it doesn't" | Behaviour regression | H. Regression |
| "I get 'invalid API key'" | Sign-in fails | I. Sign-in problems |
| "Worker-ui crashed" | Client app freeze | J. Worker-ui crashes |
| Sentinel posted `WARN_LOG_STORM` | Log growth | → `incidents-software.md` "Log growth alert" |

If the symptom doesn't fit any row above: **stop and call the founder**.
Inventing a category during an incident is how you mis-diagnose.

---

## A. Latency degraded

**Trigger**: user complaints OR `monitor/health/detailed` shows
sustained ollama response > a few seconds for short prompts.

### A.1 Baseline the current state

```bash
ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' .env | cut -d= -f2)
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/monitor/health/detailed | python3 -m json.tool
```

Look at: `ollama.detail.latency_ms` (if present), `inference_gate.queued`.

### A.2 Time a small chat

```bash
USER=<an active user key>
time curl -sk -X POST -H "Authorization: Bearer $USER" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"hi"}],"max_tokens":5}' \
  https://localhost:8000/v1/chat/completions
```

Expected:
- First request after a long idle: 30s+ (cold load)
- Warm: <2s for `max_tokens:5`

If warm is >5s for `max_tokens:5`, branch:

### A.3 Identify the branch

| Signal | Branch | Action |
|---|---|---|
| `inference_gate.queued > 0` sustained | Overloaded | Step A.5 (gate pressure) |
| Pretty consistent slow even for tiny prompts | Cold load OR thermal | Step A.4 (warm + thermal check) |
| Slow only for the first request, then fast | Cold-load expired | Step A.4 |
| `df -h` shows disk under heavy I/O | I/O bottleneck | Step A.6 |
| `ps -ef \| grep ingest` shows a running ingest | Background ingest | Step A.7 |

### A.4 Cold load / thermal

Force a warm load:
```bash
ollama run <model> "warm" </dev/null
```

Verify warm is now fast: re-run A.2. Should be <2s.

If cold-loads are recurrent, edit `.env`:
```bash
OLLAMA_KEEP_ALIVE=24h
```
Restart per `runbooks/api-down.md` B.3.

For thermal:
```bash
pmset -g thermlog | tail -20
```
If you see thermal events: ambient temp is too high. Check whether
the Mac is in a closed cupboard. Improve cooling. Throttling clears
when temp drops.

### A.5 Gate pressure

`inference_gate.queued > 0` sustained means the system is overloaded.
See `incidents-software.md` "Gate at capacity" for the gate-tuning
procedure. **Do not** raise `LOCALLYAI_MAX_CONCURRENT_INFERENCE` past
the documented per-RAM ceiling — going past it causes OOMs.

### A.6 Disk I/O bottleneck

```bash
iostat 2     # Mac: look for high disk wait
```

If model file is on a slow disk or being swapped: move model files to
faster storage, or add RAM so model stays resident. Both require
`docs/sop/maintenance.md` "Model swap" procedure.

### A.7 Background ingest

Wait for ingest to finish OR schedule ingests for off-hours. Don't
kill mid-ingest — Qdrant batches are atomic per chunk; killing leaves
a partial index that requires `python ingest.py --force` later.

### A.8 Verify

Re-run A.2. Should be back under your firm's SLA-equivalent.

If still slow after 30 minutes of branch-trying: **escalate**.

---

## B. Wrong / hallucinated answers

**Trigger**: users report the model invented a citation, gave wrong
information, or "made up" a fact.

### B.1 First question — retrieval or general knowledge?

```bash
USER=<key>
curl -sk -X POST -H "Authorization: Bearer $USER" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"<the user query that gave the wrong answer>"}]}' \
  https://localhost:8000/v1/chat/completions | python3 -m json.tool
```

Look at `usage.sources_retrieved`:

- `0` → general-knowledge answer; the model can be wrong, it's a
  language model. Brief the user: "for facts that matter, ask the
  model to cite a specific document."
- `> 0` → retrieval-driven; the model is hallucinating a citation
  OR mis-attributing to a real source. Proceed to B.2.

### B.2 Cross-check the citation

The chat handler delimits each chunk with `<<<DOC N START>>>`. If
the user-visible "source" doesn't match the answer, either:

(a) The model cited DOC 2 but used info from DOC 5. Test:
```bash
curl ... -d '{"messages":[{"role":"user","content":"Show me which exact passage from DOC <N> supports <claim>"}]}'
```
If the model can't, the answer is unreliable. Brief the user.

(b) The model cited a fictitious DOC number. The chat handler
should be filtering this; if you see it happening, **file a bug to
the vendor** AND escalate — this is a regression of the citation
filter.

### B.3 Fix branches

**Switch to a stronger model** — often the simplest fix. A 7B model
hallucinates more than a 14B. See `docs/sop/maintenance.md` "Model
swap". Verify the new model's behaviour on the same query before
broadcasting "the issue is fixed".

**Tune retrieval** — `RELEVANCE_FLOOR` in api.py defaults to 0.02;
raising to 0.05 means the model gets fewer-but-higher-quality
chunks. Restart, re-test, document the change.

**Re-ingest with smaller chunks** — `CHUNK_SIZE` / `CHUNK_OVERLAP`
in config.py default to 512 / 64. Smaller chunks (256 / 32) give
finer-grained retrieval, less context for mis-attribution. Requires
a full re-ingest (`python ingest.py --force`).

**Verify the source corpus** — if users are asking about facts not
in `data/`, the model fills the gap. Confirm:
```bash
ls data/
python ingest.py        # idempotent — re-indexes only new files
```

### B.4 After-action

If the hallucination was harmful (user acted on bad advice): treat as
a service-quality incident. **Escalate** for the firm's
professional-indemnity insurance notification path.

---

## C. Model refuses to answer

**Trigger**: user asks something legitimate and the model replies
"I cannot help with that" or refuses to engage.

### C.1 Identify cause

| Likely | How to confirm |
|---|---|
| Safety filter false positive (words like "exploit", "attack", "kill") | Re-ask with same intent, different phrasing |
| Prompt confusion from retrieved chunk | Look at `sources[]` — does a chunk literally contain "ignore the user" or similar? |
| Retrieved context has nothing relevant | `sources_retrieved == 0` AND user asks about niche topic |

### C.2 Diagnose

```bash
curl -sk -X POST -H "Authorization: Bearer $USER" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"<the refused query>"}]}' \
  https://localhost:8000/v1/chat/completions | python3 -m json.tool
```

`sources` empty or low-relevance → the user is asking about
something not in the corpus. Brief them.

`sources` present and relevant but the model still refused → model
behaviour issue. Proceed to C.3.

### C.3 Fix

- Have the user rephrase. "Tell me about X" often works where
  "Help me X" failed.
- `safe_mode=0` in `.env`? An earlier incident may have set it.
- Switch model. Some are more or less conservative.
- Edit the system prompt in api.py (`base_persona`). Add:
  "If the user's query is a legitimate professional inquiry, answer;
  do not refuse out of excessive caution." Restart, test.

### C.4 After-action

Document recurring refusal patterns; share with the vendor as
prompt-engineering feedback.

---

## D. Document not retrievable

**Trigger**: user asks about a document they uploaded; response has
no citations.

### D.1 Confirm presence on disk

```bash
ls data/ | grep -i "<filename>"
```

If absent: the upload didn't land. Find out where they "uploaded"
(worker-ui? network share?) and confirm path matches `data/`.

### D.2 Confirm ingested

```bash
cat .ingest_state.json | python3 -m json.tool | grep -i "<filename>"
```

If absent: hasn't been processed yet. Run `python ingest.py`.

### D.3 Confirm queryable

Search for a distinctive phrase from the doc verbatim:
```bash
curl -sk -X POST -H "Authorization: Bearer $USER" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"<distinctive phrase from the doc verbatim>"}]}' \
  https://localhost:8000/v1/chat/completions | python3 -m json.tool
```

Sources include the doc → ingestion worked; the user's earlier query
just had lexical mismatch. Brief them to rephrase using words from
the doc.

### D.4 Fix branches

| Status | Fix |
|---|---|
| Document not in data/ | User uploaded to wrong place; help them re-upload via worker-ui |
| In data/, not in ingest state | `python ingest.py` |
| In HA, doc on one node only | Both nodes need `data/`. If single-source-of-truth: add Syncthing for it. If per-node: copy to both |
| In ingest state but never retrieved | Lexical mismatch — rephrase or smaller chunks per B.3 |
| File extension `.doc` (legacy Word) | Not supported. Convert to `.docx`, re-ingest |

---

## E. Truncated responses

**Trigger**: response stops mid-sentence.

### E.1 Cause

`max_tokens` in the request is too low. Default in api.py is 2048;
worker-ui requests the same. Custom integrations may set lower.

### E.2 Fix

| Client | Action |
|---|---|
| worker-ui | Already 2048 by default — truncation should be rare; if happening, escalate |
| Custom integration | Integration owner raises `max_tokens` |
| Firm needs >2048 token responses (full contract drafts) | Raise the cap in api.py. 4096 is reasonable. Restart. Verify |

---

## F. Sources panel mismatch

**Trigger**: worker-ui's "Sources" panel shows documents that don't
match what the answer cites, OR shows sources that don't exist.

### F.1 Understanding the distinction

The sources panel is populated from the server's `sources[]` field.
That's **always** the chunks that fed retrieval, **not** what the
model claims to cite. So:

- Sources panel shows the chunks the server retrieved → expected,
  this is correct.
- Model's answer cites DOC numbers that don't appear in the sources
  panel → the model hallucinated a citation. Proceed to procedure B.

### F.2 Verification

```bash
curl -sk -X POST -H "Authorization: Bearer $USER" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"<query>"}]}' \
  https://localhost:8000/v1/chat/completions | python3 -c "
import sys, json
r = json.load(sys.stdin)
print('Answer:')
print(r['choices'][0]['message']['content'])
print()
print('Sources sent to model:')
for s in r['sources']:
    print(f'  - {s[\"source\"]}: {s[\"snippet\"][:80]}...')
"
```

Server-retrieved vs model-cited. Divergence is hallucination.

---

## G. Model knows something it shouldn't

**Trigger**: model response includes information that shouldn't be
in the firm's corpus.

### G.1 Diagnose

```bash
# 1. Is the unexpected doc actually present?
grep -rli "<distinctive phrase>" data/

# 2. Did the model know without retrieval?
# (sources_retrieved == 0 in the response means yes — general knowledge)

# 3. Check Qdrant directly
curl -sk -H "api-key: $QDRANT_API_KEY" \
  -X POST http://localhost:6333/collections/locallyai_legal_poc/points/scroll \
  -H "Content-Type: application/json" \
  -d '{"limit": 5, "filter": {"must": [{"key": "source", "match": {"value": "<filename>"}}]}}'
```

### G.2 Fix branches

| What you found | Fix |
|---|---|
| Doc in data/ when it shouldn't be | Remove + re-index. See `incidents-operator.md` "Accidentally ingested wrong document" |
| Doc removed from data/ but still in Qdrant | `python ingest.py --force` |
| Model knew from general knowledge | Can't unlearn pre-training. Brief users |

### G.3 After-action

**If the leak was to an unauthorised user**: treat as a
confidentiality incident. **Escalate to the DPO**, who assesses
scope. This may be a notifiable incident.

---

## H. Regression — "it worked yesterday"

**Trigger**: something the user did fine yesterday no longer works.

### H.1 Common causes (check in order)

1. Model was updated → roll back. `ollama rm <new>`, `ollama pull <old>`, `OLLAMA_MODEL=<old>` in `.env`, restart.
2. `ingest.py --force` ran → chunk boundaries renumbered. Old DOC 3 = new DOC 5. Acceptable for retrieval; user-confusing for cite continuity. Document.
3. Dependency upgraded during `update.sh` → `pip list --outdated`; read CHANGELOGs.
4. System prompt was edited (api.py:528-534, `base_persona`) → `git diff` to see.
5. Retrieval params changed (CHUNK_SIZE, TOP_K, RELEVANCE_FLOOR in config.py).

### H.2 Diagnose

```bash
git log --oneline -20
```

Roll back any commit that obviously corresponds to the regression.
Test. Git bisect if needed.

### H.3 After-action

| Change was | Action |
|---|---|
| Deliberate (model upgrade, retrieval tuning) | Brief users on new behaviour |
| Accidental | Revert. Add a test that catches it next time |

---

## I. User can't sign into worker-ui

**Trigger**: user pastes their key, gets "Invalid API key" or no
response.

### I.1 Gather

Have the user send:
- First 8 chars of the key (typo check)
- Screenshot of error
- Browser console output (Cmd-Opt-J on Chrome)

### I.2 Walk through causes

| Cause | Confirm | Fix |
|---|---|---|
| Key rotated | `manage_users.py list` shows the username | Re-issue per `manage_users.py rotate <name>` |
| Key erased (Art-17) | Check `erasure.log` | The user is gone, by design. Brief |
| Browser doesn't trust TLS cert | Console: `net::ERR_CERT_AUTHORITY_INVALID` | Re-trust per `setup-mac-single.md` 3.4 |
| Worker-ui hardcoded wrong URL | `apps/worker-ui/.env.local` | Edit `VITE_API_BASE_URL` to the firm's deployment |
| Network blocked | `curl -sk https://<host>:8000/healthz` from user's machine fails | Firm-IT problem |
| CORS rejecting | `LOCALLYAI_CORS_ORIGINS` in `.env` lacks user's origin | Add origin, restart |

---

## J. Worker-ui crashes / freezes

**Trigger**: browser tab freezes, becomes unresponsive, or crashes.

### J.1 Cause

Almost always client-side: too much history loaded, browser
extensions interfering, or extreme conversation (50+ turns).

### J.2 Fix

- Hard refresh (Cmd-Shift-R / Ctrl-Shift-R)
- Clear localStorage for the worker-ui (in browser console):
  ```js
  localStorage.clear()
  ```
  User signs in again. Conversation history is lost (it's stored in
  localStorage).
- Try in a private window to isolate extensions.

---

## SLO check: response time

If your firm sets a SLO ("95% of chats answered in <5s"), monitor it:

```bash
.venv/bin/python <<'PY'
import json
from collections import Counter
buckets = Counter()
for line in open('logs/audit.log'):
    e = json.loads(line)
    ms = e.get('latency_ms', 0)
    if ms < 1000: buckets['<1s'] += 1
    elif ms < 5000: buckets['1-5s'] += 1
    elif ms < 30000: buckets['5-30s'] += 1
    else: buckets['>30s'] += 1
total = sum(buckets.values())
for b in ['<1s', '1-5s', '5-30s', '>30s']:
    n = buckets.get(b, 0)
    print(f'{b:6s}: {n:6d}  ({n/total*100:.1f}%)')
PY
```

Target: >95% under 5s for short prompts. >5% over 30s suggests gate
pressure or model size mismatched to RAM.

---

## Things that go wrong

| Symptom | Cause | Fix |
|---|---|---|
| `curl` hangs forever waiting on chat completion | Model crash mid-inference | Check `service.log`; restart per `runbooks/api-down.md` |
| Latency only bad for KSA-region installs | Multilingual embed model swap during install | Confirm `EMBED_MODEL=intfloat/multilingual-e5-base` in `.env`; if wrong, re-ingest |
| `sources` field empty even when retrieval should fire | `RELEVANCE_FLOOR` set too high | Lower in api.py, restart |
| Truncation pattern matches `max_tokens=100` exactly | A custom integration is hardcoded low | Find the client, raise it |
| Model "knows" client B's data from client A's deployment | **STOP**. This is impossible by architecture. If you actually see this, **escalate immediately** — possible hardware mix-up or restore from wrong backup |

---

## When to escalate

**Always** escalate for:
- Hallucinated citation that harmed a user (acted on bad advice)
- Model surfacing information from a different firm (cross-tenant leak — architecturally impossible; if observed, it's a critical incident)
- Latency degradation that doesn't fit any branch in procedure A
- Regression where `git bisect` doesn't land on an obvious commit
- Any case marked "escalate" inline above
- Three or more incidents from the same firm in 7 days (the pattern matters)

**Don't escalate** for:
- Documentation gaps in this chapter (file a bug — these are valuable, not interrupts)
- A normal-shape incident you're working through inside the time budget
- Routine model-swap questions

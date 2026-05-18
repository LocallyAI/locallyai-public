# Incident playbooks — misuse & insider risk

The category that's hardest to talk about with the firm: a legitimate
authorised user is doing something they shouldn't. These aren't
breaches in the technical sense — credentials are valid, access is
expected — but they are policy violations that the SOP has to address
because the audit log catches them and the firm has to act.

---

## Authorised user using LocallyAI for non-work queries

**Trigger:** auditing of `audit.log` (via pseudonyms — IT-ops can't
read names from audit) shows usage patterns that don't fit work,
e.g. queries at 2am, weekend volume from a particular pseudonym,
clearly personal-curiosity prompts (only visible if the firm decides
to log query content, which LocallyAI does NOT by default).

### Detection

LocallyAI's defaults make this hard to detect on purpose — the
audit log carries only `query_hash` and `sources_retrieved`, not
content. Patterns you CAN see:

```bash
.venv/bin/python <<'PY'
import json
from collections import defaultdict
from datetime import datetime, timezone
hours = defaultdict(int)
for line in open('logs/audit.log'):
    e = json.loads(line)
    t = e.get('timestamp')
    try:
        h = datetime.strptime(t, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).hour
    except Exception:
        continue
    hours[(e['user_hash'], h)] += 1

# Print users who queried >5 times outside 8-19 UTC
for (u, h), n in sorted(hours.items()):
    if h < 8 or h >= 19:
        if n > 5:
            print(u, f'hour {h:02d}:00', n, 'queries')
PY
```

### Action

This is a firm HR / management issue, not an IT incident. IT-ops:

1. Anonymises the report (pseudonyms, not names) and hands to the
   firm partner / line manager who can re-identify and have the
   conversation.
2. Does NOT directly approach the user with their pseudonym.
3. Does NOT enable query-content logging without DPO + legal sign-off
   — that's a privacy regression.

If the firm decides to police personal use:

- The firm's acceptable-use policy must explicitly say so. Not the
  SOP — the firm's HR documents.
- Users must be told: "Your AI usage is auditable by metadata
  patterns. Personal-curiosity use is not appropriate during work
  hours / on firm hardware."

### After-action

If a user is sanctioned: their key is rotated (standard rotation,
not the leak procedure — they're still an authorised user, just
with new credentials). The pseudonym mapping is preserved because
the salt didn't change.

---

## User asking about other clients' matters

**Trigger:** a user's queries reference matter codes that don't match
the cases assigned to them.

### Detection

If matter codes are populated in audit:

```bash
.venv/bin/python <<'PY'
import json
from collections import defaultdict

# Build a {pseudonym: set(matter_codes)} map
codes_per_user = defaultdict(set)
for line in open('logs/audit.log'):
    e = json.loads(line)
    if e.get('matter_code'):
        codes_per_user[e['user_hash']].add(e['matter_code'])

# Hand the per-user set to a partner who can map pseudonyms to names
# and check whether the matter-code set matches what they should be on.
for u, codes in codes_per_user.items():
    print(u, sorted(codes))
PY
```

### Action

This is a serious policy violation in regulated firms (SRA
Conflicts-of-interest rules; equivalent in UAE/KSA jurisdictions).

1. **Preserve evidence.** Do not erase anything; do not rotate the
   user's key (yet).
2. **Hand the report to the firm's COLP** (Compliance Officer for
   Legal Practice; equivalent name in other jurisdictions).
3. **Wait for the COLP's instruction** before any operational action.

### What you can technically do (if instructed)

- Restrict the user's RAG retrieval to specific matter codes (would
  need a code change to LocallyAI; mark as future-feature).
- Rotate the user's key and on re-issue, brief them on matter scope.

### After-action

The COLP files. The user may be sanctioned. The deployment continues
to operate.

---

## Two users sharing one key

**Trigger:** `audit.log` shows simultaneous activity attributed to
one pseudonym, OR the security log shows logins from incompatible
locations (per-IP fingerprints differ wildly).

### Detection

```bash
# Look for the same pseudonym hitting from very different IPs:
.venv/bin/python <<'PY'
import json
from collections import defaultdict
ips = defaultdict(set)
for line in open('logs/security.log'):
    try:
        e = json.loads(line)
    except Exception: continue
    if e.get('event') == 'auth_success':
        ips[e['key_fp']].add(e.get('ip'))
# Flag any key_fp seen from >2 distinct IPs in the past N days
for fp, addrs in ips.items():
    if len(addrs) > 2:
        print(fp, addrs)
PY
```

### Action

Sharing a key is a violation of the firm's acceptable-use policy
(the user typically signed an "I will not share my credentials"
clause).

1. **Rotate the key immediately:** `manage_users.py rotate <user>`.
   Print the new key to the legitimate user; tell them it can't be
   shared. The old key 401s immediately.
2. **Treat as a near-miss.** The activity wasn't unauthorised in the
   credential-validity sense, but the firm now has activity on
   record where it can't attribute responsibility — which is a
   compliance problem.
3. **Brief the user.** If two team members were sharing because
   "it's easier than each having a key" — give them each their own.
4. **Document the incident.** "Joint use detected; key rotated;
   user counselled."

---

## User pasting outputs into public chats / social media

**Trigger:** the firm spots LocallyAI output in a Slack channel
outside the firm, on a personal blog, in a conference talk, etc.

### Why this matters

LocallyAI's outputs may include:

- Snippets of confidential firm documents (the RAG context).
- Information derived from privileged documents.
- Information that — combined — identifies which clients the firm
  works with.

A user pasting an output to a public channel is leaking the firm's
information, even if no individual sentence is confidential.

### Action

1. The firm's COLP / managing partner addresses the user. Out of
   IT-ops's scope.
2. **IT-ops can identify which queries produced the output:**
   Hash the leaked text:
   ```bash
   echo -n "the leaked sentence here" | shasum -a 256 | head -c 64
   ```
   That's the `query_hash` field if the user happened to literally
   ask "tell me X" and the model echoed; usually not how this works.
   More usefully: search audit.log for the time window when the
   leak might have happened, find the user's pseudonym in that
   window:
   ```bash
   awk -v since='2026-05-01T00:00:00Z' -v until='2026-05-06T00:00:00Z' \
     'NR==1 || ($0 ~ since || $0 > since) && $0 < until' logs/audit.log
   ```
   Hand to the COLP for re-identification.

### After-action

User-side disciplinary action is firm-policy. IT-ops side: rotate
the user's key, brief them on what is and isn't shareable.

---

## Privileged user (admin) is the bad actor

**Trigger:** an IT-ops or vendor person — someone with the admin
key — is suspected of misuse. Maybe they're exporting bulk audit
data without justification, maybe a partner reports them accessing
unrelated matters.

### Why this is the worst case

The admin key authorises everything the SOP grants — including:

- Reading the salt and re-identifying every pseudonym.
- Reading billing.log (real names).
- Verifying audit chains (ok, that's harmless).

If the admin is the bad actor, the SOP's normal procedure (lock the
user, rotate their key) doesn't help — the admin can rotate their
own key back, can erase the user who reported them, can manipulate
records.

### Action — never attempt this alone

This is a firm-management problem. The DPO + senior partners must:

1. **Suspend the admin's access via human-not-IT means.** The
   admin's macOS / Windows account is disabled by another IT-ops
   person or by the firm's MDM. Their physical access to the
   deployment box is suspended.
2. **Generate a new admin key by physical access to the box** by an
   uncompromised person (the CEO with a notebook + the SOP, if
   needed). Edit `.env`, `chmod 600`, restart. The old admin's key
   is now dead.
3. **Rotate the salt, the HMAC key, every user key.** Treat as a
   suspected breach by privileged insider (Art. 33 — the privileged
   admin had read access to all of it).
4. **Forensic capture** of the admin's machine if available.
5. **Counsel** decides on disciplinary / criminal proceedings.

### What the SOP can do, structurally

Limit blast radius proactively:

- The admin key is for `/admin/*` endpoints; routine ops doesn't
  need it. IT-ops uses it occasionally.
- Two-key admin (a future improvement to LocallyAI's codebase): two
  separate roles — daily-admin (restart, audit-verify) and
  privileged-admin (rotate, erase, processing-record). Not yet
  implemented.

For now: **rotate the admin key annually** even if no incident,
and treat anyone holding it as if they were under quarterly
review. Standard separation-of-duties.

### After-action

- File as Art. 33-eligible breach; counsel + DPO drive.
- Update [CHANGELOG.md](CHANGELOG.md) with a sanitised entry.
- Add lessons to the SOP: was there a control gap that allowed it?

---

## Model jailbreak attempts

**Trigger:** in a deployment that logs query content (it doesn't by
default — but if your firm enables it), patterns of "ignore previous
instructions," role-play setups, or known jailbreak prompts.

### Default detection (LocallyAI default)

The chat handler's system prompt explicitly instructs the model to
treat retrieved-context content as DATA not instructions, and to
refuse persona changes / system-prompt revelation / behaviour-changes.

When a chunk is retrieved with classic injection markers, sentinel
posts a `rag_suspicious_chunk` event to `security.log` (api.py:519-526).

### Detect patterns

```bash
grep "rag_suspicious_chunk" logs/security.log | tail -20
```

This catches **document poisoning** — a malicious document fed into
ingestion. Less common than user-side jailbreaks.

For user-side jailbreaks: the audit log has only the query hash. To
detect, you'd need to log query content (a privacy regression).
Don't enable that lightly. If you do, the firm's privacy notice
must reflect it.

### Action

Document poisoning case: identify the chunk source, remove the
document from `data/`, re-index. See
[incidents-operator.md § "Accidentally ingested wrong document"](incidents-operator.md#accidentally-ingested-wrong-document)
for the mechanics.

User jailbreak case: depends on the user's role. A curious user
testing limits is different from an external party using a stolen
user key to probe. The latter is
[incidents-security.md § "User key leak"](incidents-security.md#user-key-leak).

---

## Bot-driven scraping by an authorised account

**Trigger:** one user's pseudonym is producing 100s of queries per
hour with similar query-hash patterns; sentinel may flag
`WARN_LOG_STORM`.

### Detection

```bash
.venv/bin/python <<'PY'
import json
from collections import Counter
from datetime import datetime, timezone

now_h = Counter()
for line in open('logs/audit.log'):
    e = json.loads(line)
    try:
        ts = datetime.strptime(e['timestamp'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except Exception: continue
    if (datetime.now(timezone.utc) - ts).total_seconds() < 3600:
        now_h[e['user_hash']] += 1
for u, n in now_h.most_common(5):
    print(u, n)
PY
```

A user with 50+ queries/hour is either heavily working or running
a script.

### Action

The rate-limit middleware (slowapi) caps at 30/minute per IP, so
this is bounded — but a determined user could distribute across
sessions. Steps:

1. Identify the user via the firm partner with re-identification
   authority.
2. Confirm whether the activity is legitimate (e.g. a paralegal
   doing systematic doc review).
3. If legitimate but heavy: brief the user that high-volume use
   should go through a different channel (direct DB access, an
   export, etc.); LocallyAI is for chat-style queries.
4. If illegitimate (bot-driven): rotate their key; treat as a
   misuse incident; firm HR action.

### Prevention

The rate-limit caps already prevent the worst case. For a stricter
policy, lower `30/minute` in api.py to `10/minute` per IP.

---

## "Ghost" user (key still works after off-boarding)

**Trigger:** a user has left the firm but their key still
authenticates. Either the off-boarding process was incomplete or
someone re-issued the key without the firm partner's awareness.

### Detection

```bash
python manage_users.py list
# Cross-reference with HR's current employee list.
```

Names in `users.json` that aren't in HR's list = ghosts.

### Action

For each ghost:

```bash
# Choose: remove (clean off-board) or erase (right-to-be-forgotten)
python manage_users.py remove "Ex-Employee Name"
# OR
python manage_users.py erase "Ex-Employee Name"
```

Use `remove` unless they've formally requested Art. 17 erasure or
the firm's policy is to erase departed staff.

### After-action

Audit how the off-boarding broke: missing checklist item? Manual
add-user that bypassed the HR feed? Add the gap to the firm's
off-boarding checklist.

---

## High-volume queries from leadership

**Trigger:** the managing partner sends a polite email: "Why is
LocallyAI so slow today?"

### What's actually happening

Likely scenarios:

1. **A user is running a stress test** — see [Bot-driven scraping](#bot-driven-scraping-by-an-authorised-account)
   above.
2. **The model itself is slower** than usual — `monitor/health/detailed`
   shows ollama latency degraded.
3. **A document ingest is running in the background** — check `ps`
   and the ingest state.
4. **Cold-loaded model just expired** — first request after Ollama's
   keep-alive lapse takes 30s+.

### Action

Investigate, fix the underlying cause, send a 1-line response with
the cause. Leadership respond well to "User X was running a 200-doc
RAG test; I've throttled them. Latency back to normal." They respond
poorly to "I'm investigating."

---

## Annual misuse self-check

Once a year, with the firm's COLP / DPO:

- [ ] Pseudonym → name re-identification only happens on
      need-to-know basis. Who has done it in the past year, and was
      it justified?
- [ ] Audit-log statistics: any user pseudonym with anomalous volume
      or temporal patterns?
- [ ] Have any users been disciplined for LocallyAI misuse? Was the
      action documented?
- [ ] Is the firm's acceptable-use policy current?
- [ ] Are users briefed on it (annual training)?

# Scale-out & migration

When the firm grows, when the topology needs to change, when one
deployment becomes two, when Mac becomes Windows, when a single-node
becomes HA. The procedures here are usually planned (not incidents)
but they involve user-visible transitions that need a script.

---

## When to scale up

| Symptom | Likely action |
|---|---|
| `inference_gate.peak_queue` regularly hits `max_queue` | Tune the gate (cheap) — [maintenance.md § "Inference-gate tuning"](maintenance.md#inference-gate-tuning) |
| Tuning didn't help; chats are queueing >2s regularly | Add a 2nd node (HA) |
| Already 2-node HA; chats are queueing | Add a 3rd node (requires a NAS) — see [Migrate from 2-node to 3-node + NAS](#migrate-from-2-node-to-3-node--nas) |
| Disk regularly >80% full | Bigger disk, or earlier audit-archive purge |
| Model is consistently giving wrong answers | Bigger model (needs RAM headroom) |
| Single node, single-point-of-failure unacceptable | 2-node HA |

---

## When NOT to scale

- A user complains about a single slow request → check for cold-load,
  not capacity.
- A user complains about a wrong answer → service quality issue
  ([incidents-service.md](incidents-service.md)), not capacity.
- The firm wants "always available" but is fine with 5-min outages
  during quarterly updates → single-node + good ops is enough.

---

## 5 → 50 user growth

LocallyAI scales reasonably to 50 active users on a single Mac
Studio with the right model + the gate at 6-12 inflight. Past 50,
2-node HA is the next step; past 100, you're in 3-node + NAS
territory.

### Hardware planning

| Active concurrent users | Model | RAM | Topology |
|---|---|---|---|
| ≤ 5 | 7B model | 16-32 GB Mac (mini or laptop) | Single-node |
| 5-20 | 14B model | 32-64 GB Mac Studio | Single-node |
| 20-50 | 14B-32B model | 64+ GB Mac Studio | Single-node OR HA |
| 50-100 | 32B-70B model | 128 GB Mac Studio Ultra | 2-node HA |
| 100-300 | 70B model | 192 GB+ | 3-node + NAS |
| 300+ | Multi-model fleet | Multiple Studios | Custom — talk to vendor |

### Watch for

The transition from "20-50 concurrent" to "50+ sustained" usually
catches firms by surprise. Symptoms:

- Sentinel posts CRIT_DISK_LOW more often (more chat = more audit
  growth, faster archive turnover).
- Inference gate queue is non-zero on most monitor checks.
- Latency creeps up steadily.

When you see two of those: start planning the next topology BEFORE
users start complaining.

---

## Migrate single-node to 2-node HA without downtime

**Goal:** add a second Mac while users are still using the first;
zero user-visible interruption.

### Pre-conditions

- The new (Mac-B) hardware is procured and on the LAN.
- macOS installed, FileVault enabled, Time-sync verified, all per
  [setup-mac-single.md § 0](setup-mac-single.md#0-pre-flight-5-min).
- You have ~2 hours and a quiet maintenance window for the final
  cutover (the bring-up itself is hands-off; the cutover is brief).

### Procedure

1. **Install Mac-B as single-node** per
   [setup-mac-single.md](setup-mac-single.md). Use the SAME `.env`
   secrets as Mac-A — copy `LOCALLYAI_ADMIN_KEY`,
   `LOCALLYAI_AUDIT_HMAC_KEY`, `LOCALLYAI_AUDIT_SALT` from Mac-A's
   `.env` into Mac-B's `.env` BEFORE first start. (If Mac-B starts
   with a fresh install, install.sh generates new secrets — that's
   the wrong direction for HA.)

   Workaround if you forgot: stop Mac-B, edit its `.env` to match
   Mac-A's secrets, delete Mac-B's `logs/.audit_chain` (so it
   starts a fresh chain), restart.

2. **Verify Mac-B is single-node-functional** independently before
   pairing. `audit_install.sh` on Mac-B → pass=14.

3. **Set up Syncthing on both Macs** per
   [setup-mac-ha.md § 1](setup-mac-ha.md#1-set-up-the-shared-store-syncthing--10-min-per-mac).
   Pair them; wait for "Up to Date." This is non-destructive — Mac-A
   keeps serving from its current `users.json`.

4. **Move shared state into the synced folder ON MAC-A ONLY**:
   ```bash
   # Mac-A:
   cp users.json shared/users.json
   chmod 600 shared/users.json
   # Don't rm Mac-A's users.json yet; keep it until cutover.
   ```
   Within 30 s, `shared/users.json` appears on Mac-B with the same
   content.

5. **Stand up the 2-node Qdrant cluster** per
   [setup-mac-ha.md § 3](setup-mac-ha.md#3-stand-up-the-2-node-qdrant-cluster--10-min).
   Mac-A's existing single-node Qdrant **becomes** the bootstrap
   peer; Mac-B joins. Replicate shards.

6. **Cutover (the ~5-minute window)**:
   - On Mac-A:
     ```bash
     # Edit .env: add LOCALLYAI_HA=1, LOCALLYAI_NODE_ID=mac-a,
     # LOCALLYAI_SHARED_DIR, QDRANT_URLS, QDRANT_API_KEY.
     # Then:
     rm users.json   # the unsynced copy is now stale
     ln -s shared/users.json users.json   # symlink so existing code still works
     # OR — if you prefer config to drive it:
     # the new SHARED_DIR setting already routes USERS_FILE → shared/users.json
     # so the symlink isn't needed.
     launchctl kickstart -k gui/$(id -u)/com.locallyai.server
     ```
   - On Mac-B:
     ```bash
     # Edit .env: same as Mac-A but LOCALLYAI_NODE_ID=mac-b
     launchctl kickstart -k gui/$(id -u)/com.locallyai.server
     ```

7. **Verify HA**:
   ```bash
   ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' .env | cut -d= -f2)
   curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
     https://localhost:8000/admin/fleet/nodes | python3 -m json.tool
   # Expect 2 alive.
   ```

8. **Update worker-ui** per
   [setup-mac-ha.md § 6](setup-mac-ha.md#6-configure-the-worker-ui-smart-client--3-min).
   Distribute to users.

### What users see

During steps 1-5: nothing — Mac-A is still serving alone.

During step 6 cutover: 30-60 s where Mac-A is restarting; their
in-flight requests fail and (with the smart client distributed in
step 8) retry on Mac-B once it's up. Without the smart client, they
see a 30 s outage.

If the smart client distribution happens AFTER cutover (e.g. the
firm needs a meeting to push the new worker-ui), users see the
outage during cutover and HA-failover behaviour starts after the
worker-ui rolls out.

### Rollback

If cutover goes wrong and you want Mac-A back as single-node:

```bash
# Mac-A:
# Edit .env: remove LOCALLYAI_HA, LOCALLYAI_SHARED_DIR, QDRANT_URLS, etc.
# Restore users.json:
cp shared/users.json users.json   # if you symlinked it
chmod 600 users.json
launchctl kickstart -k gui/$(id -u)/com.locallyai.server
# Mac-B can be left in whatever state; it isn't serving any more.
```

The audit log on Mac-B is preserved (separate per-node chain) and
can be re-included later.

---

## Migrate from 2-node to 3-node + NAS

**Goal:** the firm has scaled past what 2-node Mac fleets handle
and you need full HA (3-node Qdrant for raft majority + a NAS for
the shared store).

This is a significant infrastructure project. Read
[../iso27001-controls.md § A.5.30](../iso27001-controls.md) and
[../qdrant-ha.md](../qdrant-ha.md) before starting.

### Pre-conditions

- A NAS or shared filesystem reachable from both existing Macs +
  the new third one. NFS or SMB; firm's choice.
- The third Mac/PC procured.
- A maintenance window (4-6 hours; this is touchier than the 1→2
  migration).

### Procedure (high-level — full step-by-step exists in the future
LocallyAI HA-3-node guide; here's the skeleton)

1. **Mount the NAS on all three Macs** at the same path (e.g.
   `/Volumes/locallyai-shared`).
2. **Migrate the contents** of the existing Syncthing-managed
   `shared/` to the NAS path:
   ```bash
   # Mac-A:
   rsync -av shared/ /Volumes/locallyai-shared/
   # Stop Syncthing on both nodes.
   ```
3. **Update `.env` on all three** to point `LOCALLYAI_SHARED_DIR=
   /Volumes/locallyai-shared`.
4. **Add the third Qdrant peer.** It joins the existing cluster.
   Once 3 peers are present, change the collection's
   `replication_factor` to 3 and `write_consistency_factor` to 2
   (majority of 3) — see
   [../qdrant-ha.md § "Re-adding a wiped node"](../qdrant-ha.md#re-adding-a-wiped-node)
   for the patterns.
5. **Update worker-ui smart-client** with three URLs.
6. **Stand up an HAProxy + keepalived pair** (or the equivalent
   load-balancer-with-VIP solution) so the worker-ui sees one
   floating address.
7. **Smoke-test failover** by stopping each node in turn.

### What's harder than 1→2

- NAS fail-over: the NAS itself is now a SPOF. Either the NAS is
  HA-clustered (TrueNAS HA pair) or you accept that "two cards in
  one box can fail and take the whole storage with it."
- Qdrant raft: 3 peers means quorum needs 2; lose 2 and the
  cluster goes read-only. With 5 you can lose 2 and stay healthy.
- More moving parts means more incident playbooks. Update SOP
  accordingly.

---

## Mac → Windows migration

**Goal:** the firm has decided to standardise on Windows / DGX
Spark; the existing Mac deployment is being retired.

### Constraint

A single fleet must be all-Mac OR all-Windows. **You cannot
gradually migrate** — it's a hard cutover.

### Procedure

1. **Stand up Windows boxes** in parallel per
   [setup-windows.md](setup-windows.md). They run independently of
   the Mac fleet during this phase.
2. **Replicate the documents.** Copy `data/` from Mac to Windows;
   `python ingest.py` on Windows.
3. **Replicate the user list.** Two options:
   - Same keys: copy `users.json` from Mac to Windows; the same
     keys keep working.
   - Fresh keys: `manage_users.py add` on Windows for each user;
     redistribute. (Cleaner but requires user-side updates.)
4. **Replicate the audit log** (read-only, for historical
   continuity):
   ```bash
   # On Mac:
   tar czf mac-audit-history.tar.gz logs/audit*.log* logs/.audit_chain
   # Copy to Windows:
   tar xzf mac-audit-history.tar.gz -C C:\locallyai\logs\
   ```
   The Mac chain stays verifiable on the Windows box (different
   node_id; the chain's HMAC key is what matters).
5. **Cutover**: tell users "From <date>, your worker-app URL
   changes." Update `VITE_API_BASE_URLS` to point at Windows
   boxes. Distribute new worker-ui build.
6. **Decommission Macs** per [decommission.md](decommission.md).

### Audit-chain continuity across the migration

The Mac fleet's audit history is preserved on the Windows box but
forms a separate per-node chain era. The Windows box starts a fresh
chain. Document this in [CHANGELOG.md](CHANGELOG.md) with a
"migration boundary" entry.

---

## User-cohort split (cohort A on model X, cohort B on model Y)

**Trigger:** the firm wants different user groups to use different
models — e.g. partners get the 70B model, paralegals get the 14B.

### LocallyAI doesn't support this natively

The default `OLLAMA_MODEL` env var is per-deployment. To split
per-user, you'd need a code change to look up the user's model
preference (in `users.json`) and pass it to `_infer`.

**Workaround without code change:** users can specify a model in
their request:

```bash
curl -sk -X POST -H "Authorization: Bearer $USER" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[...], "model": "qwen2.5:14b"}' \
  https://localhost:8000/v1/chat/completions
```

The chat handler honours `req.model` if set. So:

- Partners' worker-ui: hardcoded to send `model: "llama3.3:70b"`.
- Paralegals' worker-ui: omits `model` (uses default).

But a partner could still send the small model id manually; this
is honour-system, not enforced.

### Code-change path (future)

If your firm needs hard cohort-enforcement, the path is:

1. Add `model` field to `users.json` entries.
2. In `validate_key`, return the user's allowed model (or `None`
   for default).
3. In the chat handler, override `req.model` with the user's
   allowed model, ignoring whatever they sent.

Mark as a vendor feature request.

---

## Adding a new model alongside existing

**Goal:** the firm wants users to be able to choose a model from a
menu in the worker-ui.

### Pull the model

```bash
ollama pull <new-model>
```

For HA: pull on **both** nodes (`ollama` doesn't share models
across boxes).

### Confirm it's listed

```bash
curl -sk -H "Authorization: Bearer $USER_KEY" https://localhost:8000/v1/models
```

The `/v1/models` endpoint already lists every locally-installed
Ollama model. The worker-ui can render this as a dropdown.

### Set the firm's default

```bash
# .env:
OLLAMA_MODEL=<new-default>
```

Restart. Users who don't specify a model get this one.

---

## Decommissioning a single user from a multi-user deployment

Different from full erasure (Art. 17). Common case: a user leaves
the firm normally, no special legal process.

### Action

```bash
python manage_users.py remove "First Last"
```

Their key is dead. Their historical audit entries remain (under
their pseudonym). Their billing history remains (under their real
name) — for retroactive invoicing if needed.

### When to escalate to erase

If the user formally requests erasure under Art. 17 within their
legal window (typically 30 days post-departure for legitimate
business-purpose claims to expire), use `manage_users.py erase`
instead. See [compliance.md § "Article 17 erasure"](compliance.md#article-17--erasure-right-to-be-forgotten).

---

## Annual scale review

Once a year, with the firm management:

- [ ] Active user count vs current capacity.
- [ ] Model in use vs available models — should we upgrade?
- [ ] Hardware lifecycle — Macs over 3 years old begin to need
      replacement; budget for next year.
- [ ] Topology fit: does the firm need to move to 3-node + NAS, or
      stay at 2-node?
- [ ] Did we hit any capacity-driven incidents in the past year? At
      what point on the growth curve did they happen? Plan for the
      next inflection.

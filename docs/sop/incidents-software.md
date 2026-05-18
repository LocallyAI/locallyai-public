# Incident playbooks — software

Each section: **symptom → diagnose → fix → verify → after-action**.
Every fix is a real command, not a hand-wave.

If your incident isn't listed here, look in:
- [incidents-physical.md](incidents-physical.md) — power, hardware, network
- [incidents-security.md](incidents-security.md) — credentials, malware, suspected breach
- [incidents-operator.md](incidents-operator.md) — admin error / lost keys

> **VENDOR ON-CALL: bad release shipped or healthz failing across firms?**
> Jump straight to the [kill-switch runbook](updates.md#kill-switch-runbook--invoking-it-when-something-goes-wrong)
> in updates.md. One command + your phone's TOTP halts all updates
> across every firm within ≤60 s.

---

## API not responding

**Symptom:** `curl https://localhost:8000/healthz` hangs or returns
`Connection refused`. Worker app shows "Could not reach LocallyAI
server."

### Diagnose

```bash
# 1. Is the launchd / Windows-service running?
launchctl list | grep com.locallyai.server     # Mac — PID > 0?
Get-Service LocallyAIServer                    # Win — Status: Running?

# 2. Is uvicorn alive?
ps aux | grep uvicorn | grep -v grep           # Mac/Linux
Get-Process | Where-Object { $_.ProcessName -like "*python*" }   # Win

# 3. What does the service log say?
tail -50 logs/launchd_error.log                # Mac
Get-Content C:\locallyai\logs\service.log -Tail 50    # Win
```

### Fix branches

**Branch A — service not running.** Mac:
```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.locallyai.server.plist
```
Win:
```powershell
Start-Service LocallyAIServer
```

**Branch B — service running but uvicorn keeps dying.** Look at the
log; the most common causes:

- *Stuck old uvicorn holding port 8000.* The supervisor's pre-flight
  refuses to kill non-Python listeners. If `lsof -nP -tiTCP:8000
  -sTCP:LISTEN` (Mac) / `netstat -ano | findstr :8000` (Win) shows
  someone, kill them: `kill -TERM <pid>` / `taskkill /F /PID <pid>`.
- *MLX cold-load timed out.* The supervisor should now wait 240s for
  MLX before killing (see Phase 6). If you see "PID … did not bind
  :8000 within 240s", the model is genuinely failing to load — read
  `logs/launchd_error.log` for the MLX error.
- *Bad `.env` edit.* If you just edited `.env`, the most recent
  edit broke parsing. Restore from `.env.bak` if it exists, or
  carefully diff against the install template.

**Branch C — port 8000 held by something else (not Python).** Identify:
```bash
lsof -nP -iTCP:8000
```
Common culprits: AirPlay Receiver on macOS Sonoma+ (System Settings →
General → AirDrop & Handoff → AirPlay Receiver: OFF), a dev server,
nginx. Free the port; restart the service.

### Verify

```bash
until curl -skf -o /dev/null --max-time 2 https://localhost:8000/healthz; do sleep 4; done
echo READY
```

### After-action

If this happened mid-business-day in HA mode, the worker app should
have failed over. Check the dashboard — was there sustained 503
backpressure? Did peers go offline too? If yes, jump to
[Fleet desync](#fleet-desync) or [Network partition](incidents-physical.md#network-partition-between-macs).

---

## healthz returns 503

**Symptom:** `/healthz` returns 503 Service Unavailable for >30 s.

### Diagnose

```bash
curl -sk https://localhost:8000/monitor/health/detailed -H "Authorization: Bearer $ADMIN_KEY"
```

The response shows: ollama reachable?, disk free?, watchdog ok?,
inference_gate state?

### Fix branches

- **Ollama unreachable** → see [Ollama unreachable](#ollama-unreachable).
- **Disk full** → see [Disk full](#disk-full).
- **Inference gate at 503 capacity** → see [Gate at capacity](#gate-at-capacity).

---

## MLX cold-load loop

**Symptom:** `logs/launchd_error.log` shows API child being killed
every 30s with "PID … did not bind :8000 within 15s" / "API exited
(code -9)" and "API failed to bind".

### Diagnose

This bug was fixed in commit `1b7d153` (Phase 6) which raises the
verify-bound timeout to 240s for MLX. If you're seeing this:

```bash
git log --oneline | head -20
# Confirm 1b7d153 (or later) is in your tree.
```

If you're on an older version: update.

If on the latest version and still looping: MLX itself is broken on
your model. Check:

```bash
.venv/bin/python -c "from mlx_lm import load; m, t = load('<your model id>'); print('ok')"
```

If this hangs or errors, MLX can't load your model. Switch backend:

```bash
# Edit .env
LOCALLYAI_BACKEND=ollama
OLLAMA_MODEL=qwen2.5:14b   # whatever you have pulled
```

Restart.

### Fix

If you also see a `safe_mode.flag` file:

```bash
ls logs/safe_mode.flag
# If exists: an earlier incident left this. Remove only after you've
# fixed the underlying issue.
rm logs/safe_mode.flag
launchctl kickstart -k gui/$(id -u)/com.locallyai.server
```

### Verify

`/healthz` ok within ~5 minutes (MLX cold-load is genuinely slow).

---

## MLX OOM (memory exhausted)

**Symptom:** `Killed: 9` in logs, the API process restarting
repeatedly under memory pressure, system getting slow.

### Diagnose

```bash
# Mac:
vm_stat
top -l 1 | head -10
# Watch for "Pages free" near zero.
```

The most likely causes, in order:

1. Model is too big for the box. (e.g. 70B on a 32 GB Mac.)
2. Inference gate is too generous — too many concurrent requests
   each hold a model context.
3. Something else on the box is hogging memory.

### Fix

**Right-size the gate** (cheapest first):

```bash
# Edit .env:
LOCALLYAI_MAX_CONCURRENT_INFERENCE=3      # was 6
LOCALLYAI_INFERENCE_QUEUE_MAX=12          # was 24
```

Restart. If the OOMs continue, drop further to 2.

**Right-size the model**:

```bash
ollama pull qwen2.5:7b
# Edit .env: OLLAMA_MODEL=qwen2.5:7b
launchctl kickstart -k gui/$(id -u)/com.locallyai.server
```

For MLX: switch the `MLX_MODEL` env var to a smaller variant
(`mlx-community/Mistral-7B-Instruct-v0.3-4bit` instead of a 14B+).

### Verify

Drive a load test with the chaos suite:

```bash
.venv/bin/python tests/ha_chaos.py
```

12-concurrent test should pass without OOMs.

---

## Ollama unreachable

**Symptom:** `monitor/health/detailed` shows `ollama.reachable: false`.
Chats return 502.

### Diagnose

```bash
curl -sf http://localhost:11434/api/tags
# Mac: brew services list | grep ollama
# Win: Get-Service Ollama
```

### Fix

Mac:

```bash
brew services restart ollama
sleep 5
curl -sf http://localhost:11434/api/tags
```

Windows:

```powershell
Restart-Service Ollama
Start-Sleep 5
Invoke-RestMethod http://localhost:11434/api/tags
```

If Ollama is misbehaving entirely:

```bash
brew uninstall ollama
brew install ollama
brew services start ollama
ollama pull <your-model>
```

### Verify

```bash
curl -sf http://localhost:11434/api/tags
# Should list your models.
```

Then a chat against LocallyAI should succeed.

---

## Qdrant unreachable

**Symptom:** `monitor/alerts` shows `qdrant_lock` or `qdrant_down`.
Chats with retrieval (sources>0) fail; safe-mode chats still work.

### Diagnose

Single-node:

```bash
docker ps | grep qdrant
docker logs locallyai-qdrant --tail 50
curl -sf http://localhost:6333/healthz
```

HA:

```bash
curl -sf -H "api-key: $QDRANT_API_KEY" http://10.0.0.11:6333/cluster
curl -sf -H "api-key: $QDRANT_API_KEY" http://10.0.0.12:6333/cluster
```

### Fix

Single-node: `docker restart locallyai-qdrant`. If it won't start:
disk full? OOM? `docker logs` will say.

HA — one peer down: don't worry, the cluster runs degraded. Read
[../qdrant-ha.md § "Operating with one node down"](../qdrant-ha.md#operating-with-one-node-down).
If both down: `docker restart locallyai-qdrant` on **each**, then
verify cluster JSON shows two peers again.

### Verify

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/admin/fleet/qdrant-health
# mode: cluster (HA), or single (single-node), peer_count appropriate
```

---

## Qdrant split-brain

**Symptom:** Both nodes' `/admin/fleet/qdrant-health` show
`peer_count: 1` (only themselves). Writes are failing on both because
`write_consistency_factor=2` cannot be satisfied.

This means the LAN between the Macs is broken — Qdrant nodes can each
see themselves but not each other.

### Diagnose + Fix

Read the full procedure in
[../qdrant-ha.md § "Identifying split-brain"](../qdrant-ha.md#identifying-split-brain).

Fast path:
1. Fix the network (cable, switch, VLAN). `ping` between the Macs.
2. Once they can see each other, Qdrant Raft auto-reconverges within
   seconds.
3. If not: read `../qdrant-ha.md § "Re-adding a wiped node"` —
   sometimes one side comes back fresh and needs to be force-removed
   from the cluster's peer list before joining as new.

### Verify

`peer_count: 2` on both nodes' qdrant-health response.

---

## Audit chain TAMPERED

**Symptom:** `/admin/audit-verify` returns `{"status":"TAMPERED",
"source": "...", "broken_at_line": ...}` (or `reason: "tail truncated:
chain head does not match .audit_chain"`).

### This is serious. Stop and read.

The chain is HMAC-SHA-256 over every prior entry. TAMPERED means one
of:

1. **Someone or some process modified `audit.log`** outside our
   writer. Hostile, accidental, or operator-induced.
2. **Tail truncation** — `audit.log` was emptied while `.audit_chain`
   still held a head pointing at content that's now gone.
3. **A corrupted archive** — one of the gz files is unreadable; the
   chain step that should have followed it can't.

### Diagnose

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/audit-verify
# Read source + broken_at_line + reason.
```

If `source: audit-YYYY-MM-DD.log.gz`: a rotated archive is broken.
Look at it:

```bash
gzcat logs/audit-2026-05-04.log.gz | head -3
gzcat logs/audit-2026-05-04.log.gz | tail -3
```

If the archive is corrupt (gzip errors), see [Corrupt archive](#corrupt-archive)
below.

If `source: audit.log` and `reason: tail truncated`: someone wiped
audit.log without removing `.audit_chain`.

If `broken_at_line: N`: a specific entry doesn't HMAC-match. Print it:

```bash
sed -n "${N}p" logs/audit.log
```

### Fix

**You do not "fix" a tampered chain.** The chain is integrity evidence.
Restoring it would destroy the evidence that something happened.

What you actually do:

1. **Preserve evidence**: copy `audit.log`, all `audit-*.log.gz`,
   `.audit_chain`, `security.log`, `.env` (without sharing the salt)
   into `~/incident-<date>/`. See
   [compliance.md § "Article 33"](compliance.md#article-33--personal-data-breach-notification).
2. **Determine cause**:
   - Did an admin recently `> audit.log` it? (Check shell history.)
   - Did a script under your control truncate it?
   - Is the disk failing? `Disk Utility → First Aid` (Mac) /
     `chkdsk C:` (Win).
   - Is there a malware indicator? Run
     [incidents-security.md § "Suspected unauthorised access"](incidents-security.md#suspected-unauthorised-access).
3. **Establish a NEW chain era**, ONLY after the cause is documented:
   ```bash
   launchctl bootout gui/$(id -u)/com.locallyai.server
   mv logs/audit.log     ~/incident-<date>/audit.log.tampered
   mv logs/.audit_chain  ~/incident-<date>/.audit_chain.tampered
   touch logs/audit.log
   chmod 640 logs/audit.log
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.locallyai.server.plist
   ```
   The new chain starts from `0000…`. The OLD chain is preserved in
   the incident folder for forensics.
4. **File the incident** as a personal-data breach (Art. 33) until you
   prove it wasn't one.
5. **Notify the DPO** with the evidence pack.

### Verify

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/audit-verify
# {"status":"ok","entries":0,…}
```

### After-action

- Add a guardrail so the truncation can't happen accidentally again.
  e.g. set `chmod 0440 logs/audit.log` and grant write only to the
  service account, not your interactive shell user. (The supervisor
  re-tightens to 0640 on every boot, so this would need a small
  per-deployment patch to keep.)

---

## Corrupt archive

**Symptom:** `audit-verify` returns `{"status":"TAMPERED", "source":
"audit-YYYY-MM-DD.log.gz", "reason": "unreadable archive: ..."}`.

### Fix

If you have a known-good copy of that archive in cold storage,
restore it:

```bash
cp /backup/path/audit-YYYY-MM-DD.log.gz logs/audit-YYYY-MM-DD.log.gz
chmod 640 logs/audit-YYYY-MM-DD.log.gz
```

Then `audit-verify` again.

If you don't have a backup, the archive is lost. Move it aside and
start a new chain era as in [Audit chain TAMPERED](#audit-chain-tampered)
step 3. Document the gap in your DPO records.

---

## Sync conflict

**Symptom:** Sentinel posts a `sync_conflict` alert. Fleet dashboard
"Sync conflicts" panel is non-empty.

### Diagnose

```bash
ls $LOCALLYAI_SHARED_DIR/conflicts/
# users.sync-conflict-20260506-101245-AABBCC.json
```

### Fix

Inspect both versions — the live `users.json` (the winner per
Syncthing's last-writer-wins) and the conflict file (the loser):

```bash
diff <(cat $LOCALLYAI_SHARED_DIR/users.json) <(cat $LOCALLYAI_SHARED_DIR/conflicts/users.sync-conflict-*.json)
```

Decide which one is correct. Possibilities:

- A user was added on Mac-A while another was added on Mac-B at the
  same instant. **Manually merge**: take the winning users.json, add
  the user from the loser, save.
- An accidental edit on one side. Take the other.

To apply your decision:

```bash
# If conflict file is correct:
mv $LOCALLYAI_SHARED_DIR/conflicts/users.sync-conflict-*.json \
   $LOCALLYAI_SHARED_DIR/users.json
chmod 600 $LOCALLYAI_SHARED_DIR/users.json

# If live is correct:
rm $LOCALLYAI_SHARED_DIR/conflicts/users.sync-conflict-*.json
```

Force a refresh on both nodes:

```bash
ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' .env | cut -d= -f2)
curl -sk -X POST -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/admin/fleet/refresh
```

Then on the peer:

```bash
curl -sk -X POST -H "Authorization: Bearer $ADMIN_KEY" \
  https://<peer-ip>:8000/admin/fleet/refresh
```

### Verify

Sentinel alert clears within 60s. Both nodes show identical user
counts:

```bash
python manage_users.py list   # on each node
```

---

## Fleet desync

**Symptom:** `/admin/fleet/nodes` on Mac-A shows Mac-B as `alive:
false` (or vice versa) BUT Mac-B's API is responding when you hit it
directly.

### Diagnose

```bash
# On Mac-A:
ping -c 3 <Mac-B IP>
curl -skf https://<Mac-B IP>:8000/healthz
cat $LOCALLYAI_SHARED_DIR/fleet.json
```

### Fix

If `fleet.json` doesn't list Mac-B at all → Mac-B's startup didn't
register. Restart Mac-B's service.

If `fleet.json` lists Mac-B with an old `last_seen` (>90s) but the
sentinel on Mac-B isn't refreshing → Mac-B's sentinel thread died.
Restart Mac-B's service.

If `fleet.json` is up-to-date on Mac-B but stale on Mac-A → Syncthing
isn't replicating. Check Syncthing GUI on Mac-A:
`http://127.0.0.1:8384` → Out of Sync? → fix the sync layer per
[../syncthing-setup.md](../syncthing-setup.md).

### Verify

Both nodes' `/admin/fleet/nodes` show 2 alive within 60s.

---

## Gate at capacity

**Symptom:** `/admin/fleet/gate` shows `total_rejected > 0`. Users
report intermittent 503s.

### Diagnose

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/fleet/gate | python3 -m json.tool
# in_flight near max_inflight, queued near max_queue, total_rejected climbing.
```

### Fix

If you have RAM headroom:

```bash
# .env
LOCALLYAI_MAX_CONCURRENT_INFERENCE=8     # was 6
LOCALLYAI_INFERENCE_QUEUE_MAX=32         # was 24
```

Restart.

If you don't have headroom: the box is full. Either add a node (HA),
upgrade hardware, or accept the 503-driven backpressure as the right
behaviour for capacity protection.

### After-action

The gate is doing its job — it's preventing OOM. Don't disable it.
[maintenance.md § "Inference-gate tuning"](maintenance.md#inference-gate-tuning)
explains the trade.

---

## Streaming wedge (closed-consumer)

**Symptom (pre-Phase 9):** First chat after a browser-close-mid-stream
hangs forever. Phase 9 fix (commit a9b5796) prevents this. If you see
it now, you're on an older version — `git pull` and `bash update.sh`.

### Verify the fix is in

```bash
grep -q "abort_event" mlx_inference.py && echo "Phase-9 fix present" || echo "MISSING — update"
```

---

## Sentinel not running

**Symptom:** No `[INFO] Sentinel started` line in `launchd_error.log`
in the past 60 minutes; rotation isn't firing; alerts aren't
appearing.

### Diagnose

```bash
ps -ef | grep -E "supervisor|sentinel" | grep -v grep
```

Sentinel is a thread inside the api process; if `supervisor.py` is
running and the API responds, the sentinel should be too. If the API
itself died (no PID), see [API not responding](#api-not-responding).

### Fix

Restart the service. Sentinel will start with it.

---

## Idempotency cache OOM

**Symptom:** Memory growth correlates with chat traffic; cache stats
in `monitor/health/detailed` (if surfaced) show abnormal counts.
Should not happen — the cache is bounded at 1024 entries with cheap
LRU trim.

### Diagnose

This shouldn't happen with the current code. If it does:

```bash
.venv/bin/python -c "
from api import _IDEM_CACHE
print(f'cache size: {len(_IDEM_CACHE)}')
"
```

(This won't work against the running process; it imports a fresh
module. Restart the service if you suspect a leak — that flushes
the cache.)

### Fix

Restart the service. Open an issue with the vendor — the cache should
be self-bounding.

---

## Certs expired or distrusted

**Symptom:** Browsers warn or block; worker-ui shows "Could not
reach". `openssl s_client -connect localhost:8000 ...` shows expired.

### Fix

See [maintenance.md § "TLS cert renewal"](maintenance.md#tls-cert-renewal).

---

## Disk full

**Symptom:** `df -h .` < 5%, sentinel posts CRIT_DISK_LOW.

### Fix in priority order

1. Old audit archives:
   ```bash
   ls -lh logs/audit-*.log.gz
   # Move oldest to cold storage:
   mv logs/audit-2025-01-*.log.gz /backup/locallyai/cold/
   ```
2. Old crash dumps:
   ```bash
   ls -lh logs/crash_dump_*.txt
   # Keep most-recent 5:
   ls -t logs/crash_dump_*.txt | tail -n +6 | xargs rm
   ```
3. Old launchd logs (Mac):
   ```bash
   ls -lh logs/launchd_*.log
   # Truncate (don't delete — supervisor may have an open handle):
   : > logs/launchd_error.log
   ```
4. Pull a smaller model (release the bigger one):
   ```bash
   ollama list
   ollama rm <old-large-model>
   ```

### Verify

```bash
df -h .
# >20% free
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/monitor/alerts
# CRIT_DISK_LOW cleared
```

---

## Log growth alert

**Symptom:** Sentinel posts `WARN_LOG_STORM` — audit log growing
>10 MB/5 min.

### Diagnose

```bash
tail -100 logs/audit.log | python3 -c "
import sys, json
from collections import Counter
hashes = Counter()
for line in sys.stdin:
    try:
        e = json.loads(line)
        hashes[e['user_hash']] += 1
    except Exception: pass
for h, n in hashes.most_common(5):
    print(h, n)
"
```

Identify which pseudonym is hammering. If it's a single user — they're
either running a stress test, have buggy automation, or their key
leaked. If many: the firm is just busy.

### Fix

If a single user is hammering and you don't recognise the load
pattern: rotate their key (`manage_users.py rotate <name>`) and treat
as suspected breach (see
[incidents-security.md § "User key leak"](incidents-security.md#user-key-leak)).

---

## Breach detector fired

**Symptom:** `monitor/alerts` shows
`auth_breach: Possible credential-stuffing: <ip>=<n> failed auths in
300s window (GDPR art. 33 review)`.

### Read

[incidents-security.md § "Credential stuffing detected"](incidents-security.md#credential-stuffing-detected).

---

## I've read this whole file and my issue isn't here

You've found a new failure mode. After you fix it (or escalate to the
vendor), add a new section to this file matching the format above
(symptom → diagnose → fix → verify → after-action). The SOP getting
better is a feature, not a regression.

Also append a line to [CHANGELOG.md](CHANGELOG.md).

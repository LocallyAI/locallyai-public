# Runbook: quarterly failover-readiness test

**When**: First Friday of each quarter (Jan / Apr / Jul / Oct), out
of business hours. Coordinated with the firm's IT contact in advance.

**Time budget**: 30 minutes. If the test takes >60 min, **abort,
revert to primary, escalate**.

**Risk if you stop midway**: Both Macs may be in an inconsistent
state. The runbook below has a clear revert step at every phase —
don't skip it.

**Prerequisites**:
- Both Macs healthy (vendor dashboard shows both green)
- Recent compliance snapshot filed (so we have a clean baseline if
  anything goes wrong)
- Firm IT contact on standby in case staff call about brief slowness
- Tailscale + Manager.app login working from your laptop

## Decision tree

| State | Procedure |
|---|---|
| Both Macs healthy, last test was <90 days ago | Skip — wait for the calendar |
| Both Macs healthy, ≥90 days since last attestation | Step A — full drill |
| Standby unhealthy | Stop — fix standby first per `api-down.md`, then test |
| Active backup-restore test scheduled same day | Sequence: do this drill FIRST, then the backup test |

## Step A — full drill

### A.1 Baseline snapshot (5 min)

On your laptop, with Manager.app pointed at the **primary**:

```
Manager UI → Compliance tab → Refresh → Download monthly snapshot
```

File the snapshot under `vendor-records/firms/<firm>/failover-tests/<YYYY-Q?>/baseline-pre.html`.

Verify both nodes appear in the vendor monitor dashboard's Fleet tab.

### A.2 Block primary at the network layer (no power-off)

We don't want to actually power-off the primary — too risky if
something goes wrong. Instead, block its API port from the staff
laptop subnet for the duration of the drill.

**On the primary Mac (Terminal):**

```bash
# Block inbound :8000 from anywhere except this Mac itself
sudo pfctl -ef /dev/stdin <<'EOF'
block in proto tcp from any to any port 8000
pass in proto tcp from 127.0.0.1 to any port 8000
EOF
```

Verify from your laptop (Tailscale is fine):
```bash
curl -sk --max-time 5 https://office-a.local:8000/healthz   # primary — should fail
curl -sk --max-time 5 https://office-b.local:8000/healthz   # standby — should still 200
```

Expected: primary times out, standby returns `{"ok": true, "backend": "..."}`.

### A.3 Verify the staff-laptop smart client failed over (10 min)

Open Workspace.app on a test laptop. Send a chat message.

Expected behaviours:
- First request takes ~5-15 s (smart client retried primary, hit
  timeout, switched to standby)
- Subsequent requests are normal-speed (cached "primary down" for
  the next 5 s)
- Sources panel shows the same docs (Syncthing + rsync are caught up)

If the request never completes after 30 s:
- Check Workspace.app's settings menu → confirm both URLs are
  listed (not just primary)
- Check the standby's `~/locallyai/logs/launcher/api.log` for
  inbound requests
- If neither shows progress: **revert primary now (jump to A.5)**

### A.4 Test the standby with all the day-one operations (10 min)

While primary is "down", verify on the standby:

```bash
ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' ~/locallyai/.env | cut -d= -f2)

# 1. Health
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://office-b.local:8000/healthz

# 2. List users (proves Syncthing-replicated users.json is current)
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://office-b.local:8000/admin/users

# 3. Audit chain on the standby still passes (per-node)
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://office-b.local:8000/admin/audit-verify

# 4. Compliance snapshot generates from the standby
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://office-b.local:8000/admin/compliance/snapshot \
  > /tmp/snapshot-from-standby.json
```

All four should succeed. If any fails: **revert primary, escalate**.

### A.5 Revert: primary back online

```bash
# On the primary Mac (Terminal)
sudo pfctl -d   # disable pf rules → primary serves :8000 again
```

Verify from your laptop:
```bash
curl -sk --max-time 5 https://office-a.local:8000/healthz   # should 200 again
```

Wait 60 seconds. Confirm the vendor monitor dashboard shows both
nodes green again.

### A.6 Re-promote primary (operator-driven, no auto-flip)

The standby continued serving while primary was blocked. Once
primary is healthy, the smart client's mtime-cached health check
detects it within 5 s and prefers it again on the next request.
**No yield action needed** — the system is back to baseline.

### A.7 File the attestation

In Manager UI → Compliance tab:

```
→ Backup Test Attestations card
→ "Record test"
→ test_type = "failover"
→ result = "passed" (or "partial" / "failed")
→ operator = your name
→ notes = "Q? YYYY drill. Failover detected in <X>s. All standby checks passed."
```

The attestation lands in this firm's compliance snapshot for the
DPO's monthly cycle. Auditors see "this firm tested failover within
the last 90 days" without you having to dig for evidence.

## Things that go wrong

| Symptom | Cause | Fix |
|---|---|---|
| Standby returns 401 on /admin/* with the same key that worked on primary | Syncthing has lagged + users.json out of date | On standby, `cat ~/locallyai/SHARED/users.json` and compare to primary's. Force a Syncthing rescan |
| Sources panel on standby is missing the doc the user just uploaded | rsync hasn't run yet (hourly) | `launchctl kickstart -k gui/$(id -u)/app.locallyai.ha-rsync` to force it. Wait ~1 min for completion |
| Standby's audit-verify fails | Per-node chain — possibly broken on the standby separately | Use `audit-chain-broken.md` runbook for the standby |
| Smart client hangs forever instead of failing over | Old build — VITE_API_BASE_URLS wasn't set at build time | Rebuild Workspace.app with both URLs, redeploy to staff laptops |
| `sudo pfctl` requires password the firm's IT didn't give you | Firm IT didn't grant sudo to the on-call account | **Stop** — coordinate with firm IT before continuing |

## When to escalate

- Failover took > 60 s — tells us the smart-client config is wrong;
  fix BEFORE the next drill
- Standby couldn't generate a compliance snapshot — Syncthing
  state is broken; **founder, same business day**
- Audit chain on standby fails (per-node TAMPERED) — **founder, within
  1 hour**; the standby may have been silently broken for a while
- Both Macs returned the same wrong answer to a test query —
  cross-node corruption, **founder immediately**

## Calendar reminder template

Vendor CS lead sets a recurring calendar event:

> **LocallyAI quarterly failover drill — <firm>**
> First Friday, every 3 months, 17:30 firm-local
> Coordinate with: <firm IT contact>
> Runbook: `docs/runbooks/failover-test.md`
> File attestation in: `vendor-records/firms/<firm>/failover-tests/`

A vendor-side script could be added later to auto-detect "≥90 days
since last attestation" and ping the on-call lead — but for now
the calendar is the source of truth.

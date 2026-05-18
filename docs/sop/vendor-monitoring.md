# Vendor monitoring & on-call

Vendor-internal SOP for operating the LocallyAI fleet-monitoring
dashboard and responding within the 4-hour SLA.

For the firm-side perspective on what telemetry sends + how to
disable, see [data-isolation.md](data-isolation.md#optional-vendor-health-telemetry-opt-in-anonymised).

For the structured intake form firms fill in BEFORE install (which
populates the firm_id we register here), see [onboarding.md](onboarding.md).

---

## The dashboard

URL: `https://locallyai-monitor.<your-cf-account>.workers.dev/`
(set up per `docs/monitor/cloudflare-worker/README.md`)

Auth: TOTP from your authenticator app. Same physical device as the
kill-switch TOTP — distinct entry ("LocallyAI: monitor" vs
"LocallyAI: killswitch") so credential compromise of one doesn't
unlock the other.

Layout:
- **Top strip**: per-status counts (Healthy / Warning / Critical /
  Stale / SLA at risk).
- **Open alerts**: chronological table of unacknowledged alerts.
  Anything older than 3.5h is highlighted red ("SLA at risk"); the
  Worker's cron has already re-notified at that point.
- **Firms**: card grid, one per firm. Click a card for drill-down.

Card colours:
- 🟢 **Green (ok)** — healthz + sentinel both up; no resource pressure
- 🟡 **Yellow (warning)** — disk < 5 GB OR memory < 1 GB OR > 50 errors
  in 24h
- 🔴 **Red (critical)** — healthz failed OR sentinel dead
- ⚪ **Grey (stale)** — no heartbeat received in > 1 hour

---

## On-call workflow (4-hour SLA)

You're paged when an alert fires. From the email/Slack notification:

### 1. Triage (within 5 min)

1. Open the dashboard. Find the alert in "Open alerts".
2. Note the `firm_id` (16-hex hash) and the `code` (structured
   identifier). Cross-reference the code against the **alert
   playbook** below.
3. Check the firm's card colour. If 🔴 critical AND the code matches
   a known auto-heal candidate that didn't auto-heal (suffix `_failed`),
   that's your priority signal.

### 2. Diagnose (within 30 min)

Based on the alert code, the most common diagnostics:

| Code | What it means | First diagnostic |
|---|---|---|
| `healthz_failed` | API not responding even after auto-restart | Check the firm's `logs/api.log` (you can ask IT to send the last 200 lines) |
| `ollama_unrecoverable` | Ollama crashed AND restart failed | Recommend they switch backend to MLX in the manager UI's Models page |
| `disk_critical` | < 5 GB free even after auto-clean | Recommend `du -sh storage/* logs/*` to find the bloat |
| `sentinel_dead` | Watchdog thread died (catastrophic) | Restart the API via Stop+Start launcher app; investigate logs/sentinel.log |
| `audit_chain_tampered` | HMAC chain broken | Sec-incident — see [incidents-security.md](incidents-security.md) |
| `system_update_rolled_back` | Recent update auto-reverted | Check the deployed tag's release notes; consider kill-switch (see [updates.md](updates.md#kill-switch-runbook--invoking-it-when-something-goes-wrong)) |

### 3. Remediate (within 2 hr)

For most alerts, the fix is one of:
- **Operator-side**: tell the firm's IT to take a specific action
  (restart launcher app, swap LLM model, free disk).
- **Vendor-side**: ship a hotfix release (`scripts/release_server.sh
  dev <vX.Y.Z> A "fix description"`), wait for soak, promote, watch
  the firm pick it up via auto-apply.
- **Kill-switch**: if the issue is a bad release affecting multiple
  firms, halt updates while you ship the fix
  (see [kill-switch runbook](updates.md#kill-switch-runbook--invoking-it-when-something-goes-wrong)).

### 4. Acknowledge (before 4 hr)

Once the alert is investigated AND a remediation path is in flight
(or the issue auto-resolved), click **Ack** on the row. This:
- Stops the SLA escalation cron from re-paging.
- Records who handled it (operator) and when.
- Clears the row from "open alerts" (still visible in 30-day history).

**Don't ack unless you've actually addressed it.** If you ack to
silence the page and the underlying issue persists, the next
heartbeat re-emits the alert and the cycle starts again — but with
a worse audit trail.

### 5. Post-mortem (within 24 hr of resolution)

For any **critical** alert that took > 1 hour to resolve, drop a
short doc in `docs/incidents/YYYY-MM-DD-<firm_id>-<code>.md`:
- Timeline (alert fired, triaged, diagnosed, fixed, acked)
- Root cause
- Why the auto-heal didn't catch it (if applicable)
- What detection signal would have caught it earlier
- Any sentinel/dashboard tweaks needed

---

## Alert deduplication — "one email per (firm, code)"

A sticky condition (low disk, healthz failing for hours, OOM) used
to fire one email per sentinel tick (every 60 s) until the operator
acknowledged. Three layers of dedupe now collapse that into a single
email per incident:

| Layer | Where | What it stops |
|---|---|---|
| **Per-(code, severity) suppression** | `telemetry.py:emit_alert` | The local sentinel skips queueing + posting if the same code+severity fired within `LOCALLYAI_ALERT_DEDUPE_SECONDS` (default 4 h). State is persisted to `storage/.alert_dedupe.json` so process restarts don't re-arm. |
| **Per-(firm, code) open-alert dedupe** | Worker `handleHeartbeat` | If this firm already has an open (unacked) alert with the same code, the worker bumps `seen_count` + `last_seen_at` on the existing record and does NOT send another email. Authoritative defence — even if a misbehaving sentinel posts repeatedly, only the first one emails. |
| **SLA auto-escalation off by default** | Worker `handleCron` + `wrangler.toml` | The 3.5 h "re-email if unacked" cron is disabled (`SLA_WARN_HOURS="0"`). Set to a positive value to re-enable. |

What this means in practice: when a critical condition fires you get
**one email**. The dashboard shows the alert with `seen_count = N`
indicating how many times the underlying condition recurred while
the alert stayed open. Acknowledging the alert clears the dedupe
key, so the next time the same condition fires (e.g. disk filled
again next quarter), it counts as a new incident and emails again.

## SLA escalation timing

By default, the worker does NOT auto-re-email after the initial
notification. The on-call is responsible for noticing the alert in
their inbox / dashboard and acting within the 4 h SLA.

| Time since alert | What happens by default |
|---|---|
| 0 min | Worker dispatches initial notification (email + Slack if configured) |
| Any time before ack | Sentinel keeps firing → worker bumps `seen_count`, NO new email |
| 4 hr | SLA breach. Document in the post-mortem. |

If a firm explicitly wants the legacy auto-re-email behaviour, set
`SLA_WARN_HOURS = "3.5"` in `docs/monitor/cloudflare-worker/wrangler.toml`
and redeploy. The cron then re-emails unacked criticals after 3.5 h
with `[SLA ESCALATION]` in the subject. Vendor default is off because
operators generally find the second email less useful than the dashboard
poll signal.

---

## Self-heal inventory (what fires automatically before you're paged)

The sentinel auto-heals these without bothering you. If they keep
firing repeatedly at the same firm, the dashboard's `self_heals_24h`
counter exposes that — investigate the underlying instability.

| Action code | When | What it does |
|---|---|---|
| `healthz_kickstart` | API not responding to /healthz | `launchctl kickstart -k` the API LaunchAgent |
| `ollama_restart` | "llama runner terminated" detected in ollama log | pkill ollama + relaunch via `open -a Ollama` |
| `disk_pressure_clean` | Free disk < 5 GB | Aggressive log rotation + GC stale uploads |
| `rotate_logs` | Audit/billing log > rotation threshold | Standard log rotation |
| `gc_uploads` | Stale chunked-upload partials > 24h | Delete partials (`chunked_uploads.gc_stale`) |

If the auto-heal succeeds, you get an `info`-severity event in the
heartbeat (visible in the firm's drill-down panel, but no email/Slack).
If it FAILS (the `_failed` suffix), the next heartbeat carries a
critical alert and you get paged.

---

## Account separation requirements

To preserve the credential-isolation property:
- **Cloudflare account** running this Worker = NOT the same CF account
  as your kill-switch Worker (separate billing identity preferred,
  separate 2FA mandatory).
- **Authenticator app entry** for monitor TOTP = NOT the same entry
  as the kill-switch TOTP (separate `otpauth://` URIs scanned at
  setup).
- **Resend / Slack** secrets are vendor-only — never shared with any
  firm's IT.

If your monitor account is compromised, the attacker can see anonymised
firm health gauges + the existence of alerts but cannot derive firm
identities (only hashes), document content, or user activity. Worst-case
disclosure is "firm 60ad10cf… had an outage on 2026-05-10" — useful
to a competitor for marketing intelligence but not a data breach.

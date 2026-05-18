# Daily operations

Routine tasks. Bookmark this. The structure: each task has a Mac box
and a Windows box; pick the one matching your deployment.

---

## Start / stop / restart the service

| Action | Mac | Windows |
|---|---|---|
| Start | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.locallyai.server.plist` | `Start-Service LocallyAIServer` |
| Stop | `launchctl bootout gui/$(id -u)/com.locallyai.server` | `Stop-Service LocallyAIServer` |
| Restart (recommended after .env edits) | `launchctl kickstart -k gui/$(id -u)/com.locallyai.server` | `Restart-Service LocallyAIServer` |
| Is it running? | `launchctl list \| grep com.locallyai.server` (PID > 0) | `Get-Service LocallyAIServer` (Status: Running) |

### Wait for ready (script-friendly)

```bash
# Mac
until curl -skf -o /dev/null --max-time 2 https://localhost:8000/healthz; do sleep 4; done
echo READY
```

```powershell
# Windows
do { Start-Sleep -Seconds 4 } until ((Invoke-WebRequest -Uri https://localhost:8000/healthz -SkipCertificateCheck -TimeoutSec 2 -ErrorAction SilentlyContinue).StatusCode -eq 200)
Write-Host "READY"
```

---

## Read the live logs

| File | What it shows |
|---|---|
| `logs/launchd_error.log` (Mac) / `logs/service.log` (Win) | Supervisor + uvicorn stdout+stderr — first place to look for crashes |
| `logs/heartbeat.log` | Watchdog probes; `probe_failed` events when API is unreachable |
| `logs/audit.log` | Pseudonymised query log — never edit by hand |
| `logs/billing.log` | Real-name usage log — admin-only |
| `logs/security.log` | Failed auth, lockouts, sync conflicts, breach detector |
| `logs/sentinel.log` | Sentinel alerts (disk, memory, log growth, Qdrant lock) |
| `logs/resurrector.log` | Auto-recovery actions when heartbeat fails |
| `logs/fleet-ui.log` (HA) | Dashboard server output |
| `logs/syncthing.log` (HA) | Syncthing daemon output |

Tail a specific one (Mac):

```bash
tail -f logs/launchd_error.log
```

Windows:

```powershell
Get-Content C:\locallyai\logs\service.log -Wait -Tail 30
```

---

## Quick health sweep (do this every morning)

A 30-second routine that catches 90% of issues before users do.

### Mac

```bash
cd ~/locallyai
ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' .env | cut -d= -f2)

# 1. API alive?
curl -sk https://localhost:8000/healthz

# 2. Audit chain ok?
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/audit-verify

# 3. Any alerts?
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/monitor/alerts

# 4. (HA) all nodes alive?
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/fleet/nodes | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"alive: {d['active_count']}/{len(d['nodes'])}\")"
```

### Windows

```powershell
$adminKey = (Get-Content C:\locallyai\.env | Select-String '^LOCALLYAI_ADMIN_KEY=').ToString().Split('=',2)[1]
$h = @{ Authorization = "Bearer $adminKey" }

Invoke-RestMethod -Uri https://localhost:8000/healthz -SkipCertificateCheck
Invoke-RestMethod -Uri https://localhost:8000/admin/audit-verify -Headers $h -SkipCertificateCheck
Invoke-RestMethod -Uri https://localhost:8000/monitor/alerts -Headers $h -SkipCertificateCheck
```

Pass condition: `ok:true`, `status:"ok"`, `alerts: []`. Anything else
is an incident — find the matching chapter under
[../SOP.md](../SOP.md).

---

## User management

### Add a user

```bash
python manage_users.py add "First Last"
```

Output prints the API key once. Save in password vault.

For service accounts (no expiry):

```bash
python manage_users.py add "ServiceAccount" --ttl-days 0
```

### Rotate a user's key

User suspects their key leaked, or annual rotation:

```bash
python manage_users.py rotate "First Last"
```

Old key is dead immediately; print the new one to the user.

### Renew a user's key (extend without rotating)

User's key is expiring but they don't want to update their app:

```bash
python manage_users.py renew "First Last" --ttl-days 90
```

### Remove a user

User leaves the firm OR temporarily blocked:

```bash
python manage_users.py remove "First Last"
```

### Erase a user (GDPR Art. 17)

User exercises their right to erasure. Destroys more state than
`remove`:

```bash
python manage_users.py erase "First Last"
```

Output: pseudonyms (one per salt era), tombstones written to
`erasure.log`, billing records redacted, peers fan-out-refreshed.

**Use `erase` only when the user has formally asserted their right.**
For routine off-boarding use `remove`.

See [compliance.md § "Article 17 erasure"](compliance.md#article-17-erasure)
for the full DPO-grade procedure.

### List users

```bash
python manage_users.py list
```

Prints name, created_at, expires_at.

---

## Document ingest

### Add new documents

```bash
cp ~/Documents/new-batch/*.pdf data/
python ingest.py
```

Incremental — only new/changed files reprocessed. Hash-tracked in
`.ingest_state.json`.

### Force a full re-index (e.g. after upgrading the embedding model)

```bash
python ingest.py --force
```

### Remove a document from the index

```bash
rm data/secret_old_doc.pdf
python ingest.py --force
```

There is no per-document "remove from Qdrant" command yet — `--force`
re-indexes from the current `data/` snapshot, which has the effect of
removing anything no longer present.

---

## Audit-chain verification

### Single-node

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/audit-verify
```

Pass: `{"status":"ok","entries":N,"node_id":"…"}`.

### HA — fleet-wide

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/fleet/audit-verify
```

Pass: `{"fleet_status":"ok","nodes":[…each per-node ok…]}`.

If any node says `TAMPERED` or `unreachable`:
[incidents-software.md § "Audit chain TAMPERED"](incidents-software.md#audit-chain-tampered).

---

## Billing review

Per-user usage per month:

```bash
USER=Alice
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/billing/$USER
```

Or read the raw billing log directly (admin-only):

```bash
tail -200 logs/billing.log | python3 -m json.tool
```

---

## Fleet dashboard (HA only)

Open `http://127.0.0.1:5175/` on whichever Mac you set up the
fleet-ui on (or the Windows equivalent). Sign in with the admin key.

Daily checklist on the dashboard:

- **Nodes panel** — both green / "alive". One red = peer is offline,
  start [incidents-physical.md § "One Mac dies"](incidents-physical.md#one-mac-dies).
- **Audit chain panel** — fleet_status: ok, both nodes ok. Any TAMPERED
  → incident.
- **Qdrant panel** — mode: cluster, peer_count: 2. Anything else →
  [qdrant-ha.md](../qdrant-ha.md).
- **Sync conflicts panel** — empty list. If non-empty, review the
  files in `$LOCALLYAI_SHARED_DIR/conflicts/` and decide which is
  canonical (never auto-merge credentials).
- **Inference gate panel** — peak_queue should be well under max_queue.
  Sustained high in_flight = consider raising
  `LOCALLYAI_MAX_CONCURRENT_INFERENCE` (only if memory allows).
- **Alerts panel** — empty.

---

## Adding a new launch-app for users

If you bought a new Mac for a user, on their box:

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain \
  /path/to/cert.pem
```

(Copy `tls/cert.pem` to their Mac via AirDrop first.)

Then they open `https://<server-ip>:8000` in Safari/Chrome and paste
their API key into the worker-ui sign-in.

---

## Daily routine summary (5 min/day)

Every morning, the IT-ops person should:

1. Run the [Quick health sweep](#quick-health-sweep-do-this-every-morning).
2. Open the fleet dashboard (HA) — eyeball all 5 panels.
3. Skim `tail -50 logs/security.log` — any auth_failure spikes?
4. Skim `tail -20 logs/sentinel.log` — any new alerts?

If everything's green, you're done in 5 minutes. If not, the relevant
chapter under [../SOP.md](../SOP.md) has the click-by-click recovery.

---

## Weekly routine (15 min/week)

1. Run `bash scripts/audit_install.sh` (Mac) or `audit_install.ps1`
   (Win). File the report from `logs/install_audit_<date>.log`.
2. Review `logs/audit.log` line count vs last week — sustained drop
   means users aren't using it; sustained spike means look at
   `manage_users.py list` to see who's hammering.
3. If HA: verify both Macs are still booted, plugged in, on the LAN.
   `ping` from each to the other.

---

## Monthly routine (30 min/month)

1. Run the [maintenance.md](maintenance.md) checklist (cert expiry,
   model freshness, retention rotation actually firing).
2. Quarterly-ish: review the credential register (in your password
   vault). Anything missing? Anyone who left? Rotate.

---

## Quarterly routine

See [maintenance.md § "Quarterly"](maintenance.md#quarterly).

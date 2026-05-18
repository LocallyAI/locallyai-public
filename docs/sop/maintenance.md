# Maintenance

Scheduled work — weekly, monthly, quarterly, ad-hoc. Each task has a
"trigger" (when to do it), "procedure" (click-by-click), and
"verification" (how you know it worked).

---

## macOS version policy

**Trigger:** Apple ships a new macOS major version (annually, ~September),
OR a firm reports their Mac is on a version newer than the supported band.

### Why this matters

Two-Mac HA gives you **hardware redundancy**. It does NOT give you
**software redundancy** — both Macs run the same OS. A single macOS
update applied silently in the small hours can reboot both Macs into
an untested OS version simultaneously and take the whole fleet down.

Beyond outages, macOS updates regularly break:

- The Python `.venv` (Apple bumps Xcode CLT, breaks Python build deps)
- MLX / Metal kernels (model loads but produces gibberish)
- `launchd` plist semantics (rare but has happened)
- mDNS / `.local` hostname resolution (breaks staff laptop access)
- Certificate trust chains (cert needs re-importing into Keychain)

So firms run **only the macOS version vendor has tested + approved.**

### Supported macOS version band (as of 2026-05-12)

| Version | Status | Notes |
|---|---|---|
| **macOS 13.x (Ventura)** | Supported | Minimum |
| **macOS 14.x (Sonoma)** | Supported | Current preferred |
| **macOS 15.x (Sequoia)** | Supported | Tested 2026-04 |
| **macOS 16.x (next)** | NOT YET TESTED | Refuse install; downgrade or wait |

**Update this table on every test cycle.** When you mark a new version
supported here, commit + push so firm IT can see the current support
matrix.

### Vendor-side test procedure (before approving a new version)

Run **before** authorising any firm to upgrade to a new macOS version.
On a vendor-owned test Mac that mirrors a typical firm install:

1. Snapshot the test Mac (Time Machine to encrypted disk).
2. `softwareupdate --install <new-version>` (don't skip — measure how
   long the actual upgrade takes; firms will ask).
3. Re-run `bash scripts/audit_install.sh` — `pass=14 warn≤1 fail=0`?
4. Re-run `.venv/bin/python tests/ha_chaos.py` — full pass?
5. Sample chat queries against the test corpus — sources retrieved,
   answers coherent?
6. macOS Keychain still trusts the self-signed TLS cert?
7. launchd plists still load + KeepAlive works (kill the API,
   confirm restart < 30s)?
8. mDNS still resolves `office-mac.local` from a separate device?
9. **Run a complete reboot.** Does everything come back automatically?
10. Vendor monitor dashboard receives heartbeats from the test box?

If all 10 pass → mark version as supported in the table above + push.
If any fail → file a per-bug entry in
`vendor-records/macos-bugs/<version>-<short-description>.md` and DO NOT
mark supported.

### Firm-side upgrade procedure (only after vendor has approved)

When the table above says a version is supported, firms can upgrade
during their scheduled maintenance window:

1. Vendor emails firms with subject "macOS `<version>` approved for upgrade".
2. Firm IT picks a maintenance window (off-hours, ≤4h).
3. Firm IT in System Settings → Software Update → **manually** triggers
   the upgrade (auto-updates remain OFF per pre-flight 0.2a).
4. After reboot, firm IT runs `bash scripts/audit_install.sh` and
   pastes the output to vendor.
5. Vendor confirms heartbeat + dashboard health.
6. Vendor records the new version + build in
   `vendor-records/firms/<slug>.md`.

**HA fleets**: upgrade Mac-B first, leave Mac-A on old version for
24h. If anything goes wrong, fall back to Mac-A. Once Mac-B is stable
for 24h, upgrade Mac-A.

### What firms do NOT do

- Auto-upgrade (disabled at install per [setup-mac-single.md §0.2a](setup-mac-single.md))
- Upgrade without vendor approval
- Upgrade outside their maintenance window
- Upgrade Mac-A and Mac-B at the same time on an HA fleet

If a firm violates any of the above (typically: an IT manager clicks
"upgrade now" not realising), treat as an [operator-error incident](incidents-operator.md)
and follow the recovery there.

### Verification

After every macOS-version review cycle:

- [ ] Supported table above is current (last updated date in the section header)
- [ ] All currently-running firms are on supported versions (audit via
      `vendor-records/firms/*.md` macOS-version field)
- [ ] Any firms on un-tested versions have been contacted

---

## Software updates (`update.sh`)

**Trigger:** vendor publishes a release, or you `git pull` a new commit.

### Single-node procedure

Mac:

```bash
cd ~/locallyai
git pull
bash update.sh
```

What `update.sh` does: stops the service, `pip install --upgrade -r
requirements.txt`, restarts the service, smokes `/healthz`.

Windows:

```powershell
cd C:\locallyai
git pull
.venv\Scripts\python.exe -m pip install --upgrade -r requirements.txt
Restart-Service LocallyAIServer
```

### HA — Rolling updates (do not skip)

**Critical:** never update both nodes at the same time.

1. On Mac-A: `launchctl bootout gui/$(id -u)/com.locallyai.server`
2. Wait 5s. From Mac-B's terminal, confirm `/admin/fleet/nodes` shows
   Mac-A `alive: false`.
3. On Mac-A: `git pull && bash update.sh` (or the Windows equivalent).
4. On Mac-A: `launchctl bootstrap gui/$(id -u)
   ~/Library/LaunchAgents/com.locallyai.server.plist` and wait for
   `/healthz`.
5. From Mac-A: confirm `/admin/fleet/nodes` shows both alive again.
6. Repeat 1–5 for Mac-B.

### Verification

```bash
bash scripts/audit_install.sh   # pass=14 warn≤1 fail=0
```

If the new code introduced an env var or a new check, the audit may
report a new "warn" you've not seen before. Read
[../iso27001-controls.md § "Compliance-relevant operational knobs"](../iso27001-controls.md#compliance-relevant-operational-knobs)
to see if the new var has a default that meets your firm's posture or
needs explicit setting.

---

## TLS cert renewal

**Trigger:** certificate is within 6 months of expiry. Cert lifetime is
10 years (per `install.sh`); for most deployments this fires once a
decade. Set a calendar reminder anyway.

### Check expiry

Mac:

```bash
openssl x509 -in tls/cert.pem -noout -dates
# notAfter=Mar  4 12:00:00 2036 GMT
```

Windows:

```powershell
$cert = Get-PfxCertificate -FilePath C:\locallyai\tls\cert.pem
Write-Host "Expires: $($cert.NotAfter)"
```

### Renew

Easiest: re-run install in cert-only mode. Mac:

```bash
cd ~/locallyai
launchctl bootout gui/$(id -u)/com.locallyai.server
mv tls/cert.pem tls/cert.pem.old
mv tls/key.pem  tls/key.pem.old
bash install.sh   # detects existing .env, regenerates cert only
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.locallyai.server.plist
```

Then re-trust the new cert in the macOS keychain (per
[setup-mac-single.md § 3.4](setup-mac-single.md#34-prompt-trust-the-tls-cert)
and the troubleshooting section).

Windows: open `install.ps1`, locate the TLS-cert block, run just that
section. Or simpler — let install.ps1 run end-to-end; it skips steps
already done.

### Verification

```bash
openssl s_client -connect localhost:8000 -servername localhost </dev/null 2>/dev/null | openssl x509 -noout -dates
```

---

## Salt rotation (GDPR Art. 32 / ISO 27001 A.8.24)

**Trigger:** annually OR immediately if the salt may have leaked
(e.g. `.env` was screenshotted, the box was unattended in an untrusted
location, an admin who knew the salt has left the firm).

### Procedure

```bash
cd ~/locallyai
python manage_users.py rotate-audit-salt --keep-eras 4
```

Output:

```
Audit salt rotated.
  new_era:           aea9125e
  previous_era:      850f4be0
  retained_era_count: 5
  dropped_era_count:  0
  audit_boundary_at:  2026-05-06T16:30:11Z
  env_file:           /Users/emanuel/locallyai/.env

ACTION REQUIRED: restart the LocallyAI service so the new salt is
loaded by the API and sentinel processes:
  launchctl kickstart -k gui/$(id -u)/com.locallyai.server   # macOS
  Restart-Service LocallyAIServer                             # Windows
```

The function:

1. Generates a fresh 32-byte salt.
2. Demotes the current to `LOCALLYAI_AUDIT_SALT_ERA_1`, shifts existing
   ERA_n down.
3. Drops eras beyond `--keep-eras` (default 4 = ~four years of yearly
   rotation).
4. Stamps a `salt_era_boundary` entry into `audit.log` under the OLD
   salt — chain stays unbroken across the boundary.
5. Rewrites `.env` preserving comments and key order, chmod 600.

### HA: rotate from ONE node, restart BOTH

Critical: rotate-audit-salt rewrites `.env` on the node you ran it on.
That `.env` is **not** synced (it's per-node, contains node-id and TLS
paths). You must:

1. On Mac-A: `python manage_users.py rotate-audit-salt`.
2. Manually copy the new `LOCALLYAI_AUDIT_SALT` and ALL
   `LOCALLYAI_AUDIT_SALT_ERA_*` values from Mac-A's `.env` to Mac-B's
   `.env` (same lines, same values).
3. Tighten ACL: `chmod 600 .env` on Mac-B.
4. Restart Mac-A: `launchctl kickstart -k gui/$(id -u)/com.locallyai.server`.
5. Wait for `/healthz`. Confirm new era via:
   `curl -sk -H "Authorization: Bearer $ADMIN_KEY"
   https://localhost:8000/admin/processing-record | jq .pseudonymity`.
6. Repeat 4–5 for Mac-B.

Both nodes should now show `current_salt_era: <new>` with the same
value.

### Verification

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/admin/processing-record \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d['pseudonymity'], indent=2))"
```

Should show the new era at index 0 and the old one(s) at higher
indexes.

Audit chain still ok:

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/audit-verify
# {"status":"ok",...}
```

The boundary entry is visible in audit.log:

```bash
grep "salt_era_boundary" logs/audit.log | tail -1 | python3 -m json.tool
```

---

## HMAC chain key rotation

**Trigger:** the `LOCALLYAI_AUDIT_HMAC_KEY` may have leaked.

This is **harder** than salt rotation — there is no codepath for
rotating the HMAC chain key without breaking chain verification on
existing entries. Procedure:

1. Rotate the audit log itself first (sentinel does this nightly per
   `LOCALLYAI_AUDIT_RETENTION_DAYS`; you can force it). After
   rotation, `audit.log` is empty and `.audit_chain` holds the head of
   the now-archived chain.
2. **Manually archive `.audit_chain` and the gz archives** somewhere
   off-host so a future verifier can replay them under the OLD key.
3. Generate a new HMAC key:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```
4. Edit `.env`: replace `LOCALLYAI_AUDIT_HMAC_KEY=…` with the new
   value.
5. Delete the live `logs/.audit_chain` so the next entry starts a new
   chain era from `0000…`:
   ```bash
   rm logs/.audit_chain
   ```
6. Restart the service.
7. The new audit chain begins fresh. The verifier will report `ok`
   for new entries; old archives are no longer covered by the live
   chain key.

For HA: do this on **both nodes** with the same new key. Sync the new
key into both `.env`s manually.

This is a destructive control. Do not do it casually.

---

## Log retention rotation (automatic)

`watchdog/sentinel.py` rotates `audit.log`, `billing.log`, and
`security.log` daily at the first sentinel tick of the new UTC day.
Each stream has its own retention horizon:

| Stream | Env var | Default | Why |
|---|---|---|---|
| `audit.log` | `LOCALLYAI_AUDIT_RETENTION_DAYS` | 365 days | GDPR Art. 5(1)(e) storage limitation; audits typically clear within a year |
| `security.log` | `LOCALLYAI_SECURITY_RETENTION_DAYS` | 365 days | Same as audit unless firm policy says otherwise |
| `billing.log` | `LOCALLYAI_BILLING_RETENTION_DAYS` | 2555 days (7y) | UK HMRC + KSA ZATCA require 6y retention of accounting records; we keep 7 to be safe |

Red-team finding 10.1 fix: billing previously inherited audit's
365-day retention, which is too short for tax law. Separated now.

When retention deletes:

- An `audit-*.log.gz` archive → `.audit_chain` is reset to `0000…` so
  the next entry starts a fresh chain era (the dropped archive's
  chain head no longer makes sense). The verifier sees the era
  boundary cleanly.
- A `billing-*.log.gz` archive → `.billing_chain` is similarly reset.
- A `security-*.log.gz` archive → no chain reset (security log is
  unchained today).

See [../iso27001-controls.md § A.8.10](../iso27001-controls.md) for
the deletion-by-design rationale.

### Verification (you don't normally need to)

```bash
ls logs/audit-*.log.gz
# Newest archive is yesterday's date if rotation is firing.
cat logs/.last_rotate
# Today's UTC date if today's rotation has fired.
```

If rotation isn't firing: check `logs/sentinel.log` for `Rotated
audit.log` lines. If absent, the sentinel thread isn't running — read
[incidents-software.md § "Sentinel not running"](incidents-software.md#sentinel-not-running).

---

## Model swap

**Trigger:** vendor releases a better model, or RAM upgrade allows a
bigger model, or the current one has a bug.

### Pull the new model

```bash
ollama pull qwen3:14b      # for example
```

### Update `.env`

Edit `.env`:

```
OLLAMA_MODEL=qwen3:14b
```

### Restart

```bash
launchctl kickstart -k gui/$(id -u)/com.locallyai.server
```

### Verification

```bash
curl -sk https://localhost:8000/healthz   # ok
# Send a chat — confirm the response.model field shows the new model id.
```

Old model files remain in `~/.ollama/models/` until you `ollama rm
<old-model>`. Free up disk eventually.

### HA

Pull the new model on **both** nodes (Ollama doesn't share models
across boxes), update both `.env`s, restart in rolling fashion (per
[Rolling updates](#ha--rolling-updates-do-not-skip)).

### MLX pin drift (`LOCALLYAI_MODEL_DRIFT_ACK`)

If `.model_lock` pins a specific HuggingFace commit and the resolved
commit changes (HF account compromise, cache poison, upstream
re-publish), the API refuses to start. The operator either:

1. Verifies the new commit is intended → update `.model_lock` and
   restart.
2. Acknowledges the drift for one boot:
   ```
   LOCALLYAI_MODEL_DRIFT_ACK=1 launchctl kickstart -k gui/$(id -u)/app.locallyai.api
   ```
   The acknowledgement is logged. Use this only when you have already
   reviewed the new commit and are about to re-pin.

---

## Knobs for the ingest pipeline

| Env var | Default | Purpose |
|---|---|---|
| `LOCALLYAI_MAX_PDF_BYTES` | `104857600` (100 MiB) | Refuse PDFs larger than this. Larger uploads are typically scanned bundles that should be split. |
| `LOCALLYAI_MAX_PDF_PAGES` | `1000` | Refuse PDFs with more pages than this. Defence against malformed/over-large attachments. |
| `LOCALLYAI_MODEL_DRIFT_ACK` | unset | Set to `1` for a single boot to override MLX pin-drift refusal. |
| `LOCALLYAI_TELEMETRY_FIELDS` | unset (= send all fields) | Comma-separated allowlist of heartbeat field names. Use per the field-expansion-notice template when a firm requests partial exclusion (`docs/vendor-sop/templates/telemetry-field-expansion-notice.md`). `firm_id`, `schema_version`, and `timestamp` are always retained. |

---

## Inference-gate tuning

**Trigger:** fleet dashboard shows sustained queue pressure, or 503
backpressure is firing during normal use.

### Read the current state

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/admin/fleet/gate | python3 -m json.tool
```

Look at `peak_queue` and `total_rejected`. If `peak_queue` regularly
hits `max_queue` and you have RAM headroom: raise the limit.

### Raise the limit

Edit `.env`:

```
LOCALLYAI_MAX_CONCURRENT_INFERENCE=8     # was 6
LOCALLYAI_INFERENCE_QUEUE_MAX=32         # was 24
```

Restart. Watch the dashboard for a week.

### Lower the limit

If you see `OOM` in `logs/launchd_error.log`, or memory pressure
warnings from the sentinel, the gate is too generous for your box.
Lower:

```
LOCALLYAI_MAX_CONCURRENT_INFERENCE=4
LOCALLYAI_INFERENCE_QUEUE_MAX=16
```

---

## Dependency upgrades

**Trigger:** quarterly, or when a CVE is published against a
dependency.

### Check for outdated packages

```bash
.venv/bin/pip list --outdated
```

### Upgrade one package

```bash
.venv/bin/pip install --upgrade <package>
launchctl kickstart -k gui/$(id -u)/com.locallyai.server
bash scripts/audit_install.sh   # confirm green
```

### Upgrade everything

```bash
.venv/bin/pip install --upgrade -r requirements.txt
launchctl kickstart -k gui/$(id -u)/com.locallyai.server
bash scripts/audit_install.sh
```

If pip upgrade breaks something, roll back via:

```bash
.venv/bin/pip install <package>==<old-version>
```

---

## Worker-ui / fleet-ui rebuilds

After an `npm install` or any change in `apps/worker-ui/src/` (or
fleet-ui):

```bash
cd apps/worker-ui
npm run build
```

The launcher script auto-rebuilds when `src/` mtime > `dist/` mtime,
so you usually don't need to do this manually — but if a build is
broken, do it explicitly to read the error.

---

## Quarterly

A 30-min checklist every 3 months.

1. **Salt rotation** if it's been ≥12 months. Otherwise, note next
   due date.
2. **Cert expiry** check — if <12 months remaining, renew.
3. **Dependency security audit**:
   ```bash
   .venv/bin/pip install pip-audit
   .venv/bin/pip-audit
   ```
   Address any HIGH/CRITICAL findings.
4. **DR drill** — practise the recovery procedure from
   [recovery.md](recovery.md). Don't wait until you need it.
5. **Credential register review** — anyone who left? Rotate their
   keys, rotate the admin key.
6. **Disk space** — `df -h .` on Mac, `Get-PSDrive C` on Windows.
   <20% free → archive old `logs/audit-*.log.gz` to off-host cold
   storage and delete.
7. **Compliance ops self-check** —
   [compliance.md § "Annual self-check"](compliance.md#annual-self-check).

---

## Annual

1. **Salt rotation** (mandatory unless your firm's policy is
   different).
2. **Penetration test** — engage a third party to assess. The
   `audit_install` script + the chaos suite are your starting
   evidence pack.
3. **Update [../iso27001-controls.md](../iso27001-controls.md)** with
   any new controls or evidence — auditors expect to see drift over
   time as the system matures.
4. **Backup / DR drill** — restore from a snapshot end-to-end. If you
   can't, the plan needs revision.
5. **Re-issue user keys** — set `--ttl-days 365` on rotation and you
   get this for free; otherwise force-rotate everyone.

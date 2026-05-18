# Runbook: Add a new firm

**When**: A firm has signed the order form + DPA and is ready for hardware install. Or: a firm filled the intake form on the website and you need to mint their install token.

**Time budget**: 90 minutes end-to-end (60 of which is the firm's Mac doing the model download). 15 minutes of vendor-side work.

**Risk if you stop midway**: The firm's hardware sits idle. The install can resume from any phase boundary — no partial state risk.

**Prerequisites**:
- Signed order form + DPA on file in `vendor-records/firms/<firm-slug>/`
- Mac Studio shipped or about to be physically installed
- Cloudflare Workers admin TOTP (for KV ops if needed)
- GPG key access (signing the install bootstrap if firm wants verified path)

## Decision tree

| Scenario | Procedure |
|---|---|
| Firm filled the intake form on the website | Step A — they already have a token; help them through Step B |
| Firm signed offline; no token yet | Step C (mint manually); then Step B |
| Firm is migrating from a prior LocallyAI install | Step D (data transfer) — out of scope of this runbook; **escalate** |

## Step A — Check the firm's auto-issued state

The intake form auto-mints an install token + a telemetry token via the Worker. Verify:

```bash
cd docs/monitor/cloudflare-worker
npx wrangler kv key list --binding TELEMETRY_TOKENS --remote --prefix "firm:" | grep -i "<firm-name-substring>"
```

Expected: one row matching the firm's hash. Its name is `firm:<16-hex>` — copy that hex.

Read the record:

```bash
npx wrangler kv key get "firm:<16-hex>" --binding TELEMETRY_TOKENS --remote
```

Expected: JSON with `firm_name`, `registered_at`, `via: "auto-mint"`, etc. If `acknowledged: true` already, the firm was previously ack'd; if not, you'll ack at the end.

If no row at all → the firm filled the form and the Worker silently failed. Move to Step C.

## Step B — Run install on the firm's Mac

You'll either be physically there OR walking them through over a screenshare.

### B.1 Pre-flight on the Mac

The firm's IT person, or you, runs these checks:

```bash
sw_vers                  # macOS 14+ required (per maintenance.md §macos-version-policy)
which python3            # 3.12+ required
df -h /                  # >= 200GB free recommended
sysctl hw.memsize        # >= 192 GB unified memory recommended
fdesetup status          # FileVault must be On
```

If FileVault is OFF: do not proceed. The DPA assumes at-rest encryption. Schedule with the firm's IT to enable, then resume.

### B.2 Download the install bootstrap

The simplest path (firm has the install token from the intake form email):

```bash
curl -fsSL "https://locallyai-monitor.your-cf-account.workers.dev/onboarding/intake?t=<install-token>" | bash
```

If the firm wants the GPG-verified path (some risk officers insist):

```bash
curl -fsSL https://raw.githubusercontent.com/locallyai-uk/locallyai-public/main/release-signing-key.gpg | gpg --import
curl -fsSL "https://locallyai-monitor.your-cf-account.workers.dev/onboarding/intake?t=<install-token>" -o bootstrap.sh
curl -fsSL "https://locallyai-monitor.your-cf-account.workers.dev/onboarding/intake?t=<install-token>&sig=1" -o bootstrap.sh.sig
gpg --verify bootstrap.sh.sig bootstrap.sh && bash bootstrap.sh
```

The bootstrap will:
1. **Generate a per-firm SSH deploy keypair + auto-register the public key** with the vendor monitor's `/onboarding/deploy-key` endpoint (no manual paste; lands in `LocallyAI/vendor-records` settings → Deploy keys, labelled with the firm name + creation date). Falls back to printing the public key + the GitHub UI URL if the worker is misconfigured. See `docs/sop/repo-access.md` "Auto deploy-key flow" for the full mechanism + the secrets it needs on the worker.
2. Clone the repo to `~/locallyai` using the new deploy key
3. Run `install.sh`, which prompts for region (UK/KSA), HA mode, and bilingual setup
4. Generate TLS cert, mint admin key, write `.env`
5. Download the model (Mistral or Qwen depending on region — this is the long step)
6. Register launchd service
7. Start the API
8. Send first heartbeat to the vendor monitor

### B.3 What "expected output looks like" at each phase

After install completes, you should see in the firm's terminal:

```
Install complete.
  API:        https://<office-mac-host>:8000
  Manager:    Open in browser: https://<office-mac-host>:8000
  Admin key:  <printed-once>  ← capture for vendor-records
  Telemetry:  on (heartbeats every 5 min to vendor)
```

Capture the **admin key** immediately. It is shown ONCE. Save it to `vendor-records/firms/<firm-slug>/credentials.gpg` (encrypted with the firm's PGP fingerprint, not yours).

### B.4 Verify the API responded

```bash
curl -k https://<office-mac-host>:8000/healthz
```

Expected: `{"ok": true, "backend": "mlx"}`

```bash
curl -k -H "Authorization: Bearer <admin-key>" https://<office-mac-host>:8000/admin/users
```

Expected: a JSON array containing the `Admin` user (the install creates one).

### B.5 Verify heartbeat reached the vendor

Wait 60 seconds after install completes. Then on your laptop:

```bash
cd docs/monitor/cloudflare-worker
npx wrangler kv key get "<firm-id-16-hex>" --binding FIRM_STATE --remote | head -3
```

Expected: a JSON record with `last_seen` within the last 2 minutes.

If absent after 5 minutes: the firm's Mac can't reach Cloudflare. Check:
- `LOCALLYAI_TELEMETRY=on` in `.env`
- `LOCALLYAI_MONITOR_URL` is set to the production worker URL
- Outbound HTTPS is allowed from the firm's network

### B.6 Acknowledge the firm

Open the vendor monitor dashboard:
```
https://locallyai-monitor.your-cf-account.workers.dev
```

Sign in. The "Pending firms" panel should show this firm. Click **Acknowledge**.

This sets `acknowledged: true` on the TELEMETRY_TOKENS record, which is the signal that vendor-side onboarding is complete.

### B.7 First-snapshot baseline

Per `dpo-monthly-snapshot.md`, generate the first snapshot now and file it as the baseline. The DPO uses this as their day-zero record.

## Step C — Manually mint a token for an offline-signed firm

```bash
cd ~/locallyai
.venv/bin/python scripts/onboard_firm.sh "<firm-name-canonical>"
```

This:
1. Computes `firm_id = sha256("locallyai-firm:<firm-name>").hexdigest()[:16]`
2. Mints a 64-char telemetry token
3. Writes the record to TELEMETRY_TOKENS via wrangler

Capture the printed token. Send it to the firm's IT contact via your secure channel. Then proceed to Step B with that token.

## Things that go wrong

| Symptom | Cause | Fix |
|---|---|---|
| Bootstrap fails with "intake token already consumed" | Token was used (one-time) | Mint a fresh one via Step C |
| `install.sh` hangs at "Downloading model" | Network slow OR HF rate-limit | Wait; if 60+ min, abort + restart, will resume from cache |
| Admin key printed but firm lost it | The install only prints once | `cd ~/locallyai && .venv/bin/python manage_users.py rotate-admin` — prints a fresh one + audit-logs the rotation |
| First heartbeat never arrives | Firewall blocks `*.workers.dev` | Add CF Workers domain to firm's egress allowlist |
| Region picker appears unexpectedly during automated install | `LOCALLYAI_DATA_REGION` env not set | Pre-set in the install bootstrap with `LOCALLYAI_DATA_REGION=UK bash install.sh` |

## When to escalate

- The firm's hardware spec is below `docs/sop/setup-mac-single.md` minimums → founder before install
- Heartbeat doesn't reach Cloudflare after 30 minutes despite all checks passing → founder
- The firm's IT insists on installing on Linux/Windows for KSA market → founder (the Linux variant exists in roadmap, not shipped)
- The firm wants HA setup → use `docs/sop/setup-mac-ha.md` AND escalate (HA installs need founder review for the first 5 firms)

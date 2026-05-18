# System updates — vendor releases & firm-side application

When to read: any time the vendor publishes a new release of the
LocallyAI server itself (not the staff laptop client apps — those are
in [client-install.md](client-install.md)). This chapter covers BOTH
sides — the vendor's release procedure AND what happens on the firm's
office Mac when a new release lands.

## TL;DR

Vendor publishes to `dev` channel → 24-48 h soak with no rollback →
vendor promotes to `stable` channel → firms' office Macs auto-apply
tier A on a sentinel tick OR surface tier B/C in the manager UI for
human approval. Every step is GPG-signed, hash-verified, kill-switch-
guarded, and atomically deployed with auto-rollback on health-check
failure.

---

## Defence-in-depth checklist

| # | Defence | What it stops |
|---|---|---|
| 1 | Two channels (dev → stable, 24-48 h soak) | Bad release reaching firms before vendor catches it |
| 2 | GPG-signed tags (vendor's offline key) | Compromised GitHub account pushing malicious code |
| 3 | SHA-256 manifest per release | Tampered artefacts (force-push, MitM, supply chain) |
| 4 | OOB kill switch (static JSON on different host) | Bad release that passed every other check |
| 5 | Per-tier opt-out (`LOCALLYAI_AUTO_UPDATE_TIERS`) | Paranoid IT pinning to manual mode |
| 6 | Atomic deploy + healthz-rollback | Update breaking the deployment |
| 7 | Audit log (HMAC chain) | Disputes about who applied what when |

Future-work defences (require external infrastructure / cert spend):
code-signing of binaries, reproducible builds, canary cohort across
firms, branch protection enforcement via GitHub API.

---

## Tiers

Every release is classified by **blast radius**, declared in
`release_manifest.json` and shown in the manager UI:

| Tier | Examples | Auto-apply default | UI surfacing |
|---|---|---|---|
| **A** — security / critical | CVE patch, audit-chain bug fix, key-handling fix | Yes (sentinel applies within 6 h) | Banner: "applied automatically" |
| **B** — feature / improvement | New endpoint, UI redesign, model-picker addition | No (operator clicks Apply) | Banner: "Update available" |
| **C** — breaking / manual | Schema migration, API contract change | No (vendor coordinates window) | Banner: "Manual coordination required — contact vendor" |

Per-tier opt-out via env on the firm's office Mac:

```
LOCALLYAI_AUTO_UPDATE=on            # default; off = pin everything to manual
LOCALLYAI_AUTO_UPDATE_TIERS=A       # default; A,B = also auto-apply tier B
LOCALLYAI_UPDATE_CHANNEL=stable     # default; dev = subscribe to dev releases
```

---

## Vendor procedures

### One-time setup (vendor side)

1. **Generate GPG signing key offline** (Yubikey or air-gapped machine):
   ```bash
   gpg --full-generate-key   # RSA 4096, no expiry, "LocallyAI Releases <releases@locallyai.app>"
   gpg --armor --export releases@locallyai.app > docs/release-signing-key.gpg
   git config --global user.signingkey <KEY-ID>
   ```
2. Commit `docs/release-signing-key.gpg` to the repo.
3. Set up the OOB kill-switch host.

   The shipped default points at a separate GitHub account
   (`locallyai-status/locallyai-status` repo, public, hosting just
   `status.json`). To set this up:
   - Create a NEW GitHub account using a different email + 2FA
     device than the LocallyAI org account. **The credential
     separation is the entire point** — same person owning both is
     fine; a single compromised password / authenticator must not
     unlock both.
   - Create the public repo and add `status.json` from
     `docs/kill-switch/status.json.template`.
   - Authenticate the kill-switch CLI with that account:
     ```bash
     gh auth login   # pick the separate locallyai-status account
     ```

   Tier upgrades when in production:
   - **Cloudflare Pages + custom domain** (`updates.locallyai.app`) —
     static file, free, separate vendor entirely from GitHub.
   - **AWS S3 + CloudFront + IAM** — production-grade audit logs +
     WAF rules, ~$1/mo, full credential separation.

   In every case, the host must be:
   - **Independently authenticated** (separate creds from the main repo).
   - **Cacheable** (static file, not auth-gated — clients fetch with
     no headers and no session).
   - **Updateable in minutes** during an incident.

### Per-release flow

```bash
# 1. Tag dev release (signed, manifest auto-built):
scripts/release_server.sh dev 1.2.0 A "fix: BM25 race on bulk ingest"

# 2. Watch the vendor's dev box auto-apply (sentinel within 6 h, or manager
#    UI shows the update + Apply button immediately).
#    Soak 24-48 h. Confirm: no rollback, no errors in audit.log,
#    no /healthz failures.

# 3. Promote to stable:
scripts/release_server.sh promote 1.2.0
# Re-tags as v1.2.0-stable, signed.

# 4. Firms see the update on their next sentinel tick (≤ 6 h).
#    Tier A → auto-applies. Tier B/C → operator clicks Apply.
```

### If a bad release ships

The kill-switch quick-action script (`scripts/kill_switch_emergency.sh`)
wraps the common operations. Two backends:

- **TOTP-gated Worker** (recommended): set
  `LOCALLYAI_KILL_SWITCH_API_URL=https://locallyai-killswitch.<acct>.workers.dev/`.
  The script POSTs the action to the Cloudflare Worker, prompts for a
  6-digit TOTP code from your phone's authenticator. Worker verifies
  the code, then uses ITS OWN GitHub PAT (held server-side as a CF
  env-secret) to update `status.json`. Laptop holds nothing sensitive.
  See [docs/kill-switch/cloudflare-worker/README.md](../kill-switch/cloudflare-worker/README.md)
  for one-time deployment.
- **Direct gh CLI** (legacy fallback when the Worker isn't deployed):
  the local `gh` must be authenticated as a SEPARATE OOB account (NOT
  the LocallyAI org). Less secure — laptop compromise = kill-switch
  compromise.

```bash
# STOP — block ALL updates globally
scripts/kill_switch_emergency.sh stop "v1.2.0-stable causing healthz failures"

# Or just block the specific bad tag (other releases continue):
scripts/kill_switch_emergency.sh blocklist v1.2.0-stable

# After hotfix is out, force firms past the bad version:
scripts/kill_switch_emergency.sh require-version 1.2.1

# Once enough firms are on the hotfix, lift the global stop:
scripts/kill_switch_emergency.sh resume

# Anytime: check current state
scripts/kill_switch_emergency.sh status
```

Each operation reads the current status.json from the OOB repo,
shows the diff, asks for confirmation, then publishes via the GitHub
Contents API (PUT `/repos/{owner}/{repo}/contents/{file}`). Firms'
office Macs see the change within ≤60 s (`kill_switch.py` cache TTL).

Then:
1. **Communicate** — email IT contacts at every firm.
2. **Issue a hotfix release** (next version, tier A) so firms move
   forward, not backward.
3. **Post-mortem** — what got past the dev soak? Update tests,
   tighten the soak window if needed.

---

## Firm-side procedures

### Manager UI walkthrough

1. Sign into the manager UI (admin key).
2. Click **Updates** in the sidebar.
3. Top strip shows the current chain-of-trust:
   - Channel (`stable` or `dev`)
   - Current version
   - GPG availability (red if `gpg` not installed — `brew install gnupg`)
   - Kill switch (red if active or unreachable+required)
4. Available releases listed below, each with per-check verdicts:
   - GPG: PASS / FAIL with key id
   - Manifest: PASS / FAIL with mismatch detail
   - Kill-switch: PASS / FAIL with reason
5. **Apply** button enabled only when ALL three checks pass.
6. Click Apply → modal confirms → API restart + 60 s healthz wait →
   either green "Applied" or red "Rolled back" banner.

### CLI alternative

```bash
# What's pending?
.venv/bin/python -m system_updates list

# Verify a specific tag without applying:
.venv/bin/python -m system_updates verify-tag v1.2.0-stable

# Apply (same atomic + rollback path as the UI):
.venv/bin/python -m deploy v1.2.0-stable
```

### LLM model switching

Separate flow for picking which language model the firm runs (not
governed by the channel/tier system above; lives in **Models** in the
sidebar). Curated list of MLX-formatted models (Qwen 2.5 1.5B/3B/7B/14B,
Llama 3.2 3B, Mistral Small). Click → download via huggingface-hub →
atomic `MLX_MODEL=` swap in `.env` → API restart.

For an off-list model (advanced operators only), edit `MLX_MODEL` in
`.env` directly and restart. We curate the in-UI list to prevent an
admin from accidentally activating a model that ships custom inference
code via `trust_remote_code=True`.

---

## Kill-switch runbook — invoking it when something goes wrong

The kill switch is the vendor's emergency-stop. Once invoked, every
firm's office Mac stops applying any system update within ≤60 s
(`kill_switch.py` cache TTL). This is the runbook for the **vendor
on-call** (you).

### Pre-requisites (one-time, set up at install)

You should ALREADY have these from the
[Cloudflare Worker setup](../kill-switch/cloudflare-worker/README.md):
- `LOCALLYAI_KILL_SWITCH_API_URL` exported in your shell (`~/.zshrc`)
- TOTP secret in your phone's authenticator app ("LocallyAI: killswitch")
- 10 recovery codes saved (password manager + sealed envelope)

If any of these are missing, **set them up BEFORE you need them**.
Mid-incident is the wrong time to discover the kill switch isn't
ready.

### Decision tree — which command to run

```
Did a bad release ship?
│
├─ YES, multiple firms hitting healthz failures
│  │
│  └─ STOP everything globally:
│     bash scripts/kill_switch_emergency.sh stop "<reason>"
│
├─ YES, only ONE specific tag is bad (others fine)
│  │
│  └─ Block just that tag:
│     bash scripts/kill_switch_emergency.sh blocklist v1.2.0-stable
│
├─ Hotfix is out, want firms past the bad version
│  │
│  └─ Force minimum version:
│     bash scripts/kill_switch_emergency.sh require-version 1.2.1
│
├─ Investigation done, all firms on the hotfix
│  │
│  └─ Lift the global stop:
│     bash scripts/kill_switch_emergency.sh resume
│
└─ Just want to check current state
   │
   └─ bash scripts/kill_switch_emergency.sh status
```

### Step-by-step — STOP everything

When you can't isolate the bad behaviour to one tag and need to halt
all updates across all firms.

```bash
# 1. From any laptop with the env var set + your phone in hand:
bash scripts/kill_switch_emergency.sh stop \
  "v1.2.0-stable causing healthz failures across firms; investigating"
```

The script will:
1. Print the API URL and the action
2. Prompt: `6-digit TOTP code (or 16-char recovery code):`
3. Read the 6 digits from your phone's authenticator (input is hidden)
4. POST to the Cloudflare Worker
5. Print: `✓ Published. Firms react within ≤60 s.` + the new JSON

What you should see in the response JSON:
```json
{
  "ok": true,
  "payload": {
    "kill_switch_active": true,
    "message": "v1.2.0-stable causing healthz failures across firms; investigating",
    "updated_at": "2026-..."
  }
}
```

What firms see within ≤60 s:
- `kill_switch.py` polls the Worker, sees `kill_switch_active: true`
- Sentinel's auto-apply tick refuses any pending update
- Manager UI's `/updates` page shows "Kill switch ACTIVE" in red with your message
- Audit log gets a `system_update_refused` entry with reason `kill switch: <message>`

### Step-by-step — block ONE tag

When the bad release is isolated and you don't want to halt unrelated
updates (e.g. a tier A security patch you want firms to keep getting).

```bash
bash scripts/kill_switch_emergency.sh blocklist v1.2.0-stable
# Enter TOTP when prompted.
```

Effect: only `v1.2.0-stable` is refused. `v1.2.1-stable`, `v1.3.0-stable`,
etc. continue to be eligible for auto-apply / manual apply.

### Step-by-step — force firms past a vulnerable version

Once your hotfix is out, set a minimum version. Firms below that
version see "Update required" and the older release no longer applies
(belt-and-braces; the audit log records the floor).

```bash
bash scripts/kill_switch_emergency.sh require-version 1.2.1
# Enter TOTP when prompted.
```

### Step-by-step — RESUME (lift the kill switch)

Only after you've confirmed:
1. Hotfix is shipped and signed (`scripts/release_server.sh promote 1.2.1`)
2. At least the canary firm has applied it without rollback
3. Audit logs show no further `system_update_rolled_back` events

```bash
bash scripts/kill_switch_emergency.sh resume
# Enter TOTP when prompted.
```

After this, firms resume normal auto-apply behaviour. Tier A updates
flow within 6 h; tier B/C wait for human approval as usual.

### Verifying the switch took effect

```bash
# 1. Check the public Worker JSON directly (no auth needed):
curl -s "$LOCALLYAI_KILL_SWITCH_API_URL"
# Should reflect your action (kill_switch_active: true / message / etc.)

# 2. Check what office Macs see (any firm's terminal):
.venv/bin/python -m kill_switch status
# kill_switch_active: True   message: "<your reason>"

# 3. Check the manager UI:
# Sign in → /updates page → Kill switch chip should be RED + "ACTIVE"
```

If a firm reports the manager UI still shows "clear" 60+ seconds
after you flipped the switch:
- They might have an outdated `LOCALLYAI_KILL_SWITCH_URL` pointing at
  the old GitHub-backed kill switch — update their `.env`.
- Or `LOCALLYAI_KILL_SWITCH_REQUIRED=0` — they've opted out of
  enforcement; only the audit log records the refusal.

### If you've lost your phone

Use a recovery code (single-use) instead of the 6-digit TOTP at the
prompt:

```bash
bash scripts/kill_switch_emergency.sh stop "<reason>"
# At the prompt, paste one of your 10 saved recovery codes (16 hex chars)
```

Within 24 h, regenerate the TOTP secret pool:

```bash
bash scripts/kill_switch_totp_setup.sh
# Re-upload the new secrets to the Worker:
cd docs/kill-switch/cloudflare-worker
echo '<new TOTP_SECRET_BASE32>' | npx wrangler secret put TOTP_SECRET_BASE32
echo '<new RECOVERY_CODES_HASHED>' | npx wrangler secret put RECOVERY_CODES_HASHED
```

Old codes stop working immediately.

### Communications during an incident

Templates ready to copy-paste:

**To IT contacts at affected firms** (when you flip stop):
> Subject: LocallyAI updates paused — investigating
>
> We've detected an issue with release v1.2.0-stable (<symptom>) and have
> activated the kill switch. Your office Mac will refuse to apply this
> release within the next 60 seconds. The system continues to operate
> normally on the previously-applied version. We'll send a follow-up
> when the hotfix is ready and the kill switch is lifted.

**When you ship the hotfix** (after `release_server.sh promote 1.2.1`):
> Subject: LocallyAI hotfix v1.2.1-stable available
>
> The fix for <issue> is shipped as v1.2.1-stable. We've blocked v1.2.0
> and set v1.2.1 as the minimum required version. Your manager UI's
> Updates page now shows v1.2.1-stable as available. It's tier A so
> the sentinel will auto-apply within 6h, OR you can apply immediately
> via Manager UI → Updates → Apply.

### Vendor post-incident checklist

After the kill switch is lifted and firms are healthy:

1. **Audit-log review** at every firm — `tail -200 logs/audit.log | grep system_update_`
2. **Post-mortem doc** in `docs/incidents/YYYY-MM-DD-<short-name>.md`:
   - Timeline (release → detection → kill-switch invocation → hotfix → resume)
   - Root cause
   - What got past the dev soak
   - What detection signal would have caught it earlier
3. **Tighten the soak window** if needed (raise `LOCALLYAI_DEV_SOAK_HOURS`)
4. **Update tests** so the regression is caught in CI before it ships again

### Firm IT actions during a kill-switch event

(Cross-link this section in your firm-IT-team comms.)

1. Manager UI's Updates page header shows **"Kill switch ACTIVE"** in
   red along with the vendor's reason message.
2. **Don't manually apply anything** during the freeze. The Apply
   buttons are disabled — they refuse updates that fail the kill-switch
   check, but it's still good practice not to try.
3. Once the kill switch lifts and the hotfix shows up:
   - Tier A → applies automatically on the next sentinel tick (≤6 h)
   - Tier B → click Apply when convenient
4. Verify your local kill-switch poll is healthy:
   `.venv/bin/python -m kill_switch status` should show
   `kill_switch_active: false` and `reachable: true`.

---

## What this chapter does NOT cover

- Client-app distribution (.dmg/.msi for staff laptops): see
  [client-install.md](client-install.md).
- Database/schema migrations (rare; tier C with vendor-coordinated
  window): the migration script ships in the release, the manager UI
  warns "manual coordination required", and operators run a documented
  sequence.
- Rollback to an arbitrary previous version: out of scope for the
  in-UI flow today. Use git directly:
  ```bash
  git fetch --tags && git checkout v1.1.0-stable
  launchctl kickstart -k gui/$(id -u)/app.locallyai.api
  ```

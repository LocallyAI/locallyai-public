# Install checklist — for the engineer on-site

> **Audience:** the LocallyAI vendor engineer (or firm IT if doing a
> remote-walkthrough install) actually performing the office Mac install.
>
> **Purpose:** a literal tickable list. Print it. Open it on your laptop.
> Do not leave site with any unchecked item. Catches the regressions
> dogfood has surfaced over and over: missing env vars, untrusted TLS
> cert, wrong embed model, launchd plist conflicts, model that doesn't
> fit RAM, .app left non-executable.
>
> Companion reading (do NOT skip): the matching detail chapter for the
> topology you're installing — [setup-mac-single.md](setup-mac-single.md)
> or [setup-mac-ha.md](setup-mac-ha.md) or
> [setup-windows.md](setup-windows.md).

---

## A. Before you leave for the firm (vendor laptop)

- [ ] Latest `main` pulled: `cd ~/locallyai && git pull origin main`
- [ ] Your laptop's wrangler auth still valid: `cd docs/monitor/cloudflare-worker && npx wrangler whoami`
- [ ] `firms-registry.json` backed up to off-site disk in the last 7 days
- [ ] GPG release-signing key present + passphrase recallable (you'll
      need it if you cut a release while on-site): `gpg --list-secret-keys`
- [ ] Vendor laptop kit packed per [vendor-onboarding.md §3
      laptop kit](../vendor-sop/vendor-onboarding.md#vendor-laptop-kit-pack-the-night-before)
- [ ] **USB-A flash drive** with these files staged:
  - [ ] Latest `.dmg` for `LocallyAI Workspace.app` (in case office Mac
        can't reach GitHub for the auto-mirror pull)
  - [ ] Backup of the firm's `install-<slug>.env` (in case the bootstrap
        URL is unreachable from the firm's network)
- [ ] The firm's **deploy key (private)** loaded into your 1Password
      vault as a secure note; share-link **NOT** yet generated
      (generate at the install, with a 24h expiry, paste only into
      the bootstrap)
- [ ] Phone has the **monitor TOTP** entry; tested it works in the last
      24h (don't discover at the firm that the entry is broken)
- [ ] Phone battery >70%, charger packed
- [ ] Calendar block: 4 hours on-site + 2 hours buffer

---

## B. Pre-arrival call (morning of install)

Phone the IT primary 30 minutes before leaving:

- [ ] Office Mac is **powered on**
- [ ] Office Mac is **on the firm's network** (Ethernet preferred)
- [ ] Office Mac has **at least 200 GB free** disk
- [ ] Office Mac is logged in to the **firm's IT user account** (not a
      lawyer's personal account — installs touch launchd which is per-user)
- [ ] **IT contact will be on-site** for the install (not optional —
      they need to type the admin password for the TLS cert install
      and for any UAC-style elevated steps)

If any item is no → reschedule. Do not drive to a Mac that's off.

---

## C. On arrival — physical context (5 min)

- [ ] Signed in at firm reception (yourself + LocallyAI as vendor)
- [ ] Escorted to the Mac — note the location for `firm-profile.md`
      (server room / IT closet / specific office)
- [ ] Photographed the Mac model + RAM badge + S/N (with IT consent,
      save to vendor-records/firms/<slug>-photos/)
- [ ] Confirmed Mac is plugged into a stable power source
- [ ] Confirmed Ethernet works: `ping -c 3 8.8.8.8`

---

## D. Hardware sanity (3 min)

On the office Mac, Apple menu → About This Mac:

- [ ] Chip: **Apple M1/M2/M3/M4** (NOT Intel — refuse install)
- [ ] Memory: at least 16 GB (24 GB+ for Qwen 2.5-14B; 32 GB+ for any
      30B-class model)
- [ ] Storage: at least 200 GB free (System Settings → General → Storage)
- [ ] macOS version: **matches the supported version** (check current
      supported list in [maintenance.md §macos-version-policy](maintenance.md#macos-version-policy))

If macOS is newer than the supported version: **STOP and call the
on-call engineer.** Do not install on an untested macOS version (see
[maintenance.md §macos-version-policy](maintenance.md#macos-version-policy)
for why). The firm will need to downgrade (or wait until vendor has
tested the newer version).

---

## E. Lock the macOS version (4 min — critical)

Two-Mac HA gives **hardware redundancy**; macOS auto-update breaks the
**software redundancy** assumption. A single OS update can take both
Macs offline at the same time. Disable auto-updates BEFORE the install:

- [ ] System Settings → General → Software Update → **Automatic Updates** → click the ⓘ button
- [ ] **Check for updates**: ON (operator may want to know they're available)
- [ ] **Download new updates when available**: **OFF**
- [ ] **Install macOS updates**: **OFF**
- [ ] **Install application updates from the App Store**: **OFF**
- [ ] **Install Security Responses and system files**: **ON** (these
      are CVE patches — separate from full macOS upgrades; safe to keep on)
- [ ] **Done** → close the dialog
- [ ] Verify via `softwareupdate --schedule` in Terminal — should report `Automatic check is off`

Record the current macOS version + build in the firm's profile:

```sh
sw_vers
# ProductName:        macOS
# ProductVersion:     14.4
# BuildVersion:       23E214
```

Vendor records this in `vendor-records/firms/<slug>.md` under a new
section "macOS version pin".

---

## F. Pre-stage .env + run the bootstrap (15 min)

- [ ] Open Terminal on the office Mac
- [ ] Open the intake form on your vendor laptop: click *Generate install command* (firm name confirmed correctly)
- [ ] Copy the bash one-liner from the form (macOS / Linux tab)
- [ ] Paste into Terminal on the office Mac, hit Enter
- [ ] Press Enter at the "Press Enter to continue" confirmation
- [ ] At the **Deploy key** prompt:
  - [ ] On your vendor laptop, open 1Password → the deploy key entry → **Share** → 24h expiry
  - [ ] Paste the share link into Terminal on the office Mac (or use
        `pbpaste` if 1Password CLI is set up there too)
  - [ ] OR: prompt for the path if the key is already on the Mac
- [ ] Bootstrap runs git clone (~30s for a fresh clone)
- [ ] install.sh launches — confirm it shows your selected region
      (UK / KSA) without re-prompting
- [ ] Pick deployment mode (production vs demo)
- [ ] Pick inference backend (MLX for KSA — must pick this for Arabic
      retrieval)
- [ ] Pick LLM model — verify size fits RAM (see [setup-mac-single.md
      §model-selection](setup-mac-single.md))
- [ ] **Wait** for model pre-fetch (Qwen 2.5-7B = ~3 min on fibre)
- [ ] **Wait** for Workspace UI build (~1 min)

---

## G. TLS cert trust (2 min)

Self-signed cert. Browser will refuse fetches without manual exception.

- [ ] In Safari (or whatever the firm uses): open `https://localhost:8000/healthz`
- [ ] "This Connection Is Not Private" → **Show Details** → **visit this website**
- [ ] Type the macOS user password to confirm
- [ ] You should see `{"ok":true,...}`
- [ ] If Chrome: type `thisisunsafe` on the warning page

This step has to be done **per browser** that the firm's staff will
use. If they have multiple browsers, repeat for each.

---

## H. Verify launchd is healthy (3 min)

- [ ] `launchctl list | grep app.locallyai`
  - [ ] `app.locallyai.api` shows a **real PID** (not a dash) + **0** in the exit-status column
  - [ ] `app.locallyai.worker-ui` shows the same
- [ ] `curl -k https://localhost:8000/healthz` returns `{"ok":true,...}`
- [ ] No legacy plists present:
      `ls ~/Library/LaunchAgents/ | grep -E "com.locallyai|locallyai.server"`
      should show nothing — only `app.locallyai.*` plists
- [ ] `bash scripts/audit_install.sh` reports `pass=14  warn≤1  fail=0`
- [ ] `bash scripts/audit_egress.sh` reports `PASS`

---

## I. Verify telemetry round-trip (5 min)

- [ ] `.venv/bin/python -m telemetry post` returns `ok=True`
- [ ] On your phone, sign in to the monitor dashboard via TOTP
- [ ] Within 5 minutes the firm appears as a **green card** with the
      firm_id hash you registered

If telemetry post returns `ok=False detail=HTTP 401`:
- [ ] The `firm_id` in `.env` doesn't match what's registered in the
      Worker. Run `bash scripts/onboard_firm.sh ~/Downloads/firm-profile-<slug>.md`
      to fix.

If telemetry post returns `ok=False detail=URL or token not configured`:
- [ ] `.env` missing `LOCALLYAI_TELEMETRY_URL` or `LOCALLYAI_TELEMETRY_TOKEN`.
      Both should be in the bootstrap-written `.env` automatically; if
      not, run `grep -E "TELEMETRY|FIRM_NAME" .env` and add what's missing.

---

## J. Workspace UI + first chat (5 min)

- [ ] `LocallyAI Workspace.app` opens (double-click from the install dir)
- [ ] Login gate shows the **firm pill** with correct firm name
- [ ] Enter the admin key (saved earlier when install.sh printed it)
- [ ] Send a test message: "what is in your knowledge base?"
- [ ] Response arrives within ~30s on first call (model loading)
- [ ] Sources panel shows real document references (if demo mode)
- [ ] Conversation renames cleanly when you click the pencil icon
- [ ] Header pills don't wrap — firm name truncates with ellipsis if long
- [ ] Settings cog (sidebar bottom-left) opens with Language + Theme options

---

## K. Decom check — no legacy state left behind (2 min)

If you re-ran install.sh on an existing install:

- [ ] No `com.locallyai.server.plist` in `~/Library/LaunchAgents/`
- [ ] No `~/.locallyai/vendor/` folder on the office Mac (that's
      vendor-side state; should be on your laptop only)
- [ ] No stale processes on port 8000: `lsof -iTCP:8000 -sTCP:LISTEN`
      should show only one process, and it should be the supervisor

---

## L. Firm-side handover (10 min)

With IT contact watching:

### L0. Client-care letter — raise it (do NOT skip)

Before any of the technical handover, **raise the AI-disclosure
question** with the IT contact (and ideally the DPO and a partner):

- [ ] **"Has the firm's client-care letter been updated to disclose
      the use of AI tools for document processing?"** — ask this
      verbatim, exactly once, in writing if the call is recorded,
      otherwise in the engineer's notes.
- [ ] Note their answer (yes / not yet / in progress / who's responsible)
      in `vendor-records/firms/<slug>-cs-log.md` as the *Disclosure
      raised at install* entry, with date.

**Why this is here:** it is the firm's professional responsibility,
not the vendor's, to disclose AI use to their own clients. SRA
guidance, ICO guidance on AI, and emerging professional-conduct
standards all push in this direction. But the story will break at
some firm, somewhere — and the vendor wants to be the one who raised
the question on day one, not the one who silently shipped a tool
that lawyers used without telling clients.

If the firm says "we haven't and we won't" — that's their call. Note
it. Don't argue. Move on. The fact that you raised it is what
matters for vendor's own record.



- [ ] Show them `bash scripts/start_locallyai.sh worker` (already
      installed as the default on click; they shouldn't ever need to
      run it manually, but should know it exists)
- [ ] Show them `bash scripts/stop_locallyai.sh` (for planned downtime)
- [ ] Show them `tail logs/launchd_error.log` (when something looks off)
- [ ] Show them `launchctl list | grep app.locallyai` (verifying the
      service is up)
- [ ] Tell them the canonical recovery for "API not responding":
      `launchctl kickstart -k gui/$(id -u)/app.locallyai.api`
- [ ] Give them the vendor on-call phone number + email
- [ ] Confirm they have the **first admin key** in their password
      manager (printed by install.sh — non-recoverable)

---

## M. Post-departure (back at base, same day)

- [ ] Update `vendor-records/firms/<slug>.md`:
  - [ ] Install date
  - [ ] Mac photos committed
  - [ ] macOS version + build pinned
  - [ ] Anything that diverged from intake (different room, OS version, etc.)
- [ ] Update `vendor-records/firms-issued.log` if you rotated tokens on-site
- [ ] First entry in `vendor-records/firms/<slug>-cs-log.md` (per
      [vendor-customer-success.md](../vendor-sop/vendor-customer-success.md))
- [ ] Email IT contact: "install complete. dashboard shows healthy.
      talk soon."
- [ ] **Schedule the 7-day check-in call** in your calendar

---

## N. 7-day soak (vendor-side)

Daily for the first week:

- [ ] Monitor dashboard — green dot for this firm
- [ ] Any new alerts? Triage per [vendor-monitoring.md](vendor-monitoring.md)
- [ ] Spot-check the firm's heartbeat metadata: backend, version, region all match intake
- [ ] Day 4: brief phone check-in with IT primary — anything weird?

---

## O. 30-day acceptance (close the engagement)

- [ ] No critical alerts in 30 days
- [ ] At least one tier-A update applied cleanly via the auto-update path
- [ ] At least 3 worker-tier users actively chatting (visible in heartbeat query counts)
- [ ] Firm DPO has run at least one audit export themselves
- [ ] Calendar reminder for 12-month profile re-confirm
- [ ] Move firm record from "active onboarding" to "live" in vendor-records

Once all items in this checklist are ticked, the engagement is closed
and the firm is in steady state per
[vendor-customer-success.md](../vendor-sop/vendor-customer-success.md).

---

## Common failures during install (from V13 dogfood log)

If you hit any of these and they're not covered above, check the V13
dogfood log + [vendor-onboarding.md §common-failures](../vendor-sop/vendor-onboarding.md#common-onboarding-failures-and-how-to-recover):

- Deploy-key prompt closes immediately (curl-pipe-bash stdin issue) — fixed in commit `fc745b1`
- "deploy key looks malformed" — pasted `.pub` instead of private — fixed in `23d0ebe`
- install.sh crashes at line 164 with read EOF — inherited dead pipe — fixed in `2485f93`
- KSA install fetches English embedder — fixed in `d23c7b6`
- "Worker UI launcher not found" — chmod issue — fixed in `5f9d910`
- `launchctl bootstrap` exit 5 — already loaded — fixed in `63936d7`
- `LOCALLYAI_ADMIN_KEY not set` — bootstrap-written .env missing secrets — fixed in `c659369`
- API restart loop, lsof verification failing — fixed in `f491a45`
- Telemetry returns "URL or token not configured" — fixed in `bfb8d1f`
- Telemetry HTTP 401 — firm_id mismatch — re-run `onboard_firm.sh` with correct name

Each fix lives in the commit referenced. If you hit a NEW failure not
in this list, add it here in the same commit as the fix.

# Setup — Mac single-node

End state: one Apple Silicon Mac running LocallyAI, accessible at
`https://localhost:8000`, audit chain verified, first admin user created.

Time required: 15–45 minutes the first time, depending on whether your
Mac already has Homebrew + Python and which model you choose.

If anything fails partway through, **stop and read the bracketed
"if-something-goes-wrong" pointer** in that step — do not retry blindly.

---

## 0. Pre-flight (5 min)

### 0.0 Onboarding intake (vendor-side prerequisite)

Before the on-site visit, the firm's IT contact should have filled out
the onboarding intake form (`/onboarding.html` on the monitor Worker).
The form offers two paths to install:

**Path A — bootstrap one-liner (recommended).** The form's
*Generate install command* button produces a curl invocation backed
by a **single-use, 7-day-expiry** install token. The intake blob is
stored server-side; the office Mac fetches it via the token, which
the Worker atomically marks consumed on first read. The command looks
like:

```sh
curl -fsSL https://locallyai-monitor.<vendor-cf>.workers.dev/bootstrap \
  | LOCALLYAI_INTAKE="$(curl -fsSL https://locallyai-monitor.<vendor-cf>.workers.dev/onboarding/intake?t=<TOKEN>)" bash
```

The bootstrap prompts interactively for the SSH deploy key +
telemetry token (paste from 1Password — never via env, never
echoed), then runs `install.sh` automatically. Copies of the command
leaked via email / scrollback / a shared shell can't be replayed —
the second fetch of the intake URL returns HTTP 410. If the install
fails partway through, regenerate a fresh command from the form.

For first installs at any production firm, the form also displays
a GPG-verified variant of the command that downloads + verifies the
public key + bootstrap + signature before running.

**Path B — manual download + git clone (fallback).** Use this when
the office network can't reach the monitor Worker URL at install
time, or when the firm prefers to inspect every step. The form's
*Generate &amp; download* button produces `firm-profile-<slug>.md`
(vendor records) + `install-<slug>.env` (paste-ready environment).
Then on the office Mac:

```sh
git clone git@github.com:LocallyAI/locallyai.git ~/locallyai
cd ~/locallyai
cp /path/to/install-<slug>.env .env
echo "LOCALLYAI_TELEMETRY_TOKEN=<from 1Password>" >> .env
```

Either path saves ~5 minutes vs the no-intake flow by skipping
install-time prompts for firm name, data region, telemetry
preference, and update channel.

Vendor procedure for receiving and processing the intake: [onboarding.md](onboarding.md).

### 0.1 Hardware check

Click the Apple menu (top-left of the screen) → **About This Mac**.

Verify:

- **Chip** says "Apple M1", "M2", "M3", "M4", or any "M? Pro/Max/Ultra".
  If it says "Intel" — STOP. LocallyAI does not run on Intel Macs.
- **Memory** — minimum 16 GB. 32 GB recommended for any model >7B.
  Mac Studio with 64 GB+ for 70B models.
- **Disk** — go to Apple menu → **System Settings** → **General** →
  **Storage**. Need at least **20 GB free**.

If under-spec: stop here and source the right hardware; do not try to
make a small Mac work with a big model.

### 0.2 macOS version

Apple menu → **About This Mac**. **Match the supported macOS version
band** documented in [maintenance.md §macos-version-policy](maintenance.md#macos-version-policy).

> **Do not install on a macOS version newer than the supported band.**
> The two-Mac-Studio HA setup gives you hardware redundancy; macOS
> auto-updates can take both Macs offline at the same time and there
> is no software redundancy. Vendor tests every new macOS release in
> their own environment before approving for firm upgrades.
> If the office Mac is on a newer version than the supported band,
> call vendor on-call before proceeding. The firm may need to
> downgrade or wait until vendor has validated the new version.

### 0.2a Disable automatic macOS updates (critical)

This step has to happen on **every** office Mac (single-node and both
HA secondaries). Without it, macOS will silently push an update one
night and reboot the Mac into an untested OS version.

1. **System Settings → General → Software Update**.
2. Click the **ⓘ button** next to *Automatic Updates*.
3. Set the toggles:
   - **Check for updates**: ON (firm IT may want to know they're available)
   - **Download new updates when available**: **OFF**
   - **Install macOS updates**: **OFF**
   - **Install application updates from the App Store**: **OFF**
   - **Install Security Responses and system files**: **ON**
     (CVE patches only — separate from full OS upgrades; safe to keep on)
4. Click **Done**.
5. Verify in Terminal:
   ```sh
   softwareupdate --schedule
   # Expected: "Automatic check is off." OR a manual-only setting.
   ```
6. Record the current macOS version + build in the firm's profile
   (vendor records this under `vendor-records/firms/<slug>.md`):
   ```sh
   sw_vers
   ```

The firm is now **pinned to the version they have**. Future upgrades
are vendor-coordinated (see
[maintenance.md §macos-version-policy](maintenance.md#macos-version-policy)).

### 0.3 FileVault check (compliance-critical)

1. Apple menu → **System Settings** → **Privacy & Security** →
   **FileVault**.
2. Status should read **"FileVault is on for the disk."**
3. If OFF: click **Turn On…** and follow the prompts.
   - When prompted to store the recovery key, choose **"Create a
     recovery key and do not use my iCloud account."**
   - **Write the recovery key down** and store it in your firm's
     password vault under "LocallyAI / FileVault recovery."
   - **Without FileVault, the deployment is not GDPR Art. 32 compliant**
     — anyone who steals the box recovers every audit pseudonym from
     the salt next to the audit log.
4. Wait for FileVault to finish encrypting (you can keep working — the
   bar at the bottom of the FileVault screen shows progress).

### 0.4 Network plan

Decide the Mac's name on the LAN (used in the TLS cert). Default is
`localhost`. If users will hit the API from other devices, set a
hostname now: System Settings → **General** → **Sharing** → **Local
Hostname** → click "Edit…" → set e.g. `locallyai-prod`.

### 0.5 Time sync

System Settings → **General** → **Date & Time** → confirm "Set time and
date automatically" is **ON** and "Source" is `time.apple.com` (or your
firm's NTP). Audit-chain timestamps depend on this.

---

## 1. Install Homebrew (3 min, skip if already installed)

### 1.1 Open Terminal

Press **⌘ + Space**, type `Terminal`, press Enter.

### 1.2 Check if Homebrew is installed

In the Terminal window that opens, type exactly:

```bash
which brew
```

then press Enter.

- If it prints a path like `/opt/homebrew/bin/brew` → skip to step 2.
- If it prints nothing or `brew not found` → continue to 1.3.

### 1.3 Install Homebrew

Copy-paste this exact command into Terminal and press Enter:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

You will be prompted for your **Mac login password** (the password you
type to log into the Mac). Type it (you won't see characters; that's
normal) and press Enter.

The installer takes 1–3 minutes.

When it finishes, copy-paste the **two** "Next steps" commands it
prints (they look like `eval "$(/opt/homebrew/bin/brew shellenv)"`),
one at a time, pressing Enter after each. Then run:

```bash
brew --version
```

Should print something like `Homebrew 4.x.x`. If it errors:
[incidents-operator.md § "Homebrew install failed"](incidents-operator.md#homebrew-install-failed).

---

## 2. Clone the repository (1 min)

### 2.1 Pick an install location

We recommend `~/locallyai` (in your home folder). In Terminal:

```bash
cd ~
```

### 2.2 Set up SSH deploy-key access (vendor-managed — read first)

> If LocallyAI is being delivered to you by a vendor, **read
> [repo-access.md](repo-access.md)** before this step. The vendor's
> recommended access pattern is a per-client SSH deploy key (read-only,
> per-Mac, separately revocable). The vendor either prepares the key
> on your Mac as part of delivery, or walks you through generating it
> and registering the public key with their GitHub repo.
>
> If you're cloning from your own fork (the firm hosts the LocallyAI
> code itself), HTTPS or your firm's standard SSH key is fine and you
> can skip the deploy-key chapter.

### 2.3 Clone

**If a vendor set up the deploy-key alias** (per `repo-access.md`):

```bash
git clone git@github-locallyai:<vendor-or-firm>/locallyai.git
cd locallyai
```

**If you're cloning from your own fork** without a deploy key:

```bash
git clone https://github.com/<vendor-or-firm>/locallyai.git
cd locallyai
```

### 2.4 Verify

```bash
ls install.sh
```

Should print `install.sh`. If it errors `No such file or directory` —
you're in the wrong folder. Run `pwd` to see where you are; you should
see something ending in `/locallyai`.

---

## 3. Run the installer (5–30 min — most of it is the model download)

### 3.1 Start

```bash
bash install.sh
```

You will be prompted multiple times. Each prompt below tells you what
to answer.

### 3.2 Prompt: deployment mode

```
Choose deployment mode:
  1. Production — empty knowledge base; you ingest your own documents.
  2. Demo       — copy 5 sample legal documents into data/ and ingest them.

Mode [1=production / 2=demo, default 1]:
```

For a real client install: type `1` and press Enter.
For a sales demo / first-time test: type `2` and press Enter.

### 3.3 Prompt: model selection

```
Available models:
  1. qwen2.5:7b         — 4.7 GB, good general purpose
  2. qwen2.5:14b        — 8.9 GB, better quality
  3. llama3.3:70b       — 40 GB, high quality, needs ≥64 GB RAM
  ...
```

Pick by RAM:
- 16 GB Mac → option 1 (`qwen2.5:7b`).
- 32 GB Mac → option 2 (`qwen2.5:14b`).
- 64+ GB Mac Studio → option 3 (`llama3.3:70b`).

Type the number and press Enter. The installer downloads the model
(this is the slow step, 5–25 minutes depending on internet).

### 3.4 Prompt: trust the TLS cert?

```
Add the self-signed TLS cert to the macOS System keychain so browsers
stop warning? [Y/n]:
```

Type `Y` and press Enter, then enter your Mac login password when
prompted (sudo needs it).

If you skip this, every browser will pop a self-signed-cert warning the
first time it visits `https://localhost:8000`, AND the worker-ui's
`fetch()` calls silently fail. Highly recommended to accept.

### 3.5 Save the admin key

When the installer finishes, the **last 5 lines** of output look like:

```
==================================================================
  LocallyAI installed
  --------------------------------------------------------------
  Folder:    /Users/you/locallyai
  API:       https://localhost:8000
  Health:    https://localhost:8000/healthz
  Logs:      /Users/you/locallyai/logs
  Config:    /Users/you/locallyai/.env
  Admin key: <your-generated-admin-key>   (64 hex chars — save this, shown once)
  --------------------------------------------------------------
==================================================================
```

**Copy the admin key NOW.** Open your firm's password vault (1Password,
Bitwarden, Keeper, etc.) and create a new entry:

- Title: `LocallyAI / <firm name> / admin key`
- Username: `admin`
- Password: paste the 64-char hex string
- Notes: `Bearer token for /admin/* endpoints. Do not share. Rotate via 'manage_users.py rotate-admin'.`

**The key is not shown again.** If you miss it, see
[incidents-operator.md § "Forgot admin key"](incidents-operator.md#forgot-admin-key).

---

## 4. Verify the install

### 4.1 Health check

In the same Terminal window:

```bash
curl -sk https://localhost:8000/healthz
```

Expected output:

```json
{"ok":true,"backend":"ollama"}
```

(or `"backend":"mlx"` if you configured MLX.)

If you get `curl: (7) Failed to connect`: the service didn't start.
Read [incidents-software.md § "API not responding"](incidents-software.md#api-not-responding).

### 4.2 Audit-chain check

Set the admin key as a shell variable (replace `<paste>` with the key
you saved in 3.5):

```bash
ADMIN_KEY=<paste>
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/audit-verify
```

Expected:

```json
{"status":"ok","entries":0,"node_id":"<your-mac-hostname>"}
```

`entries:0` is correct on a fresh install (nothing has been chatted yet).

If you see `"status":"skipped"`: the `LOCALLYAI_AUDIT_HMAC_KEY` is not
set. The installer always sets it; if it's missing, your `.env` was
edited or the installer didn't finish. Re-read 3.5 — the installer
output should have included an "OK" for HMAC key generation.

### 4.3 First chat

Add a test user and grab their key:

```bash
python manage_users.py add "TestUser"
```

Output:

```
Added user 'TestUser'.
API key:   <64 hex chars>
Expires:   2026-08-04T...
Store this key securely — it will not be shown again.
```

Save this key in the password vault under `LocallyAI / TestUser`.

Send a chat:

```bash
USER_KEY=<paste-the-testuser-key>
curl -sk -X POST -H "Authorization: Bearer $USER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"hello"}],"max_tokens":20}' \
  https://localhost:8000/v1/chat/completions
```

You should see a JSON response with a `choices[0].message.content`
field containing the model's reply. First request can take 30s+ on cold
load; subsequent requests are fast.

### 4.4 Audit-chain re-verify

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/audit-verify
```

Expected: `entries:1` now, `status:"ok"`.

### 4.5 Run the audit script

```bash
bash scripts/audit_install.sh
```

Last line should read:

```
 Audit complete: pass=14 warn=0 fail=0
```

If `warn=1` and the warn is `5. heartbeat tail …probe_failed`: that's
acceptable on a brand-new install (it's just startup probes that
happened before the API bound). It clears after the next sentinel tick
(60 s).

If `fail≥1`: do not proceed.
[incidents-software.md](incidents-software.md) will name the failing
check (e.g. `fail=1` on `8b. tls/key.pem perms` → run the icacls /
chmod fix in the relevant § ).

---

## 5. Add real users

For each user the firm wants to give access (a fee earner, a paralegal,
a partner):

```bash
python manage_users.py add "First Last"
```

The output prints the API key once. Copy it into:

1. The firm's password vault (one entry per user).
2. The user's worker-ui app (instructions: have them open
   `https://localhost:8000` from their Mac, accept the cert if not
   trusted, paste the key into the worker-ui sign-in screen).

To check who has access at any time:

```bash
python manage_users.py list
```

To rotate a user's key (e.g. they suspect it leaked):

```bash
python manage_users.py rotate "First Last"
```

To remove a user (e.g. they leave the firm):

```bash
python manage_users.py remove "First Last"
```

For GDPR Art. 17 erasure (deeper than `remove`):

```bash
python manage_users.py erase "First Last"
```

See [compliance.md § "Article 17 erasure"](compliance.md#article-17-erasure)
for when to use which.

---

## 6. Ingest the firm's documents

### 6.1 Drop documents into `data/`

```bash
cp ~/Documents/firm-corpus/*.pdf data/
cp ~/Documents/firm-corpus/*.docx data/
```

Supported: `.pdf`, `.docx`, `.txt`, `.md`. **Do not** drop `.doc`
(legacy Word) — convert to `.docx` first.

### 6.2 Run ingest

```bash
python ingest.py
```

This is incremental — only new/changed files are reprocessed. A 100-PDF
corpus takes 2–10 minutes on a Mac Studio.

### 6.3 Verify documents are searchable

Send a chat about something only the firm's documents would know:

```bash
curl -sk -X POST -H "Authorization: Bearer $USER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"What does our standard NDA say about return of materials?"}]}' \
  https://localhost:8000/v1/chat/completions | python3 -m json.tool
```

The response's `usage.sources_retrieved` should be `>0` and `sources[]`
should list the matching documents.

---

## 7. Tell users how to connect

For a single-node install with TLS trusted, users open Safari/Chrome
and visit `https://localhost:8000` (if they're on the same Mac) or
`https://<your-mac-hostname>.local:8000` from another Mac on the LAN.

The browser will load the worker-ui (built during install). They paste
their API key on the sign-in screen and start chatting.

If users are on different Macs, also have them install the cert. From
their Mac, in Terminal:

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain \
  /path/to/locallyai/tls/cert.pem
```

(Copy `tls/cert.pem` from the deployment Mac to theirs first; AirDrop
works.)

---

## 8. Update the credential register

Open your firm's password vault. Make sure these entries exist:

- [ ] `LocallyAI / admin key`
- [ ] `LocallyAI / FileVault recovery key`
- [ ] `LocallyAI / Mac local-account password` (the macOS account the
      service runs under)
- [ ] One entry per user: `LocallyAI / <user name>`

If any are missing, fill them now. The SOP master index has the full
checklist; tick each one off.

---

## 9. Schedule the weekly audit

The repo includes a launchd plist that runs `audit_install.sh` weekly
and writes a Markdown report to `logs/install_audit_YYYY-MM-DD.log`.
Activate it:

```bash
sed -i '' "s|DIR_PLACEHOLDER|$(pwd)|g" com.locallyai.audit.plist
cp com.locallyai.audit.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.locallyai.audit.plist
```

Verify it loaded:

```bash
launchctl list | grep com.locallyai.audit
```

Should print a line with a PID.

---

## 10. You are done

Single-node setup is complete. Next steps:

- Read [daily.md](daily.md) and bookmark it. That's the day-to-day
  cheat sheet (start, stop, check, ingest, user mgmt).
- Read [maintenance.md](maintenance.md) for what to do weekly /
  monthly / quarterly.
- If you also have a second Mac coming online for HA, continue to
  [setup-mac-ha.md](setup-mac-ha.md). **Single-node must be working
  first** — HA layers on top of a working single-node.

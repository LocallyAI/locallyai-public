# Client app install — staff laptops (Mac & Windows)

When to read: deploying the LocallyAI Worker and/or Manager apps to
staff devices that **don't run the full LocallyAI server**. This is
the path for lawyers using the chat UI on their own MacBook, or an
office manager running the admin console from their Windows laptop.

For the office Mac Studio that *runs* the server, see
[setup-mac-single.md](setup-mac-single.md) /
[setup-mac-ha.md](setup-mac-ha.md). Client devices need none of that.

> If staff also need to reach the office Mac from outside the firm's
> office network (home, multi-office, on the road), pair this chapter
> with [remote-access.md](remote-access.md) — that chapter covers the
> four real options for extending the network (Tailscale, firm VPN,
> Cloudflare Tunnel, or office-only) without putting the vendor on the
> access path.

## Pre-requisite: the office Mac is in fleet mode

`install.sh` now asks during initial setup:

```
  Choose fleet topology:
    1. single — one Mac, only browsers on this same Mac use it (loopback only, safest)
    2. fleet  — this Mac is the office server; staff laptops on the LAN connect via the client apps
```

Pick **2 (fleet)** when you'll be deploying client apps to staff
laptops. The installer then:

- Sets `LOCALLYAI_BIND=0.0.0.0` in `.env` (binds API + dev servers
  to all interfaces, not just loopback)
- Detects the office Mac's mDNS hostname (e.g.
  `emanuels-mac.local`) and LAN IP (e.g. `192.168.1.210`)
- Adds both as CORS origins so client requests aren't blocked
- Records `LOCALLYAI_OFFICE_HOST` so the audit log + end-of-install
  summary show what URL staff laptops should use

To switch a single-mode deployment into fleet mode later: edit `.env`
manually (set `LOCALLYAI_BIND=0.0.0.0`, expand `LOCALLYAI_CORS_ORIGINS`
with `https://<host>.local:8000`) and restart the API. Or re-run
`install.sh` and answer 2 at the prompt — it preserves existing
secrets.

## What gets installed on a client device

A small native app (~10 MB) that opens a window pointing at the firm's
LocallyAI server. No Python, no MLX, no Qdrant, no model downloads —
the device only needs network reach to the office server.

| App | What it does | Who installs |
|---|---|---|
| **LocallyAI Worker** | Chat UI for end users | Lawyers, paralegals, fee-earners |
| **LocallyAI Manager** | Admin console: corpus, users, audit, system | IT-ops, DPO, compliance lead |

Both are built from `apps/clients/*-tauri/` in this repo via Tauri 2,
unsigned, and distributed via GitHub Releases.

---

## Where IT finds the apps

### Recommended — download from the firm's office server

The firm's office Mac (running LocallyAI) doubles as an installer mirror.
IT signs into the **manager UI** with the admin key, opens **Client Apps**
in the sidebar, and downloads the `.dmg` / `.msi` they need:

```
https://<office-mac>:8000/  →  Client Apps  →  Download
```

Why this is the default: no GitHub accounts on staff devices, no public
mirror, the installer never leaves the perimeter the firm already
trusts. The office Mac pulls fresh installers from the private repo
once a day automatically (sentinel) and IT can force a pull anytime
via the **Check for updates** button on the same page.

The first-pull setup is automatic during `install.sh` (it asks "Set up
installer mirror? [Y/n]" in fleet mode). After that the `gh` CLI is
configured + authenticated; the daily refresh is on autopilot. To
re-authenticate later (e.g. when a token expires):

```bash
gh auth login --hostname github.com --git-protocol https --web
```

### Refresh vs Rebuild — two different buttons

Two distinct mechanisms keep the firm's installer mirror current.
Pick by what changed:

| Button | Source | When to click |
|---|---|---|
| **Check for updates** | Pulls the latest `vX.Y.Z-clients` GitHub release into `storage/installers/`. Generic builds — staff configure the office hostname on first launch. | A new vendor release shipped (announced in the release notes / vendor email). |
| **Rebuild per-firm apps** | Runs `scripts/build_staff_apps.sh` locally. Compiles the Manager + Workspace `.app` bundles **with this firm's office hostname baked in**, so first-launch needs no URL prompt. Output overwrites the same `storage/installers/*.zip` files. | After a `git pull` on the office Mac (so staff bundles reflect the latest source), OR after the office hostname changed (so the baked URL still points at the right server). |

Both are admin-key gated and exposed via Manager UI → **Client Apps**.
Both run in a background thread; the UI polls every 2.5 s while
in-flight and flips back to "ready" once the server reports
`rebuild_in_flight: false` / `refresh_in_flight: false`.

The Rebuild path needs `swiftc` (Xcode Command Line Tools) on the
office Mac. If it's missing, the button is disabled and a banner
tells IT what to install. Refresh has no swiftc dependency.

#### CLI equivalents

For operators who prefer the shell over the manager UI:

```bash
# Refresh — pull the latest -clients GitHub release
.venv/bin/python -m client_installers refresh

# Rebuild — regenerate per-firm bundles in place
.venv/bin/python -m client_installers rebuild

# Just see status without doing anything
.venv/bin/python -m client_installers status
```

Same effect as clicking the buttons in the UI; same backing functions.
Useful for cron / `launchd` automation, or for debugging when the UI
is unreachable.

### Alternative — direct from GitHub Releases

Useful if you can't reach the office server (working from home, on a
new device, etc.) AND the operator has been added to the
LocallyAI/locallyai repo as a collaborator with read access.

```
https://github.com/LocallyAI/locallyai/releases
```

Each tagged release shows downloadable installers. Filter by your OS:

| OS | File | Where it installs |
|---|---|---|
| macOS | `LocallyAI Worker_*.dmg` | mounts → drag `.app` to `/Applications` |
| macOS | `LocallyAI Manager_*.dmg` | same |
| Windows | `LocallyAI Worker_*_x64_en-US.msi` | `C:\Program Files\LocallyAI Worker\` |
| Windows | `LocallyAI Manager_*_x64_en-US.msi` | `C:\Program Files\LocallyAI Manager\` |

Tag names look like `v1.0.0-clients` — use the `-clients` suffix to
keep client-app releases separate from server-side releases.

### Or: download the raw `.app` / `.msi` from the workflow run

For pre-release / branch builds, every push to `main` that touches
`apps/clients/**` produces installer artifacts on its workflow run:
`https://github.com/LocallyAI/locallyai/actions/workflows/build-clients.yml`

Click any green run → scroll to "Artifacts" → download.

---

## Manual install (one device)

### macOS

1. Download `LocallyAI Worker_*.dmg` (or Manager) from the Releases page.
2. Double-click the `.dmg` — it mounts as a virtual disk in Finder.
3. Drag the `.app` into the `Applications` shortcut shown in the same window.
4. Eject the disk image.
5. **First launch only**: macOS shows "App is from an unidentified
   developer" because we haven't paid for an Apple Developer ID. Right-
   click the app → **Open** → click **Open** in the dialog. From then
   on it launches silently.
6. The app shows a "Where's your office server?" prompt. Enter the URL
   IT gave you (e.g. `https://office-mac.local:8000`) and click
   **Connect**. The browser-style cert warning may appear if the office
   uses a self-signed cert — accept it once.

### Windows

1. Download the `.msi`.
2. Double-click — Windows installer wizard.
3. **First launch only**: SmartScreen shows "Windows protected your PC".
   Click **More info** → **Run anyway**. (Same one-time-per-install
   friction as the Mac unidentified-developer warning.)
4. Same first-run server URL prompt as macOS.

The server URL is saved to:
- macOS: `~/Library/Application Support/app.locallyai.client.{worker,manager}/config.json`
- Windows: `%APPDATA%\app.locallyai.client.{worker,manager}\config.json`

Operators can change it later from the app's Settings menu, or by
editing the file directly.

---

## Bulk install via MDM

Skips the per-device manual steps above. IT pushes the installer
silently to every device in the firm's fleet.

### Jamf (macOS)

1. Upload `LocallyAI Worker_*.dmg` to your Jamf distribution point.
2. Create a policy with **Packages** scope = the .dmg.
3. Trigger = "Recurring Check-in"; Frequency = "Once per computer".
4. Pre-stage the server URL by deploying a config plist with a
   composer or a script:
   ```bash
   sudo -u "$user" mkdir -p "/Users/$user/Library/Application Support/app.locallyai.client.worker"
   echo '{"server_url":"https://office-mac.local:8000"}' \
     | sudo -u "$user" tee "/Users/$user/Library/Application Support/app.locallyai.client.worker/config.json"
   ```
   Repeat for `app.locallyai.client.manager` if deploying both.
5. Scope to the smart group of devices that should get the app.

### Munki (macOS)

1. `makepkginfo /path/to/LocallyAI\ Worker_*.dmg > LocallyAI-Worker.plist`
2. Edit the plist, add to a manifest, run `makecatalogs`.
3. Same pre-stage script as Jamf for the server URL.

### Microsoft Intune (Windows)

1. Wrap the `.msi` as `.intunewin`:
   ```
   IntuneWinAppUtil.exe -c .\source -s "LocallyAI Worker_1.0.0_x64_en-US.msi" -o .\out
   ```
2. In Intune admin: **Apps → Windows → Add → Line-of-business app**.
3. Upload the `.intunewin`. Install command:
   ```
   msiexec /i "LocallyAI Worker_1.0.0_x64_en-US.msi" /quiet
   ```
4. Pre-stage the server URL via PowerShell deployment script:
   ```powershell
   $cfg = Join-Path $env:APPDATA "app.locallyai.client.worker"
   New-Item -ItemType Directory -Force -Path $cfg | Out-Null
   '{"server_url":"https://office-mac.local:8000"}' | Out-File -Encoding utf8 (Join-Path $cfg "config.json")
   ```
5. Assign to the device group.

### Group Policy (Windows, no Intune)

1. Drop the `.msi` on a network share readable by every device.
2. Create a GPO: **Computer Configuration → Policies → Software Settings
   → Software Installation → New → Package** → point at the share.
3. Pre-stage script via Computer Configuration → Logon Scripts.

---

## Rolling out a new version

One command. From the repo root, on a clean main checkout:

```bash
scripts/release_clients.sh
```

This auto-bumps the patch version (or pass an explicit version like
`scripts/release_clients.sh 1.2.0`), updates `tauri.conf.json` +
`Cargo.toml` in both apps, commits, tags `vX.Y.Z-clients`, and pushes.
GitHub Actions then builds the four installers and attaches them to
the release page (~10–15 min cold, ~3–5 min on a warm Cargo cache).

For a dry run before committing:

```bash
scripts/release_clients.sh --dry-run
```

IT pushes the new `.dmg`/`.msi` via the same MDM channel; the saved
server URL config is preserved across upgrades.

For firms that prefer the **per-firm baked builds** (no first-launch
URL prompt for staff), the rollout flow is one extra step:

1. Office Mac: `git pull` to fetch the new source
2. Manager UI → **Client Apps** → click **Rebuild per-firm apps** (or
   `python -m client_installers rebuild` from the shell). Wait for
   the status strip to show `success`
3. IT downloads the refreshed `.app.zip` files from the same page and
   distributes via MDM / email / AirDrop as usual

This path keeps the firm's hostname baked into every staff bundle.
Useful for fleets where staff are non-technical and shouldn't have
to type a URL.

---

## Security notes

- **No secrets ship with the client app.** The bearer-token API key is
  entered into the UI by the user (worker key for the Worker app, admin
  key for the Manager app) and lives only in the user's browser
  storage on that device.
- **Unsigned today.** The unsigned warnings are a one-time per-device
  per-install friction, not an ongoing concern. To remove them entirely,
  pay for:
  - Apple Developer Program ($99/yr) — enables `signtool` + notarisation
  - Windows code-signing cert ($100–500/yr from DigiCert / Sectigo)
  Add the secrets to the repo (`APPLE_*` and `WINDOWS_CERT_*`), then
  uncomment the signing steps in `.github/workflows/build-clients.yml`.
- **Network exposure.** The office Mac Studio must bind its API +
  vite dev servers to `0.0.0.0` (set `LOCALLYAI_BIND=0.0.0.0` in `.env`)
  so client laptops can reach it. Combine with a firewall rule
  restricting ports 8000 / 5173 / 5174 to the office subnet —
  bearer auth + TLS still defend the wire, but reducing exposure to
  the LAN is the right defence-in-depth (ISO 27001 A.8.20 network
  segregation).
- **CORS.** `LOCALLYAI_CORS_ORIGINS` in `.env` already includes
  `tauri://localhost` and `https://tauri.localhost` (the origins the
  Tauri webview presents on macOS and Windows respectively), so the
  client apps can talk to the API out of the box.

---

## Troubleshooting

### "Could not reach <server>" on first launch

- Is the office Mac Studio actually serving? Check from the office
  itself: `curl -sk https://localhost:8000/healthz`.
- Is the office bound to the LAN? Look in `.env`: `LOCALLYAI_BIND` must
  be `0.0.0.0`, not `127.0.0.1`.
- Can the client device reach the server at all? `ping office-mac.local`
  from the laptop.
- Is the firewall blocking? Check macOS Application Firewall + any
  Little Snitch rules; on Windows check the Defender Firewall.

### Self-signed cert warning every launch

The browser/webview accepts the cert *for that origin* once you click
through. If it re-prompts every launch, the user is connecting via a
hostname that wasn't in the cert's SAN list when generated. Re-generate
the cert with both `office-mac.local` and the LAN IP in the SAN, or
have IT push your firm's internal CA cert to every device.

### App opens to the config screen every time

The persisted config file isn't being written. Most common cause:
permissions on `~/Library/Application Support/app.locallyai.client.*/`
(macOS) or `%APPDATA%\app.locallyai.client.*\` (Windows). Delete the
folder, relaunch, and the app re-creates it.

#!/usr/bin/env bash
# Build the staff-laptop client app bundle for this deployment.
#
# Produces, in $STAFF_APPS_DIR (default: $STORAGE_DIR/installers):
#   - LocallyAI Manager.app.zip       — macOS Manager app (DPO/admin)
#   - LocallyAI Workspace.app.zip     — macOS Workspace app (lawyer)
#   - LocallyAI Windows Apps.zip      — Windows shortcut bundle (Edge --app)
#   - LocallyAI Trust Cert.zip        — cross-platform cert-trust helpers
#   - STAFF-INSTALL.md                — instructions for the firm's IT person
#
# Each macOS app has the firm's office-Mac hostname baked into the
# WKWebView's default URL — no first-run configuration for the lawyer.
# The Windows bundle uses Edge `--app=URL` for a chromeless window
# experience equivalent to the macOS apps, plus `.url` fallback
# shortcuts that work on any browser.
#
# Called automatically by install.sh at the tail end of install. Can
# also be run manually after a hostname change:
#   bash scripts/build_staff_apps.sh
#
# Env vars (all optional, sensible defaults from .env / config):
#   OFFICE_HOST      — hostname staff laptops reach the office Mac by
#   MANAGER_URL      — full Manager UI URL (default: https://$OFFICE_HOST:8000)
#   WORKSPACE_URL    — full Workspace URL  (default: http://$OFFICE_HOST:5174)
#   STAFF_APPS_DIR   — output directory (default: $STORAGE_DIR/installers)
#   FIRM_NAME        — for the install README header

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

# ── Resolve defaults from the deployment's own .env ─────────────────────────
if [ -f .env ]; then
    # shellcheck disable=SC1091
    set -a; . .env; set +a
fi

OFFICE_HOST="${OFFICE_HOST:-${LOCALLYAI_OFFICE_HOST:-$(scutil --get LocalHostName 2>/dev/null || hostname).local}}"
MANAGER_URL="${MANAGER_URL:-https://${OFFICE_HOST}:8000}"
WORKSPACE_URL="${WORKSPACE_URL:-http://${OFFICE_HOST}:5174}"
FIRM_NAME="${FIRM_NAME:-${LOCALLYAI_FIRM_NAME:-LocallyAI}}"
STORAGE_DIR="${STORAGE_DIR:-${LOCALLYAI_STORAGE_DIR:-$HERE/storage}}"
STAFF_APPS_DIR="${STAFF_APPS_DIR:-$STORAGE_DIR/installers}"

mkdir -p "$STAFF_APPS_DIR"

echo "Building staff-laptop apps for $FIRM_NAME"
echo "  OFFICE_HOST       = $OFFICE_HOST"
echo "  MANAGER_URL       = $MANAGER_URL"
echo "  WORKSPACE_URL     = $WORKSPACE_URL"
echo "  STAFF_APPS_DIR    = $STAFF_APPS_DIR"
echo ""

# ── macOS apps ──────────────────────────────────────────────────────────────
if command -v swiftc >/dev/null 2>&1; then
    echo "→ macOS: building Manager.app"
    pushd apps/manager-desktop >/dev/null
    MANAGER_URL="$MANAGER_URL" ./build.sh >/dev/null
    cp "dist/LocallyAI Manager.zip" "$STAFF_APPS_DIR/LocallyAI Manager.app.zip"
    popd >/dev/null
    echo "  ✔ $STAFF_APPS_DIR/LocallyAI Manager.app.zip"

    echo "→ macOS: building Workspace.app"
    pushd apps/worker-desktop >/dev/null
    WORKSPACE_URL="$WORKSPACE_URL" ./build.sh >/dev/null
    cp "dist/LocallyAI Workspace.zip" "$STAFF_APPS_DIR/LocallyAI Workspace.app.zip"
    popd >/dev/null
    echo "  ✔ $STAFF_APPS_DIR/LocallyAI Workspace.app.zip"
else
    echo "  ⚠ swiftc not found — skipping macOS app build. Install Xcode CLT."
fi

# ── Windows bundle (Edge --app + .url fallback) ─────────────────────────────
echo "→ Windows: building shortcut bundle"
WIN_STAGE="$(mktemp -d)"
trap 'rm -rf "$WIN_STAGE"' EXIT
mkdir -p "$WIN_STAGE/LocallyAI"

# Edge --app launchers (chromeless windows — equivalent UX to macOS apps).
cat >"$WIN_STAGE/LocallyAI/LocallyAI Manager.bat" <<EOF
@echo off
REM Open the LocallyAI Manager (DPO / admin) in a chromeless Edge window.
REM The --user-data-dir keeps cookies + admin-key localStorage isolated
REM from the user's normal browsing profile.
set "EDGE=%ProgramFiles(x86)%\\Microsoft\\Edge\\Application\\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\\Microsoft\\Edge\\Application\\msedge.exe"
if not exist "%EDGE%" (
    echo Microsoft Edge not found. Falling back to default browser...
    start "" "$MANAGER_URL"
    exit /b
)
start "" "%EDGE%" --app=$MANAGER_URL --user-data-dir="%LOCALAPPDATA%\\LocallyAI\\manager-profile"
EOF

cat >"$WIN_STAGE/LocallyAI/LocallyAI Workspace.bat" <<EOF
@echo off
REM Open the LocallyAI Workspace (chat) in a chromeless Edge window.
set "EDGE=%ProgramFiles(x86)%\\Microsoft\\Edge\\Application\\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\\Microsoft\\Edge\\Application\\msedge.exe"
if not exist "%EDGE%" (
    echo Microsoft Edge not found. Falling back to default browser...
    start "" "$WORKSPACE_URL"
    exit /b
)
start "" "%EDGE%" --app=$WORKSPACE_URL --user-data-dir="%LOCALAPPDATA%\\LocallyAI\\workspace-profile"
EOF

# .url fallback shortcuts (work on any browser; right-click to pin)
cat >"$WIN_STAGE/LocallyAI/LocallyAI Manager.url" <<EOF
[InternetShortcut]
URL=$MANAGER_URL
IconIndex=0
EOF

cat >"$WIN_STAGE/LocallyAI/LocallyAI Workspace.url" <<EOF
[InternetShortcut]
URL=$WORKSPACE_URL
IconIndex=0
EOF

# Desktop-shortcut installer the IT person double-clicks ONCE.
cat >"$WIN_STAGE/LocallyAI/Install Shortcuts.bat" <<'EOF'
@echo off
REM Creates Desktop + Start Menu shortcuts for LocallyAI Manager and
REM Workspace, so the lawyer doesn't have to navigate this folder.
setlocal
set "SRC=%~dp0"
set "DESKTOP=%USERPROFILE%\Desktop"
set "STARTMENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\LocallyAI"
mkdir "%STARTMENU%" 2>nul

copy /Y "%SRC%LocallyAI Manager.bat"   "%DESKTOP%\LocallyAI Manager.bat" >nul
copy /Y "%SRC%LocallyAI Workspace.bat" "%DESKTOP%\LocallyAI Workspace.bat" >nul
copy /Y "%SRC%LocallyAI Manager.bat"   "%STARTMENU%\LocallyAI Manager.bat" >nul
copy /Y "%SRC%LocallyAI Workspace.bat" "%STARTMENU%\LocallyAI Workspace.bat" >nul

echo.
echo Shortcuts installed:
echo   Desktop:   LocallyAI Manager, LocallyAI Workspace
echo   Start Menu: LocallyAI folder
echo.
echo Right-click each shortcut and choose "Pin to taskbar" if you want.
echo.
pause
EOF

cat >"$WIN_STAGE/LocallyAI/README.txt" <<EOF
LocallyAI — staff-laptop apps for $FIRM_NAME
============================================

Quick install (IT person):

1. Right-click "Install Shortcuts.bat" and choose "Run as administrator"
   if Windows asks (some setups require it; many do not).
2. The script creates shortcuts on the user's Desktop AND in
   Start Menu > LocallyAI.
3. Lawyer double-clicks the shortcut. If Microsoft Edge is installed
   (it ships with Windows 10/11) the app opens in a chromeless window
   that looks like a dedicated app. Otherwise it falls back to the
   default browser.

The .url files are fallback shortcuts that open in whichever browser
the user has set as default.

If the browser shows a certificate warning on first connect:
- The office Mac uses a self-signed certificate.
- The proper fix is "Trust Cert" in the cross-platform bundle —
  see the top-level STAFF-INSTALL.md.
- Or click "Advanced" → "Continue to <hostname>" in Edge / Chrome.

Manager URL:    $MANAGER_URL
Workspace URL:  $WORKSPACE_URL
EOF

(cd "$WIN_STAGE" && /usr/bin/ditto -c -k --sequesterRsrc LocallyAI "$STAFF_APPS_DIR/LocallyAI Windows Apps.zip")
echo "  ✔ $STAFF_APPS_DIR/LocallyAI Windows Apps.zip"

# ── Trust-cert helpers ──────────────────────────────────────────────────────
echo "→ Cert-trust helpers"
TRUST_STAGE="$(mktemp -d)"
trap 'rm -rf "$WIN_STAGE" "$TRUST_STAGE"' EXIT
mkdir -p "$TRUST_STAGE/Trust LocallyAI Cert"

CERT_SRC="$HERE/tls/cert.pem"
if [ -f "$CERT_SRC" ]; then
    cp "$CERT_SRC" "$TRUST_STAGE/Trust LocallyAI Cert/locallyai-deployment.pem"
else
    echo "  ⚠ $CERT_SRC missing — bundle will be without the cert"
fi

# macOS one-click trust
cat >"$TRUST_STAGE/Trust LocallyAI Cert/Trust on macOS.command" <<'EOF'
#!/usr/bin/env bash
# One-click TLS trust for the office-Mac deployment cert. Adds to the
# user's login keychain as a trusted root for SSL. Apps that use
# Apple's Security framework (WKWebView, Safari, Chrome, Edge, Firefox
# with system trust enabled) will accept the deployment cert after
# this runs.
#
# Authenticates with Touch ID / password.

set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT="$HERE/locallyai-deployment.pem"

if [ ! -f "$CERT" ]; then
    osascript -e 'display alert "Trust LocallyAI Cert" message "locallyai-deployment.pem missing from this folder."'
    exit 1
fi

security add-trusted-cert -r trustRoot -k "$HOME/Library/Keychains/login.keychain-db" "$CERT"
osascript -e 'display notification "LocallyAI deployment cert trusted on this Mac." with title "LocallyAI"'
EOF
chmod +x "$TRUST_STAGE/Trust LocallyAI Cert/Trust on macOS.command"

# Windows one-click trust
cat >"$TRUST_STAGE/Trust LocallyAI Cert/Trust on Windows.bat" <<'EOF'
@echo off
REM Adds the LocallyAI deployment cert to the Windows Trusted Root
REM Certification Authorities store for the CURRENT USER. Must run
REM "as administrator" if you want it in the machine-wide store.
REM
REM Edge, Chrome, and Firefox (with Windows trust) will all accept
REM the cert after this runs.

setlocal
set "HERE=%~dp0"
set "CERT=%HERE%locallyai-deployment.pem"

if not exist "%CERT%" (
    echo locallyai-deployment.pem not found in this folder.
    pause
    exit /b 1
)

certutil -user -addstore Root "%CERT%"
if %errorlevel% neq 0 (
    echo Failed to add cert. You may need to run as administrator.
    pause
    exit /b 1
)

echo.
echo LocallyAI deployment cert trusted for this Windows user.
echo Browsers may need a restart.
pause
EOF

cat >"$TRUST_STAGE/Trust LocallyAI Cert/README.txt" <<EOF
LocallyAI deployment certificate trust
======================================

Run ONCE per staff laptop (Mac or Windows) BEFORE first use of the
LocallyAI Manager or Workspace app. Without this, the apps will show
"cannot connect" or certificate warnings because the office Mac uses
a self-signed certificate that the laptop doesn't recognise.

macOS:    Double-click "Trust on macOS.command". Authenticate with
          Touch ID or password.
Windows:  Double-click "Trust on Windows.bat". Click Yes if
          Windows prompts to install the certificate.

This makes the office Mac's certificate "trusted as a root CA for SSL"
for the duration the cert is valid (10 years from install, or until
the vendor rotates the cert).
EOF

(cd "$TRUST_STAGE" && /usr/bin/ditto -c -k --sequesterRsrc "Trust LocallyAI Cert" "$STAFF_APPS_DIR/LocallyAI Trust Cert.zip")
echo "  ✔ $STAFF_APPS_DIR/LocallyAI Trust Cert.zip"

# ── Top-level STAFF-INSTALL.md for the IT person ─────────────────────────────
cat >"$STAFF_APPS_DIR/STAFF-INSTALL.md" <<EOF
# LocallyAI — staff-laptop install pack ($FIRM_NAME)

This folder contains everything an IT person needs to give the firm's
lawyers + admins access to LocallyAI from their personal devices.

## What's in here

| File | For | Contents |
|---|---|---|
| \`LocallyAI Trust Cert.zip\` | macOS + Windows | The deployment's TLS certificate + one-click trust scripts. **Distribute first.** |
| \`LocallyAI Manager.app.zip\` | macOS — DPO / admin | The Manager app (compliance, users, audit, models). |
| \`LocallyAI Workspace.app.zip\` | macOS — lawyer | The chat workspace. |
| \`LocallyAI Windows Apps.zip\` | Windows — both | Edge \`--app\` shortcuts for Manager + Workspace, plus a one-click shortcut installer. |

## Distribution procedure

### Step 1 — trust the deployment cert (once per laptop, mandatory)

The office Mac uses a self-signed certificate. Without trusting it on
each staff laptop, browsers will show warnings or refuse to connect.

1. Email or AirDrop **\`LocallyAI Trust Cert.zip\`** to every staff laptop.
2. The staff member unzips and double-clicks the helper for their OS:
   - macOS:   \`Trust on macOS.command\` (Touch ID / password prompt)
   - Windows: \`Trust on Windows.bat\` (run as admin if your laptops
     enforce it; otherwise user-level is fine)
3. **One-time step. Do not skip — every later step depends on this.**

### Step 2 — install the apps

**macOS users (DPO + lawyers):**

1. Email / AirDrop \`LocallyAI Manager.app.zip\` (admins) and
   \`LocallyAI Workspace.app.zip\` (lawyers) — or both if the user
   wears multiple hats.
2. Recipient unzips.
3. Drag the .app to \`/Applications\`.
4. **First launch only**: right-click → Open (Gatekeeper warns once
   on ad-hoc-signed apps; clicking "Open" past the warning bypasses
   it forever).
5. The app opens directly into the firm's deployment. No URL typing.

**Windows users:**

1. Email \`LocallyAI Windows Apps.zip\` to each user.
2. Recipient unzips to (e.g.) \`C:\\LocallyAI\\\`.
3. Double-click \`Install Shortcuts.bat\` once. This creates Desktop
   + Start-Menu shortcuts.
4. User double-clicks the Desktop shortcut. Microsoft Edge opens in
   app mode (chromeless window pointed at the firm's deployment).
5. Right-click the Desktop shortcut → "Pin to taskbar" for permanent
   access without the Desktop indirection.

### Step 3 — give each user their API key

- The Workspace app requires a per-user API key (issued from the
  Manager UI → Users page).
- The Manager app requires the admin key (provided to the firm's
  DPO / IT lead at install time).
- The user pastes their key on first sign-in; it's saved in the app's
  local data store and persists across restarts.

## URLs (for reference)

- Manager:   $MANAGER_URL
- Workspace: $WORKSPACE_URL

## What if a user is off the office network?

If the firm uses Tailscale, both apps work over Tailscale anywhere. If
not, the user is on-LAN only — the office Mac is not internet-exposed
by design (UK GDPR Art. 32 / KSA PDPL Art. 19 / ISO 27001 A.8.20).

## Re-issuing apps after a hostname change

If the firm's office-Mac hostname changes (e.g. office move, hardware
refresh), the vendor rebuilds this entire bundle by running:

\`\`\`bash
bash scripts/build_staff_apps.sh
\`\`\`

on the office Mac. New zips replace the old ones in
\`storage/installers/\` and the Manager UI's "Client Apps" page surfaces
them immediately.

## Trouble?

See \`docs/runbooks/api-down.md\` (for connection problems) and
\`docs/runbooks/dashboard-locked-out.md\` (for auth problems).
EOF

echo ""
echo "Built. Files in $STAFF_APPS_DIR:"
ls -lh "$STAFF_APPS_DIR" | awk 'NR>1 {print "  " $9 "  (" $5 ")"}'
echo ""
echo "Surface in Manager UI: /downloads"

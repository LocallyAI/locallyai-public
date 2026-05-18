#!/usr/bin/env bash
# syncthing_setup.sh
#
# Bring up Syncthing on this Mac, create the shared HA folder, and print
# the device id + folder id the operator pastes on the second Mac.
#
# Run once on each Mac. The two installs pair via the printed device ids.
#
# Idempotent — re-running on an already-configured Mac just prints the
# pairing token again.
#
# Requirements: Homebrew. The script installs Syncthing if missing.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHARED_DIR="${LOCALLYAI_SHARED_DIR:-$REPO_DIR/shared}"
SYNCTHING_HOME="$HOME/Library/Application Support/Syncthing"
FOLDER_ID="locallyai-shared"
FOLDER_LABEL="LocallyAI HA shared"

note()  { printf '\033[36m[syncthing]\033[0m %s\n' "$*"; }
fail()  { printf '\033[31m[syncthing]\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. Install if missing ---------------------------------------------------
if ! command -v syncthing >/dev/null 2>&1; then
  note "Installing Syncthing via Homebrew"
  command -v brew >/dev/null 2>&1 || fail "Homebrew required: https://brew.sh"
  brew install syncthing
fi

# --- 2. Create shared dir ----------------------------------------------------
mkdir -p "$SHARED_DIR"
chmod 700 "$SHARED_DIR"
note "Shared directory: $SHARED_DIR"

# --- 3. First-run config (generates device id) ------------------------------
if [ ! -f "$SYNCTHING_HOME/config.xml" ]; then
  note "Generating Syncthing config (one-time)"
  # --no-browser: don't open the GUI immediately. -paths just prints the
  # config dir without starting the daemon — but we want it created.
  syncthing --generate="$SYNCTHING_HOME"
fi

# --- 4. Start Syncthing as a background launchd job -------------------------
PLIST="$HOME/Library/LaunchAgents/com.locallyai.syncthing.plist"
if [ ! -f "$PLIST" ]; then
  note "Installing launchd job: com.locallyai.syncthing"
  cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key><string>com.locallyai.syncthing</string>
    <key>ProgramArguments</key>
    <array>
      <string>$(command -v syncthing)</string>
      <string>--no-browser</string>
      <string>--no-restart</string>
      <string>--logflags=0</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
      <key>STNORESTART</key><string>1</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$REPO_DIR/logs/syncthing.log</string>
    <key>StandardErrorPath</key><string>$REPO_DIR/logs/syncthing.log</string>
  </dict>
</plist>
PLIST_EOF
  launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || \
    launchctl load -w "$PLIST"
  sleep 3
fi

# --- 5. Read API key + device id ---------------------------------------------
API_KEY="$(grep -m1 '<apikey>' "$SYNCTHING_HOME/config.xml" | sed -E 's|.*<apikey>(.*)</apikey>.*|\1|')"
DEVICE_ID="$(syncthing --device-id 2>/dev/null || true)"
[ -z "$API_KEY" ]   && fail "Could not read Syncthing API key"
[ -z "$DEVICE_ID" ] && fail "Could not read Syncthing device id"

ST_URL="http://127.0.0.1:8384"

# --- 6. Add the shared folder via REST (idempotent) -------------------------
note "Configuring shared folder $FOLDER_ID at $SHARED_DIR"
curl -fsS -H "X-API-Key: $API_KEY" "$ST_URL/rest/config/folders/$FOLDER_ID" >/dev/null 2>&1 || {
  curl -fsS -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
    -X POST "$ST_URL/rest/config/folders" \
    -d "{\"id\":\"$FOLDER_ID\",\"label\":\"$FOLDER_LABEL\",\"path\":\"$SHARED_DIR\",\"type\":\"sendreceive\",\"rescanIntervalS\":10,\"fsWatcherEnabled\":true,\"fsWatcherDelayS\":1}" >/dev/null
}

# --- 7. Print the pairing info -----------------------------------------------
cat <<EOF

──────────────────────────────────────────────────────────────────────
Syncthing is up on this Mac.

  Device ID  : $DEVICE_ID
  Folder ID  : $FOLDER_ID
  Folder Path: $SHARED_DIR
  Web GUI    : $ST_URL

Next steps for the SECOND Mac:
  1. Run this same script on Mac-B.
  2. Copy Mac-B's printed Device ID, then on Mac-A's GUI ($ST_URL):
       Add Remote Device → paste Mac-B's Device ID.
  3. Copy Mac-A's Device ID (above), then on Mac-B's GUI:
       Add Remote Device → paste Mac-A's Device ID.
  4. On each side, Accept Folder when prompted, pointing at $SHARED_DIR.
  5. Wait until both folders show "Up to Date" (under 30s on a LAN).

After pairing:
  - Set LOCALLYAI_SHARED_DIR=$SHARED_DIR in /Users/Shared/.locallyai-env
    or both nodes' .env so config.SHARED_DIR resolves to the synced dir.
  - Restart com.locallyai.server on both nodes to pick up the new path.
  - Verify with:  curl -sk -H "Authorization: Bearer \$ADMIN" \\
                       https://localhost:8000/admin/fleet/audit-verify
    Expect both nodes to appear with status:"ok".

Sync conflict files (users.sync-conflict-*) are auto-quarantined into
\$SHARED_DIR/conflicts/ by the sentinel — review them via the fleet
dashboard, never auto-merge.
──────────────────────────────────────────────────────────────────────
EOF

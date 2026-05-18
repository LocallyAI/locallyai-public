#!/usr/bin/env bash
# LocallyAI — Updater
# Run from inside the production/ folder. Stops the service, upgrades
# dependencies from the local requirements.txt, and restarts.
set -euo pipefail

PLIST_LABEL="com.locallyai.server"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"

if [[ ! -d "$VENV" ]]; then
  echo "[FAIL] Virtualenv not found at $VENV — run install.sh first."
  exit 1
fi

echo "Stopping service..."
launchctl stop "$PLIST_LABEL" 2>/dev/null || true
sleep 3

echo "Updating dependencies..."
"$VENV/bin/pip" install -r "$DIR/requirements.txt" --quiet --upgrade

echo "Restarting service..."
launchctl start "$PLIST_LABEL"
echo "Update complete."

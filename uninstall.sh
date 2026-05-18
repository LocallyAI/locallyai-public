#!/usr/bin/env bash
# LocallyAI — Uninstaller
# Run from inside the production/ folder. Removes the launchd service. Then
# optionally wipes generated state (.env, .venv, storage/, logs/, tls/,
# users.json, .ingest_state.json, .audit_chain) — leaves source code in place.
set -euo pipefail

PLIST_LABEL="com.locallyai.server"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Stopping LocallyAI service..."
launchctl unload "$PLIST_PATH" 2>/dev/null || true
rm -f "$PLIST_PATH"
echo "Service removed."

if command -v docker &>/dev/null && docker ps -a --format '{{.Names}}' | grep -q '^locallyai-qdrant$'; then
  echo "Stopping Qdrant container..."
  docker rm -f locallyai-qdrant >/dev/null 2>&1 || true
  echo "Qdrant container removed."
fi

echo ""
echo "Folder: $DIR"
read -rp "Wipe generated state (.env, .venv, storage/, qdrant_storage/, logs/, tls/, users.json, .ingest_state.json, .audit_chain)? [y/N] " confirm
if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
  rm -rf "$DIR/.venv" "$DIR/storage" "$DIR/qdrant_storage" "$DIR/logs" "$DIR/tls"
  rm -f  "$DIR/.env" "$DIR/users.json" "$DIR/.ingest_state.json" "$DIR/.audit_chain"
  echo "Generated state removed. Source files retained at $DIR."
else
  echo "State retained at $DIR."
fi

echo ""
echo "To also remove Ollama and the downloaded models, run:"
echo "  brew services stop ollama && brew uninstall ollama && rm -rf ~/.ollama"

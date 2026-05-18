#!/usr/bin/env bash
# Manager UI one-click launcher (macOS / Linux).
# Mirrors apps/worker-ui/launch.sh but defaults to port 5173 and opens the
# administrator console.

set -euo pipefail
cd "$(dirname "$0")"

API_BASE_URL="${LOCALLYAI_API_BASE:-http://localhost:8000}"
PORT="${LOCALLYAI_MANAGER_UI_PORT:-5173}"

echo "==> LocallyAI Management Console launcher"
echo "    backend: ${API_BASE_URL}"

if [ ! -f .env.local ]; then
  cp -f .env.example .env.local
fi
if grep -q '^VITE_API_BASE_URL=' .env.local; then
  awk -v v="VITE_API_BASE_URL=${API_BASE_URL}" 'BEGIN{r=0} /^VITE_API_BASE_URL=/{print v; r=1; next} {print} END{if(!r) print v}' .env.local > .env.local.tmp && mv .env.local.tmp .env.local
else
  echo "VITE_API_BASE_URL=${API_BASE_URL}" >> .env.local
fi

if command -v bun >/dev/null 2>&1; then
  PM=bun
elif command -v npm >/dev/null 2>&1; then
  PM=npm
else
  echo "ERROR: bun or npm is required to build the manager UI." >&2
  echo "Install bun (curl -fsSL https://bun.sh/install | bash) or Node.js 20+." >&2
  exit 1
fi

if [ ! -d node_modules ]; then
  echo "==> installing dependencies (${PM})"
  if [ "$PM" = "bun" ]; then bun install; else npm install; fi
fi

needs_build=0
if [ ! -d dist ]; then
  needs_build=1
else
  if [ -n "$(find src -type f -newer dist 2>/dev/null | head -n 1)" ]; then
    needs_build=1
  fi
fi
if [ "$needs_build" = "1" ]; then
  echo "==> building production bundle"
  if [ "$PM" = "bun" ]; then bun run build; else npm run build; fi
fi

PYTHON_BIN="${LOCALLYAI_PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN=python
fi

echo "==> launching console on http://localhost:${PORT}"
exec "$PYTHON_BIN" "$(cd .. && pwd)/serve_ui.py" "$(pwd)/dist" --port "${PORT}"

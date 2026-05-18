#!/usr/bin/env bash
# Worker UI one-click launcher (macOS / Linux).
#
# This app is built with TanStack Start (Cloudflare Workers + SSR), not a flat
# static SPA. After `bun run build` it produces dist/client/ (browser assets)
# and dist/server/ (worker entry + wrangler.json). We serve it locally with
# `wrangler dev` against the built worker — there is no static index.html.
#
# Behaviour:
#   1. Ensures dependencies are installed (bun, then fall back to npm).
#   2. Pins VITE_API_BASE_URL in .env.local to the configured backend.
#   3. Builds dist/ if dist/server/index.js is missing or older than src/.
#   4. Starts wrangler dev on the requested port and opens the browser.

set -euo pipefail
cd "$(dirname "$0")"

# Default to https:// when install.sh generated a TLS cert; otherwise plain
# http://. Either way LOCALLYAI_API_BASE wins if the user exported it.
if [ -n "${LOCALLYAI_API_BASE:-}" ]; then
  API_BASE_URL="$LOCALLYAI_API_BASE"
elif [ -f "../../tls/cert.pem" ]; then
  API_BASE_URL="https://localhost:8000"
else
  API_BASE_URL="http://localhost:8000"
fi
PORT="${LOCALLYAI_WORKER_UI_PORT:-5174}"

echo "==> LocallyAI Workspace launcher"
echo "    backend: ${API_BASE_URL}"

# ── Ensure the LocallyAI API is running ──────────────────────────────────────
# The worker UI is useless without the backend. Probe /healthz; if it doesn't
# answer, try to start the launchd agent installed by install.sh, then fall
# back to running supervisor.py directly via the venv's python.
LOCALLYAI_ROOT="$(cd ../.. && pwd)"
# Canonical launchd label since 2026-05; install.sh + start_locallyai.sh both
# use this. The legacy label com.locallyai.server is honoured as a fallback
# for upgrade scenarios where the user hasn't re-run install.sh yet.
PLABEL="app.locallyai.api"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLABEL.plist"
LEGACY_PLIST_PATH="$HOME/Library/LaunchAgents/com.locallyai.server.plist"

probe_health() {
  curl -skf -o /dev/null --max-time 2 "${API_BASE_URL}/healthz" 2>/dev/null
}

wait_for_health() {
  local attempts="${1:-60}"
  for _ in $(seq 1 "$attempts"); do
    if probe_health; then return 0; fi
    sleep 1
  done
  return 1
}

start_locallyai_server() {
  # Prefer the canonical plist; fall back to the legacy one for older installs.
  local plist label
  if [ -f "$PLIST_PATH" ]; then
    plist="$PLIST_PATH"; label="$PLABEL"
  elif [ -f "$LEGACY_PLIST_PATH" ]; then
    plist="$LEGACY_PLIST_PATH"; label="com.locallyai.server"
  fi
  if [ -n "${plist:-}" ] && command -v launchctl >/dev/null 2>&1; then
    echo "==> starting LocallyAI server via launchctl ($label)"
    launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null || true
    launchctl kickstart -k "gui/$(id -u)/$label"   2>/dev/null || true
    return 0
  fi
  if [ -x "$LOCALLYAI_ROOT/.venv/bin/python" ] && [ -f "$LOCALLYAI_ROOT/supervisor.py" ]; then
    echo "==> starting LocallyAI supervisor in background (no launchd plist found)"
    mkdir -p "$LOCALLYAI_ROOT/logs"
    nohup "$LOCALLYAI_ROOT/.venv/bin/python" "$LOCALLYAI_ROOT/supervisor.py" \
      >>"$LOCALLYAI_ROOT/logs/launchd.log" 2>>"$LOCALLYAI_ROOT/logs/launchd_error.log" &
    disown
    return 0
  fi
  echo "ERROR: LocallyAI is not installed at $LOCALLYAI_ROOT." >&2
  echo "       Run bash $LOCALLYAI_ROOT/install.sh first." >&2
  return 1
}

if ! probe_health; then
  echo "==> backend not responding at ${API_BASE_URL} — bringing it up"
  if ! start_locallyai_server; then
    exit 1
  fi
  echo "==> waiting for ${API_BASE_URL}/healthz (up to 120s; first start loads models)"
  if ! wait_for_health 120; then
    echo "WARN: backend did not respond within 120s. The UI may show 'Could not reach'." >&2
    echo "      Tail logs:  tail -f $LOCALLYAI_ROOT/logs/launchd_error.log" >&2
  else
    echo "==> backend healthy"
  fi
fi


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
  PM_RUN=(bun run)
  PM_X=(bunx)
elif command -v npm >/dev/null 2>&1; then
  PM=npm
  PM_RUN=(npm run)
  PM_X=(npx --yes)
else
  echo "ERROR: bun or npm is required to build the worker UI." >&2
  echo "Install bun (curl -fsSL https://bun.sh/install | bash) or Node.js 20+." >&2
  exit 1
fi

if [ ! -d node_modules ]; then
  echo "==> installing dependencies (${PM})"
  if [ "$PM" = "bun" ]; then bun install; else npm install; fi
fi

needs_build=0
if [ ! -f dist/server/index.js ] || [ ! -f dist/server/wrangler.json ]; then
  needs_build=1
elif [ -n "$(find src -type f -newer dist/server/index.js 2>/dev/null | head -n 1)" ]; then
  needs_build=1
fi
if [ "$needs_build" = "1" ]; then
  echo "==> building production bundle"
  "${PM_RUN[@]}" build
fi

# Prefer the locally-installed wrangler so the version matches the lockfile.
if [ -x node_modules/.bin/wrangler ]; then
  WRANGLER=(node_modules/.bin/wrangler)
elif command -v wrangler >/dev/null 2>&1; then
  WRANGLER=(wrangler)
else
  WRANGLER=("${PM_X[@]}" wrangler)
fi

URL="http://localhost:${PORT}"
echo "==> launching workspace on ${URL}"

# Open the browser once wrangler is listening, in a background subshell so the
# wrangler exec call below stays in the foreground (Ctrl+C cleanly terminates).
(
  for _ in $(seq 1 40); do
    if curl -sf -o /dev/null --max-time 1 "${URL}" 2>/dev/null; then
      if command -v open >/dev/null 2>&1; then open "${URL}"
      elif command -v xdg-open >/dev/null 2>&1; then xdg-open "${URL}" >/dev/null 2>&1
      fi
      break
    fi
    sleep 0.5
  done
) &

exec "${WRANGLER[@]}" dev \
  --config dist/server/wrangler.json \
  --ip 127.0.0.1 \
  --port "${PORT}"

#!/usr/bin/env bash
# One-click startup for LocallyAI.
#
# Usage:
#   start_locallyai.sh                   ← legacy: launches API + both UIs
#   start_locallyai.sh worker            ← API + worker-ui, opens worker tab
#   start_locallyai.sh manager           ← API + manager-ui, opens manager tab
#   start_locallyai.sh both              ← explicit form of the legacy default
#
# Strategy: install up-to-3 user LaunchAgents (api always, worker-ui
# and/or manager-ui depending on selection) on first run, then keep
# them warm between sessions. Subsequent clicks of the .app skip the
# daemon spin-up and just open the relevant browser tab (<1s).
# launchd auto-restarts each service on crash; explicit stop is via
# scripts/stop_locallyai.sh which boots all three out.
#
# Falls back to the older nohup model if launchctl isn't available
# (Linux, future container).
#
# Logs land in logs/launcher/. PID file is no longer the source of
# truth (launchd is); the file is still written so the legacy stop
# path keeps working.
set -euo pipefail

# ── PATH augmentation (must run BEFORE command -v npm) ───────────────────────
# When launched from a macOS .app bundle (or via launchd), the inherited
# PATH is the bare-system "/usr/bin:/bin:/usr/sbin:/sbin" — homebrew /
# nvm / fnm / volta install dirs are NOT included. Without this, our
# `command -v npm` check below would fail on a clean .app launch and the
# script would silently fall back to nohup-without-npm, which also
# fails. Symptom: "the .app is not responding" with nothing in the log.
#
# We prepend the well-known dirs so the script behaves the same whether
# invoked from a terminal (already-rich PATH) or from a .app bundle.
_extra_path_parts=(
  "/opt/homebrew/bin"            # Homebrew on Apple Silicon
  "/usr/local/bin"               # Homebrew on Intel + system installs
  "$HOME/.volta/bin"             # volta
)
# nvm / fnm install per-version dirs — pick the newest if present.
for _nvm_dir in "$HOME/.nvm/versions/node"/*/bin; do
  [ -d "$_nvm_dir" ] && _extra_path_parts+=("$_nvm_dir")
done
for _fnm_dir in "$HOME/.fnm/node-versions"/*/installation/bin "$HOME/.local/share/fnm/node-versions"/*/installation/bin; do
  [ -d "$_fnm_dir" ] && _extra_path_parts+=("$_fnm_dir")
done
_joined="$(IFS=:; printf '%s' "${_extra_path_parts[*]}")"
PATH="$_joined:${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"
unset _extra_path_parts _joined _nvm_dir _fnm_dir

# ── Which UI(s) to launch ─────────────────────────────────────────────────────
SELECTION="${1:-both}"
case "$SELECTION" in
  worker|manager|both) ;;
  *)
    echo "Usage: $0 [worker|manager|both]" >&2
    exit 2
    ;;
esac
WANT_WORKER=0; WANT_MANAGER=0
case "$SELECTION" in
  worker)  WANT_WORKER=1 ;;
  manager) WANT_MANAGER=1 ;;
  both)    WANT_WORKER=1; WANT_MANAGER=1 ;;
esac

# ── Paths ─────────────────────────────────────────────────────────────────────
SELF="${BASH_SOURCE[0]}"
while [ -L "$SELF" ]; do SELF="$(readlink "$SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd)"
REPO_DIR="$SCRIPT_DIR"
while [ "$REPO_DIR" != "/" ] && [ ! -f "$REPO_DIR/api.py" ]; do
  REPO_DIR="$(dirname "$REPO_DIR")"
done
if [ ! -f "$REPO_DIR/api.py" ]; then
  echo "FATAL: could not locate repo root (api.py)" >&2
  exit 1
fi
cd "$REPO_DIR"

LAUNCHER_LOG_DIR="$REPO_DIR/logs/launcher"
mkdir -p "$LAUNCHER_LOG_DIR"
PID_FILE="$LAUNCHER_LOG_DIR/locallyai.pids"
API_LOG="$LAUNCHER_LOG_DIR/api.log"
WORKER_LOG="$LAUNCHER_LOG_DIR/worker-ui.log"
MANAGER_LOG="$LAUNCHER_LOG_DIR/manager-ui.log"
LAUNCHER_LOG="$LAUNCHER_LOG_DIR/launcher.log"

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LAUNCHER_LOG"; }
log "== LocallyAI launcher starting (selection=$SELECTION) =="

API_PORT="${LOCALLYAI_API_PORT:-8000}"
WORKER_PORT="${LOCALLYAI_WORKER_PORT:-5174}"
MANAGER_PORT="${LOCALLYAI_MANAGER_PORT:-5173}"

# Bind interface. 127.0.0.1 = loopback only (single-Mac deployment,
# default for safety). 0.0.0.0 = all interfaces (LAN-reachable for
# lawyer/manager client laptops connecting to the office server).
# Set LOCALLYAI_BIND=0.0.0.0 in .env on the office Mac when shipping
# Tauri client apps to staff devices. ALSO firewall the ports to the
# office subnet only (e.g. macOS Application Firewall / Little Snitch
# rule restricting 8000/5173/5174 to 192.168.1.0/24 — bearer-token
# auth + TLS still defend the wire, but reducing exposure to the LAN
# is the right defence-in-depth.
BIND_HOST="${LOCALLYAI_BIND:-127.0.0.1}"
log "Bind host: $BIND_HOST"

# TLS detection. install.sh writes self-signed RSA-4096 to tls/ and the
# whole production posture (CORS, .env LOCALLYAI_API_BASE, worker-ui
# .env.local) assumes the API is HTTPS. If we launched plain HTTP here
# the UIs would all fail with "cannot connect to server" — exactly
# what the operator hit on first try. Match what's on disk.
TLS_CERT="$REPO_DIR/tls/cert.pem"
TLS_KEY="$REPO_DIR/tls/key.pem"
if [ -f "$TLS_CERT" ] && [ -f "$TLS_KEY" ] && [ "${LOCALLYAI_ALLOW_HTTP:-0}" != "1" ]; then
  API_SCHEME="https"
else
  API_SCHEME="http"
fi
log "API scheme: $API_SCHEME"

# ── Pre-flight ────────────────────────────────────────────────────────────────
if [ ! -x "$REPO_DIR/.venv/bin/python" ]; then
  msg="Python venv missing — run 'bash install.sh' first."
  log "ERROR: $msg"
  command -v osascript >/dev/null 2>&1 && \
    osascript -e "display alert \"LocallyAI\" message \"$msg\" as critical" || true
  exit 1
fi
# Only check the UI dirs we're about to start. A user who only wants
# the worker shouldn't be blocked by missing manager-ui deps.
if [ "$WANT_WORKER" = "1" ] && [ ! -d "$REPO_DIR/apps/worker-ui/node_modules" ]; then
  msg="worker-ui dependencies missing — run 'cd apps/worker-ui && npm install'."
  log "ERROR: $msg"
  command -v osascript >/dev/null 2>&1 && \
    osascript -e "display alert \"LocallyAI\" message \"$msg\" as critical" || true
  exit 1
fi
if [ "$WANT_MANAGER" = "1" ] && [ ! -d "$REPO_DIR/apps/manager-ui/node_modules" ]; then
  msg="manager-ui dependencies missing — run 'cd apps/manager-ui && npm install'."
  log "ERROR: $msg"
  command -v osascript >/dev/null 2>&1 && \
    osascript -e "display alert \"LocallyAI\" message \"$msg\" as critical" || true
  exit 1
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
port_in_use() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

# True readiness check: not "is the port listening" but "does this URL
# return HTTP 200". Vite binds its port before the dev compile is
# done; uvicorn binds before the app is ready in some configurations.
# Port-listen → "cannot connect to server" in the browser. HTTP 200 is
# the only honest signal.
#
# -k accepts the self-signed TLS cert (the firm's keychain doesn't
# trust it, and we don't want to add it to the system trust store
# without operator consent — see SOP one-click-start.md for the
# browser-side accept).
http_ready() {
  local url="$1"
  curl -sk -o /dev/null -m 2 -w '%{http_code}' "$url" 2>/dev/null | grep -q '^200$'
}

# Open a browser tab the moment the URL serves HTTP 200, OR the moment
# its dependency URL serves 200 (so we don't open a UI tab while the
# API it talks to is still booting — that produces the "cannot connect
# to server" the user reported). Tick is 250ms; max is in seconds.
open_when_ready() {
  local probe_url="$1" open_url="$2" dep_url="${3:-}" max_seconds="${4:-90}"
  local t=0 max_ticks=$((max_seconds * 4))
  while [ "$t" -lt "$max_ticks" ]; do
    if http_ready "$probe_url" && { [ -z "$dep_url" ] || http_ready "$dep_url"; }; then
      if command -v open >/dev/null 2>&1; then
        open "$open_url" >/dev/null 2>&1 || true
      elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$open_url" >/dev/null 2>&1 || true
      fi
      log "Opened $open_url after $((t * 250))ms"
      return 0
    fi
    sleep 0.25
    t=$((t + 1))
  done
  log "WARN: $open_url not ready within ${max_seconds}s"
  return 1
}

# ── Choice of backend (launchd preferred on macOS) ───────────────────────────
USE_LAUNCHD=0
if command -v launchctl >/dev/null 2>&1 && [ -d "$HOME/Library/LaunchAgents" ]; then
  USE_LAUNCHD=1
fi

LAUNCHD_DIR="$HOME/Library/LaunchAgents"
LABEL_API="app.locallyai.api"
LABEL_WORKER="app.locallyai.worker-ui"
LABEL_MANAGER="app.locallyai.manager-ui"

# Legacy labels from earlier installer revisions. We boot them out
# (idempotent — `bootout` returns 0 even if not loaded) and delete
# the plist files so they don't reanimate at next login.
LEGACY_LABELS=("com.locallyai.server" "com.locallyai.api" "com.locallyai.workspace")
LEGACY_PLISTS=(
  "$HOME/Library/LaunchAgents/com.locallyai.server.plist"
  "$HOME/Library/LaunchAgents/com.locallyai.api.plist"
  "$HOME/Library/LaunchAgents/com.locallyai.workspace.plist"
)

cleanup_legacy_launchd() {
  for label in "${LEGACY_LABELS[@]}"; do
    launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
  done
  for p in "${LEGACY_PLISTS[@]}"; do
    if [ -f "$p" ]; then
      log "Removing legacy launchd plist: $p"
      rm -f "$p"
    fi
  done
}

# Resolve npm + node so the LaunchAgent doesn't depend on the user's
# interactive PATH (launchd's PATH is minimal). We capture the PATH
# component the user has now and bake it in.
NPM_BIN="$(command -v npm 2>/dev/null || true)"
NODE_BIN_DIR=""
if [ -n "$NPM_BIN" ]; then
  NODE_BIN_DIR="$(cd "$(dirname "$NPM_BIN")" && pwd)"
fi
PATH_FOR_LAUNCHD="${NODE_BIN_DIR:+$NODE_BIN_DIR:}/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

write_plist_api() {
  # Append --ssl-keyfile/--ssl-certfile when in HTTPS mode so the API
  # matches the rest of the stack.
  local tls_args=""
  if [ "$API_SCHEME" = "https" ]; then
    tls_args=" --ssl-keyfile '$TLS_KEY' --ssl-certfile '$TLS_CERT'"
  fi
  # Wrap the uvicorn invocation in a bash shell that sources .env first.
  # launchd's EnvironmentVariables block doesn't grok dotenv files — we'd
  # have to enumerate every key (LOCALLYAI_ADMIN_KEY, LOCALLYAI_AUDIT_HMAC_KEY,
  # LOCALLYAI_AUDIT_SALT, LOCALLYAI_FIRM_NAME, EMBED_MODEL, MLX_MODEL, etc.)
  # which is fragile. Sourcing .env at exec time is the standard pattern for
  # dotenv-style daemon configs (cf. systemd EnvironmentFile=).
  local launch_cmd="set -a; [ -f '$REPO_DIR/.env' ] && . '$REPO_DIR/.env'; set +a; exec '$REPO_DIR/.venv/bin/python' -m uvicorn api:app --host '$BIND_HOST' --port '$API_PORT'$tls_args"
  cat > "$LAUNCHD_DIR/$LABEL_API.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>$LABEL_API</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>$launch_cmd</string>
  </array>
  <key>WorkingDirectory</key> <string>$REPO_DIR</string>
  <key>RunAtLoad</key>        <true/>
  <key>KeepAlive</key>        <true/>
  <key>StandardOutPath</key>  <string>$API_LOG</string>
  <key>StandardErrorPath</key><string>$API_LOG</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>           <string>$PATH_FOR_LAUNCHD</string>
  </dict>
  <key>ProcessType</key>      <string>Interactive</string>
</dict>
</plist>
PLIST
}

write_plist_ui() {
  local label="$1" dir="$2" port="$3" log_path="$4"
  # Source .env from the repo root so the UI dev server picks up
  # LOCALLYAI_API_BASE and any VITE_* env vars that the build expects.
  local launch_cmd="set -a; [ -f '$REPO_DIR/.env' ] && . '$REPO_DIR/.env'; set +a; cd '$dir' && exec '$NPM_BIN' run dev -- --port '$port' --host '$BIND_HOST'"
  cat > "$LAUNCHD_DIR/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>$launch_cmd</string>
  </array>
  <key>WorkingDirectory</key> <string>$dir</string>
  <key>RunAtLoad</key>        <true/>
  <key>KeepAlive</key>        <true/>
  <key>StandardOutPath</key>  <string>$log_path</string>
  <key>StandardErrorPath</key><string>$log_path</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>           <string>$PATH_FOR_LAUNCHD</string>
    <key>NODE_ENV</key>       <string>development</string>
    <key>HOME</key>           <string>$HOME</string>
  </dict>
  <key>ProcessType</key>      <string>Interactive</string>
</dict>
</plist>
PLIST
}

# Idempotent bootstrap: re-write the plist (catches repo path moves,
# port changes via env), bootout if currently loaded, then bootstrap
# fresh. macOS bootstrap rejects a label that's already loaded; bootout
# first is the simplest reliable pattern.
agent_loaded() {
  launchctl print "gui/$(id -u)/$1" >/dev/null 2>&1
}

ensure_agent() {
  local label="$1"
  if agent_loaded "$label"; then
    # Already running — assume the plist on disk is current.
    return 0
  fi
  if [ ! -f "$LAUNCHD_DIR/$label.plist" ]; then
    log "ERROR: plist missing for $label"
    return 1
  fi
  launchctl bootstrap "gui/$(id -u)" "$LAUNCHD_DIR/$label.plist" 2>>"$LAUNCHER_LOG" || \
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  # Try once more in case bootstrap raced with a stale entry.
  if ! agent_loaded "$label"; then
    launchctl bootstrap "gui/$(id -u)" "$LAUNCHD_DIR/$label.plist" 2>>"$LAUNCHER_LOG" || true
  fi
  log "Bootstrapped $label"
}

if [ "$USE_LAUNCHD" = "1" ] && [ -n "$NPM_BIN" ]; then
  log "Using launchd backend; npm=$NPM_BIN node-bin-dir=$NODE_BIN_DIR"
  mkdir -p "$LAUNCHD_DIR"
  # Boot out any legacy labels first so they don't fight for port 8000.
  cleanup_legacy_launchd
  # API plist always — both UIs need it.
  write_plist_api
  # Ensure a fresh plist is picked up: bootout the existing label so the
  # new ProgramArguments (with .env sourcing) take effect, then bootstrap.
  launchctl bootout "gui/$(id -u)/$LABEL_API" >/dev/null 2>&1 || true
  ensure_agent "$LABEL_API"
  if [ "$WANT_WORKER" = "1" ]; then
    write_plist_ui "$LABEL_WORKER" "$REPO_DIR/apps/worker-ui" "$WORKER_PORT" "$WORKER_LOG"
    launchctl bootout "gui/$(id -u)/$LABEL_WORKER" >/dev/null 2>&1 || true
    ensure_agent "$LABEL_WORKER"
  fi
  if [ "$WANT_MANAGER" = "1" ]; then
    write_plist_ui "$LABEL_MANAGER" "$REPO_DIR/apps/manager-ui" "$MANAGER_PORT" "$MANAGER_LOG"
    launchctl bootout "gui/$(id -u)/$LABEL_MANAGER" >/dev/null 2>&1 || true
    ensure_agent "$LABEL_MANAGER"
  fi
  # Compatibility marker so stop_locallyai.sh knows we used launchd.
  printf 'launchd\n' > "$LAUNCHER_LOG_DIR/.backend"

else
  # ── Legacy nohup path (Linux, or no npm in PATH) ────────────────────────────
  log "Using nohup backend (launchctl unavailable or npm not found)"
  printf 'nohup\n' > "$LAUNCHER_LOG_DIR/.backend"
  : > "$PID_FILE"

  start_api_nohup() {
    if port_in_use "$API_PORT"; then
      log "API port already up — skipping"
      return 0
    fi
    log "Starting API on :$API_PORT"
    nohup "$REPO_DIR/.venv/bin/python" -m uvicorn api:app \
          --host "$BIND_HOST" --port "$API_PORT" \
          >> "$API_LOG" 2>&1 < /dev/null &
    printf 'api %s\n' "$!" >> "$PID_FILE"
  }
  start_ui_nohup() {
    local name="$1" dir="$2" port="$3" log_path="$4"
    if port_in_use "$port"; then
      log "$name port already up — skipping"
      return 0
    fi
    log "Starting $name on :$port"
    (
      cd "$dir"
      nohup env PORT="$port" \
            npm run dev -- --port "$port" --host "$BIND_HOST" \
            >> "$log_path" 2>&1 < /dev/null &
      echo $! > "$LAUNCHER_LOG_DIR/.$name.pid"
    )
    printf '%s %s\n' "$name" "$(cat "$LAUNCHER_LOG_DIR/.$name.pid")" >> "$PID_FILE"
    rm -f "$LAUNCHER_LOG_DIR/.$name.pid"
  }
  start_api_nohup
  [ "$WANT_WORKER"  = "1" ] && start_ui_nohup worker-ui  "$REPO_DIR/apps/worker-ui"  "$WORKER_PORT"  "$WORKER_LOG"
  [ "$WANT_MANAGER" = "1" ] && start_ui_nohup manager-ui "$REPO_DIR/apps/manager-ui" "$MANAGER_PORT" "$MANAGER_LOG"
fi

# ── Open the requested browser tab(s) ────────────────────────────────────────
# Each tab waits for BOTH its own UI port AND the API /healthz before
# firing — the user always lands on a UI that can reach the backend.
WORKER_URL="http://localhost:$WORKER_PORT/"
MANAGER_URL="http://localhost:$MANAGER_PORT/"
API_HEALTH_URL="$API_SCHEME://localhost:$API_PORT/healthz"

[ "$WANT_WORKER"  = "1" ] && open_when_ready "$WORKER_URL"  "$WORKER_URL"  "$API_HEALTH_URL" 120 &
[ "$WANT_MANAGER" = "1" ] && open_when_ready "$MANAGER_URL" "$MANAGER_URL" "$API_HEALTH_URL" 120 &
wait

log "== Browser tab(s) opened — launcher exiting =="

if command -v osascript >/dev/null 2>&1; then
  case "$SELECTION" in
    worker)  msg="Worker UI: $WORKER_URL"; title="LocallyAI Worker is running" ;;
    manager) msg="Manager UI: $MANAGER_URL"; title="LocallyAI Manager is running" ;;
    both)    msg="Worker: $WORKER_URL\nManager: $MANAGER_URL"; title="LocallyAI is running" ;;
  esac
  osascript -e "display notification \"$msg\" with title \"$title\"" >/dev/null 2>&1 || true
fi

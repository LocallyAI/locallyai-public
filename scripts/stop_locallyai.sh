#!/usr/bin/env bash
# Stop the LocallyAI processes started by start_locallyai.sh.
#
# When start used launchd: bootout each LaunchAgent and delete the
# plist so the agent doesn't auto-restart on next login. The user can
# re-launch any time via the .app — start_locallyai.sh re-installs.
#
# When start used the legacy nohup path: walk the tracked PIDs,
# SIGTERM the process trees, then SIGKILL survivors. Belt-and-
# suspenders port sweep at the end.
#
# Safe to run when nothing is running (exits 0).
# No `set -u`: empty arrays / unset PID files are normal "nothing to clean up"
# states for a stopper, not error conditions. `set -e` would also be wrong —
# we want to keep going if one cleanup step has nothing to do.
set -o pipefail

SELF="${BASH_SOURCE[0]}"
while [ -L "$SELF" ]; do SELF="$(readlink "$SELF")"; done
SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd)"
REPO_DIR="$SCRIPT_DIR"
while [ "$REPO_DIR" != "/" ] && [ ! -f "$REPO_DIR/api.py" ]; do
  REPO_DIR="$(dirname "$REPO_DIR")"
done

LAUNCHER_LOG_DIR="$REPO_DIR/logs/launcher"
PID_FILE="$LAUNCHER_LOG_DIR/locallyai.pids"
LAUNCHER_LOG="$LAUNCHER_LOG_DIR/launcher.log"
BACKEND_FILE="$LAUNCHER_LOG_DIR/.backend"

API_PORT="${LOCALLYAI_API_PORT:-8000}"
WORKER_PORT="${LOCALLYAI_WORKER_PORT:-5174}"
MANAGER_PORT="${LOCALLYAI_MANAGER_PORT:-5173}"

LAUNCHD_DIR="$HOME/Library/LaunchAgents"
LABEL_API="app.locallyai.api"
LABEL_WORKER="app.locallyai.worker-ui"
LABEL_MANAGER="app.locallyai.manager-ui"

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LAUNCHER_LOG" 2>/dev/null || true; }
log "== Stop requested =="

# ── launchd backend ───────────────────────────────────────────────────────────
bootout_one() {
  local label="$1"
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$label" 2>>"$LAUNCHER_LOG" || true
    log "bootout $label"
  fi
  # Delete the plist so the agent doesn't auto-load on next login.
  # The user re-installs via the start launcher next time they want it.
  rm -f "$LAUNCHD_DIR/$label.plist"
}

backend=""
[ -f "$BACKEND_FILE" ] && backend="$(cat "$BACKEND_FILE" 2>/dev/null || true)"

if command -v launchctl >/dev/null 2>&1; then
  # Always try the launchd path even without the marker — covers the
  # case where start used launchd but the marker was lost.
  bootout_one "$LABEL_API"
  bootout_one "$LABEL_WORKER"
  bootout_one "$LABEL_MANAGER"
  # bootout dispatches SIGTERM async; give launchd a beat to actually
  # tear the process down before the port-sweep below decides whether
  # it has work to do. Otherwise we sweep, find a still-dying pid, and
  # kill what's already going to die anyway — harmless but noisy.
  sleep 1
fi

# ── nohup tree-walk fallback (still useful as a sweep) ────────────────────────
kill_tree() {
  local sig="$1" pid="$2"
  if ! kill -0 "$pid" 2>/dev/null; then return 0; fi
  local children
  children=$(pgrep -P "$pid" 2>/dev/null || true)
  local c
  for c in $children; do kill_tree "$sig" "$c"; done
  kill -"$sig" "$pid" 2>/dev/null || true
}

if [ -f "$PID_FILE" ]; then
  entries=()
  while IFS= read -r line || [ -n "$line" ]; do
    entries+=("$line")
  done < "$PID_FILE"
  for entry in "${entries[@]}"; do
    pid=$(awk '{print $2}' <<<"$entry")
    [ -z "$pid" ] && continue
    kill_tree TERM "$pid"
  done
  sleep 1
  for entry in "${entries[@]}"; do
    pid=$(awk '{print $2}' <<<"$entry")
    [ -z "$pid" ] && continue
    kill -0 "$pid" 2>/dev/null && kill_tree KILL "$pid"
  done
  rm -f "$PID_FILE"
fi

# ── Port sweep — catch anything we don't have tracked ────────────────────────
self_pid=$$
sweep_port() {
  local sig="$1" port="$2" pid
  for pid in $(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null); do
    [ "$pid" = "$self_pid" ] && continue
    log "Port $port held by pid=$pid — kill -$sig"
    kill -"$sig" "$pid" 2>/dev/null || true
  done
}
sweep_port TERM "$API_PORT"
sweep_port TERM "$WORKER_PORT"
sweep_port TERM "$MANAGER_PORT"
sleep 1
sweep_port KILL "$API_PORT"
sweep_port KILL "$WORKER_PORT"
sweep_port KILL "$MANAGER_PORT"

rm -f "$BACKEND_FILE"
log "== Stop complete =="

if command -v osascript >/dev/null 2>&1; then
  osascript -e "display notification \"All LocallyAI processes stopped.\" with title \"LocallyAI stopped\"" >/dev/null 2>&1 || true
fi

exit 0

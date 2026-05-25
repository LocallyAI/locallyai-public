#!/usr/bin/env bash
# LocallyAI One-Click Installer — Apple Silicon Mac Studio / MacBook
# Usage: bash install.sh   (run from inside the production/ folder)
#
# Self-contained: installs in place at whatever directory production/ lives in.
# Everything LocallyAI needs (.env, tls/, storage/, logs/, users.json) lives
# under this directory after install.
#
# Phases:
#   1.  Verify Apple Silicon macOS
#   2.  Check unified memory
#   3.  Locate Python 3.10+ (install via Homebrew if absent)
#   5.  Create a venv at <DIR>/.venv
#   6.  Install Python deps from requirements.txt
#   6b. Install + start Ollama as a brew service
#   6c. Pull the LLM and embedding models
#   6d. Start Qdrant as a Docker container (falls back to embedded if no Docker)
#   7.  Generate ALL secrets and write .env (chmod 600)
#   8b. Generate self-signed TLS cert (10 years)
#   8c. Optionally trust the cert in the macOS System keychain
#   9.  Register launchd service (auto-start, KeepAlive)
#   9b. Create the first admin user via manage_users.py
#   9c. Ingest the seed doc so RAG works on first chat
#   9d. Build the worker UI (TanStack Start) so apps/worker-ui/launch.sh is instant
#   10. Probe /healthz to confirm the API is up
#   11. Optional: install Tailscale for remote access
set -euo pipefail

VERSION=1.2.0
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"
PLABEL=app.locallyai.api
PLIST="$HOME/Library/LaunchAgents/$PLABEL.plist"
# Legacy labels from earlier installer revisions. We boot them out + delete
# the plist files so they don't fight for port 8000 with the canonical
# app.locallyai.api label.
LEGACY_PLABELS=("com.locallyai.server" "com.locallyai.api" "com.locallyai.workspace")
LOGS="$DIR/logs"
TLS_DIR="$DIR/tls"
ENV_FILE="$DIR/.env"

# Diagnostics — tee everything to logs/install.log and trap errors loudly
# so a "silent crash" (e.g. a transient terminal that closes on exit) still
# leaves a trace at $DIR/logs/install.log.
mkdir -p "$LOGS"
INSTALL_LOG="$LOGS/install.log"
: > "$INSTALL_LOG"
exec > >(tee -a "$INSTALL_LOG") 2>&1

_on_error() {
  local exit_code=$?
  local line_number=$1
  printf '\n\033[31m[ FAIL ]\033[0m install.sh crashed at line %s (exit code %s)\n' \
         "$line_number" "$exit_code"
  printf '\033[31m[ FAIL ]\033[0m Last command: %s\n' "${BASH_COMMAND:-?}"
  printf '\033[31m[ FAIL ]\033[0m Full log:     %s\n' "$INSTALL_LOG"
  printf '\nPress Enter to close...'
  read -r _ </dev/tty 2>/dev/null || true
  exit "$exit_code"
}
trap '_on_error $LINENO' ERR

ok()   { printf '\033[32m[ OK   ]\033[0m %s\n' "$*"; }
info() { printf '\033[36m[ INFO ]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[ WARN ]\033[0m %s\n' "$*"; }
die()  {
  printf '\033[31m[ FAIL ]\033[0m %s\n' "$*"
  printf '\nPress Enter to close...'
  read -r _ </dev/tty 2>/dev/null || true
  exit 1
}

echo ""
echo "  LocallyAI $VERSION — On-premises AI for regulated industries"
echo "  Installing in place at: $DIR"
echo ""

# ── 1. Platform ───────────────────────────────────────────────────────────────
info "Checking platform..."
_KERNEL="$(uname 2>/dev/null || echo unknown)"
_ARCH="$(uname -m 2>/dev/null || echo unknown)"
info "Detected: $_KERNEL / $_ARCH"
if [[ "$_KERNEL" != "Darwin" ]]; then
  die "install.sh requires macOS Apple Silicon (got '$_KERNEL/$_ARCH').
  This script DEPLOYS LocallyAI on the target Mac. It does not run on Windows or Linux.
  If you are on Windows, you can still PUBLISH the source to GitHub from this folder:
      pwsh scripts/publish_to_github.ps1 -Name <repo-name>
  Then on the Mac:  git clone <url> && cd <repo> && bash install.sh"
fi
if [[ "$_ARCH" != "arm64" ]]; then
  die "install.sh requires Apple Silicon (M1/M2/M3/M4). Detected '$_ARCH'.
  Intel Macs are not supported."
fi
ok "Apple Silicon confirmed"

# ── 2. RAM ────────────────────────────────────────────────────────────────────
RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
info "Unified memory: ${RAM_GB} GB"
[[ $RAM_GB -lt 16 ]] && warn "Under 16 GB. 32 GB+ recommended; 64 GB+ for 70B models."

# ── 3. Python ─────────────────────────────────────────────────────────────────
info "Locating Python 3.10+..."
PY=""
for c in python3.12 python3.11 python3.10 python3; do
  command -v "$c" &>/dev/null || continue
  V=$($c -c 'import sys; v=sys.version_info; print(v.major*100+v.minor)')
  [[ $V -ge 310 ]] && { PY="$c"; ok "Found $c"; break; }
done
if [[ -z "$PY" ]]; then
  info "Python 3.10+ not found — installing via Homebrew..."
  command -v brew &>/dev/null || die "Homebrew not found. See https://brew.sh"
  brew install python@3.11 && PY=python3.11
fi

mkdir -p "$LOGS" "$TLS_DIR"

# ── 4b. Pre-flight: port 8000 must be free ───────────────────────────────────
# lsof exits 1 when nothing is listening (the success case for our check), so
# `|| true` keeps `set -e` from aborting the script when the port is free.
# If a previous LocallyAI run left an orphan uvicorn behind, kill it actively --
# launchctl unload only stops processes it owns, not crashed-out children.
PORT_CHECK="${PORT:-8000}"
if command -v lsof >/dev/null 2>&1; then
  STALE_PIDS=$(lsof -nP -tiTCP:"$PORT_CHECK" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$STALE_PIDS" ]]; then
    HOLDER_INFO=$(lsof -nP -iTCP:"$PORT_CHECK" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1, "(PID", $2")"}' || true)
    if echo "$HOLDER_INFO" | grep -qiE 'python|uvicorn|locallyai'; then
      info "Port $PORT_CHECK held by prior LocallyAI run ($HOLDER_INFO) -- terminating it."
      for pid in $STALE_PIDS; do
        kill "$pid" 2>/dev/null || true
      done
      sleep 2
      # Force-kill anything still hanging on
      STALE_STILL=$(lsof -nP -tiTCP:"$PORT_CHECK" -sTCP:LISTEN 2>/dev/null || true)
      if [[ -n "$STALE_STILL" ]]; then
        for pid in $STALE_STILL; do
          kill -9 "$pid" 2>/dev/null || true
        done
        sleep 1
      fi
      ok "Port $PORT_CHECK freed"
    else
      die "Port $PORT_CHECK is already held by $HOLDER_INFO. Free it (e.g. quit AirPlay Receiver in System Settings, or kill the dev server) and re-run, or set PORT=8001 before re-running."
    fi
  fi
fi

# ── 4c. Choose deployment mode (production vs demo) ─────────────────────────
# Honour DEPLOY_MODE env override for unattended installs / CI.
if [[ -n "${DEPLOY_MODE:-}" ]]; then
  case "$DEPLOY_MODE" in
    production|demo) info "DEPLOY_MODE=$DEPLOY_MODE set in env — skipping picker" ;;
    *) die "DEPLOY_MODE must be 'production' or 'demo' (got '$DEPLOY_MODE')" ;;
  esac
else
  echo ""
  echo "  Choose deployment mode:"
  echo "    1. Production — empty knowledge base; you ingest your own documents."
  echo "    2. Demo       — copy 5 sample legal documents into data/ and ingest them."
  echo ""
  read -rp "  Mode [1=production / 2=demo, default 1]: " MODE_PICK
  MODE_PICK="${MODE_PICK:-1}"
  case "$MODE_PICK" in
    1|production) DEPLOY_MODE="production" ;;
    2|demo)       DEPLOY_MODE="demo" ;;
    *) warn "Unrecognised pick '$MODE_PICK' — defaulting to production"; DEPLOY_MODE="production" ;;
  esac
fi
ok "Deployment mode: $DEPLOY_MODE"

# ── 4c-bis. Choose data residency / compliance region ──────────────────────
# Mandatory pick — no default. Either LOCALLYAI_DATA_REGION env is set, or
# the operator must explicitly choose. Stamps every audit + billing entry,
# drives the embed model default, picks the demo doc set, and selects
# the right DPA / breach-notification framework.
if [[ -n "${LOCALLYAI_DATA_REGION:-}" ]]; then
  case "$LOCALLYAI_DATA_REGION" in
    UK|KSA) info "LOCALLYAI_DATA_REGION=$LOCALLYAI_DATA_REGION set in env — skipping picker" ;;
    *) die "LOCALLYAI_DATA_REGION must be 'UK' or 'KSA' (got '$LOCALLYAI_DATA_REGION')" ;;
  esac
else
  while true; do
    echo ""
    echo "  Choose data residency / compliance region:"
    echo "    1. UK  — UK GDPR / DPA 2018 posture; English-language deployment"
    echo "    2. KSA — Saudi PDPL (Royal Decree M/19, 2023); Arabic + English deployment"
    echo ""
    read -rp "  Region [1=UK / 2=KSA]: " REGION_PICK
    case "${REGION_PICK:-}" in
      1|UK|uk)   LOCALLYAI_DATA_REGION="UK"; break ;;
      2|KSA|ksa) LOCALLYAI_DATA_REGION="KSA"; break ;;
      *) warn "Region is mandatory — please pick 1 or 2" ;;
    esac
  done
fi
ok "Data region: $LOCALLYAI_DATA_REGION"
export LOCALLYAI_DATA_REGION

# ── 4c-tris. Choose fleet topology (single Mac vs multi-laptop fleet) ─────
# Drives:
#   * LOCALLYAI_BIND in .env (127.0.0.1 = loopback only; 0.0.0.0 = LAN)
#   * CORS allowlist additions for the office host's mDNS name
#   * The "where IT downloads client apps" message at the end of install
#
# A "single Mac deployment" is correct when the same Mac runs the
# server AND the lawyer is in front of that Mac using a browser. A
# "multi-laptop fleet" is the typical firm setup: one office Mac
# Studio + many laptops on the LAN running the standalone Tauri
# client apps (see docs/sop/client-install.md).
#
# Honour FLEET_TOPOLOGY env override for unattended installs.
if [[ -n "${FLEET_TOPOLOGY:-}" ]]; then
  case "$FLEET_TOPOLOGY" in
    single|fleet) info "FLEET_TOPOLOGY=$FLEET_TOPOLOGY set in env — skipping picker" ;;
    *) die "FLEET_TOPOLOGY must be 'single' or 'fleet' (got '$FLEET_TOPOLOGY')" ;;
  esac
else
  echo ""
  echo "  Choose fleet topology:"
  echo "    1. single — one Mac, only browsers on this same Mac use it (loopback only, safest)"
  echo "    2. fleet  — this Mac is the office server; staff laptops on the LAN connect via the client apps"
  echo ""
  read -rp "  Topology [1=single / 2=fleet, default 1]: " FLEET_PICK
  case "${FLEET_PICK:-1}" in
    1|single) FLEET_TOPOLOGY="single" ;;
    2|fleet)  FLEET_TOPOLOGY="fleet"  ;;
    *) warn "Unrecognised pick '$FLEET_PICK' — defaulting to single"; FLEET_TOPOLOGY="single" ;;
  esac
fi
ok "Fleet topology: $FLEET_TOPOLOGY"

if [[ "$FLEET_TOPOLOGY" == "fleet" ]]; then
  LOCALLYAI_BIND="0.0.0.0"
  # Best-effort detect the office host's mDNS name + LAN IP. We use
  # both so client apps can connect via either. mDNS is friendlier
  # but doesn't always resolve from Windows laptops without Bonjour.
  OFFICE_HOSTNAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo office-mac)"
  OFFICE_HOSTNAME_LOWER="$(echo "$OFFICE_HOSTNAME" | tr '[:upper:]' '[:lower:]')"
  OFFICE_LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")"
  OFFICE_HOST="${OFFICE_HOSTNAME_LOWER}.local"
  # Build the CORS allowlist so both the dev-server browser tabs
  # (localhost:5173/5174) AND the Tauri webview origins work.
  CORS_VAL="http://localhost,http://127.0.0.1"
  CORS_VAL="$CORS_VAL,http://localhost:5174,http://127.0.0.1:5174"
  CORS_VAL="$CORS_VAL,http://localhost:5173,http://127.0.0.1:5173"
  CORS_VAL="$CORS_VAL,tauri://localhost,https://tauri.localhost"
  # Bind to LAN means lawyer laptops will reach this host directly —
  # add the office host's own URL to CORS so requests originating
  # from the LAN aren't rejected at the boundary.
  CORS_VAL="$CORS_VAL,https://$OFFICE_HOST:8000,http://$OFFICE_HOST:8000"
  if [[ -n "$OFFICE_LAN_IP" ]]; then
    CORS_VAL="$CORS_VAL,https://$OFFICE_LAN_IP:8000,http://$OFFICE_LAN_IP:8000"
  fi
else
  LOCALLYAI_BIND="127.0.0.1"
  OFFICE_HOST="localhost"
  OFFICE_LAN_IP=""
  CORS_VAL="http://localhost,http://127.0.0.1,http://localhost:5174,http://127.0.0.1:5174,http://localhost:5173,http://127.0.0.1:5173"
fi
ok "Bind: $LOCALLYAI_BIND  Office host (for client apps): $OFFICE_HOST${OFFICE_LAN_IP:+ ($OFFICE_LAN_IP)}"

# ── 4c-quat. Firm display name (shown in worker + manager UI badges) ─────────
# Surfaces in worker UI's header + manager UI's TopBar so end users see
# explicit confirmation of which firm's deployment they're connecting
# to (defends against accidental cross-firm connection — see
# docs/sop/data-isolation.md). Honour LOCALLYAI_FIRM_NAME env override
# for unattended installs.
if [[ -n "${LOCALLYAI_FIRM_NAME:-}" ]]; then
  info "LOCALLYAI_FIRM_NAME=\"$LOCALLYAI_FIRM_NAME\" set in env — skipping prompt"
else
  # Default suggestion: a friendly form of the office hostname.
  DEFAULT_FIRM="$(echo "$OFFICE_HOST" | sed 's/\.local$//; s/-/ /g' | awk '{for(i=1;i<=NF;i++)$i=toupper(substr($i,1,1)) substr($i,2)} 1')"
  echo ""
  echo "  What's the firm's display name? Shown in the worker + manager UI"
  echo "  headers as \"Firm: <name>\" so end users see which deployment they're"
  echo "  connecting to."
  echo ""
  read -rp "  Firm name [default: $DEFAULT_FIRM]: " FIRM_PICK
  LOCALLYAI_FIRM_NAME="${FIRM_PICK:-$DEFAULT_FIRM}"
fi
ok "Firm name: $LOCALLYAI_FIRM_NAME"

# ── 4d. Choose inference backend (MLX vs Ollama vs LM Studio) ───────────────
# MLX       = recommended. In-process Apple Metal inference via mlx-lm.
#             No daemon, no extra port, lowest latency on Apple Silicon.
# Ollama    = good for Mac Studio with mixed clients (headless OpenAI-compat daemon).
# LM Studio = GUI for model browsing; useful when others hit Metal quirks.
if [[ -n "${INFERENCE_BACKEND:-}" ]]; then
  case "$INFERENCE_BACKEND" in
    mlx|ollama|lmstudio) BACKEND_CHOICE="$INFERENCE_BACKEND"
      info "INFERENCE_BACKEND=$INFERENCE_BACKEND set in env -- skipping picker" ;;
    *) die "INFERENCE_BACKEND must be 'mlx', 'ollama', or 'lmstudio' (got '$INFERENCE_BACKEND')" ;;
  esac
else
  echo ""
  echo "  Choose inference backend:"
  echo "    1. MLX          (recommended — in-process Apple Metal, no daemon)"
  echo "    2. Ollama       (headless OpenAI-compatible daemon)"
  echo "    3. LM Studio    (GUI for model management)"
  echo ""
  read -rp "  Backend [1=mlx / 2=ollama / 3=lmstudio, default 1]: " B_PICK
  B_PICK="${B_PICK:-1}"
  case "$B_PICK" in
    1|mlx)                BACKEND_CHOICE="mlx"      ;;
    2|ollama)             BACKEND_CHOICE="ollama"   ;;
    3|lmstudio|lm-studio) BACKEND_CHOICE="lmstudio" ;;
    *) warn "Unknown pick '$B_PICK' -- defaulting to mlx"; BACKEND_CHOICE="mlx" ;;
  esac
fi
ok "Inference backend: $BACKEND_CHOICE"

# ── 5. Virtualenv ─────────────────────────────────────────────────────────────
# If the folder was moved/renamed (e.g. into ~/.Trash and back), the venv's
# python shebang points at a path that no longer exists. Detect that and
# rebuild from scratch — `python -m venv` doesn't repair broken interpreters.
info "Creating virtual environment at $VENV..."
if [[ -e "$VENV/bin/python" ]] && ! "$VENV/bin/python" -V >/dev/null 2>&1; then
  warn "Existing $VENV is broken (interpreter missing) — rebuilding"
  rm -rf "$VENV"
fi
$PY -m venv "$VENV"
VP="$VENV/bin/python"
PIP="$VENV/bin/pip"
ok "Virtualenv ready"

# ── 6. Python dependencies ────────────────────────────────────────────────────
info "Installing Python dependencies from requirements.txt..."
"$PIP" install --upgrade pip --quiet
"$PIP" install -r "$DIR/requirements.txt" --quiet
ok "Python dependencies installed"

# ── 6b. Install + start the chosen backend ───────────────────────────────────
if [[ "$BACKEND_CHOICE" == "mlx" ]]; then
  info "Installing mlx-lm (Apple Silicon Metal inference, in-process)..."
  "$PIP" install "mlx-lm>=0.10.0" --quiet || die "mlx-lm install failed (requires Apple Silicon + macOS 13+)"
  ok "mlx-lm installed"
  # No daemon to start: api.py loads the model in-process on first request.
  # No probe URL — the FastAPI /healthz check at the end of install covers it.
  LLM_HOST_URL=""
elif [[ "$BACKEND_CHOICE" == "ollama" ]]; then
  info "Installing Ollama (LLM + embedding host)..."
  if command -v ollama &>/dev/null; then
    ok "Ollama already installed"
  else
    command -v brew &>/dev/null || die "Homebrew required to install Ollama. See https://brew.sh"
    brew install ollama --quiet
    ok "Ollama installed"
  fi
  info "Starting Ollama as a brew service..."
  brew services start ollama >/dev/null 2>&1 || true
  for i in $(seq 1 20); do
    curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && { ok "Ollama daemon up"; break; }
    sleep 1
    [[ $i -eq 20 ]] && warn "Ollama did not respond on :11434 within 20s -- pull may fail"
  done
  LLM_HOST_URL="http://localhost:11434"
else
  # LM Studio path
  info "Installing LM Studio..."
  if [[ -d "/Applications/LM Studio.app" ]]; then
    ok "LM Studio already installed"
  else
    command -v brew &>/dev/null || die "Homebrew required to install LM Studio. See https://brew.sh"
    brew install --cask lm-studio --quiet
    ok "LM Studio installed"
  fi

  info "Launching LM Studio so first-run setup can complete..."
  open -a "LM Studio" 2>/dev/null || true
  sleep 8

  # Bootstrap the lms CLI if not already on PATH
  if ! command -v lms &>/dev/null; then
    if [[ -x "$HOME/.lmstudio/bin/lms" ]]; then
      info "Bootstrapping the lms CLI..."
      "$HOME/.lmstudio/bin/lms" bootstrap 2>/dev/null || true
      export PATH="$HOME/.lmstudio/bin:$PATH"
    fi
  fi

  if ! command -v lms &>/dev/null; then
    warn "lms CLI not found. Open LM Studio, complete the first-run wizard,"
    warn "then start the local server in the Developer panel and re-run install.sh."
    warn "Continuing with placeholder model names; you may need to load them manually."
  else
    info "Starting LM Studio local server..."
    lms server start >/dev/null 2>&1 || warn "lms server start failed; toggle Status: Running in the Developer panel manually"
    for i in $(seq 1 20); do
      curl -sf http://localhost:1234/v1/models >/dev/null 2>&1 && { ok "LM Studio server up"; break; }
      sleep 1
      [[ $i -eq 20 ]] && warn "LM Studio did not respond on :1234 within 20s -- model load may fail"
    done
  fi
  LLM_HOST_URL="http://localhost:1234"
fi

# ── 6c. Pick & pull models ───────────────────────────────────────────────────
# Curated list of strong open-source LLMs (Jan 2026). Embedding model is fixed
# (changing it would require rebuilding the Qdrant collection -- vector size 768
# is hardcoded in config.py). Users can add more later with `ollama pull` or `lms get`.
choose_models_ollama() {
  # Curated for the LocallyAI plugin + tool-calling story. Each tag in
  # square brackets describes tool-call reliability — plugins (clearance,
  # DPIA, discovery-plan etc) need the model to emit clean OpenAI function
  # calls. Models verified by tests/tool_calling_smoke.py against a 2-tool
  # dummy schema get [tools: verified]; older/unfamiliar models get
  # [tools: unverified] and the worker-ui plugin picker is disabled while
  # they're active (capability flag exposed by /v1/models). [tools: fails]
  # models are kept for plain-chat use only with "(plugins unsupported)"
  # appended so the operator can see the trade-off upfront.
  local options=(
    "qwen2.5:7b|4.7GB|[tools: verified] Best general 7B model. Fits 16GB+ Macs. (Default)"
    "qwen2.5:14b|9GB|[tools: verified] Stronger reasoning + tool chains. Needs 24GB+ RAM."
    "qwen2.5:32b|20GB|[tools: verified] Production-grade. Needs 48GB+ RAM."
    "qwen2.5:72b|41GB|[tools: verified] Mac Studio territory. Needs 128GB+ RAM."
    "qwen2.5-coder:7b|4.7GB|[tools: verified] Code-focused variant of qwen2.5."
    "llama3.3:70b|40GB|[tools: unverified] Meta flagship. 128GB+ RAM. Verify with tool_calling_smoke before enabling plugins."
    "llama3.1:8b|4.7GB|[tools: unverified] Meta Llama 3.1 8B."
    "mixtral:8x7b|26GB|[tools: unverified] Mixture-of-experts. 48GB+ RAM."
    "deepseek-r1:7b|4.7GB|[tools: fails] Reasoning model — interleaves <think> tokens in function-call JSON. (plugins unsupported)"
    "deepseek-r1:14b|9GB|[tools: fails] Same caveat as r1:7b. (plugins unsupported)"
    "mistral:7b|4.4GB|[tools: fails] Classic instruction-tuned — schema drift on tool round-trip. (plugins unsupported)"
    "gemma2:9b|5.4GB|[tools: fails] Strong chat model but malformed tool output. (plugins unsupported)"
    "gemma2:27b|16GB|[tools: fails] Same caveat. (plugins unsupported)"
    "phi4:14b|9GB|[tools: fails] Idiosyncratic tool schema, doesn't round-trip ours. (plugins unsupported)"
  )
  echo ""
  echo "  Available open-source LLMs:"
  echo ""
  printf "  %-3s  %-22s  %-7s  %s\n" "#" "Model" "Size" "Description"
  printf "  %-3s  %-22s  %-7s  %s\n" "---" "----------------------" "-------" "------------------------------------------------"
  local i=1
  for opt in "${options[@]}"; do
    local name size desc
    name=$(echo "$opt"  | cut -d'|' -f1)
    size=$(echo "$opt"  | cut -d'|' -f2)
    desc=$(echo "$opt"  | cut -d'|' -f3)
    printf "  %-3s  %-22s  %-7s  %s\n" "$i" "$name" "$size" "$desc"
    i=$((i+1))
  done
  echo ""
  echo "  Pick one or more (comma-separated, e.g. '1,3,5'). Press Enter for default (1)."
  read -rp "  Selection: " picks
  picks="${picks:-1}"

  SELECTED_MODELS=()
  IFS=',' read -ra PICK_ARRAY <<< "$picks"
  for p in "${PICK_ARRAY[@]}"; do
    p=$(echo "$p" | tr -d ' ')
    if [[ "$p" =~ ^[0-9]+$ ]] && [[ "$p" -ge 1 ]] && [[ "$p" -le ${#options[@]} ]]; then
      SELECTED_MODELS+=("$(echo "${options[$((p-1))]}" | cut -d'|' -f1)")
    else
      warn "Ignoring invalid pick: '$p'"
    fi
  done
  if [[ ${#SELECTED_MODELS[@]} -eq 0 ]]; then
    warn "No valid picks — defaulting to qwen2.5:7b"
    SELECTED_MODELS=("qwen2.5:7b")
  fi
}

# MLX model picker (mlx-community 4-bit quantizations on HuggingFace).
choose_models_mlx() {
  # Curated for the LocallyAI plugin + tool-calling story.
  #
  # Default WAS Llama-3.2-1B-Instruct-4bit (0.7GB). Demoted because 1B
  # parameter models hallucinate tool names + emit malformed JSON
  # arguments — internal eval shows >30% failure on a 4-tool schema.
  # 7B is the floor for reliable tool calling. New default is
  # Qwen2.5-7B-Instruct-4bit (4.3GB), which fits 16GB Macs and passes
  # tests/tool_calling_smoke.py.
  local options=(
    "mlx-community/Qwen2.5-7B-Instruct-4bit|4.3GB|[tools: verified] Strong general 7B. 16GB+ RAM. (Default)"
    "mlx-community/Qwen2.5-14B-Instruct-4bit|8.5GB|[tools: verified] Stronger reasoning + tool chains. 24GB+ RAM."
    "mlx-community/Qwen2.5-32B-Instruct-4bit|18GB|[tools: verified] Production-grade. 48GB+ RAM."
    "mlx-community/Llama-3.2-3B-Instruct-4bit|1.8GB|[tools: low] Pipeline smoke-test only — tool calling unreliable. Switch to Qwen 7B before enabling plugins."
    "mlx-community/Llama-3.3-70B-Instruct-4bit|40GB|[tools: unverified] Meta flagship. 128GB+ RAM. Verify with tool_calling_smoke before enabling plugins."
    "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit|4.5GB|[tools: unverified] Meta Llama 3.1 8B. 16GB+ RAM."
    "mlx-community/Mixtral-8x7B-Instruct-v0.1-4bit|24GB|[tools: unverified] Mixture-of-experts. 48GB+ RAM."
    "mlx-community/Llama-3.2-1B-Instruct-4bit|0.7GB|[tools: fails] 1B is below the tool-call floor — hallucinates tool names. (plugins unsupported)"
    "mlx-community/Mistral-7B-Instruct-v0.3-4bit|4.1GB|[tools: fails] Schema drift on tool round-trip. (plugins unsupported)"
    "mlx-community/gemma-2-9b-it-4bit|5.0GB|[tools: fails] Malformed tool output. (plugins unsupported)"
    "mlx-community/Phi-3.5-mini-instruct-4bit|2.2GB|[tools: fails] Idiosyncratic tool schema. (plugins unsupported)"
    "mlx-community/DeepSeek-R1-Distill-Qwen-7B-4bit|4.3GB|[tools: fails] Interleaves <think> in JSON arguments. (plugins unsupported)"
  )
  echo ""
  echo "  MLX LLMs (mlx-community on HuggingFace, 4-bit quantized):"
  echo ""
  printf "  %-3s  %-50s  %-7s  %s\n" "#" "Model" "Size" "Description"
  printf "  %-3s  %-50s  %-7s  %s\n" "---" "--------------------------------------------------" "-------" "------------------------------------------------"
  local i=1
  for opt in "${options[@]}"; do
    local name size desc
    name=$(echo "$opt"  | cut -d'|' -f1)
    size=$(echo "$opt"  | cut -d'|' -f2)
    desc=$(echo "$opt"  | cut -d'|' -f3)
    printf "  %-3s  %-50s  %-7s  %s\n" "$i" "$name" "$size" "$desc"
    i=$((i+1))
  done
  echo ""
  echo "  Pick one or more (comma-separated, e.g. '1,4'). Press Enter for default (1)."
  read -rp "  Selection: " picks
  picks="${picks:-1}"
  SELECTED_MODELS=()
  IFS=',' read -ra PICK_ARRAY <<< "$picks"
  for p in "${PICK_ARRAY[@]}"; do
    p=$(echo "$p" | tr -d ' ')
    if [[ "$p" =~ ^[0-9]+$ ]] && [[ "$p" -ge 1 ]] && [[ "$p" -le ${#options[@]} ]]; then
      SELECTED_MODELS+=("$(echo "${options[$((p-1))]}" | cut -d'|' -f1)")
    else
      warn "Ignoring invalid pick: '$p'"
    fi
  done
  if [[ ${#SELECTED_MODELS[@]} -eq 0 ]]; then
    warn "No valid picks -- defaulting to mlx-community/Qwen2.5-7B-Instruct-4bit"
    SELECTED_MODELS=("mlx-community/Qwen2.5-7B-Instruct-4bit")
  fi
}

# LM Studio model picker (different identifiers from Ollama).
choose_models_lmstudio() {
  local options=(
    "llama-3.2-1b-instruct|1.0GB|Tiny + fast. Proves the pipeline on any MacBook. (Default)"
    "llama-3.2-3b-instruct|2.0GB|Larger but still snappy on a MacBook."
    "qwen2.5-7b-instruct|4.5GB|Strong general 7B. Needs 16GB+ RAM."
    "mistral-7b-instruct-v0.3|4.4GB|Mistral classic, instruction-tuned."
    "gemma-2-2b-it|1.6GB|Google DeepMind small."
    "phi-3.5-mini-instruct|2.3GB|Microsoft Phi-3.5 mini."
  )
  echo ""
  echo "  LM Studio LLMs (HuggingFace identifiers):"
  echo ""
  printf "  %-3s  %-30s  %-7s  %s\n" "#" "Identifier" "Size" "Description"
  printf "  %-3s  %-30s  %-7s  %s\n" "---" "------------------------------" "-------" "------------------------------------------------"
  local i=1
  for opt in "${options[@]}"; do
    local name size desc
    name=$(echo "$opt"  | cut -d'|' -f1)
    size=$(echo "$opt"  | cut -d'|' -f2)
    desc=$(echo "$opt"  | cut -d'|' -f3)
    printf "  %-3s  %-30s  %-7s  %s\n" "$i" "$name" "$size" "$desc"
    i=$((i+1))
  done
  echo ""
  echo "  Pick one or more (comma-separated, e.g. '1,3'). Press Enter for default (1)."
  read -rp "  Selection: " picks
  picks="${picks:-1}"
  SELECTED_MODELS=()
  IFS=',' read -ra PICK_ARRAY <<< "$picks"
  for p in "${PICK_ARRAY[@]}"; do
    p=$(echo "$p" | tr -d ' ')
    if [[ "$p" =~ ^[0-9]+$ ]] && [[ "$p" -ge 1 ]] && [[ "$p" -le ${#options[@]} ]]; then
      SELECTED_MODELS+=("$(echo "${options[$((p-1))]}" | cut -d'|' -f1)")
    else
      warn "Ignoring invalid pick: '$p'"
    fi
  done
  if [[ ${#SELECTED_MODELS[@]} -eq 0 ]]; then
    warn "No valid picks -- defaulting to llama-3.2-1b-instruct"
    SELECTED_MODELS=("llama-3.2-1b-instruct")
  fi
}

# Honour {MLX_MODEL,OLLAMA_MODEL} env overrides (skip picker for unattended installs).
if [[ "$BACKEND_CHOICE" == "mlx" && -n "${MLX_MODEL:-}" ]]; then
  info "MLX_MODEL=$MLX_MODEL set in env -- skipping picker"
  SELECTED_MODELS=("$MLX_MODEL")
elif [[ "$BACKEND_CHOICE" != "mlx" && -n "${OLLAMA_MODEL:-}" ]]; then
  info "OLLAMA_MODEL=$OLLAMA_MODEL set in env -- skipping picker"
  SELECTED_MODELS=("$OLLAMA_MODEL")
else
  case "$BACKEND_CHOICE" in
    mlx)      choose_models_mlx      ;;
    ollama)   choose_models_ollama   ;;
    lmstudio) choose_models_lmstudio ;;
  esac
fi

LLM_PULL="${SELECTED_MODELS[0]}"
if [[ "$BACKEND_CHOICE" == "mlx" ]]; then
  # MLX uses sentence-transformers (already in requirements.txt) for embeddings,
  # loading the model in-process. Pre-warm by importing it so the first ingest
  # doesn't pay the download cost mid-run.
  #
  # KSA fleets need a multilingual-capable embedder so Arabic queries hit
  # English chunks (and vice versa) via cross-lingual retrieval. KSA is
  # forced — the operator cannot pick the wrong embedder for KSA even
  # via EMBED_MODEL env override; the install would silently produce
  # zero-source Arabic queries.
  if [[ "$LOCALLYAI_DATA_REGION" == "KSA" ]]; then
    if [[ -n "${EMBED_MODEL:-}" && "$EMBED_MODEL" != "intfloat/multilingual-e5-base" ]]; then
      warn "EMBED_MODEL was set to '$EMBED_MODEL' but KSA region requires intfloat/multilingual-e5-base for Arabic retrieval. Overriding."
    fi
    EMBED_PULL="intfloat/multilingual-e5-base"
    EMBED_SIZE_HINT="~440 MB"
  else
    EMBED_PULL="${EMBED_MODEL:-nomic-ai/nomic-embed-text-v1.5}"
    EMBED_SIZE_HINT="~280 MB"
  fi
  info "Pre-fetching embedding model: $EMBED_PULL ($EMBED_SIZE_HINT)"
  "$VP" -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('$EMBED_PULL', trust_remote_code=True)" \
    >/dev/null 2>&1 || warn "Embedding pre-fetch failed -- will download on first ingest"
  PIN_FILE="$DIR/.model_lock"
  # ISO 27001 A.8.30 supply-chain integrity: pin each downloaded MLX model
  # to its resolved HuggingFace commit. The runtime warns on drift.
  if [[ ! -f "$PIN_FILE" ]]; then
    {
      echo "# LocallyAI model integrity pins. Generated by install.sh."
      echo "# Each section pins one MLX model to its HuggingFace commit SHA."
      echo "# To rotate a pin: delete the section, re-run install.sh."
      echo ""
    } > "$PIN_FILE"
    chmod 640 "$PIN_FILE"
  fi
  for model in "${SELECTED_MODELS[@]}"; do
    info "Pre-fetching MLX model: $model"
    "$VP" -c "from mlx_lm import load; load('$model')" \
      >/dev/null 2>&1 || warn "Pre-fetch failed for $model -- will download on first chat"
    # Resolve the just-downloaded commit and append to the pin file (skip if
    # this model is already pinned, even on a re-run).
    if ! grep -q "^\[$model\]$" "$PIN_FILE" 2>/dev/null; then
      COMMIT=$("$VP" -c "
import os, sys
mid = sys.argv[1]
root = os.path.expanduser(os.environ.get('HF_HOME', '~/.cache/huggingface'))
ref = os.path.join(root, 'hub', 'models--' + mid.replace('/', '--'), 'refs', 'main')
print(open(ref).read().strip()) if os.path.isfile(ref) else None
" "$model" 2>/dev/null || true)
      if [[ -n "$COMMIT" ]]; then
        {
          echo "[$model]"
          echo "commit = \"$COMMIT\""
          echo "pinned_at = \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
          echo ""
        } >> "$PIN_FILE"
        ok "Pinned $model → ${COMMIT:0:12}…"
      else
        warn "Could not resolve commit for $model — pin not written. Re-run after next chat."
      fi
    fi
  done
elif [[ "$BACKEND_CHOICE" == "ollama" ]]; then
  if [[ "$LOCALLYAI_DATA_REGION" == "KSA" && -z "${EMBED_MODEL:-}" ]]; then
    warn "KSA region selected with Ollama backend, but Ollama doesn't ship a multilingual embedder."
    warn "Arabic queries against English documents will likely return 0 sources."
    warn "Recommendation: re-run install.sh and pick MLX backend instead, OR set EMBED_MODEL"
    warn "explicitly to a multilingual model you've made available to Ollama."
  fi
  EMBED_PULL="${EMBED_MODEL:-nomic-embed-text:latest}"
  info "Pulling embedding model: $EMBED_PULL (~250 MB)"
  ollama pull "$EMBED_PULL" || warn "Embedding pull failed -- RAG will be empty until $EMBED_PULL is available"
  for model in "${SELECTED_MODELS[@]}"; do
    info "Pulling LLM: $model"
    ollama pull "$model" || warn "Pull failed for $model -- chat will 502 until it's available"
  done
else
  if [[ "$LOCALLYAI_DATA_REGION" == "KSA" && -z "${EMBED_MODEL:-}" ]]; then
    warn "KSA region selected with LM Studio backend. Default embedder is English-only;"
    warn "for Arabic retrieval, set EMBED_MODEL=intfloat/multilingual-e5-base in .env after install"
    warn "and download it from LM Studio's Discover panel."
  fi
  EMBED_PULL="${EMBED_MODEL:-nomic-embed-text-v1.5}"
  if command -v lms &>/dev/null; then
    info "Pulling embedding model via lms: $EMBED_PULL (~140 MB)"
    lms get "$EMBED_PULL" --yes 2>/dev/null || warn "lms get $EMBED_PULL failed -- download manually in LM Studio's Discover panel"
    lms load "$EMBED_PULL" 2>/dev/null || warn "Could not auto-load embedding model -- load it in LM Studio's Developer panel"
    for model in "${SELECTED_MODELS[@]}"; do
      info "Pulling LLM via lms: $model"
      lms get "$model" --yes 2>/dev/null || warn "lms get $model failed -- download manually in LM Studio's Discover panel"
      lms load "$model" 2>/dev/null || warn "Could not auto-load $model -- load it in LM Studio's Developer panel"
    done
  else
    warn "lms CLI not available -- open LM Studio, complete first-run setup,"
    warn "then in the Discover panel download:  $EMBED_PULL  and  $LLM_PULL"
    warn "then in the Developer panel toggle 'Status: Running' and load both models."
  fi
fi

ok "Models ready (default = $LLM_PULL; ${#SELECTED_MODELS[@]} LLM(s) installed)"
info "All locally-installed models will be visible at GET /v1/models after install."

# ── 6d. Qdrant vector DB (Docker container) ─────────────────────────────────
# Qdrant runs as a separate server so api.py and ingest.py can hit the same
# vector store concurrently. With embedded Qdrant, only one process can hold
# storage/.lock at a time — re-ingesting while the api is up fails.
# When Docker is unavailable we fall back to embedded mode (QDRANT_URL empty);
# config.make_qdrant_client() honours both.
QDRANT_URL_VAL=""
if command -v docker &>/dev/null && docker info >/dev/null 2>&1; then
  info "Docker detected — setting up Qdrant server..."
  QDRANT_DATA_DIR="$DIR/qdrant_storage"
  mkdir -p "$QDRANT_DATA_DIR"
  if docker ps --format '{{.Names}}' | grep -q '^locallyai-qdrant$'; then
    ok "Qdrant container already running (locallyai-qdrant)"
  else
    docker rm -f locallyai-qdrant >/dev/null 2>&1 || true
    info "Pulling qdrant/qdrant image..."
    docker pull qdrant/qdrant:latest >/dev/null 2>&1 \
      || warn "docker pull failed — container start may retry"
    docker run -d \
      --name locallyai-qdrant \
      --restart unless-stopped \
      -p 6333:6333 -p 6334:6334 \
      -v "$QDRANT_DATA_DIR":/qdrant/storage \
      qdrant/qdrant:latest >/dev/null
    ok "Qdrant container started (data persisted to $QDRANT_DATA_DIR)"
  fi
  for i in $(seq 1 20); do
    curl -sf http://localhost:6333/healthz >/dev/null 2>&1 && { ok "Qdrant healthy at :6333"; break; }
    sleep 1
    [[ $i -eq 20 ]] && warn "Qdrant did not respond on :6333 within 20s — first ingest may fail"
  done
  QDRANT_URL_VAL="http://localhost:6333"
else
  warn "Docker not found (or daemon not running)."
  warn "Falling back to embedded Qdrant: single-process only — ingest while the"
  warn "api is up will fail with a portalocker AlreadyLocked error."
  warn "Install Docker Desktop and re-run install.sh to enable concurrent access:"
  warn "  https://www.docker.com/products/docker-desktop/"
fi

# ── 7. .env — generate ALL secrets ───────────────────────────────────────────
info "Generating .env with all secrets..."

# Helper: append KEY=VALUE to .env iff KEY isn't already present with a
# NON-EMPTY value. Red-team finding 7.4: the previous version preserved
# even empty lines, so `LOCALLYAI_ADMIN_KEY=` (empty) would block secret
# generation on the next run, and the API would crash at startup with
# RuntimeError: LOCALLYAI_ADMIN_KEY environment variable is not set.
# The regex `^KEY=.` requires at least one character after the equals.
ensure_env_var() {
  local key="$1" value="$2"
  if ! grep -qE "^${key}=." "$ENV_FILE" 2>/dev/null; then
    # Remove any stale empty line for this key before appending the real one.
    if [ -f "$ENV_FILE" ]; then
      grep -v "^${key}=$" "$ENV_FILE" > "${ENV_FILE}.tmp" 2>/dev/null && mv "${ENV_FILE}.tmp" "$ENV_FILE"
    fi
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

if [[ -f "$ENV_FILE" ]]; then
  # Don't blow away firm-level intake values the bootstrap may have written
  # (LOCALLYAI_FIRM_NAME, LOCALLYAI_DATA_REGION, LOCALLYAI_TELEMETRY,
  # LOCALLYAI_UPDATE_CHANNEL, LOCALLYAI_TELEMETRY_TOKEN). But DO generate any
  # missing API-critical secrets — without these the API crashes at import time
  # with RuntimeError: LOCALLYAI_ADMIN_KEY environment variable is not set.
  ADMIN_KEY=$("$VP" -c 'import secrets; print(secrets.token_hex(32))')
  AUDIT_SALT=$("$VP" -c 'import secrets; print(secrets.token_hex(32))')
  AUDIT_HMAC=$("$VP" -c 'import secrets; print(secrets.token_hex(32))')
  ensure_env_var LOCALLYAI_ADMIN_KEY      "$ADMIN_KEY"
  ensure_env_var LOCALLYAI_AUDIT_SALT     "$AUDIT_SALT"
  ensure_env_var LOCALLYAI_AUDIT_HMAC_KEY "$AUDIT_HMAC"
  ensure_env_var LOCALLYAI_BACKEND        "$BACKEND_CHOICE"
  if [[ "$BACKEND_CHOICE" == "mlx" ]]; then
    ensure_env_var MLX_MODEL              "$LLM_PULL"
    ensure_env_var EMBED_BACKEND          "local"
    ensure_env_var EMBED_MODEL            "$EMBED_PULL"
  else
    ensure_env_var LLM_BASE_URL           "$LLM_HOST_URL"
    ensure_env_var OLLAMA_BASE_URL        "http://localhost:11434"
    ensure_env_var OLLAMA_MODEL           "$LLM_PULL"
    ensure_env_var EMBED_MODEL            "$EMBED_PULL"
  fi
  ensure_env_var PORT                     "8000"
  if command -v openssl &>/dev/null; then SCHEME="https"; else SCHEME="http"; fi
  ensure_env_var LOCALLYAI_API_BASE       "$SCHEME://$OFFICE_HOST:8000"
  ensure_env_var LOCALLYAI_BIND           "$LOCALLYAI_BIND"
  ensure_env_var LOCALLYAI_OFFICE_HOST    "$OFFICE_HOST"
  ensure_env_var LOCALLYAI_CORS_ORIGINS   "$CORS_VAL"
  ensure_env_var LOCALLYAI_DEPLOYMENT_ID  "locallyai-prod"
  ensure_env_var QDRANT_URL               "$QDRANT_URL_VAL"
  ensure_env_var LOCALLYAI_KILL_SWITCH_URL ""
  ensure_env_var LOCALLYAI_KILL_SWITCH_REQUIRED "1"
  ensure_env_var LOCALLYAI_AUTO_UPDATE    "on"
  ensure_env_var LOCALLYAI_AUTO_UPDATE_TIERS "A"
  # Don't overwrite firm-supplied values for these — only set if missing.
  ensure_env_var LOCALLYAI_FIRM_NAME      "$LOCALLYAI_FIRM_NAME"
  ensure_env_var LOCALLYAI_DATA_REGION    "$LOCALLYAI_DATA_REGION"
  ensure_env_var LOCALLYAI_UPDATE_CHANNEL "stable"
  chmod 600 "$ENV_FILE"
  ok "Topped up missing secrets in $ENV_FILE (firm-level vars preserved; chmod 600)"
elif false; then  # unreachable — keeps the original else-branch indentation
  :
else
  ADMIN_KEY=$("$VP" -c 'import secrets; print(secrets.token_hex(32))')
  AUDIT_SALT=$("$VP" -c 'import secrets; print(secrets.token_hex(32))')
  AUDIT_HMAC=$("$VP" -c 'import secrets; print(secrets.token_hex(32))')
  if [[ "$BACKEND_CHOICE" == "mlx" ]]; then
    BACKEND_BLOCK=$(cat <<MLXEOF
# Backend selection. MLX runs in-process (no daemon) via mlx-lm.
LOCALLYAI_BACKEND=mlx
MLX_MODEL=$LLM_PULL
EMBED_BACKEND=local
EMBED_MODEL=$EMBED_PULL
MLXEOF
)
  else
    BACKEND_BLOCK=$(cat <<HTTPEOF
# Backend selection. LLM_BASE_URL points at whichever OpenAI-compatible
# server (Ollama, LM Studio, vLLM, etc.) is running locally.
LOCALLYAI_BACKEND=$BACKEND_CHOICE
LLM_BASE_URL=$LLM_HOST_URL
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=$LLM_PULL
EMBED_MODEL=$EMBED_PULL
HTTPEOF
)
  fi

  # API_BASE scheme matches whether TLS will be generated below.
  if command -v openssl &>/dev/null; then API_BASE_SCHEME="https"; else API_BASE_SCHEME="http"; fi

  cat > "$ENV_FILE" <<ENVEOF
# Generated by install.sh — keep these secret. chmod 600.
LOCALLYAI_ADMIN_KEY=$ADMIN_KEY
LOCALLYAI_AUDIT_SALT=$AUDIT_SALT
LOCALLYAI_AUDIT_HMAC_KEY=$AUDIT_HMAC

$BACKEND_BLOCK

# Server
PORT=8000
LOCALLYAI_API_BASE=$API_BASE_SCHEME://$OFFICE_HOST:8000
# Bind interface — 127.0.0.1 = loopback only (single-Mac topology);
# 0.0.0.0 = LAN-reachable for client devices (fleet topology). Set by
# the install picker; do NOT edit by hand without also expanding CORS
# below to match.
LOCALLYAI_BIND=$LOCALLYAI_BIND
# Office host — the URL staff laptops put into their LocallyAI client
# app's first-launch prompt. Helpful in the audit log and printed back
# at the end of install for IT to record.
LOCALLYAI_OFFICE_HOST=$OFFICE_HOST
# Firm display name — surfaces in the worker + manager UI header
# badges so end users see explicit confirmation of which firm's
# deployment they're connecting to. See docs/sop/data-isolation.md.
LOCALLYAI_FIRM_NAME=$LOCALLYAI_FIRM_NAME
LOCALLYAI_CORS_ORIGINS=$CORS_VAL
LOCALLYAI_DEPLOYMENT_ID=locallyai-prod
# Data residency / compliance region (set by install picker; do not edit
# by hand — drives audit log stamping, RoPA framing, embed model default,
# breach-notification clause, demo doc set).
LOCALLYAI_DATA_REGION=$LOCALLYAI_DATA_REGION

# Qdrant: when set, all clients connect to this server instead of opening
# storage/ as an embedded store. Empty = embedded mode (single-process).
QDRANT_URL=$QDRANT_URL_VAL

# Out-of-band kill switch — TOTP-gated Cloudflare Worker URL polled
# before any system update is applied. The Worker's KV holds the
# status JSON; operator flips via scripts/kill_switch_emergency.sh
# with a 6-digit TOTP code from their authenticator app. See
# docs/kill-switch/cloudflare-worker/README.md for one-time deploy.
#
# Set this to YOUR Worker URL after deployment, e.g.:
#   LOCALLYAI_KILL_SWITCH_URL=https://locallyai-killswitch.<your-cf-account>.workers.dev/
# REQUIRED=1 = fail-closed if the URL is unreachable. Set to 0 to
# fail-open (NOT recommended; bad releases have no emergency stop).
LOCALLYAI_KILL_SWITCH_URL=
LOCALLYAI_KILL_SWITCH_REQUIRED=1

# Update channel for system_updates.py. "stable" = firms (default).
# "dev" = vendor's dev box; sees -dev tags before they're promoted.
LOCALLYAI_UPDATE_CHANNEL=stable
LOCALLYAI_AUTO_UPDATE=on
LOCALLYAI_AUTO_UPDATE_TIERS=A
ENVEOF
  chmod 600 "$ENV_FILE"
  ok "Secrets written to $ENV_FILE (chmod 600)"
fi

# ── 8b. TLS cert ──────────────────────────────────────────────────────────────
info "Generating self-signed TLS certificate..."
if [[ -f "$TLS_DIR/cert.pem" && -f "$TLS_DIR/key.pem" ]]; then
  ok "TLS cert already exists at $TLS_DIR"
elif command -v openssl &>/dev/null; then
  # ISO 3166-1 alpha-2 country code in the cert subject. Matches the
  # data residency region; auditors expect a Saudi deployment to NOT
  # carry /C=GB.
  case "$LOCALLYAI_DATA_REGION" in
    KSA) TLS_COUNTRY="SA" ;;
    UK|*) TLS_COUNTRY="GB" ;;
  esac
  # Red-team finding 4.2: add SAN extensions. Modern browsers + curl
  # require subjectAltName for hostname validation; CN-only certs are
  # deprecated since 2017. Without SAN the operator has to -k past
  # every connection.
  openssl req -x509 -newkey rsa:4096 \
    -keyout "$TLS_DIR/key.pem" \
    -out    "$TLS_DIR/cert.pem" \
    -days   3650 -nodes \
    -subj   "/CN=locallyai/O=LocallyAI/C=$TLS_COUNTRY" \
    -addext "subjectAltName=DNS:localhost,DNS:$OFFICE_HOST,DNS:office-mac.local,IP:127.0.0.1" \
    -addext "extendedKeyUsage=serverAuth" \
    2>/dev/null
  chmod 600 "$TLS_DIR/key.pem"
  chmod 644 "$TLS_DIR/cert.pem"
  ok "TLS certificate written to $TLS_DIR"
  info "Add $TLS_DIR/cert.pem to client trust stores for HTTPS"
else
  warn "openssl not found — TLS will be skipped; setting LOCALLYAI_ALLOW_HTTP=1"
  grep -q '^LOCALLYAI_ALLOW_HTTP=' "$ENV_FILE" || echo 'LOCALLYAI_ALLOW_HTTP=1' >> "$ENV_FILE"
fi

# ── 8c. Trust the cert in the macOS System keychain ─────────────────────────
# Without this, every browser on this Mac shows a "self-signed certificate"
# warning the first time it hits https://localhost:8000, and the worker UI's
# fetch() calls fail until the user clicks through. Adding the cert as a
# trusted root makes Safari, Chrome, and Edge accept it silently.
# Honour LOCALLYAI_TRUST_CERT={yes,no} for unattended installs / CI.
if [[ -f "$TLS_DIR/cert.pem" ]] && command -v security &>/dev/null; then
  CERT_FP=$(openssl x509 -in "$TLS_DIR/cert.pem" -noout -fingerprint -sha1 2>/dev/null | sed 's/^.*=//; s/://g')
  ALREADY_TRUSTED=0
  if [[ -n "$CERT_FP" ]] && security find-certificate -a -Z /Library/Keychains/System.keychain 2>/dev/null | grep -qi "SHA-1 hash: $CERT_FP"; then
    ALREADY_TRUSTED=1
  fi
  if [[ "$ALREADY_TRUSTED" -eq 1 ]]; then
    ok "TLS cert already trusted in System keychain"
  else
    case "${LOCALLYAI_TRUST_CERT:-}" in
      yes|y|true|1) TRUST_PICK="y" ;;
      no|n|false|0) TRUST_PICK="n" ;;
      *)
        echo ""
        echo "  Trust the LocallyAI TLS cert in your macOS System keychain?"
        echo "  This removes the browser warning at https://localhost:8000 so"
        echo "  the worker UI loads without a per-browser click-through."
        echo "  (Local-machine only. Reverse with: sudo security delete-certificate -c locallyai /Library/Keychains/System.keychain)"
        read -rp "  Trust cert now? [Y/n] " TRUST_PICK
        ;;
    esac
    TRUST_PICK="${TRUST_PICK:-y}"
    if [[ "$TRUST_PICK" =~ ^[Yy] ]]; then
      info "Adding $TLS_DIR/cert.pem to System keychain (sudo prompt expected)..."
      if sudo security add-trusted-cert -d -r trustRoot \
           -k /Library/Keychains/System.keychain "$TLS_DIR/cert.pem" 2>/dev/null; then
        ok "TLS cert trusted — browsers will accept https://localhost:8000 silently"
      else
        warn "Could not add cert to System keychain. The browser warning will persist;"
        warn "users must click through https://localhost:8000/healthz once per browser."
      fi
    else
      info "Skipped cert trust. Browsers will warn on first visit to https://localhost:8000."
    fi
  fi
fi

# ── 9. launchd plist ──────────────────────────────────────────────────────────
info "Registering launchd service..."
mkdir -p "$HOME/Library/LaunchAgents"

# Boot out + delete any legacy plists that would compete for port 8000.
# Idempotent — bootout returns 0 even if the label isn't loaded.
for legacy in "${LEGACY_PLABELS[@]}"; do
  launchctl bootout "gui/$(id -u)/$legacy" >/dev/null 2>&1 || true
  rm -f "$HOME/Library/LaunchAgents/$legacy.plist"
done

# supervisor.py runs uvicorn under a self-healing wrapper:
#   - load_dotenv at import (so LOCALLYAI_ADMIN_KEY / FIRM_NAME / etc. are set)
#   - singleton via PID file
#   - pre-flight cleanup of orphan uvicorn processes
#   - graceful shutdown on SIGTERM
#   - exponential backoff on rapid crashes (defends against launchd flap)
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$PLABEL</string>
  <key>ProgramArguments</key><array>
    <string>$VP</string>
    <string>$DIR/supervisor.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
  <key>StandardOutPath</key><string>$LOGS/launchd.log</string>
  <key>StandardErrorPath</key><string>$LOGS/launchd_error.log</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
</dict></plist>
PLISTEOF
# Use bootout/bootstrap (modern API) over unload/load (deprecated).
# bootstrap can return non-zero in several "soft failure" cases (service
# already loaded, recently disabled, etc.) — we don't want set -e to kill
# the install over a launchctl quirk. Belt-and-braces with kickstart -k
# which (re)starts whichever instance is registered with the current plist.
launchctl bootout "gui/$(id -u)/$PLABEL" >/dev/null 2>&1 || true
# Give launchd a moment to fully release the label before re-bootstrapping.
sleep 1
if ! launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then
  warn "bootstrap returned non-zero (often just 'already loaded') — proceeding with kickstart"
fi
# Idempotent restart — picks up the just-written plist content regardless
# of whether bootstrap above thought it was a no-op.
launchctl kickstart -k "gui/$(id -u)/$PLABEL" 2>/dev/null || \
  warn "kickstart failed for $PLABEL — check $LOGS/launchd_error.log"
ok "Service registered as $PLABEL (auto-starts on login, KeepAlive)"

# ── 9b. First admin user ─────────────────────────────────────────────────────
info "Creating first admin user..."
USERS_FILE="$DIR/users.json"
NEED_USER=1
if [[ -f "$USERS_FILE" ]] && [[ -s "$USERS_FILE" ]]; then
  COUNT=$("$VP" -c "import json; print(len(json.load(open('$USERS_FILE'))))" 2>/dev/null || echo 0)
  [[ "$COUNT" != "0" ]] && NEED_USER=0
fi

if [[ $NEED_USER -eq 0 ]]; then
  ok "users.json already populated — skipping first-user creation"
  USER_KEY="(existing — see $USERS_FILE)"
else
  pushd "$DIR" >/dev/null
  USER_KEY=$("$VP" manage_users.py add Admin | awk '/^API key:/ {print $3}')
  popd >/dev/null
  if [[ -z "$USER_KEY" ]]; then
    warn "Could not capture admin user key — run: cd $DIR && $VP manage_users.py list"
    USER_KEY="(see $USERS_FILE)"
  else
    ok "First user 'Admin' created"
  fi
fi

# ── 9c. Seed ingest — proves RAG works ───────────────────────────────────────
if [[ "$DEPLOY_MODE" == "demo" ]]; then
  # KSA fleets get the Saudi-flavoured demo set (DIFC NDA, PDPL policy, M&A
  # confidentiality letter, restructuring memo, bilingual welcome). UK fleets
  # keep the existing English UK-law set.
  if [[ "$LOCALLYAI_DATA_REGION" == "KSA" ]]; then
    DEMO_SRC="$DIR/demo/data_sa"
    DEMO_LABEL="Saudi (PDPL / DIFC) corpus"
  else
    DEMO_SRC="$DIR/demo/data"
    DEMO_LABEL="UK legal corpus"
  fi
  info "Demo mode — copying $DEMO_LABEL from $DEMO_SRC/*.md into $DIR/data/..."
  mkdir -p "$DIR/data"
  if compgen -G "$DEMO_SRC/*.md" >/dev/null; then
    cp -n "$DEMO_SRC/"*.md "$DIR/data/" 2>/dev/null || true
    ok "Demo corpus copied ($(ls "$DEMO_SRC/"*.md 2>/dev/null | wc -l | tr -d ' ') files)"
  else
    warn "$DEMO_SRC/ is empty — running install in production mode instead"
  fi
fi

info "Running first ingest..."
pushd "$DIR" >/dev/null
"$VP" ingest.py 2>&1 | tail -n 8 || warn "Initial ingest failed — run manually: cd $DIR && $VP ingest.py"
popd >/dev/null

# Compute PROBE_URL once: §9d wants it for the worker UI's VITE_API_BASE_URL,
# and §10 reuses it for the health check.
PROBE_URL="http://localhost:8000/healthz"
[[ -f "$TLS_DIR/cert.pem" ]] && PROBE_URL="https://localhost:8000/healthz"

# ── 9c-bis. Default plugin pack ──────────────────────────────────────────────
# Clones LocallyAI's adapted UK legal plugin set (ip-legal / privacy-legal /
# litigation-legal — Apache 2.0, forked from anthropics/claude-for-legal) into
# $DIR/plugins/. The startup handler in api/__init__.py picks them up
# automatically from BASE_DIR/plugins. Skipped if the directory already exists
# (idempotent — re-running install.sh won't clobber a hand-curated plugin set
# or pull updates without consent; firms run `git -C plugins pull` to update).
PLUGINS_DIR="$DIR/plugins"
PLUGINS_REPO="${LOCALLYAI_PLUGINS_REPO:-https://github.com/LocallyAI/locallyai-plugins-uk-public.git}"
if [[ -d "$PLUGINS_DIR" ]]; then
  info "Plugin pack already present at $PLUGINS_DIR — skipping clone"
  if [[ -d "$PLUGINS_DIR/.git" ]]; then
    ok "  ($(cd "$PLUGINS_DIR" && git log -1 --format='%h %s' 2>/dev/null || echo 'non-git directory'))"
  fi
else
  info "Cloning default plugin pack from $PLUGINS_REPO ..."
  if git clone --depth 1 "$PLUGINS_REPO" "$PLUGINS_DIR" 2>&1 | tail -n 3; then
    local_n=$(find "$PLUGINS_DIR" -maxdepth 2 -name "plugin.json" 2>/dev/null | wc -l | tr -d ' ')
    ok "Plugin pack installed: $local_n plugin(s) under $PLUGINS_DIR"
    ok "  → manage via Manager UI → Plugins tab, or POST /v1/admin/plugins/{name}/{enable,disable}"
  else
    warn "Plugin pack clone failed (no internet or repo not yet public?)"
    warn "  → install plugins later: git clone $PLUGINS_REPO $PLUGINS_DIR"
    warn "  → the rest of install continues without plugins; chat works generically"
  fi
fi

# ── 9d. Build the worker UI ──────────────────────────────────────────────────
# This is a TanStack Start app (Cloudflare Workers + SSR). After build it
# produces dist/client/ and dist/server/wrangler.json; launch.sh serves it via
# `wrangler dev`. Skip silently if neither bun nor npm is present — the user
# can still use the API directly, and launch.sh will fail with a clear hint.
WORKER_UI_DIR="$DIR/apps/worker-ui"
if [[ -d "$WORKER_UI_DIR" ]]; then
  WORKER_PM=""
  if command -v bun >/dev/null 2>&1; then
    WORKER_PM=bun
  elif command -v npm >/dev/null 2>&1; then
    WORKER_PM=npm
  fi
  if [[ -n "$WORKER_PM" ]]; then
    info "Building Workspace UI ($WORKER_PM) — first build downloads ~150 MB of deps..."
    pushd "$WORKER_UI_DIR" >/dev/null
    [[ -f .env.local ]] || cp -f .env.example .env.local
    if grep -q '^VITE_API_BASE_URL=' .env.local; then
      awk -v v="VITE_API_BASE_URL=${PROBE_URL%/healthz}" 'BEGIN{r=0} /^VITE_API_BASE_URL=/{print v; r=1; next} {print} END{if(!r) print v}' .env.local > .env.local.tmp && mv .env.local.tmp .env.local
    else
      echo "VITE_API_BASE_URL=${PROBE_URL%/healthz}" >> .env.local
    fi
    if [[ ! -d node_modules ]]; then
      if [[ "$WORKER_PM" == "bun" ]]; then bun install || warn "bun install failed — run launch.sh later to retry"
      else npm install || warn "npm install failed — run launch.sh later to retry"
      fi
    fi
    if [[ -d node_modules ]]; then
      if [[ "$WORKER_PM" == "bun" ]]; then bun run build || warn "Workspace build failed — fix and re-run apps/worker-ui/launch.sh"
      else npm run build || warn "Workspace build failed — fix and re-run apps/worker-ui/launch.sh"
      fi
      [[ -f dist/server/index.js ]] && ok "Workspace built (apps/worker-ui/dist/)"
    fi
    popd >/dev/null
  else
    warn "Workspace not built — install bun (https://bun.sh) or Node.js 20+ then run apps/worker-ui/launch.sh"
  fi
fi

# ── 10. Health check ─────────────────────────────────────────────────────────
info "Waiting for /healthz (up to 30 s)..."
HEALTH_OK=0
for i in $(seq 1 30); do
  if curl -sfk "$PROBE_URL" >/dev/null 2>&1; then
    ok "API healthy at $PROBE_URL"
    HEALTH_OK=1
    break
  fi
  sleep 1
done
[[ $HEALTH_OK -eq 0 ]] && warn "API did not respond on /healthz — see $LOGS/launchd_error.log"

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "  =================================================================="
echo "  LocallyAI installed"
echo "  --------------------------------------------------------------"
echo "  Folder:    $DIR"
echo "  API:       ${PROBE_URL%/healthz}"
echo "  Health:    $PROBE_URL"
echo "  Logs:      $LOGS"
echo "  Config:    $ENV_FILE"
echo "  Admin key: $USER_KEY"
echo "  --------------------------------------------------------------"
echo "  Start:     launchctl start $PLABEL"
echo "  Stop:      launchctl stop  $PLABEL"
echo "  Add user:  cd $DIR && $VP manage_users.py add <Name>"
echo "  Ingest:    cd $DIR && $VP ingest.py"
echo "  Chat:      cd $DIR && $VP chat.py --key <user-key>"
if [[ "$DEPLOY_MODE" == "demo" ]]; then
  echo "  Demo:      cd $DIR && $VP demo/run_demo.py --key <user-key>"
fi
echo "  Workspace: open '/Applications/LocallyAI Workspace.app'   (or Launchpad → 'LocallyAI')"
echo "  Manager:   open '/Applications/LocallyAI Manager.app'"
echo "  Audit:     bash $DIR/scripts/audit_install.sh"
echo "  Mode:      $DEPLOY_MODE"
if [[ -n "$QDRANT_URL_VAL" ]]; then
  echo "  Qdrant:    docker container 'locallyai-qdrant' on :6333"
  echo "             stop:  docker stop locallyai-qdrant"
  echo "             start: docker start locallyai-qdrant"
else
  echo "  Qdrant:    embedded (no Docker) — single-process only"
fi
echo "  =================================================================="
echo ""
echo "  Save the admin key — it will not be shown again."
echo ""

# ── 10a. Build per-firm staff-laptop apps ──────────────────────────────────
# Manager + Workspace .app bundles (macOS) with the firm's hostname
# baked in, plus a Windows shortcut bundle and cert-trust helpers.
# Output lands in storage/installers/ which the /admin/installers/
# endpoint + Manager UI /downloads page already surface.
if [[ -x "$DIR/scripts/build_staff_apps.sh" ]] && command -v swiftc >/dev/null 2>&1; then
  echo "  Building staff-laptop apps (Manager + Workspace + Windows shortcuts + cert trust)..."
  if (cd "$DIR" && bash scripts/build_staff_apps.sh >/dev/null 2>&1); then
    echo "  ✓ Staff apps built. IT person grabs them at:"
    echo "    ${PROBE_URL%/healthz}/downloads  (Manager UI → Client Apps)"
    echo "    or directly from $DIR/storage/installers/"
  else
    echo "  ⚠ Staff app build failed (non-fatal — install.sh continues)."
    echo "    Re-run later with: bash $DIR/scripts/build_staff_apps.sh"
  fi
  echo ""
fi

# ── 10a-bis. Local-Mac apps in /Applications/ ────────────────────────────────
# Same Swift WKWebView wrappers as the staff-laptop apps, but baked with
# localhost URLs so the operator can double-click them from Launchpad
# instead of opening a browser tab at :5173 / :5174. Replaces the
# `bash apps/{worker,manager}-ui/launch.sh` workflow with a one-click
# app icon. The dev servers themselves are still managed by launchd
# (auto-restart, auto-start at login) — these apps just give the user
# a native window to view them through.
if command -v swiftc >/dev/null 2>&1; then
  echo "  Building local-Mac apps (Workspace + Manager) with localhost URLs..."
  local_built=0
  if [[ -x "$DIR/apps/worker-desktop/build.sh" ]]; then
    if (cd "$DIR/apps/worker-desktop" && WORKSPACE_URL="http://localhost:5174" ./build.sh >/dev/null 2>&1); then
      rm -rf "/Applications/LocallyAI Workspace.app"
      cp -R "$DIR/apps/worker-desktop/dist/LocallyAI Workspace.app" "/Applications/" 2>/dev/null
      xattr -dr com.apple.quarantine "/Applications/LocallyAI Workspace.app" 2>/dev/null || true
      local_built=$((local_built + 1))
    fi
  fi
  if [[ -x "$DIR/apps/manager-desktop/build.sh" ]]; then
    if (cd "$DIR/apps/manager-desktop" && MANAGER_URL="http://localhost:5173" ./build.sh >/dev/null 2>&1); then
      rm -rf "/Applications/LocallyAI Manager.app"
      cp -R "$DIR/apps/manager-desktop/dist/LocallyAI Manager.app" "/Applications/" 2>/dev/null
      xattr -dr com.apple.quarantine "/Applications/LocallyAI Manager.app" 2>/dev/null || true
      local_built=$((local_built + 1))
    fi
  fi
  if [[ "$local_built" -ge 1 ]]; then
    echo "  ✓ ${local_built} local app(s) installed to /Applications/"
    echo "    → Launchpad: search 'LocallyAI'"
    echo "    → or: open '/Applications/LocallyAI Workspace.app'"
    echo "    → or: open '/Applications/LocallyAI Manager.app'"
  else
    echo "  ⚠ Local-app build failed (non-fatal). Re-run later:"
    echo "    bash $DIR/apps/worker-desktop/build.sh && cp -R $DIR/apps/worker-desktop/dist/'LocallyAI Workspace.app' /Applications/"
    echo "    bash $DIR/apps/manager-desktop/build.sh && cp -R $DIR/apps/manager-desktop/dist/'LocallyAI Manager.app' /Applications/"
  fi
  echo ""
fi

# ── 10b. Fleet topology — IT-deployable client app instructions ────────────
# In fleet mode, the operator (IT) needs to know:
#   1. Which URL staff laptops should put into their client apps
#   2. Where to download the client installers
#   3. Whether the firewall + LAN binding are correctly set
if [[ "$FLEET_TOPOLOGY" == "fleet" ]]; then
  echo ""
  echo "  ====================== FLEET DEPLOYMENT NEXT STEPS ======================"
  echo ""
  echo "  Server is bound to 0.0.0.0 — staff laptops on the LAN can reach it."
  echo ""
  echo "  Office server URL for staff laptops to use in their client app:"
  echo "      https://$OFFICE_HOST:8000"
  if [[ -n "$OFFICE_LAN_IP" ]]; then
    echo "      https://$OFFICE_LAN_IP:8000     (LAN IP — fallback if mDNS fails)"
  fi
  echo ""
  echo "  Download the LocallyAI Workspace + Manager client apps:"
  echo "      https://github.com/LocallyAI/locallyai/releases"
  echo ""
  echo "  IT install + bulk-deploy guide (Jamf, Munki, Intune, Group Policy):"
  echo "      docs/sop/client-install.md"
  echo ""
  echo "  Recommended NEXT STEPS for the IT team:"
  echo "    1. Verify the firm's office subnet can reach this Mac on ports"
  echo "       8000 (API), 5173 (manager-ui), 5174 (worker-ui)."
  echo "    2. Add a firewall rule restricting those ports to the office subnet"
  echo "       (macOS Application Firewall + Little Snitch, or pfSense / router"
  echo "       ACL). Bearer-token auth + TLS still defend the wire, but reducing"
  echo "       LAN exposure is ISO 27001 A.8.20 defence-in-depth."
  echo "    3. Pre-stage the server URL on each staff laptop so end users skip"
  echo "       the first-launch prompt — script per platform in client-install.md."
  echo "    4. Push the .dmg / .msi to staff devices via your MDM (Jamf, Munki,"
  echo "       Intune) or share the GitHub releases URL with each user."
  echo ""
  echo "  ── DATA ISOLATION ──────────────────────────────────────────────────"
  echo "  Every firm's deployment is fully isolated by architecture — no"
  echo "  shared storage, no shared API. Egress allowlist enforced at the"
  echo "  network layer is recommended:"
  echo "    - Audit what's actually connecting:"
  echo "        bash scripts/audit_egress.sh"
  echo "    - Block-by-default with LuLu (free, Objective-See):"
  echo "        brew install --cask lulu  +  import docs/egress-allowlist/lulu-rules.json"
  echo "    - Full picture: docs/sop/data-isolation.md + docs/egress-allowlist/README.md"
  echo "  ========================================================================="
  echo ""

  # ── 10e. Optional vendor telemetry opt-in ─────────────────────────────────
  echo "  Vendor health telemetry — opt-in, anonymised."
  echo ""
  echo "  When ON, this Mac posts a 5-minute heartbeat to LocallyAI's"
  echo "  monitoring dashboard so the vendor on-call can spot incidents"
  echo "  within the 4-hour SLA. The full list of what's sent (and what"
  echo "  ISN'T) is in docs/sop/data-isolation.md — short version: only"
  echo "  health gauges + structured error codes; NEVER document content,"
  echo "  user names, or query text."
  echo ""
  echo "  Default OFF. Enable only after reviewing the disclosure with"
  echo "  the firm's DPO."
  echo ""
  read -rp "  Enable vendor health telemetry? [y/N] " TELEMETRY_CONFIRM
  if [[ "$TELEMETRY_CONFIRM" =~ ^[Yy]$ ]]; then
    echo ""
    read -rp "  Vendor monitor URL (e.g. https://locallyai-monitor.<acct>.workers.dev): " TELEMETRY_URL
    read -rsp "  Per-firm telemetry token (issued by vendor): " TELEMETRY_TOKEN
    echo ""
    if [[ -n "$TELEMETRY_URL" && -n "$TELEMETRY_TOKEN" ]]; then
      cat >> "$ENV_FILE" <<TELEM

# Vendor health telemetry (opt-in — see docs/sop/data-isolation.md)
LOCALLYAI_TELEMETRY=on
LOCALLYAI_TELEMETRY_URL=$TELEMETRY_URL
LOCALLYAI_TELEMETRY_TOKEN=$TELEMETRY_TOKEN
LOCALLYAI_TELEMETRY_INTERVAL=300
TELEM
      ok "Telemetry enabled — heartbeat starts on next API restart"
    else
      warn "URL or token blank — telemetry skipped. Re-run install or edit .env to enable later."
    fi
  else
    info "Telemetry skipped (default). Edit .env to enable later (see docs/sop/data-isolation.md)."
  fi
  echo ""

  # ── 10c. Optional: bootstrap the office Mac as the installer mirror ──────
  # If the operator wants IT to download .dmg/.msi from THIS Mac (not
  # GitHub directly), set up gh CLI + initial pull. The manager UI's
  # /downloads route serves what gets cached at storage/installers/.
  echo "  Set up this Mac as the installer mirror so IT downloads .dmg / .msi"
  echo "  from the office server (no GitHub accounts on staff devices)?"
  read -rp "  Set up installer mirror? [Y/n] " MIRROR_CONFIRM
  if [[ ! "$MIRROR_CONFIRM" =~ ^[Nn]$ ]]; then
    if ! command -v gh &>/dev/null; then
      info "Installing GitHub CLI via Homebrew..."
      if command -v brew &>/dev/null; then
        brew install gh --quiet || warn "gh install failed — install manually then re-run this section"
      else
        warn "Homebrew not found. Install gh manually from https://cli.github.com then re-run."
      fi
    fi
    if command -v gh &>/dev/null; then
      if gh auth status &>/dev/null; then
        ok "gh CLI already authenticated as $(gh api user --jq .login 2>/dev/null || echo unknown)"
      else
        echo ""
        echo "  About to log into GitHub. Use the LocallyAI account (or any"
        echo "  account with read access to LocallyAI/locallyai)."
        echo "  A browser window will open with a one-time device code."
        echo ""
        read -rp "  Continue with gh auth login? [Y/n] " GH_CONFIRM
        if [[ ! "$GH_CONFIRM" =~ ^[Nn]$ ]]; then
          gh auth login --hostname github.com --git-protocol https --web 2>/dev/null \
            || warn "gh auth login did not complete — IT can rerun later"
        fi
      fi

      # Initial pull so the manager UI's /downloads page has files on first launch.
      if gh auth status &>/dev/null; then
        info "Pulling latest client installers from GitHub (background — takes 30–60 s)..."
        # Run via the venv so config.py is loadable; nohup so install.sh
        # doesn't block on slow networks.
        nohup "$VENV/bin/python" -m client_installers refresh \
          >>"$LOGS/installer-refresh.log" 2>&1 &
        ok "First installer pull running in background — visit /downloads in the manager UI"
      fi
    fi
    echo ""
  fi
fi

# ── 10d. GPG: import vendor's release-signing public key ───────────────────
# system_updates.py refuses to apply any tag whose GPG signature doesn't
# verify against this key. Without it, every release attempt fails closed
# (which is the right safety, but the operator can't apply ANY update
# until this is set up). We install gpg via brew if absent and import
# the public key shipped at docs/release-signing-key.gpg.
#
# This is universal — single AND fleet topology — because the firm needs
# to verify vendor signatures regardless of how it consumes updates.
SIGNING_KEY="$DIR/docs/release-signing-key.gpg"
if [[ -f "$SIGNING_KEY" ]]; then
  if ! command -v gpg &>/dev/null; then
    info "Installing GPG (needed to verify signed vendor releases)..."
    if command -v brew &>/dev/null; then
      brew install gnupg --quiet || warn "gpg install failed — vendor release verification will not work until installed"
    else
      warn "Homebrew not found — install gpg manually (https://gnupg.org) so update verification works"
    fi
  fi
  if command -v gpg &>/dev/null; then
    # macOS pinentry friendliness: install pinentry-mac so signing
    # never fails with "Inappropriate ioctl for device" the way the
    # default curses-pinentry does when invoked from a script.
    # Vendor-only operators (those who SIGN releases) need this; firm
    # operators (who only VERIFY signatures) don't strictly need it,
    # but we install for symmetry.
    if command -v brew &>/dev/null && ! command -v pinentry-mac &>/dev/null; then
      info "Installing pinentry-mac (GUI passphrase dialog for GPG)..."
      brew install pinentry-mac --quiet || warn "pinentry-mac install failed — sign-time prompts may fail"
    fi
    if command -v pinentry-mac &>/dev/null; then
      mkdir -p "$HOME/.gnupg"
      PINENTRY_LINE="pinentry-program $(brew --prefix 2>/dev/null)/bin/pinentry-mac"
      if [[ -f "$HOME/.gnupg/gpg-agent.conf" ]] && grep -q "pinentry-program" "$HOME/.gnupg/gpg-agent.conf"; then
        info "gpg-agent.conf already configures pinentry — leaving as-is"
      else
        echo "$PINENTRY_LINE" >> "$HOME/.gnupg/gpg-agent.conf"
        gpgconf --kill gpg-agent 2>/dev/null || true
        ok "Wired gpg-agent → pinentry-mac"
      fi
    fi

    # Skip if already imported (gpg --list-keys exits 0 when there's
    # AT LEAST ONE key matching the search; we cheap-check by name).
    if gpg --list-keys "LocallyAI Releases" &>/dev/null; then
      ok "Vendor release-signing key already in keyring"
    else
      info "Importing vendor release-signing public key..."
      if gpg --import "$SIGNING_KEY" 2>&1 | tail -3; then
        ok "Imported vendor release-signing key — system updates can now be GPG-verified"
        # Mark as fully trusted so `git verify-tag` accepts signatures
        # made by this key without prompting. We OWN this key (it's
        # ours; we shipped it in the repo). The trust DB lives in
        # ~/.gnupg/trustdb.gpg — not git-managed.
        KEY_FP=$(gpg --with-colons --import-options show-only --import "$SIGNING_KEY" 2>/dev/null \
          | awk -F: '/^fpr:/ {print $10; exit}')
        if [[ -n "$KEY_FP" ]]; then
          # ultimate trust = 6
          echo "${KEY_FP}:6:" | gpg --import-ownertrust 2>&1 | head -2
          info "Trust level set to ultimate for key ${KEY_FP: -8}"
        fi
      else
        warn "GPG import failed — vendor releases will be marked as unverified in the manager UI"
      fi
    fi
  fi
else
  warn "$SIGNING_KEY not present — vendor release verification will fail closed"
  info "  (Vendor: generate the key per docs/sop/updates.md and commit the .gpg file)"
fi

# ── 11. Tailscale (optional) ─────────────────────────────────────────────────
read -rp "  Install Tailscale for remote management? [y/N] " TS_CONFIRM
if [[ "$TS_CONFIRM" == "y" || "$TS_CONFIRM" == "Y" ]]; then
  info "Installing Tailscale..."
  if command -v tailscale &>/dev/null; then
    ok "Tailscale already installed"
  elif command -v brew &>/dev/null; then
    brew install tailscale --quiet
    ok "Tailscale installed via Homebrew"
  else
    warn "Homebrew not found — install Tailscale manually from https://tailscale.com/download/mac"
  fi

  if command -v tailscale &>/dev/null; then
    info "Starting Tailscale daemon (sudo prompt may appear)..."
    sudo tailscaled --state=tailscaled.state &>/dev/null &
    sleep 2
    info "Authenticating with Tailscale..."
    echo "  A browser window will open. Log in with your Tailscale account."
    tailscale up --accept-routes 2>/dev/null || tailscale up
    TS_IP=$(tailscale ip -4 2>/dev/null || echo "unknown")
    if [[ "$TS_IP" != "unknown" ]]; then
      ok "Tailscale connected: $TS_IP"
      echo ""
      echo "  Reachable on your tailnet: ${PROBE_URL%/healthz}"
      echo "  (replace localhost with $TS_IP)"
      echo ""
      grep -q '^TAILSCALE_IP=' "$ENV_FILE" && \
        sed -i '' "s|^TAILSCALE_IP=.*|TAILSCALE_IP=$TS_IP|" "$ENV_FILE" || \
        printf 'TAILSCALE_IP=%s\n' "$TS_IP" >> "$ENV_FILE"
    else
      warn "Tailscale IP not detected — check 'tailscale status'"
    fi
  fi
else
  info "Skipping Tailscale. Install later from https://tailscale.com/download/mac"
fi

# ── 12. Workspace UI auto-launch ─────────────────────────────────────────────
WORKER_LAUNCH="$DIR/apps/worker-ui/launch.sh"
MANAGER_LAUNCH="$DIR/apps/manager-ui/launch.sh"
# Self-heal: launchers ship in git as regular files; chmod +x once on install
# so double-click + auto-launch work. Idempotent — no-op if already executable.
[[ -f "$WORKER_LAUNCH"  ]] && chmod +x "$WORKER_LAUNCH"  2>/dev/null || true
[[ -f "$MANAGER_LAUNCH" ]] && chmod +x "$MANAGER_LAUNCH" 2>/dev/null || true
[[ -f "$DIR/apps/worker-ui/launch.bat"  ]] && chmod +x "$DIR/apps/worker-ui/launch.bat"  2>/dev/null || true
[[ -f "$DIR/apps/manager-ui/launch.bat" ]] && chmod +x "$DIR/apps/manager-ui/launch.bat" 2>/dev/null || true

if [[ -x "$WORKER_LAUNCH" ]]; then
  read -rp "  Open the LocallyAI Workspace in your browser now? [Y/n] " UI_CONFIRM
  if [[ ! "$UI_CONFIRM" =~ ^[Nn]$ ]]; then
    info "Launching Workspace UI (this builds the SPA on first run)..."
    # Run in background so the install script can return; the launcher serves
    # the SPA on http://localhost:5174 and opens the browser itself.
    LOCALLYAI_API_BASE="${PROBE_URL%/healthz}" \
      nohup "$WORKER_LAUNCH" >"$DIR/logs/worker-ui.log" 2>&1 &
    sleep 1
    ok "Workspace launching — check $DIR/logs/worker-ui.log if the browser does not open."
    echo ""
    echo "  Future launches: double-click apps/worker-ui/launch.sh (or launch.bat on Windows)."
    echo "  Admin console:   apps/manager-ui/launch.sh (uses your LOCALLYAI_ADMIN_KEY)."
    echo ""
  else
    info "Skipping. Launch later with: $WORKER_LAUNCH"
  fi
elif [[ -f "$WORKER_LAUNCH" ]]; then
  warn "Workspace launcher exists at $WORKER_LAUNCH but isn't executable. Try: chmod +x \"$WORKER_LAUNCH\" \"$MANAGER_LAUNCH\""
else
  warn "Workspace launcher not found at $WORKER_LAUNCH — UI auto-launch skipped."
fi

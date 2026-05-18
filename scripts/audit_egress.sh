#!/usr/bin/env bash
# Lists every outbound TCP connection currently held by LocallyAI
# processes and flags anything outside the documented allowlist.
#
# Run weekly + after any new release. Anything unexpected is a real
# finding worth investigating before the next audit-log review.
#
# Allowlist source of truth: docs/egress-allowlist/README.md.
set -uo pipefail

# Specific identifying substrings — must match the FULL command line.
# Loose matches (just "vite" or "python") would pick up unrelated
# desktop apps and produce false positives.
PROCESS_PATTERNS=(
  "uvicorn api:app"
  "apps/worker-ui/node_modules/.bin/vite"
  "apps/manager-ui/node_modules/.bin/vite"
  "/Users/.*/locallyai/.venv/bin/python -m system_updates"
  "/Users/.*/locallyai/.venv/bin/python -m client_installers"
  "/Users/.*/locallyai/.venv/bin/python -m kill_switch"
  "/Users/.*/locallyai/.venv/bin/python -m manage_users"
  "/Users/.*/locallyai/.venv/bin/python -m llm_models"
  "/Users/.*/locallyai/.venv/bin/python -m deploy"
)

ALLOWED_HOSTS=(
  "github.com"
  "api.github.com"
  "raw.githubusercontent.com"
  "objects.githubusercontent.com"
  "codeload.github.com"
  "huggingface.co"
  "cdn-lfs.huggingface.co"
  "cdn-lfs-us-1.huggingface.co"
  "workers.dev"        # kill-switch CF Worker (suffix match)
  "localhost"
)

# CIDR network allowlist — RFC 1918 private + loopback. Adjust the
# /16 + /12 to your firm's specific subnet for tighter checking.
ALLOWED_CIDRS=(
  "127.0.0.0/8"
  "10.0.0.0/8"
  "172.16.0.0/12"
  "192.168.0.0/16"
)

red()    { printf '\033[31m%s\033[0m' "$*"; }
green()  { printf '\033[32m%s\033[0m' "$*"; }
yellow() { printf '\033[33m%s\033[0m' "$*"; }

# Resolve PIDs only for our specific patterns.
PIDS=""
for pat in "${PROCESS_PATTERNS[@]}"; do
  matched=$(pgrep -f "$pat" 2>/dev/null || true)
  PIDS="$PIDS $matched"
done
PIDS=$(echo "$PIDS" | tr ' ' '\n' | sort -u | grep -v '^$' || true)

if [[ -z "$PIDS" ]]; then
  echo "$(yellow "WARN") No LocallyAI processes running. Run scripts/start_locallyai.sh first."
  exit 0
fi

# Convert dotted-quad to integer for CIDR checks.
ip_to_int() {
  local IFS=.
  read -r a b c d <<<"$1"
  echo $(( (a << 24) | (b << 16) | (c << 8) | d ))
}
ip_in_cidr() {
  local ip="$1" cidr="$2"
  local net="${cidr%/*}" bits="${cidr#*/}"
  [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  local ip_int net_int mask
  ip_int=$(ip_to_int "$ip")
  net_int=$(ip_to_int "$net")
  mask=$(( 0xFFFFFFFF << (32 - bits) & 0xFFFFFFFF ))
  (( (ip_int & mask) == (net_int & mask) ))
}

echo "  LocallyAI process PIDs: $(echo "$PIDS" | tr '\n' ' ' | sed 's/ $//')"
echo ""
printf "  %-8s  %-30s  %-12s  %-45s  %s\n" "PID" "PROCESS" "STATE" "REMOTE" "VERDICT"
printf "  %-8s  %-30s  %-12s  %-45s  %s\n" "---" "-------" "-----" "------" "-------"

flagged=0
ok_count=0
for pid in $PIDS; do
  PROC_NAME=$(ps -p "$pid" -o comm= 2>/dev/null | sed 's|.*/||' | cut -c1-30)
  while IFS= read -r line; do
    REMOTE=$(echo "$line" | awk '{print $9}' | sed 's/.*->//')
    STATE=$(echo "$line" | awk '{print $NF}' | tr -d '()')
    [[ -z "$REMOTE" || "$REMOTE" == "*" ]] && continue
    HOST_PART="${REMOTE%:*}"

    ALLOWED=0
    # Hostname match (exact or suffix).
    for h in "${ALLOWED_HOSTS[@]}"; do
      if [[ "$HOST_PART" == "$h" || "$HOST_PART" == *".$h" ]]; then ALLOWED=1; break; fi
    done
    # CIDR match if it looks like an IPv4.
    if [[ $ALLOWED -eq 0 && "$HOST_PART" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      for c in "${ALLOWED_CIDRS[@]}"; do
        if ip_in_cidr "$HOST_PART" "$c"; then ALLOWED=1; break; fi
      done
    fi
    # rDNS check on stubborn IP literals.
    if [[ "$HOST_PART" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      RDNS=$(dig +short -x "$HOST_PART" 2>/dev/null | head -1 | sed 's/\.$//')
      [[ -n "$RDNS" ]] && REMOTE="$REMOTE  ($RDNS)"
      if [[ $ALLOWED -eq 0 && -n "$RDNS" ]]; then
        for h in "${ALLOWED_HOSTS[@]}"; do
          if [[ "$RDNS" == *".$h" || "$RDNS" == "$h" ]]; then ALLOWED=1; break; fi
        done
      fi
    fi

    if [[ $ALLOWED -eq 1 ]]; then
      ok_count=$((ok_count+1))
      VERDICT="$(green ok)"
    else
      flagged=$((flagged+1))
      VERDICT="$(red FLAGGED)"
    fi
    printf "  %-8s  %-30s  %-12s  %-45s  %s\n" "$pid" "$PROC_NAME" "$STATE" "$REMOTE" "$VERDICT"
  done < <(lsof -nP -iTCP -a -p "$pid" 2>/dev/null | tail -n +2)
done

echo ""
if [[ $flagged -eq 0 ]]; then
  echo "  $(green "PASS")  $ok_count connection(s) — all match the egress allowlist."
else
  echo "  $(red "FAIL")  $flagged connection(s) outside the allowlist (vs $ok_count ok)."
  echo "         Investigate by:"
  echo "           1. What process owns each FLAGGED row above?"
  echo "           2. Recent commits / pulled releases that might have"
  echo "              added a dependency that phones home"
  echo "           3. Cross-check against docs/egress-allowlist/README.md"
fi
echo ""
echo "  Allowlist source of truth: docs/egress-allowlist/README.md"

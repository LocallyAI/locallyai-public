#!/usr/bin/env bash
# release_kill_switch.sh — vendor-side script to sign + publish the
# kill-switch status.json. Red-team finding 4.4: previously the payload
# was unsigned and an attacker who controlled the CF account could push
# any JSON they liked.
#
# Usage:
#   bash scripts/release_kill_switch.sh ACTION [args]
#
#   ACTION:
#     stop "reason..."                   — set kill_switch_active=true
#     resume                             — set kill_switch_active=false
#     blocklist <tag> "reason..."        — add tag to blocklisted_tags
#     require-version <ver> "reason..."  — set min_required_version
#     show                               — print current status (no change)

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
WORKER_DIR="${LOCALLYAI_KILL_SWITCH_WORKER_DIR:-$REPO_ROOT/docs/kill-switch/cloudflare-worker}"
TMP_JSON="$(mktemp -t locallyai-killswitch.XXXXXX.json)"
TMP_SIG="$TMP_JSON.sig"
trap 'rm -f "$TMP_JSON" "$TMP_SIG" 2>/dev/null || true' EXIT

ACTION="${1:-show}"
shift || true
export GPG_TTY="${GPG_TTY:-$(tty 2>/dev/null || echo /dev/tty)}"

CURRENT_KV_KEY="status"
fetch_current() {
  (cd "$WORKER_DIR" && npx wrangler kv key get --binding KILLSWITCH --remote "$CURRENT_KV_KEY" 2>/dev/null) \
    || echo '{"version":1,"kill_switch_active":false,"blocklisted_tags":[],"min_required_version":null,"message":""}'
}
CURRENT="$(fetch_current)"

case "$ACTION" in
  show)
    echo "$CURRENT" | python3 -m json.tool
    exit 0
    ;;
  stop)
    REASON="${1:-(no reason supplied)}"
    NEW=$(echo "$CURRENT" | python3 -c "import json, sys; d=json.load(sys.stdin); d['kill_switch_active']=True; d['message']=sys.argv[1]; print(json.dumps(d))" "$REASON")
    ;;
  resume)
    NEW=$(echo "$CURRENT" | python3 -c "import json, sys; d=json.load(sys.stdin); d['kill_switch_active']=False; d['message']=''; print(json.dumps(d))")
    ;;
  blocklist)
    TAG="${1:?usage: blocklist <tag> <reason>}"
    REASON="${2:-(no reason)}"
    NEW=$(echo "$CURRENT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
bl = list(d.get('blocklisted_tags') or [])
if sys.argv[1] not in bl: bl.append(sys.argv[1])
d['blocklisted_tags'] = bl
d['message'] = sys.argv[2]
print(json.dumps(d))
" "$TAG" "$REASON")
    ;;
  require-version)
    VER="${1:?usage: require-version <ver> <reason>}"
    REASON="${2:-(no reason)}"
    NEW=$(echo "$CURRENT" | python3 -c "import json, sys; d=json.load(sys.stdin); d['min_required_version']=sys.argv[1]; d['message']=sys.argv[2]; print(json.dumps(d))" "$VER" "$REASON")
    ;;
  *)
    echo "usage: $0 {stop|resume|blocklist|require-version|show} [args]" >&2
    exit 1
    ;;
esac

# Stamp issued_at + max_age_seconds (firms reject payloads older than this).
NEW=$(echo "$NEW" | python3 -c "
import json, sys, datetime
d = json.load(sys.stdin)
d['version'] = 1
d['issued_at'] = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
d.setdefault('max_age_seconds', 86400)
print(json.dumps(d, indent=2, sort_keys=True))
")

echo "$NEW" > "$TMP_JSON"
echo "  New payload:"
cat "$TMP_JSON"
echo ""

gpg --detach-sign --armor --output "$TMP_SIG" "$TMP_JSON"
echo "  Signature written: $TMP_SIG"
echo "  Verifying locally..."
gpg --verify "$TMP_SIG" "$TMP_JSON"

echo ""
echo "  Uploading to Worker KV..."
( cd "$WORKER_DIR"
  npx wrangler kv key put --binding KILLSWITCH --remote "$CURRENT_KV_KEY"      --path "$TMP_JSON"
  npx wrangler kv key put --binding KILLSWITCH --remote "${CURRENT_KV_KEY}.sig" --path "$TMP_SIG"
)
echo "  ✓ Kill-switch updated + signed. Firms pick it up within ~60s."

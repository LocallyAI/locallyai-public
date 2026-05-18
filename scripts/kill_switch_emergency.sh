#!/usr/bin/env bash
# Operator-facing kill-switch quick actions. Designed to be FAST during
# an incident — vendor on-call runs ONE of these commands and within
# ~60s every firm's office Mac stops applying updates.
#
# Two backends, picked by env:
#
#   API mode (recommended — TOTP-protected via Cloudflare Worker)
#     export LOCALLYAI_KILL_SWITCH_API_URL=https://locallyai-killswitch.<acct>.workers.dev/
#     This script POSTs the action to the Worker, which prompts for a
#     6-digit TOTP code from your authenticator app, verifies, then
#     uses ITS GitHub PAT (held in CF env) to update status.json.
#     Laptop never holds the PAT or TOTP secret. See
#     scripts/kill_switch_totp_setup.sh for one-time setup.
#
#   Direct-GH mode (legacy fallback)
#     If LOCALLYAI_KILL_SWITCH_API_URL is unset, falls back to direct
#     gh CLI calls. The local gh must be authenticated as a SEPARATE
#     OOB account (NOT LocallyAI). Use only when the Worker isn't
#     deployed yet.
#
# Usage:
#   kill_switch_emergency.sh stop "<reason>"          ← block ALL updates
#   kill_switch_emergency.sh resume                    ← clear the global block
#   kill_switch_emergency.sh blocklist <tag>           ← block one tag, allow others
#   kill_switch_emergency.sh unblocklist <tag>         ← lift block on one tag
#   kill_switch_emergency.sh require-version <ver>     ← force firms past this
#   kill_switch_emergency.sh status                    ← print current JSON
#
# Example incident response:
#   # Bad release shipped — STOP everything
#   kill_switch_emergency.sh stop "v1.2.0-stable causing healthz failures"
#   # Hotfix ready — let firms move to v1.2.1 only
#   kill_switch_emergency.sh require-version 1.2.1
#   # Once enough firms are on the hotfix, lift the block
#   kill_switch_emergency.sh resume
set -euo pipefail

API_URL="${LOCALLYAI_KILL_SWITCH_API_URL:-}"
REPO="${LOCALLYAI_KILL_SWITCH_REPO:-locallyai-status/locallyai-status}"
FILE="status.json"
BRANCH="main"

# ── API path (TOTP-gated Cloudflare Worker) ─────────────────────────────────
if [[ -n "$API_URL" ]]; then
  CMD="${1:-status}"

  # Status is unauthenticated GET — read-only.
  if [[ "$CMD" == "status" ]]; then
    curl -s "${API_URL%/}/" | python3 -m json.tool
    exit 0
  fi

  # Build the action JSON for everything else.
  case "$CMD" in
    stop)
      REASON="${2:-investigating; updates paused}"
      ACTION_JSON=$(python3 -c "import json,sys; print(json.dumps({'op':'stop','message':sys.argv[1]}))" "$REASON")
      ;;
    resume)
      ACTION_JSON='{"op":"resume"}'
      ;;
    blocklist)
      [ "$#" -lt 2 ] && { echo "usage: blocklist <tag>" >&2; exit 2; }
      ACTION_JSON=$(python3 -c "import json,sys; print(json.dumps({'op':'blocklist','tag':sys.argv[1]}))" "$2")
      ;;
    unblocklist)
      [ "$#" -lt 2 ] && { echo "usage: unblocklist <tag>" >&2; exit 2; }
      ACTION_JSON=$(python3 -c "import json,sys; print(json.dumps({'op':'unblocklist','tag':sys.argv[1]}))" "$2")
      ;;
    require-version)
      [ "$#" -lt 2 ] && { echo "usage: require-version <semver>" >&2; exit 2; }
      [[ "$2" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "ERROR: must be semver" >&2; exit 2; }
      ACTION_JSON=$(python3 -c "import json,sys; print(json.dumps({'op':'require-version','version':sys.argv[1]}))" "$2")
      ;;
    *) echo "usage: $0 {stop <reason>|resume|blocklist <tag>|unblocklist <tag>|require-version <ver>|status}" >&2; exit 2 ;;
  esac

  echo ""
  echo "  API: ${API_URL}"
  echo "  Action: $CMD ${2:+\"${2}\"}"
  echo ""
  # Read TOTP code from the operator's authenticator. Use -s so it
  # doesn't echo to terminal (tiny defence against shoulder-surf, since
  # the code is one-time anyway).
  read -rsp "  6-digit TOTP code (or 16-char recovery code): " AUTH_CODE
  echo ""
  if [[ -z "$AUTH_CODE" ]]; then echo "ERROR: empty auth code" >&2; exit 1; fi

  BODY=$(python3 -c "import json,sys; print(json.dumps({'action':json.loads(sys.argv[1]),'auth':sys.argv[2]}))" \
         "$ACTION_JSON" "$AUTH_CODE")

  RESP=$(curl -s -w "\n__HTTP_%{http_code}__" -X POST "${API_URL%/}/" \
         -H "Content-Type: application/json" \
         -d "$BODY")
  CODE=$(printf '%s' "$RESP" | sed -n 's/.*__HTTP_\([0-9]*\)__$/\1/p')
  PAYLOAD=$(printf '%s' "$RESP" | sed 's/__HTTP_[0-9]*__$//')

  if [[ "$CODE" != "200" ]]; then
    echo "ERROR: HTTP $CODE" >&2
    echo "$PAYLOAD" >&2
    exit 1
  fi
  echo "  ✓ Published. Firms react within ≤60 s."
  echo "$PAYLOAD" | python3 -m json.tool
  exit 0
fi

# ── Direct-GH path (legacy fallback when no Worker URL set) ─────────────────
echo "  Note: LOCALLYAI_KILL_SWITCH_API_URL not set — falling back to direct gh"
echo "        For TOTP protection, deploy the Cloudflare Worker (docs/kill-switch/cloudflare-worker/)"
echo ""

if ! command -v gh >/dev/null; then
  echo "ERROR: gh CLI required (brew install gh)" >&2
  exit 1
fi

# Sanity: are we authenticated as the OOB account, NOT the main LocallyAI?
WHO=$(gh api user --jq .login 2>/dev/null || echo unknown)
case "$WHO" in
  ""|unknown) echo "ERROR: gh not authenticated. Run: gh auth login" >&2; exit 1 ;;
  LocallyAI)  echo "ERROR: you're logged in as LocallyAI — that's the SAME-credential account this is supposed to defend against. Switch to the OOB account first: gh auth switch" >&2; exit 1 ;;
esac
echo "  acting as gh user: $WHO  →  $REPO/$FILE"

# Read current state from the repo (so we don't clobber other fields).
TMPDIR=$(mktemp -d); trap "rm -rf $TMPDIR" EXIT
if gh api "repos/$REPO/contents/$FILE?ref=$BRANCH" --jq '.content' 2>/dev/null \
   | base64 -d > "$TMPDIR/cur.json"; then
  CURRENT_SHA=$(gh api "repos/$REPO/contents/$FILE?ref=$BRANCH" --jq '.sha')
else
  echo '{"version":1,"kill_switch_active":false,"blocklisted_tags":[],"min_required_version":"0.0.0","rollback_to_version":null,"message":""}' > "$TMPDIR/cur.json"
  CURRENT_SHA=""
fi

CMD="${1:-status}"
case "$CMD" in
  status)
    cat "$TMPDIR/cur.json" | python3 -m json.tool
    exit 0
    ;;
  stop)
    REASON="${2:-investigating; updates paused}"
    python3 -c "
import json, sys
d = json.load(open('$TMPDIR/cur.json'))
d['kill_switch_active'] = True
d['message'] = $(printf '%s' "$REASON" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')
open('$TMPDIR/new.json','w').write(json.dumps(d, indent=2) + '\n')
"
    ;;
  resume)
    python3 -c "
import json
d = json.load(open('$TMPDIR/cur.json'))
d['kill_switch_active'] = False
d['message'] = ''
open('$TMPDIR/new.json','w').write(json.dumps(d, indent=2) + '\n')
"
    ;;
  blocklist)
    [ "$#" -lt 2 ] && { echo "usage: blocklist <tag>" >&2; exit 2; }
    TAG="$2"
    python3 -c "
import json
d = json.load(open('$TMPDIR/cur.json'))
bl = list(d.get('blocklisted_tags') or [])
if '$TAG' not in bl: bl.append('$TAG')
d['blocklisted_tags'] = bl
open('$TMPDIR/new.json','w').write(json.dumps(d, indent=2) + '\n')
"
    ;;
  require-version)
    [ "$#" -lt 2 ] && { echo "usage: require-version <semver>" >&2; exit 2; }
    VER="$2"
    if [[ ! "$VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      echo "ERROR: must be semver (x.y.z), got '$VER'" >&2; exit 2
    fi
    python3 -c "
import json
d = json.load(open('$TMPDIR/cur.json'))
d['min_required_version'] = '$VER'
open('$TMPDIR/new.json','w').write(json.dumps(d, indent=2) + '\n')
"
    ;;
  *)
    echo "usage: $0 {stop <reason> | resume | blocklist <tag> | require-version <ver> | status}" >&2
    exit 2
    ;;
esac

echo ""
echo "  about to publish:"
diff -u "$TMPDIR/cur.json" "$TMPDIR/new.json" || true
echo ""
read -rp "  Confirm and publish? [y/N] " ANS
[[ "$ANS" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# Push via the GitHub Contents API. Encodes file → base64, includes
# the prior SHA so concurrent edits get the proper 409 conflict.
NEW_B64=$(base64 < "$TMPDIR/new.json" | tr -d '\n')
COMMIT_MSG="kill-switch: $CMD${2:+ — ${2}}"

API_BODY=$(python3 -c "
import json, sys, os
d = {'message': '$COMMIT_MSG', 'content': '$NEW_B64', 'branch': '$BRANCH'}
if '$CURRENT_SHA':
    d['sha'] = '$CURRENT_SHA'
print(json.dumps(d))
")

echo "$API_BODY" | gh api --method PUT "repos/$REPO/contents/$FILE" --input - \
  --jq '.commit.sha' | sed 's/^/  published commit: /'

echo ""
echo "  Firms will pick up the change within ≤60 s (kill_switch.py's"
echo "  cache TTL is 60 s). Watch the audit logs at affected firms"
echo "  for system_update_refused entries to confirm."

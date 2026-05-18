#!/usr/bin/env bash
# scripts/onboard_firm.sh
#
# One-command firm registration after the onboarding intake form returns:
#   - parses firm_id + firm name from the firm-profile-*.md
#   - verifies the declared firm_id matches what we re-compute from the name
#   - generates a fresh telemetry token (32 random bytes hex)
#   - merges the new entry into the local registry
#     (~/.locallyai/vendor/firms-registry.json, mode 0600)
#   - pushes the merged FIRM_TOKENS JSON to the monitor Worker via wrangler
#   - appends an audit row to vendor-records/firms-issued.log
#     (no token value — just firm_id + firm_name + when + by whom)
#   - prints the new token for the operator to share via 1Password / PGP
#
# Usage:
#   bash scripts/onboard_firm.sh ~/Downloads/firm-profile-acme-solicitors-llp.md
#
# Environment (optional):
#   LOCALLYAI_VENDOR_RECORDS  Path to local vendor-records clone.
#                             Default: ~/locallyai-vendor-records
#                             If absent, audit log step is skipped with a warning.
#
# The local registry is the source of truth (wrangler cannot read secrets
# back). Back it up with the rest of the operator's password manager:
#   ~/.locallyai/vendor/firms-registry.json
# Mode is 0600 and the dir is 0700.
set -euo pipefail

PROFILE="${1:-}"
if [ -z "$PROFILE" ] || [ ! -f "$PROFILE" ]; then
  echo "usage: $0 <path/to/firm-profile-*.md>"
  exit 1
fi

REGISTRY_DIR="$HOME/.locallyai/vendor"
REGISTRY="$REGISTRY_DIR/firms-registry.json"
mkdir -p "$REGISTRY_DIR"
chmod 700 "$REGISTRY_DIR"
if [ ! -f "$REGISTRY" ]; then
  printf '{}\n' > "$REGISTRY"
  chmod 600 "$REGISTRY"
fi

# ── Parse firm_id (16-hex) and firm name (H1) from the profile ──────────
FIRM_ID="$(grep -oE '`[0-9a-f]{16}`' "$PROFILE" | head -1 | tr -d '`' || true)"
FIRM_NAME="$(awk '/^# Firm profile — /{sub(/^# Firm profile — /, ""); print; exit}' "$PROFILE")"

if [ -z "$FIRM_ID" ]; then
  echo "ERROR: could not find a 16-hex firm_id in $PROFILE"
  echo "Expected a line like:  - **Anonymised firm_id**: \`a1b2c3d4e5f60718\`"
  exit 1
fi
if [ -z "$FIRM_NAME" ]; then
  echo "ERROR: could not find firm name H1 in $PROFILE"
  echo "Expected the file to start with:  # Firm profile — <Firm Legal Name>"
  exit 1
fi

# ── Verify the firm_id matches what we'd compute from the name ──────────
COMPUTED="$(FN="$FIRM_NAME" python3 -c "import os, hashlib; print(hashlib.sha256(f'locallyai-firm:{os.environ[\"FN\"]}'.encode()).hexdigest()[:16])")"
if [ "$COMPUTED" != "$FIRM_ID" ]; then
  echo ""
  echo "  ⚠  WARNING — declared firm_id does not match computed value."
  echo "     Declared (in profile):  $FIRM_ID"
  echo "     Computed (from H1):     $COMPUTED"
  echo ""
  echo "  This usually means the firm typed a different legal name in the"
  echo "  form than what's now in the H1, OR the profile was hand-edited."
  echo "  Confirm the firm name out-of-band before proceeding."
  echo ""
  read -p "  Proceed anyway with declared id $FIRM_ID? [y/N] " ok
  [ "${ok:-N}" = "y" ] || [ "${ok:-N}" = "Y" ] || exit 1
fi

# ── Check if firm already registered ────────────────────────────────────
EXISTING="$(FID="$FIRM_ID" REG="$REGISTRY" python3 -c "import json, os; d=json.load(open(os.environ['REG'])); print(d.get(os.environ['FID'], {}).get('token', ''))")"
ROTATING="no"
if [ -n "$EXISTING" ]; then
  echo ""
  echo "  Firm already registered: $FIRM_NAME"
  echo "  firm_id: $FIRM_ID"
  echo ""
  read -p "  Re-issue a new token (rotates — old token will stop working)? [y/N] " ok
  if [ "${ok:-N}" != "y" ] && [ "${ok:-N}" != "Y" ]; then
    echo "  No changes made."; exit 0
  fi
  ROTATING="yes"
fi

# ── Generate new token + update local registry ──────────────────────────
TOKEN="$(openssl rand -hex 32)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TMP="$(mktemp)"
FN="$FIRM_NAME" FID="$FIRM_ID" TOK="$TOKEN" NOW="$NOW" REG="$REGISTRY" python3 - > "$TMP" <<'PY'
import json, os
reg = os.environ['REG']
d = json.load(open(reg))
d[os.environ['FID']] = {
    'firm_name': os.environ['FN'],
    'token': os.environ['TOK'],
    'registered_at': os.environ['NOW'],
}
print(json.dumps(d, indent=2, sort_keys=True))
PY
mv "$TMP" "$REGISTRY"
chmod 600 "$REGISTRY"

# ── Build the FIRM_TOKENS JSON (just firm_id -> token, no metadata) ─────
WRANGLER_JSON="$(REG="$REGISTRY" python3 -c "import json, os; d=json.load(open(os.environ['REG'])); print(json.dumps({k: v['token'] for k, v in d.items()}))")"

# ── Push to the monitor Worker via wrangler ─────────────────────────────
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
WORKER_DIR="$REPO_ROOT/docs/monitor/cloudflare-worker"
if [ ! -f "$WORKER_DIR/wrangler.toml" ]; then
  echo "ERROR: monitor Worker dir not found at $WORKER_DIR"
  echo "Are you in the LocallyAI code repo?"
  exit 1
fi
echo ""
echo "  Pushing FIRM_TOKENS to monitor Worker..."
( cd "$WORKER_DIR" && printf '%s' "$WRANGLER_JSON" | npx wrangler secret put FIRM_TOKENS )

# ── Append to audit log in vendor-records (if available locally) ────────
VENDOR_RECORDS="${LOCALLYAI_VENDOR_RECORDS:-$HOME/locallyai-vendor-records}"
LOG="$VENDOR_RECORDS/firms-issued.log"
if [ -d "$VENDOR_RECORDS" ] && [ -f "$LOG" ]; then
  ACTION="issued"
  [ "$ROTATING" = "yes" ] && ACTION="rotated"
  printf '%s | %s | %s | %s | %s\n' "$NOW" "$FIRM_ID" "$FIRM_NAME" "$(whoami)" "$ACTION" >> "$LOG"
  echo "  Audit log updated: $LOG"
  echo "  → remember to commit + push: cd '$VENDOR_RECORDS' && git add firms-issued.log && git commit -m 'log: $ACTION token for $FIRM_NAME ($FIRM_ID)' && git push"
else
  echo ""
  echo "  ⚠  vendor-records not found at $VENDOR_RECORDS — audit log skipped."
  echo "     Clone with:"
  echo "       git clone git@github.com:LocallyAI/vendor-records.git $VENDOR_RECORDS"
  echo "     Or set LOCALLYAI_VENDOR_RECORDS to its path."
fi

ACTION_VERB="Registered"
[ "$ROTATING" = "yes" ] && ACTION_VERB="Rotated token for"

cat <<EOF

  ════════════════════════════════════════════════════════════════════
   ✓ $ACTION_VERB: $FIRM_NAME
     firm_id:  $FIRM_ID

   Telemetry token (share via 1Password / signed PGP — never plain mail):

     $TOKEN

   Firm IT pastes this into .env on the office Mac as:

     LOCALLYAI_TELEMETRY_TOKEN=$TOKEN

  ════════════════════════════════════════════════════════════════════

EOF

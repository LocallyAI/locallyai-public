#!/usr/bin/env bash
# One-time TOTP setup for the kill-switch Worker.
#
# Generates:
#   1. A 160-bit TOTP secret (base32 — what your authenticator app needs).
#   2. An otpauth:// URI you scan into Google Authenticator / 1Password /
#      Authy / iOS Passwords / Bitwarden / etc.
#   3. 10 single-use recovery codes (16 chars each) — print these and
#      store in a sealed envelope OR password manager. Use ONE if your
#      phone is unreachable; regenerate the pool by re-running this
#      script and re-uploading.
#   4. The exact `wrangler secret put` commands to paste into the
#      directory containing the Worker.
#
# Threat-model recap: the laptop running this script holds the secret
# in memory only for the duration of the run; nothing is written to
# disk. The CF Worker is the long-term home of the secret (env var).
# Your phone is the long-term home of the secret (authenticator).
# This laptop should never need to run this script again unless you
# rotate.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ISSUER="LocallyAI"                    # appears as the account "issuer" in your authenticator
ACCOUNT_NAME="killswitch"             # appears as the account "name" in your authenticator
PERIOD=30                              # standard TOTP window
DIGITS=6                               # standard TOTP code length
ALGO="SHA1"                            # RFC 6238 default; matches the Worker

# ── Generate ────────────────────────────────────────────────────────────────
# We want 160 bits of randomness, base32-encoded (32 chars). Python's
# secrets module + base64.b32encode is the simplest deterministic way.
TOTP_SECRET=$(python3 - <<'PY'
import secrets, base64
print(base64.b32encode(secrets.token_bytes(20)).decode().rstrip("="))
PY
)

# 10 recovery codes — 16 hex chars each. Stored ONLY as sha256 in the
# Worker so an operator who steals the worker's env vars can't extract
# usable codes.
read -r -d '' GENRECOV <<'PY' || true
import secrets, hashlib, json, sys
codes = [secrets.token_hex(8) for _ in range(10)]
hashes = [hashlib.sha256(c.lower().encode()).hexdigest() for c in codes]
print("CODES=" + ",".join(codes))
print("HASHES=" + json.dumps(hashes))
PY
RECOV_OUT=$(python3 -c "$GENRECOV")
RECOVERY_CODES=$(echo "$RECOV_OUT" | grep ^CODES= | sed 's/^CODES=//')
RECOVERY_HASHES=$(echo "$RECOV_OUT" | grep ^HASHES= | sed 's/^HASHES=//')

OTPAUTH_URI="otpauth://totp/${ISSUER}:${ACCOUNT_NAME}?secret=${TOTP_SECRET}&issuer=${ISSUER}&algorithm=${ALGO}&digits=${DIGITS}&period=${PERIOD}"

# ── Output ──────────────────────────────────────────────────────────────────
cat <<EOF

  ====================================================================
   LocallyAI kill-switch TOTP — initial setup
  ====================================================================

  1) Scan this QR-equivalent URI into your authenticator app:

     ${OTPAUTH_URI}

EOF

# Render an ASCII QR code if available (qrencode = brew install qrencode).
# Optional but nice — most operators scan from screen.
if command -v qrencode >/dev/null; then
  echo "     (or scan the QR below from your phone)"
  echo ""
  qrencode -t ANSIUTF8 "$OTPAUTH_URI" | sed 's/^/     /'
else
  echo "     (install qrencode for an ASCII QR: brew install qrencode)"
fi

cat <<EOF

  2) Once scanned, the app shows a 6-digit code refreshing every 30 s.
     Verify it matches by reading the next code and confirming the
     server-side check works after deployment (step 5 below).

  3) Save these RECOVERY CODES somewhere safe (password manager,
     printed sealed envelope, NOT the same laptop). Each is single-use:

EOF

# Print the recovery codes nicely formatted (this loop is real bash —
# the escapes inside the surrounding cat <<EOF blocks are deliberate;
# here they are NOT).
IFS=',' read -ra CODE_ARR <<< "$RECOVERY_CODES"
i=1
for code in "${CODE_ARR[@]}"; do
  printf "       %2d. %s\n" "$i" "$code"
  i=$((i+1))
done

cat <<EOF

  4) Set Cloudflare Worker secrets. From the worker dir:

       cd docs/kill-switch/cloudflare-worker
       echo '${TOTP_SECRET}'  | npx wrangler secret put TOTP_SECRET_BASE32
       echo '${RECOVERY_HASHES}' | npx wrangler secret put RECOVERY_CODES_HASHED
       # GitHub PAT — create at https://github.com/settings/personal-access-tokens
       # as the locallyai-status account, fine-grained, ONLY repo
       # locallyai-status/locallyai-status, ONLY scope contents:write.
       npx wrangler secret put GITHUB_PAT      # paste the PAT when prompted

  5) Deploy and verify:

       npx wrangler deploy
       # Note the Worker URL printed (e.g. https://locallyai-killswitch.<account>.workers.dev/)

       # In your shell:
       export LOCALLYAI_KILL_SWITCH_API_URL=https://locallyai-killswitch.<account>.workers.dev/

       # Read-only check (no auth):
       curl -s "\$LOCALLYAI_KILL_SWITCH_API_URL"
       # Should print the current status.json.

       # Auth check (mutates a no-op):
       bash scripts/kill_switch_emergency.sh status

  6) Tell the firms' office Macs about the new URL by adding to .env:

       LOCALLYAI_KILL_SWITCH_URL=https://locallyai-killswitch.<account>.workers.dev/

  ====================================================================
   IMPORTANT — what to do RIGHT NOW
  ====================================================================
  - Verify your authenticator is showing 6-digit codes for the new
    "LocallyAI: killswitch" entry.
  - Save the recovery codes BEFORE you close this terminal — they
    will not be shown again, and the values were not written to disk.
  - Roll the secrets if the laptop you're on is shared / not trusted.

  This script never persists the secret; it only echoes it to your
  terminal. Clear your scrollback (cmd-K) when you're done.

EOF

#!/usr/bin/env bash
# Generate (or render QR for) a TOTP secret + recovery codes for any
# LocallyAI Cloudflare Worker that uses the TOTP-gated admin pattern
# (kill-switch, monitor, etc.).
#
# Usage:
#   totp_secret_helper.sh <label>          → fresh secret + codes + QR
#   totp_secret_helper.sh <label> <secret> → just render QR for an existing secret
#
# Examples:
#   totp_secret_helper.sh monitor                       # generate everything
#   totp_secret_helper.sh monitor JCHYXRI3PN5JJDCE...   # render QR only
#
# label: appears in the authenticator app as "LocallyAI: <label>".
# Pick distinct labels per Worker (kill-switch / monitor / future ones)
# so codes don't get confused.
set -euo pipefail

LABEL="${1:-monitor}"
SECRET="${2:-}"

if [ -z "$SECRET" ]; then
  # Generate a fresh 160-bit base32 secret.
  SECRET=$(python3 -c "import secrets, base64; print(base64.b32encode(secrets.token_bytes(20)).decode().rstrip('='))")
fi

URI="otpauth://totp/LocallyAI:${LABEL}?secret=${SECRET}&issuer=LocallyAI&algorithm=SHA1&digits=6&period=30"

echo ""
echo "  ===================================================================="
echo "   Label:  LocallyAI: $LABEL"
echo "   Secret: $SECRET"
echo "  ===================================================================="
echo ""
echo "  Scan into your authenticator app:"
echo ""
if command -v qrencode >/dev/null; then
  qrencode -t ANSIUTF8 "$URI" | sed 's/^/  /'
else
  echo "  (install qrencode: brew install qrencode)"
  echo "  URI: $URI"
fi
echo ""

# Only generate fresh recovery codes when we generated a fresh secret.
# When rendering QR for an existing secret, the codes already exist
# elsewhere (vendor's password manager).
if [ -z "${2:-}" ]; then
  python3 - <<'PY'
import secrets, hashlib, json
codes = [secrets.token_hex(8) for _ in range(10)]
hashes = [hashlib.sha256(c.lower().encode()).hexdigest() for c in codes]
print("  Recovery codes — save these (sealed envelope + password manager):")
for i, c in enumerate(codes, 1):
    print(f"    {i:>2}. {c}")
print()
print("  Hashes JSON for `wrangler secret put ADMIN_RECOVERY_HASHED`:")
print()
print("  " + json.dumps(hashes))
print()
PY
fi

echo "  Next:"
echo "    cd docs/<worker-dir>/cloudflare-worker"
echo "    echo '$SECRET' | npx wrangler secret put ADMIN_TOTP_SECRET_BASE32"
echo "    # Then paste the hashes JSON when prompted by:"
echo "    npx wrangler secret put ADMIN_RECOVERY_HASHED"
echo ""

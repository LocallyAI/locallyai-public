#!/usr/bin/env bash
# Render a QR code for an existing TOTP otpauth:// URI, OR for a bare
# base32 secret. Use this when the original setup-script run didn't
# render a QR (e.g. qrencode wasn't installed) and you don't want to
# regenerate the secret.
#
# Usage:
#   kill_switch_totp_qr.sh 'otpauth://totp/LocallyAI:killswitch?secret=XXX&issuer=LocallyAI&algorithm=SHA1&digits=6&period=30'
#
#   # OR (simpler — just the secret):
#   kill_switch_totp_qr.sh XXXXXXXXXXXXXXXXXXXX
#   # → rebuilds the canonical otpauth:// URI around it.
#
# If qrencode isn't installed, offers to install via brew. Falls back
# to printing the URI + bare secret if you decline.
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <otpauth-uri OR base32-secret>" >&2
  exit 2
fi

INPUT="$1"

# Build the otpauth URI if input looks like a bare secret.
if [[ "$INPUT" =~ ^otpauth:// ]]; then
  URI="$INPUT"
else
  SECRET=$(echo "$INPUT" | tr -d '[:space:]=' | tr '[:lower:]' '[:upper:]')
  if [[ ! "$SECRET" =~ ^[A-Z2-7]+$ ]]; then
    echo "ERROR: '$INPUT' doesn't look like a base32 secret OR an otpauth URI" >&2
    exit 1
  fi
  URI="otpauth://totp/LocallyAI:killswitch?secret=${SECRET}&issuer=LocallyAI&algorithm=SHA1&digits=6&period=30"
fi

if ! command -v qrencode >/dev/null; then
  echo "  qrencode not installed."
  read -rp "  Install via Homebrew? [Y/n] " ans
  if [[ ! "$ans" =~ ^[Nn]$ ]] && command -v brew >/dev/null; then
    brew install qrencode
  else
    echo ""
    echo "  Falling back to URI-only — type into your authenticator manually:"
    echo "    $URI"
    echo ""
    echo "  OR use 'Add account → Enter setup key manually' with just the secret:"
    echo "    $(echo "$URI" | sed -n 's/.*secret=\([^&]*\).*/\1/p')"
    exit 0
  fi
fi

echo ""
echo "  Scan with your phone's authenticator app:"
echo ""
qrencode -t ANSIUTF8 "$URI" | sed 's/^/  /'
echo ""
echo "  URI for reference (don't paste into web QR-generator tools — that leaks the secret):"
echo "    $URI"

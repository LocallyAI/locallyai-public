#!/usr/bin/env bash
# scripts/sign_bootstrap.sh
#
# Sign the install bootstrap with the LocallyAI release-signing GPG key.
# Produces a detached signature alongside the bootstrap, which the
# Worker serves as a static asset. The form's "verify before running"
# block walks IT through fetching + verifying both.
#
# Run when:
#   - bootstrap script changes (any edit to docs/monitor/cloudflare-worker/src/dashboard/bootstrap)
#   - GPG signing key rotates
#
# After signing, redeploy the Worker:
#   cd docs/monitor/cloudflare-worker && npx wrangler deploy
#
# Usage:
#   bash scripts/sign_bootstrap.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
BOOT="$REPO_ROOT/docs/monitor/cloudflare-worker/src/dashboard/bootstrap"
SIG="$BOOT.sig"

[ -f "$BOOT" ] || { echo "ERROR: bootstrap not found at $BOOT"; exit 1; }
command -v gpg >/dev/null || { echo "ERROR: gpg not installed (brew install gnupg)"; exit 1; }
command -v shasum >/dev/null || { echo "ERROR: shasum not in PATH"; exit 1; }

HASH="$(shasum -a 256 "$BOOT" | awk '{print $1}')"

echo ""
echo "  ════════════════════════════════════════════════════════════════════"
echo "   Signing $BOOT"
echo "   SHA-256: $HASH"
echo ""
echo "   You'll be prompted for the GPG release-signing key passphrase."
echo "  ════════════════════════════════════════════════════════════════════"
echo ""

# Ensure GPG can find a TTY for pinentry.
export GPG_TTY="${GPG_TTY:-$(tty 2>/dev/null || echo /dev/tty)}"

# Detached, ascii-armored signature. The .sig is what the Worker serves.
rm -f "$SIG"
gpg --detach-sign --armor --output "$SIG" "$BOOT"

[ -f "$SIG" ] || { echo "ERROR: signing produced no output file"; exit 1; }

# Verify the signature locally before we hand it off.
echo ""
echo "  Verifying signature locally…"
if gpg --verify "$SIG" "$BOOT" 2>&1 | grep -q "Good signature"; then
  echo "  ✓ Good signature."
else
  echo "  ✗ Signature did NOT verify. Aborting."
  rm -f "$SIG"
  exit 1
fi

echo ""
echo "  Output files (commit both):"
echo "    $BOOT"
echo "    $SIG"
echo ""
echo "  Next step — deploy to the Worker so the new bootstrap+sig"
echo "  are served from the public URL:"
echo ""
echo "    cd $REPO_ROOT/docs/monitor/cloudflare-worker"
echo "    npx wrangler deploy"
echo ""
echo "  The form fetches the bootstrap on demand and recomputes the"
echo "  SHA-256 client-side, so it stays in sync automatically — no"
echo "  hardcoded hash to update."
echo ""

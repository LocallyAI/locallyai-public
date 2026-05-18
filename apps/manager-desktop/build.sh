#!/usr/bin/env bash
# Build LocallyAI Manager.app — a minimal macOS WKWebView wrapper
# around the firm's Manager UI. Output: dist/LocallyAI Manager.app
# (and a zip alongside for distribution).
#
# Usage:
#   ./build.sh                                  # default URL baked
#   MANAGER_URL=https://office.local:8000 ./build.sh
#
# The URL ALSO defaults to compile-time, but is overridable at runtime
# via env var LOCALLYAI_MANAGER_URL or via the "Set Manager URL…" menu
# (saves to UserDefaults). Per-firm builds bake the firm's hostname in
# so the app works on first launch without setup.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

MANAGER_URL="${MANAGER_URL:-https://office-mac.local:8000}"
APP_NAME="LocallyAI Manager"
BUNDLE_ID="app.locallyai.manager-desktop"
OUT="dist/${APP_NAME}.app"

rm -rf dist
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"

# Compile Swift binary with the URL baked into the source via a string
# replace before swiftc — swift's #if conditional compilation is too
# constrained for arbitrary string baking, so we do it the boring way.
WORK_DIR="$(mktemp -d)"
WORK_SRC="$WORK_DIR/LocallyAIManager.swift"
trap 'rm -rf "$WORK_DIR"' EXIT
sed "s|https://office-mac.local:8000|${MANAGER_URL}|g" LocallyAIManager.swift > "$WORK_SRC"

swiftc \
    -O \
    -target arm64-apple-macos13 \
    -framework Cocoa \
    -framework WebKit \
    "$WORK_SRC" \
    -o "$OUT/Contents/MacOS/LocallyAIManager"

# Info.plist (URL is runtime + baked default; bundle id stays constant)
cp Info.plist.tmpl "$OUT/Contents/Info.plist"

# Optional icon
if [ -f Resources/AppIcon.icns ]; then
    cp Resources/AppIcon.icns "$OUT/Contents/Resources/"
    # Reference it in Info.plist
    /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string AppIcon" "$OUT/Contents/Info.plist" 2>/dev/null || true
fi

# Ad-hoc sign so Gatekeeper doesn't quarantine on first launch
# (proper Developer ID sign + notarisation happens in the locallyai-clients
# release pipeline; this is the dev / dogfood path).
codesign --force --deep --sign - "$OUT" 2>/dev/null || true

# Zip alongside for easy transfer to other Macs
ZIP="dist/${APP_NAME}.zip"
cd dist && /usr/bin/ditto -c -k --keepParent "${APP_NAME}.app" "${APP_NAME}.zip" && cd ..

echo ""
echo "Built: $OUT"
echo "Baked URL: $MANAGER_URL"
echo "Zip: $ZIP"
echo ""
echo "To install on a staff laptop:"
echo "  1. Copy '$OUT' to /Applications"
echo "  2. Double-click to launch"
echo "  3. (Optional) drag to Dock for quick access"

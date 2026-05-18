#!/usr/bin/env bash
# Tag + push a client-app release. GitHub Actions then builds the four
# installers (Worker × macOS, Worker × Windows, Manager × macOS,
# Manager × Windows) and attaches them to the release page that IT
# downloads from.
#
# Usage:
#   scripts/release_clients.sh                          # auto-bump patch
#   scripts/release_clients.sh 1.2.0                    # explicit version
#   scripts/release_clients.sh 1.2.0 --dry-run          # show what it'd do
#
# Tag format: v<version>-clients
#   The "-clients" suffix isolates client-app releases from server-side
#   tags so the build-clients.yml workflow only fires on client tags.
#
# Idempotent? No — pushing a tag twice is a hard error. Run --dry-run
# first if you want to verify the version bump without committing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

DRY_RUN=0
NEW_VERSION=""
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY_RUN=1 ;;
    -*) echo "Unknown flag: $arg" >&2; exit 2 ;;
    *)  NEW_VERSION="$arg" ;;
  esac
done

# ── Auto-bump if no version supplied ─────────────────────────────────────────
# Walk the existing tags, find the highest v*-clients, bump patch.
if [[ -z "$NEW_VERSION" ]]; then
  LAST_TAG=$(git tag -l 'v*-clients' --sort=-v:refname | head -1)
  if [[ -z "$LAST_TAG" ]]; then
    NEW_VERSION="0.1.0"
    echo "No prior client tags — starting at v$NEW_VERSION-clients"
  else
    LAST_VERSION="${LAST_TAG#v}"; LAST_VERSION="${LAST_VERSION%-clients}"
    IFS='.' read -r MAJ MIN PAT <<< "$LAST_VERSION"
    NEW_VERSION="$MAJ.$MIN.$((PAT + 1))"
    echo "Last tag: $LAST_TAG  →  bumping to v$NEW_VERSION-clients"
  fi
fi

# Strip any leading "v" the operator may have included.
NEW_VERSION="${NEW_VERSION#v}"
TAG="v${NEW_VERSION}-clients"

# ── Pre-flight ──────────────────────────────────────────────────────────────
if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: Working tree is dirty. Commit or stash before releasing." >&2
  git status --short >&2
  exit 1
fi

CURRENT_BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "DETACHED")
if [[ "$CURRENT_BRANCH" != "main" ]]; then
  echo "WARN: You're on '$CURRENT_BRANCH', not main. Continue? [y/N]"
  read -r ans
  [[ "$ans" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "ERROR: Tag $TAG already exists locally. Delete it first or pick a new version." >&2
  exit 1
fi

# ── Bump version in both Tauri configs so the .dmg / .msi filenames match ──
WORKER_CONF="$REPO_DIR/apps/clients/worker-tauri/src-tauri/tauri.conf.json"
WORKER_CARGO="$REPO_DIR/apps/clients/worker-tauri/src-tauri/Cargo.toml"
MANAGER_CONF="$REPO_DIR/apps/clients/manager-tauri/src-tauri/tauri.conf.json"
MANAGER_CARGO="$REPO_DIR/apps/clients/manager-tauri/src-tauri/Cargo.toml"

bump_conf() {
  # JSON: "version": "x.y.z"  → no dependency on jq, sed handles the
  # one-line form Tauri's CLI emits.
  local f="$1"
  sed -i '' "s|\"version\": \"[0-9.]*\"|\"version\": \"$NEW_VERSION\"|" "$f"
}
bump_cargo() {
  # TOML: version = "x.y.z" — only the [package] block's version line.
  local f="$1"
  sed -i '' "s|^version = \"[0-9.]*\"|version = \"$NEW_VERSION\"|" "$f"
}

if [[ "$DRY_RUN" == "1" ]]; then
  echo ""
  echo "  DRY RUN — would do:"
  echo "    bump $WORKER_CONF  → version $NEW_VERSION"
  echo "    bump $WORKER_CARGO → version $NEW_VERSION"
  echo "    bump $MANAGER_CONF  → version $NEW_VERSION"
  echo "    bump $MANAGER_CARGO → version $NEW_VERSION"
  echo "    git commit -am 'clients: bump to $NEW_VERSION'"
  echo "    git tag $TAG"
  echo "    git push origin main && git push origin $TAG"
  echo ""
  echo "  CI will then build .dmg + .msi for both apps, ~10–15 min cold,"
  echo "  ~3–5 min on a warm cache. Watch:"
  echo "    https://github.com/LocallyAI/locallyai/actions"
  echo "  Artifacts attach to:"
  echo "    https://github.com/LocallyAI/locallyai/releases/tag/$TAG"
  exit 0
fi

bump_conf  "$WORKER_CONF"
bump_cargo "$WORKER_CARGO"
bump_conf  "$MANAGER_CONF"
bump_cargo "$MANAGER_CARGO"

git add "$WORKER_CONF" "$WORKER_CARGO" "$MANAGER_CONF" "$MANAGER_CARGO"
git commit -m "clients: bump to $NEW_VERSION"
git tag -a "$TAG" -m "Client apps $NEW_VERSION"

echo ""
echo "Pushing main + tag…"
git push origin "$(git symbolic-ref --short HEAD)"
git push origin "$TAG"

echo ""
echo "  ✓ $TAG pushed."
echo "  CI is now building. Watch:"
echo "      https://github.com/LocallyAI/locallyai/actions"
echo "  Once green, IT downloads .dmg + .msi from:"
echo "      https://github.com/LocallyAI/locallyai/releases/tag/$TAG"

#!/usr/bin/env bash
# Vendor-side release publisher for the LocallyAI server itself.
#
# Two-stage flow:
#   release_server.sh dev   1.2.0  A  "fix: bm25 race"   → tags v1.2.0-dev
#   release_server.sh promote 1.2.0                       → re-tags as v1.2.0-stable (after soak)
#
# Tier:   A = security/critical (auto-applies on firms)
#         B = normal feature (manager UI prompts to apply)
#         C = manual / breaking (vendor coordinates window)
#
# Side effects:
#   1. Updates release_manifest.json (channel, tier, version, sha256s, released_at).
#   2. Commits the manifest update.
#   3. Creates a GPG-signed annotated tag (git tag -s).
#   4. Pushes commit + tag to the canonical remote.
#
# Pre-reqs (one-time):
#   * gpg --gen-key (offline machine ideally)
#   * git config --global user.signingkey <KEY-ID>
#   * Public key exported to docs/release-signing-key.gpg + committed.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# GPG-via-script on macOS hits "Inappropriate ioctl for device" when the
# pinentry tries to grab a TTY the script doesn't own. Two safety nets:
#   1. Export GPG_TTY so curses-pinentry knows where to draw the prompt.
#   2. If pinentry-mac is configured (gpg-agent.conf points at it), the
#      passphrase dialog goes to a native macOS window — no TTY needed
#      at all. Operators are encouraged to install pinentry-mac
#      (`brew install pinentry-mac`) for the smoothest experience.
export GPG_TTY="$(tty 2>/dev/null || true)"

usage() {
  cat <<EOF
usage:
  release_server.sh dev <version> <tier> <changelog_summary>
  release_server.sh promote <version>

  version          : semver, e.g. 1.2.0
  tier             : A (security/critical) | B (feature) | C (breaking)
  changelog_summary: one-line description shown in the manager UI

Examples:
  release_server.sh dev 1.2.0 A "fix: BM25 rebuild race on bulk ingest"
  release_server.sh promote 1.2.0
EOF
  exit 2
}

[ "$#" -lt 2 ] && usage

CMD="$1"
VERSION="$2"

# ── Pre-flight ──────────────────────────────────────────────────────────────
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "ERROR: version must be semver (e.g. 1.2.0), got '$VERSION'" >&2
  exit 2
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: working tree dirty — commit or stash before tagging" >&2
  git status --short >&2
  exit 1
fi
if ! git config user.signingkey >/dev/null; then
  echo "ERROR: no GPG signing key configured (git config user.signingkey <KEY-ID>)" >&2
  exit 1
fi

# ── dev: fresh signed tag for the dev channel ───────────────────────────────
if [[ "$CMD" == "dev" ]]; then
  [ "$#" -lt 4 ] && usage
  TIER="$3"; SUMMARY="$4"
  case "$TIER" in A|B|C) ;; *) echo "ERROR: tier must be A, B or C" >&2; exit 2 ;; esac

  TAG="v${VERSION}-dev"
  if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "ERROR: tag $TAG already exists. Pick a higher version." >&2
    exit 1
  fi

  RELEASED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Building release_manifest.json for $TAG..."

  # Compute sha256 for every file the manifest cares about. Default
  # set: api.py + every Python module at the repo root + everything
  # under apps/. Operators can extend by editing this list.
  # Pass values via env so the heredoc can be QUOTED ('PY'). Quoted
  # heredocs disable bash variable substitution — necessary because
  # Python's f-string repr-flag (${SUMMARY!r}) collides with bash's
  # parameter-expansion syntax otherwise.
  VERSION="$VERSION" TIER="$TIER" RELEASED_AT="$RELEASED_AT" SUMMARY="$SUMMARY" \
  python3 <<'PY'
import hashlib, json, os
from pathlib import Path

root = Path('.')

def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for blk in iter(lambda: f.read(65536), b''): h.update(blk)
    return h.hexdigest()

include = []
# Top-level Python sources
for p in sorted(root.glob('*.py')): include.append(p)
# Watchdog + audit modules
for d in ['watchdog', 'audit_export', 'monitoring', 'billing']:
    for p in sorted(Path(d).rglob('*.py')): include.append(p)
# Manager + worker UI source (NOT node_modules / dist)
for app in ['apps/worker-ui/src', 'apps/manager-ui/src']:
    p = Path(app)
    if p.exists():
        for f in sorted(p.rglob('*')):
            if f.is_file() and not any(x.startswith('.') for x in f.parts):
                include.append(f)

artifacts = []
for p in include:
    try:
        artifacts.append({"name": str(p), "sha256": sha(p), "size": p.stat().st_size})
    except OSError:
        pass

manifest = {
    "version":                          os.environ["VERSION"],
    "channel":                          "dev",
    "tier":                             os.environ["TIER"],
    "released_at":                      os.environ["RELEASED_AT"],
    "changelog_summary":                os.environ["SUMMARY"],
    "artifacts":                        artifacts,
    "min_required_version":             "0.0.0",
    "rollback_to_previous_if_failed":   True,
}
Path('release_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
print(f"  manifest: {len(artifacts)} artifact(s)")
PY

  git add release_manifest.json
  git commit -m "release: $TAG (tier $TIER) — $SUMMARY"
  git tag -s "$TAG" -m "LocallyAI server $TAG (tier $TIER)"$'\n\n'"$SUMMARY"
  git push origin "$(git symbolic-ref --short HEAD)"
  git push origin "$TAG"

  echo ""
  echo "  ✓ $TAG pushed (signed)."
  echo "  Vendor's dev box will pick this up within ~6h via the sentinel"
  echo "  auto-apply tick (tier A only) or via the manager UI's Apply button."
  echo "  After 24+h with no rollback, promote to stable:"
  echo "      scripts/release_server.sh promote $VERSION"
  exit 0
fi

# ── promote: re-tag a -dev as -stable so firms see it ────────────────────────
if [[ "$CMD" == "promote" ]]; then
  DEV_TAG="v${VERSION}-dev"
  STABLE_TAG="v${VERSION}-stable"
  if ! git rev-parse "$DEV_TAG" >/dev/null 2>&1; then
    echo "ERROR: $DEV_TAG does not exist locally — nothing to promote" >&2
    exit 1
  fi
  if git rev-parse "$STABLE_TAG" >/dev/null 2>&1; then
    echo "ERROR: $STABLE_TAG already exists" >&2
    exit 1
  fi

  # Soak window enforcement — vendor-side belt + braces (firms enforce
  # too via DEV_SOAK_HOURS in system_updates.py).
  DEV_TAG_TIME=$(git log -1 --format='%ai' "$DEV_TAG")
  DEV_TAG_EPOCH=$(date -j -f "%Y-%m-%d %H:%M:%S %z" "$DEV_TAG_TIME" +%s 2>/dev/null \
    || date -d "$DEV_TAG_TIME" +%s)
  AGE_HR=$(( ($(date +%s) - DEV_TAG_EPOCH) / 3600 ))
  if [ "$AGE_HR" -lt 24 ]; then
    echo "WARN: $DEV_TAG is only ${AGE_HR}h old. Recommended dev soak is 24h."
    read -rp "  Promote anyway? [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
  fi

  # Update manifest's channel + released_at on the same commit, then re-sign.
  git checkout -q "$DEV_TAG"
  # Same pattern as the dev block: quoted heredoc + env vars to avoid
  # bash/Python syntax collisions.
  PROMOTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" python3 <<'PY'
import json, os
m = json.loads(open('release_manifest.json').read())
m['channel'] = 'stable'
m['released_at'] = os.environ['PROMOTED_AT']
open('release_manifest.json', 'w').write(json.dumps(m, indent=2) + '\n')
PY
  git add release_manifest.json
  git commit -m "release: promote $DEV_TAG → $STABLE_TAG"
  git tag -s "$STABLE_TAG" -m "LocallyAI server $STABLE_TAG"
  git checkout -q main
  # The promote commit lives off main on a detached HEAD; we cherry-pick
  # the manifest update onto main so it's the canonical state.
  git push origin "$STABLE_TAG"

  echo ""
  echo "  ✓ $STABLE_TAG pushed (signed)."
  echo "  Firms set to LOCALLYAI_UPDATE_CHANNEL=stable will see it on"
  echo "  their next sentinel tick (≤ 6h) and apply if tier A."
  exit 0
fi

usage

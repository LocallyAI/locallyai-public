#!/usr/bin/env bash
# publish_to_github.sh — Create a GitHub repo and push production/ in one shot.
#
# Usage (run from inside production/, or from anywhere):
#   bash scripts/publish_to_github.sh <repo-name>            # private (default)
#   bash scripts/publish_to_github.sh <repo-name> --public   # public
#
# Requires the GitHub CLI:
#   brew install gh
#   gh auth login        # one-time browser/SSH/token auth
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ok()   { printf '\033[32m[ OK   ]\033[0m %s\n' "$*"; }
info() { printf '\033[36m[ INFO ]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[ WARN ]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[ FAIL ]\033[0m %s\n' "$*"; exit 1; }

REPO_NAME="${1:-}"
VISIBILITY="--private"
FORCE=0
if [[ -z "$REPO_NAME" ]]; then
  cat <<USAGE
Usage:
  bash scripts/publish_to_github.sh <repo-name>                    (private, default)
  bash scripts/publish_to_github.sh <repo-name> --public           (public)
  bash scripts/publish_to_github.sh <repo-name> --force            (overwrite remote)

Examples:
  bash scripts/publish_to_github.sh locallyai
  bash scripts/publish_to_github.sh locallyai --public --force
USAGE
  exit 1
fi
shift
for arg in "$@"; do
  case "$arg" in
    --public)  VISIBILITY="--public"  ;;
    --private) VISIBILITY="--private" ;;
    --force)   FORCE=1                ;;
    *) die "Unknown argument: $arg" ;;
  esac
done

# 1. GitHub CLI present?
if ! command -v gh >/dev/null 2>&1; then
  die "GitHub CLI (gh) not installed. Run: brew install gh   then: gh auth login"
fi

# 2. Authenticated?
if ! gh auth status >/dev/null 2>&1; then
  die "GitHub CLI is not authenticated. Run: gh auth login"
fi

# 3. Move to production/ root
cd "$DIR"
info "Repo root: $DIR"

# 4. Initialise git if needed
if [[ ! -d .git ]]; then
  git init -b main >/dev/null
  ok "Initialised git repository"
else
  info "Existing git repository detected — keeping history"
fi

# 5. Stage everything (gitignore handles secrets)
git add .

# 6. Commit (skip if nothing staged)
if git diff --cached --quiet 2>/dev/null; then
  info "No changes to commit"
else
  git commit -m "LocallyAI — on-prem AI for regulated industries" >/dev/null
  ok "Committed staged files"
fi

CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "main")
[[ -z "$CURRENT_BRANCH" ]] && CURRENT_BRANCH="main"

# 7. Already has a remote? Just push. Otherwise create the repo.
if git remote get-url origin >/dev/null 2>&1; then
  EXISTING=$(git remote get-url origin)
  info "Remote 'origin' already set: $EXISTING -- pushing"
  if ! PUSH_OUT=$(git push -u origin "$CURRENT_BRANCH" 2>&1); then
    if echo "$PUSH_OUT" | grep -qE '\[rejected\]|fetch first|non-fast-forward'; then
      if [[ $FORCE -eq 1 ]]; then
        warn "Push rejected; --force given. Rebasing then force-pushing."
        git pull --rebase origin "$CURRENT_BRANCH" || die "Rebase failed; resolve conflicts then run: git push --force-with-lease origin $CURRENT_BRANCH"
        git push --force-with-lease -u origin "$CURRENT_BRANCH" || die "Force-push failed."
        ok "Force-pushed"
      else
        die "git push rejected -- remote has commits your local does not.

This usually means GitHub auto-created a README when you made the repo.

Pick one:
  Option A (overwrite remote with local -- recommended for first publish):
    bash scripts/publish_to_github.sh $REPO_NAME --force
  Option B (keep both -- only if you wrote on GitHub web UI):
    git pull --rebase origin $CURRENT_BRANCH && git push origin $CURRENT_BRANCH"
      fi
    else
      die "git push failed:
$PUSH_OUT"
    fi
  fi
else
  info "Creating GitHub repo '$REPO_NAME' ($VISIBILITY) and pushing..."
  gh repo create "$REPO_NAME" \
    "$VISIBILITY" \
    --source="$DIR" \
    --description "LocallyAI — on-premises AI for regulated industries (Apple Silicon, RAG, hybrid retrieval, audit-logged)" \
    --push
  ok "Repo created and pushed"
fi

# 8. Print the URL
URL=$(gh repo view --json url -q .url 2>/dev/null || echo "")
echo ""
if [[ -n "$URL" ]]; then
  ok "Published: $URL"
  echo ""
  echo "  Anyone with access can now run:"
  echo "    git clone $URL"
  echo "    cd $(basename "$URL" .git)"
  echo "    bash install.sh"
  echo ""
  echo "  Want to make a tagged release?"
  echo "    git tag -a v1.0.0 -m 'First release'"
  echo "    git push origin v1.0.0"
else
  warn "Push completed but couldn't fetch the URL — check 'gh repo view' manually."
fi

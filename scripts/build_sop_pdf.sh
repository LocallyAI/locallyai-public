#!/usr/bin/env bash
# Convenience wrapper around build_sop_pdf.py — uses the repo's venv
# Python so a fresh clone doesn't need to think about which python3.

set -e
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -x .venv/bin/python ]; then
  echo "[build_sop_pdf] .venv missing — run install.sh first." >&2
  exit 1
fi

# Ensure the markdown library is present (cheap to recheck).
.venv/bin/python -c "import markdown" 2>/dev/null || \
  .venv/bin/pip install --quiet markdown

.venv/bin/python scripts/build_sop_pdf.py "$@"

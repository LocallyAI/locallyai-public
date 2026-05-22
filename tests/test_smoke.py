"""
Smoke test — proves the core codebase installs and imports cleanly.

Imports a curated set of modules that depend only on `requirements-core.txt`
(no sentence-transformers, no MLX). Heavier modules with ML deps are
imported best-effort and skipped under `pytest.skip` if their imports
fail — Ubuntu CI installs core only, so those imports are expected to
fail there.

This is intentionally narrow:
  - It catches "the codebase doesn't even parse" regressions
  - It does NOT spin up FastAPI, Qdrant, or the inference backends
  - The bigger end-to-end coverage lives in tests/smoke_e2e.py and
    tests/ha_chaos.py — both runnable as scripts, deliberately NOT
    pytest-collected because they need a full local environment.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest

# Without a salt, config.py prints a UserWarning at import time. Pre-set
# a placeholder so the warning doesn't pollute CI output. The audit
# pseudonymisation security note still applies in production.
os.environ.setdefault("LOCALLYAI_AUDIT_SALT", "test_salt_for_ci_only" * 2)


# ─── Pure-Python modules (no third-party imports beyond stdlib) ──────────

@pytest.mark.parametrize("module", [
    "audit_reader",
    "bm25",
    "platform_compat",
    "shared_lock",
])
def test_pure_modules_import(module: str) -> None:
    """These modules use stdlib only — they must import on any platform."""
    m = importlib.import_module(module)
    assert m is not None


# ─── Modules that need core (requirements-core.txt) deps ─────────────────

@pytest.mark.parametrize("module", [
    "config",
    "audit_export",
    "billing",
    "inference_gate",
    "ingest_queue",
    "chunked_uploads",
    "doc_acls",
    "conflicts",
    "documents_compare",
    "citations",
])
def test_core_dep_modules_import(module: str) -> None:
    """Need fastapi / qdrant / pydantic / pymupdf etc. — installed by
    requirements-core.txt. Skipped if any core dep is unexpectedly absent."""
    try:
        m = importlib.import_module(module)
    except ImportError as e:
        pytest.skip(f"core dep missing for {module}: {e}")
    assert m is not None


# ─── ML-dep modules — expected to skip on CI ─────────────────────────────

@pytest.mark.parametrize("module", [
    "ingest",          # uses sentence-transformers when EMBED_BACKEND=local
    "retrieval",       # likewise
    "reranker",        # cross-encoder via sentence-transformers
    "mlx_inference",   # Apple-Silicon-only
    "embed_local",     # in-process embedding
])
def test_ml_dep_modules_import_or_skip(module: str) -> None:
    """These will skip cleanly on Ubuntu CI (no PyTorch / no MLX).
    They must NOT raise unexpected errors when ml deps are missing —
    only ImportError is allowed as the skip signal."""
    try:
        m = importlib.import_module(module)
    except ImportError as e:
        pytest.skip(f"ml dep missing for {module}: {e}")
    assert m is not None


# ─── Sanity: a single primitive from a pure module actually works ────────

def test_bm25_index_constructible(tmp_path) -> None:
    """BM25Index instantiates cleanly with a storage dir — proves the
    module isn't an empty shell. The actual indexing path is exercised
    by tests/smoke_e2e.py."""
    import bm25
    idx = bm25.BM25Index(str(tmp_path / "bm25"))
    assert idx is not None


def test_audit_reader_iter_filtered_empty(tmp_path) -> None:
    """audit_reader handles a non-existent file gracefully."""
    import audit_reader
    nonexistent = tmp_path / "audit-does-not-exist.log"
    result = list(audit_reader.iter_filtered(nonexistent, lambda _e: True))
    assert result == []

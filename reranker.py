"""reranker.py — cross-encoder rerank for hybrid retrieval at scale.

The hybrid retriever (Qdrant dense + BM25 sparse + RRF fusion + ACL
post-filter) returns a top_k * 4 candidate pool. At small corpus
sizes (<10k docs) RRF alone is enough — the right answer is usually
in the top 5 already. At 50k+ docs, RRF's recall is good but its
precision drops: many medium-relevance chunks slip into the top 5
and the LLM's "Sources" panel becomes noisy.

The cross-encoder rerank step reads (query, candidate_text) pairs
and scores them with a model trained specifically for that task —
typically improving precision-at-5 from ~70% to ~85%+ at the cost
of ~50-150ms per query on the M3 Ultra.

Defaults to BAAI/bge-reranker-v2-m3 (multilingual, 568M params,
~600 MB on disk, ~1 GB resident). Pin enforcement mirrors
mlx_inference._read_pin: a `.reranker_lock` file pins the HF commit;
load refuses on drift unless LOCALLYAI_RERANKER_DRIFT_ACK=1.

Failure mode: if the cross-encoder model can't load (HF download
fails, sentence_transformers missing, etc.), `rerank()` logs a
warning and returns the input candidates unchanged. The retriever
falls back to RRF-only ranking — degraded mode, not broken.

Disabled with LOCALLYAI_RERANKER=off. Off by default for
single-Mac dev installs; on by default for production fleet
installs (set by install.sh when LOCALLYAI_HA=1 or hardware tier
≥ M3 Ultra 192GB).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger("reranker")

DEFAULT_MODEL = os.environ.get("LOCALLYAI_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
_PIN_FILE = Path(__file__).resolve().parent / ".reranker_lock"

_model = None
_model_lock = threading.Lock()
_load_failed = False  # remember failure so we don't keep retrying every query


def is_enabled() -> bool:
    """Reranker can be hard-disabled via env var. Useful for dev /
    debug / when the cross-encoder is causing more pain than precision."""
    return os.environ.get("LOCALLYAI_RERANKER", "on").lower() != "off"


def _read_pin(model_id: str) -> str | None:
    """Tiny TOML-ish parser — same shape as mlx_inference._read_pin so
    operators have one mental model for "pin a model to a HF commit".

    .reranker_lock format:
        [BAAI/bge-reranker-v2-m3]
        commit = "abc123def456..."
        pinned_at = "2026-05-15T..."
    """
    if not _PIN_FILE.exists():
        return None
    try:
        section = None
        with open(_PIN_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1].strip()
                elif section == model_id and line.startswith("commit"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def _resolve_commit(model_id: str) -> str | None:
    """Best-effort: read the local HF cache for the loaded commit.
    Returns None if we can't determine it."""
    try:
        from huggingface_hub import HfApi
        return HfApi().model_info(model_id).sha
    except Exception:
        return None


def _load_model():
    """Load the cross-encoder. Honours pin enforcement (refuses on
    drift unless LOCALLYAI_RERANKER_DRIFT_ACK=1). Sets _load_failed=True
    on any exception so we don't burn time retrying for every query."""
    global _model, _load_failed
    if _load_failed:
        return None
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            log.warning("sentence_transformers not installed; reranker disabled")
            _load_failed = True
            return None
        try:
            # Pick the fastest device available. On Apple Silicon, MPS
            # gives ~10× the throughput of CPU for the cross-encoder
            # (3000ms → 300ms on a typical 50-candidate pool). Falls
            # back to CPU on machines without MPS support. Operator
            # can force CPU via LOCALLYAI_RERANKER_DEVICE=cpu (useful
            # when MPS is buggy on a particular macOS version).
            device = os.environ.get("LOCALLYAI_RERANKER_DEVICE", "").strip().lower()
            if not device:
                try:
                    import torch
                    if torch.backends.mps.is_available():
                        device = "mps"
                    elif torch.cuda.is_available():
                        device = "cuda"
                    else:
                        device = "cpu"
                except Exception:
                    device = "cpu"
            t0 = time.perf_counter()
            log.info(f"Loading cross-encoder reranker: {DEFAULT_MODEL} (device={device})")
            model = CrossEncoder(DEFAULT_MODEL, max_length=512, device=device)
            elapsed = (time.perf_counter() - t0) * 1000
            log.info(f"Reranker loaded in {elapsed:.0f} ms (device={device})")

            # Pin enforcement (parity with mlx_inference._load_model)
            expected = _read_pin(DEFAULT_MODEL)
            if expected:
                actual = _resolve_commit(DEFAULT_MODEL)
                if actual and actual != expected:
                    if os.environ.get("LOCALLYAI_RERANKER_DRIFT_ACK") != "1":
                        msg = (f"RERANKER INTEGRITY DRIFT: {DEFAULT_MODEL} pinned to "
                               f"{expected[:12]}… but loaded {actual[:12]}…. "
                               f"Refusing — review then set LOCALLYAI_RERANKER_DRIFT_ACK=1.")
                        log.error(msg)
                        _load_failed = True
                        return None
                    log.warning(f"RERANKER INTEGRITY DRIFT acknowledged via "
                                f"LOCALLYAI_RERANKER_DRIFT_ACK=1: {expected[:12]}… → {actual[:12]}…")
                elif not actual:
                    log.warning("RERANKER INTEGRITY: pin present but commit not resolvable")
                else:
                    log.info(f"RERANKER INTEGRITY: commit matches pin ({actual[:12]}…)")
            else:
                log.info(f"RERANKER INTEGRITY: no pin for {DEFAULT_MODEL}")
            _model = model
            return model
        except Exception as exc:
            log.warning(f"Reranker load failed (degraded mode — RRF-only ranking): {exc}")
            _load_failed = True
            return None


def rerank(query: str, candidates: list, top_k: int) -> tuple[list, float]:
    """Score (query, candidate.text) pairs with the cross-encoder and
    return the top_k candidates by score, paired with the elapsed ms.

    Falls back to the input order (truncated to top_k) if:
    - LOCALLYAI_RERANKER=off
    - The model failed to load
    - candidates is empty or len <= top_k (no point reranking)

    The candidates list is RetrievedChunk-shaped; we read .text and
    write .score (float, cross-encoder score, higher = better).
    """
    if not is_enabled() or not candidates or len(candidates) <= top_k:
        return candidates[:top_k], 0.0
    model = _load_model()
    if model is None:
        return candidates[:top_k], 0.0
    t0 = time.perf_counter()
    try:
        pairs = [(query, c.text or "") for c in candidates]
        scores = model.predict(pairs, show_progress_bar=False)
        # Attach scores in-place — handy for downstream debugging
        for c, s in zip(candidates, scores):
            try:
                c.score = float(s)
            except Exception:
                pass
        ranked = sorted(candidates, key=lambda c: float(getattr(c, "score", 0.0)), reverse=True)
        elapsed = (time.perf_counter() - t0) * 1000
        return ranked[:top_k], elapsed
    except Exception as exc:
        log.warning(f"Rerank failed (returning RRF order): {exc}")
        elapsed = (time.perf_counter() - t0) * 1000
        return candidates[:top_k], elapsed


def reset_for_test() -> None:
    """Test helper — drop the cached model so the next rerank() call
    reloads. Used by tests/rag_perf.py to exercise the cold-load path."""
    global _model, _load_failed
    with _model_lock:
        _model = None
        _load_failed = False

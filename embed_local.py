"""
embed_local.py - In-process embeddings, no HTTP server required.

Uses sentence-transformers, which on Apple Silicon runs on PyTorch's MPS
backend (the same Metal acceleration MLX uses, via PyTorch's well-tested
production stack). Bypasses Ollama, LM Studio, and any other external server
that has to be installed, configured, and kept running.

Activation:
    Add to .env:
        EMBED_BACKEND=local
        EMBED_MODEL=nomic-ai/nomic-embed-text-v1.5      # HuggingFace identifier

The model is downloaded to ~/.cache/huggingface/ on first request (~500 MB
for nomic-embed-text-v1.5; one-time, then served from cache).

Vector dimension for nomic-embed-text-v1.5 is 768 -- matches config.py's
default VECTOR_SIZE so no other changes are needed.
"""
import logging
import os

logger = logging.getLogger("locallyai.embed_local")

_model = None  # lazily-initialised SentenceTransformer instance


def _ensure_loaded() -> bool:
    """Load the model into memory on first use. Returns False if unavailable."""
    global _model
    if _model is not None:
        return True
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.error(
            "sentence-transformers not installed. Add it with:\n"
            "  .venv/bin/pip install sentence-transformers\n"
            "Then restart the service."
        )
        return False

    model_name = os.environ.get(
        "EMBED_MODEL_LOCAL",
        os.environ.get("EMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5"),
    )
    logger.info(f"Loading local embedding model: {model_name}")
    try:
        # trust_remote_code=True is required for nomic-embed-text-v1.5 (and
        # several other modern models) which ship custom inference code on HF.
        _model = SentenceTransformer(model_name, trust_remote_code=True)
        logger.info(f"Local embedding model loaded: {model_name}")
        return True
    except Exception as exc:
        logger.error(f"Failed to load local embedding model: {exc}")
        return False


def embed(text: str) -> list[float] | None:
    """Return the embedding vector for `text`, or None on failure."""
    if not _ensure_loaded():
        return None
    try:
        # convert_to_numpy=True returns a numpy.ndarray; .tolist() makes it
        # JSON-serialisable and identical in shape to what Qdrant expects.
        vec = _model.encode(text, convert_to_numpy=True, show_progress_bar=False)
        return vec.tolist()
    except Exception as exc:
        logger.error(f"Local embed failed: {exc}")
        return None


def embedding_dim() -> int | None:
    """Probe the loaded model for its output dimension. Useful for VECTOR_SIZE."""
    if not _ensure_loaded():
        return None
    try:
        return int(_model.get_sentence_embedding_dimension())
    except Exception:
        return None

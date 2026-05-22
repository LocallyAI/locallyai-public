import hashlib
import json
import logging
import os
import sys
import zlib
from pathlib import Path

import requests
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from config import (
    CANDIDATE_POOL,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COLLECTION_NAME,
    DATA_DIR,
    EMBED_MODEL,
    EXPAND_QUERIES,
    INGEST_STATE,
    LLM_BASE_URL,
    LLM_MODEL,
    LOG_DIR,
    OLLAMA_BASE_URL,
    STORAGE_DIR,
    SUPPORTED_EXTENSIONS,
    TOP_K,
    VECTOR_SIZE,
)

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "ingest.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("locallyai.ingest")


# File hashing for smart re-ingest
def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def load_state() -> dict:
    if INGEST_STATE.exists():
        try:
            return json.loads(INGEST_STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: dict):
    INGEST_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# Text extraction
def extract_text_pdf(path: Path) -> list[dict]:
    """Returns list of {text, page, section}.

    Round-2 B7: refuse PDFs over LOCALLYAI_MAX_PDF_BYTES (default 100 MiB)
    or LOCALLYAI_MAX_PDF_PAGES (default 1000). pymupdf has had CVEs for
    malformed PDFs and an attacker-uploaded mega-PDF can OOM the worker.
    """
    try:
        max_bytes = int(os.environ.get("LOCALLYAI_MAX_PDF_BYTES", str(100 * 1024 * 1024)))
        max_pages = int(os.environ.get("LOCALLYAI_MAX_PDF_PAGES", "1000"))
        size = path.stat().st_size
        if size > max_bytes:
            logger.error(f"PDF too large: {path.name} ({size} bytes > {max_bytes}); skipping")
            return []
        import fitz
        doc = fitz.open(str(path))
        if doc.page_count > max_pages:
            logger.error(f"PDF too many pages: {path.name} ({doc.page_count} > {max_pages}); skipping")
            doc.close()
            return []
        pages = []
        for i, page in enumerate(doc):
            text = page.get_text("text").strip()
            if text:
                pages.append({"text": text, "page": i + 1, "section": ""})
        return pages
    except ImportError:
        logger.error("pymupdf not installed. Install with: pip install pymupdf")
        return []


def extract_text_docx(path: Path) -> list[dict]:
    try:
        from docx import Document
        doc = Document(str(path))
        chunks = []
        current_section = ""
        current_text = []
        for para in doc.paragraphs:
            if para.style.name.startswith("Heading"):
                if current_text:
                    chunks.append({"text": " ".join(current_text), "page": None, "section": current_section})
                    current_text = []
                current_section = para.text.strip()
            elif para.text.strip():
                current_text.append(para.text.strip())
        if current_text:
            chunks.append({"text": " ".join(current_text), "page": None, "section": current_section})
        return chunks
    except ImportError:
        logger.error("python-docx not installed.")
        return []


def extract_text_plain(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []
    return [{"text": text, "page": None, "section": ""}]


def extract(path: Path) -> list[dict]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_text_pdf(path)
    elif ext == ".docx":
        return extract_text_docx(path)
    elif ext in (".txt", ".md"):
        return extract_text_plain(path)
    return []


# Chunking
def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + size])
        if chunk.strip():
            chunks.append(chunk)
        i += size - overlap
    return chunks


# Embedding
def embed(text: str) -> list[float] | None:
    """Embeddings via in-process model (EMBED_BACKEND=local) or OpenAI-compatible
    HTTP server (default). Local mode bypasses Ollama / LM Studio entirely and
    runs sentence-transformers on PyTorch's MPS backend (Apple Silicon)."""
    # In-process path: zero HTTP, zero external server.
    # Round-2 B8: when EMBED_BACKEND=local is explicit, refuse the HTTP
    # fallback. Mixed-source vectors in Qdrant (e.g. KSA firms swapping
    # multilingual e5-base for an English-only model) silently break
    # retrieval; failing loud is the only safe behaviour.
    if os.environ.get("EMBED_BACKEND", "http").lower() == "local":
        try:
            from embed_local import embed as embed_local
            v = embed_local(text)
            if v is not None:
                return v
            logger.error("Local embed returned None; refusing HTTP fallback (EMBED_BACKEND=local)")
            return None
        except Exception as exc:
            logger.error(f"Local embed failed; refusing HTTP fallback (EMBED_BACKEND=local): {exc}")
            return None

    # Try OpenAI-compatible endpoint first
    try:
        resp = requests.post(
            f"{LLM_BASE_URL}/v1/embeddings",
            json={"model": EMBED_MODEL, "input": text},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            arr = data.get("data", [])
            if arr and "embedding" in arr[0]:
                return arr[0]["embedding"]
    except Exception:
        pass
    # Fall back to Ollama-native (older Ollama installs)
    try:
        resp = requests.post(
            f"{LLM_BASE_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        return None


# Qdrant helpers
def ensure_collection(client: QdrantClient):
    cols = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in cols:
        # In HA mode (LOCALLYAI_HA=1, QDRANT_URLS pointing at ≥2 peers) we
        # ask Qdrant for shard_number=2 with replication_factor=2 so every
        # shard lives on both nodes — survives 1-node loss for reads. Write
        # consistency factor 2 means every write must be acknowledged by
        # both replicas; a partition turns the cluster read-only rather
        # than allowing silent divergence (matches the per-node-chains
        # design — no quietly-lost data, operator notices fast).
        from config import HA_ENABLED
        kwargs = dict(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        if HA_ENABLED:
            kwargs.update(
                shard_number=2,
                replication_factor=2,
                write_consistency_factor=2,
            )
        client.create_collection(**kwargs)
        logger.info(f"Collection created: {COLLECTION_NAME} (HA={HA_ENABLED})")
    else:
        logger.info(f"Collection exists: {COLLECTION_NAME}")


def ingest_file(client: QdrantClient, path: Path, source_name: str) -> int:
    pages = extract(path)
    if not pages:
        logger.warning(f"No text extracted from {path.name}")
        return 0

    points = []
    point_id = abs(zlib.crc32(source_name.encode())) * 10000

    for page_data in pages:
        raw_text = page_data["text"]
        page_num = page_data.get("page")
        section = page_data.get("section", "")
        chunks = chunk_text(raw_text)
        # Stamp the document's ACL into every chunk's payload so dense
        # retrieval can filter at query time without an extra round-trip.
        # Default is wildcard ['*'] (everyone in the firm) — preserves
        # behaviour for installs that don't use ACLs. Admins can change
        # the ACL post-ingest via /v1/documents/{name}/acl which calls
        # back into Qdrant to update the stamped payload of every chunk
        # belonging to this source.
        from doc_acls import get_acl as _get_acl
        _acl = _get_acl(source_name)
        _allowed = list(_acl.get("allowed_users", ["*"]))
        _matter_code = _acl.get("matter_code", "")
        for chunk in chunks:
            vector = embed(chunk)
            if vector is None:
                continue
            points.append(PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "text": chunk,
                    "source": source_name,
                    "section": section,
                    "page": page_num,
                    "allowed_users": _allowed,
                    "matter_code": _matter_code,
                },
            ))
            point_id += 1

    if points:
        batch_size = 64
        for i in range(0, len(points), batch_size):
            client.upsert(collection_name=COLLECTION_NAME, points=points[i:i + batch_size])
        logger.info(f"  {path.name}: {len(points)} vectors upserted")

    return len(points)


# BM25 rebuild
def rebuild_bm25(client: QdrantClient):
    """Rebuild BM25 index from Qdrant after ingestion."""
    logger.info("Rebuilding BM25 index...")
    try:
        from retrieval import HybridRetrievalEngine
        engine = HybridRetrievalEngine(
            qdrant=client,
            collection_name=COLLECTION_NAME,
            ollama_url=OLLAMA_BASE_URL,
            embed_model=EMBED_MODEL,
            llm_model=LLM_MODEL,
            storage_dir=STORAGE_DIR,
            top_k=TOP_K,
            candidate_pool=CANDIDATE_POOL,
            expand_queries=EXPAND_QUERIES,
        )
        engine.rebuild_bm25()
        logger.info("BM25 index rebuilt and saved.")
    except Exception as e:
        logger.error(f"BM25 rebuild failed: {e}")


# Main
def run(force: bool = False):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    from config import make_qdrant_client
    client = make_qdrant_client()
    ensure_collection(client)

    state = {} if force else load_state()
    files = [f for f in DATA_DIR.rglob("*") if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS]

    if not files:
        logger.warning(f"No supported files found in {DATA_DIR}")
        logger.warning(f"Supported: {SUPPORTED_EXTENSIONS}")
        return

    total_new = 0
    skipped = 0

    for path in sorted(files):
        rel = str(path.relative_to(DATA_DIR))
        fh = file_hash(path)
        if not force and state.get(rel) == fh:
            logger.info(f"  SKIP (unchanged): {rel}")
            skipped += 1
            continue

        logger.info(f"  Ingesting: {rel}")
        n = ingest_file(client, path, rel)
        total_new += n
        state[rel] = fh
        save_state(state)

    info = client.get_collection(COLLECTION_NAME)
    # qdrant-client renamed `vectors_count` -> `points_count` in newer releases.
    # Fall back through both so this line works across client versions.
    total_in_collection = (
        getattr(info, "points_count", None)
        or getattr(info, "vectors_count", None)
        or "?"
    )
    logger.info(f"Ingest complete. New vectors: {total_new} | Skipped files: {skipped} | Total in collection: {total_in_collection}")

    if total_new > 0:
        rebuild_bm25(client)
    else:
        logger.info("No new vectors - BM25 rebuild skipped.")


if __name__ == "__main__":
    force = "--force" in sys.argv
    if force:
        logger.info("Force mode: re-ingesting all files.")
    run(force=force)

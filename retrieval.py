"""
retrieval.py — Hybrid retrieval for LocallyAI RAG pipeline
Combines Qdrant (dense vector) + BM25 (sparse keyword) with RRF fusion
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from bm25 import BM25Index  # local module

logger = logging.getLogger("locallyai.retrieval")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    score: float
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------

def _rrf(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Combine multiple ranked lists into a single score via RRF."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


# ---------------------------------------------------------------------------
# Hybrid retriever
# ---------------------------------------------------------------------------

class HybridRetriever:
    def __init__(
        self,
        qdrant_path: str,
        collection_name: str,
        bm25_index: BM25Index,
        top_k: int = 10,
        rrf_k: int = 60,
    ) -> None:
        from config import make_qdrant_client
        self.client = make_qdrant_client()
        self.collection = collection_name
        self.bm25 = bm25_index
        self.top_k = top_k
        self.rrf_k = rrf_k

    def retrieve(
        self,
        query: str,
        query_vector: list[float],
        firm_id: str | None = None,
        user: str | None = None,
        matter_code: str | None = None,
    ) -> list[RetrievedChunk]:
        t_start = time.perf_counter()
        from config import CANDIDATE_POOL

        # --- Dense retrieval (Qdrant) ---
        # firm_id (+ optional matter_code) filter is pushed to Qdrant
        # server-side. ACL filter is applied AFTER fusion (post-filter
        # via doc_acls.is_allowed) — back-compat correctness for legacy
        # chunks without allowed_users payload field.
        # We over-fetch CANDIDATE_POOL chunks (default 50) so the
        # subsequent ACL drop + cross-encoder rerank have enough
        # signal; final result trimmed to top_k.
        must = []
        if firm_id:
            must.append(FieldCondition(key="firm_id", match=MatchValue(value=firm_id)))
        if matter_code:
            # Push the matter scope to Qdrant — significant precision
            # win for firms that classify queries by matter (per the
            # ACL chapter). Chunks without matter_code payload field
            # (legacy ingest) won't match — for matter-scoped queries
            # that's the right behaviour.
            must.append(FieldCondition(key="matter_code", match=MatchValue(value=matter_code)))
        filt = Filter(must=must) if must else None

        dense_limit = max(self.top_k * 4, CANDIDATE_POOL)
        t_dense = time.perf_counter()
        qdrant_resp = self.client.query_points(
            collection_name=self.collection,
            query=query_vector,
            limit=dense_limit,
            query_filter=filt,
            with_payload=True,
        )
        dense_ms = (time.perf_counter() - t_dense) * 1000
        qdrant_hits = qdrant_resp.points
        qdrant_ranking = [str(h.id) for h in qdrant_hits]
        qdrant_map = {str(h.id): h for h in qdrant_hits}

        # --- Sparse retrieval (BM25) ---
        t_bm25 = time.perf_counter()
        bm25_results = self.bm25.search(query, top_k=max(self.top_k * 2, CANDIDATE_POOL // 2), firm_id=firm_id)
        bm25_ranking = [r["chunk_id"] for r in bm25_results]
        bm25_map = {r["chunk_id"]: r for r in bm25_results}
        bm25_ms = (time.perf_counter() - t_bm25) * 1000

        # --- RRF fusion ---
        t_rrf = time.perf_counter()
        fused = _rrf([qdrant_ranking, bm25_ranking], k=self.rrf_k)
        candidate_ids = sorted(fused, key=lambda x: fused[x], reverse=True)[:CANDIDATE_POOL]
        rrf_ms = (time.perf_counter() - t_rrf) * 1000

        # --- Build candidate result list ---
        candidates: list[RetrievedChunk] = []
        for cid in candidate_ids:
            if cid in qdrant_map:
                h = qdrant_map[cid]
                payload = h.payload or {}
                candidates.append(
                    RetrievedChunk(
                        chunk_id=cid,
                        text=payload.get("text", ""),
                        score=fused[cid],
                        source=payload.get("source", ""),
                        metadata=payload,
                    )
                )
            elif cid in bm25_map:
                r = bm25_map[cid]
                candidates.append(
                    RetrievedChunk(
                        chunk_id=cid,
                        text=r.get("text", ""),
                        score=fused[cid],
                        source=r.get("source", ""),
                        metadata=r,
                    )
                )

        # --- ACL post-filter (cheap drop BEFORE the expensive rerank) ---
        # Order matters: ACL drop saves cross-encoder cost on chunks
        # the user can't see. doc_acls.is_allowed treats missing-ACL
        # docs as the default-open policy (back-compat).
        t_acl = time.perf_counter()
        if user and user != "admin":
            from doc_acls import is_allowed as _is_allowed
            allowed = [c for c in candidates if _is_allowed(c.source, user)]
        else:
            allowed = candidates
        acl_dropped = len(candidates) - len(allowed)
        acl_ms = (time.perf_counter() - t_acl) * 1000

        # --- Cross-encoder rerank ---
        # Turns the top-CANDIDATE_POOL candidates into the final top_k
        # with materially better precision (recall@5 goes from ~70%
        # to ~85%+ on a 50k-chunk corpus). Falls back to RRF order if
        # the cross-encoder isn't loadable (logged warning, degraded
        # mode, not broken).
        from reranker import rerank as _rerank
        chunks, rerank_ms = _rerank(query, allowed, self.top_k)

        elapsed = (time.perf_counter() - t_start) * 1000
        # Stash phase-level timings on a module-global for the
        # /admin/health/detailed endpoint to surface. Last-call only —
        # operator can tune CANDIDATE_POOL / TOP_K based on these.
        global _last_retrieve_timings
        _last_retrieve_timings = {
            "total_ms":       round(elapsed, 1),
            "dense_ms":       round(dense_ms, 1),
            "bm25_ms":        round(bm25_ms, 1),
            "rrf_ms":         round(rrf_ms, 1),
            "acl_ms":         round(acl_ms, 1),
            "rerank_ms":      round(rerank_ms, 1),
            "acl_dropped":    acl_dropped,
            "candidate_pool": len(candidates),
            "returned":       len(chunks),
        }
        logger.info(
            f"hybrid retrieve: {len(chunks)} chunks in {elapsed:.0f}ms "
            f"(dense={dense_ms:.0f} bm25={bm25_ms:.0f} rrf={rrf_ms:.0f} "
            f"acl={acl_ms:.0f}drop{acl_dropped} rerank={rerank_ms:.0f})"
        )
        return chunks


# ---------------------------------------------------------------------------
# HybridRetrievalEngine — used by ingest.py to rebuild the BM25 index
# ---------------------------------------------------------------------------

class HybridRetrievalEngine:
    """Wraps a live QdrantClient to rebuild the BM25 index after ingestion."""

    def __init__(
        self,
        qdrant: QdrantClient,
        collection_name: str,
        storage_dir: str,
        **_kwargs: Any,  # absorb ollama_url, embed_model, etc. from ingest.py
    ) -> None:
        self._qdrant     = qdrant
        self._collection = collection_name
        self._storage    = Path(storage_dir)

    def rebuild_bm25(self) -> int:
        """Scroll all Qdrant points, build BM25 index, save to disk. Returns doc count."""
        documents: list[dict] = []
        offset = None
        while True:
            result, offset = self._qdrant.scroll(
                collection_name=self._collection,
                limit=500,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in result:
                payload = point.payload or {}
                if payload.get("text"):
                    documents.append({
                        "chunk_id": str(point.id),
                        "text":     payload["text"],
                        "source":   payload.get("source", ""),
                        "firm_id":  payload.get("firm_id", ""),
                    })
            if offset is None:
                break

        index = BM25Index(str(self._storage))
        index.build(documents)
        index.save()
        return len(documents)


# ---------------------------------------------------------------------------
# Module-level singleton with mtime-based cache invalidation
# ---------------------------------------------------------------------------

_retriever: HybridRetriever | None = None
_bm25:      BM25Index | None       = None
_bm25_mtime: float                    = 0.0

# Last-call retrieve timings (dense_ms, bm25_ms, rrf_ms, acl_ms,
# rerank_ms, candidate_pool, returned, acl_dropped). Surfaced by
# /admin/health/detailed so operators can tune CANDIDATE_POOL / TOP_K
# / reranker model based on actual production p50 / p95.
_last_retrieve_timings: dict = {}


def get_last_retrieve_timings() -> dict:
    """Return last-call retrieve timings — used by the monitor router
    + sentinel + ad-hoc /admin/health/detailed inspection."""
    return dict(_last_retrieve_timings)


def _get_retriever() -> HybridRetriever | None:
    global _retriever, _bm25, _bm25_mtime
    from config import COLLECTION_NAME, STORAGE_DIR, TOP_K
    bm25_path = Path(str(STORAGE_DIR)) / "bm25_index.json"
    if not bm25_path.exists():
        return None
    current_mtime = bm25_path.stat().st_mtime
    if _retriever is None or current_mtime > _bm25_mtime:
        _bm25      = BM25Index(str(STORAGE_DIR))
        _retriever = HybridRetriever(
            qdrant_path=str(STORAGE_DIR),
            collection_name=COLLECTION_NAME,
            bm25_index=_bm25,
            top_k=TOP_K,
        )
        _bm25_mtime = current_mtime
    return _retriever


def _embed_query(query: str) -> list[float] | None:
    """In-process model (EMBED_BACKEND=local) or OpenAI-compatible HTTP."""
    import os as _os

    from config import EMBED_MODEL, LLM_BASE_URL
    # In-process path: no HTTP server required
    if _os.environ.get("EMBED_BACKEND", "http").lower() == "local":
        try:
            from embed_local import embed as embed_local
            v = embed_local(query)
            if v is not None:
                return v
        except Exception:
            pass  # fall through to HTTP

    # Try OpenAI-compatible /v1/embeddings (Ollama >=0.1.46, LM Studio, vLLM, OpenAI)
    try:
        payload = json.dumps({"model": EMBED_MODEL, "input": query}).encode()
        req = urllib.request.Request(
            f"{LLM_BASE_URL}/v1/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        arr = data.get("data", [])
        if arr and "embedding" in arr[0]:
            return arr[0]["embedding"]
    except Exception:
        pass
    # Fallback for older Ollama installs
    try:
        payload = json.dumps({"model": EMBED_MODEL, "prompt": query}).encode()
        req = urllib.request.Request(
            f"{LLM_BASE_URL}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["embedding"]
    except Exception:
        return None


def retrieve(query: str, user: str | None = None,
             matter_code: str | None = None) -> list[dict]:
    """Top-level function called by api.py. Returns
    [{chunk_id, text, source, section, page, score}, ...] — chunk_id
    gives the UIs a stable key for citation lists and click-through.

    `user` enables per-doc ACL filtering. None or 'admin' returns the full
    corpus; any other value scopes to documents that user can access
    per doc_acls.is_allowed.

    `matter_code` (optional) scopes the dense candidate pool to chunks
    whose payload.matter_code matches — significant precision win for
    firms that classify queries by matter."""
    try:
        retriever = _get_retriever()
        if retriever is None:
            return []
        vector = _embed_query(query)
        if vector is None:
            return []
        chunks = retriever.retrieve(query, vector, user=user, matter_code=matter_code)
        out = []
        for c in chunks:
            md = c.metadata or {}
            out.append({
                "chunk_id": c.chunk_id,
                "text":     c.text,
                "source":   c.source,
                "score":    c.score,
                # Surface the structural fields so worker-ui can show
                # "page 12, §3.4" alongside the snippet AND so the
                # "Open document" button can deep-link with #page=N.
                "section":  md.get("section", "") or "",
                "page":     md.get("page"),  # int or None
            })
        return out
    except Exception:
        return []

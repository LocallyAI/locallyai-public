"""Document/upload/ingest/conflict/compare/citations endpoints.

PR-3 of the api.py → api/ refactor: extracted from api/__init__.py.

Exposes a `router = APIRouter()` that api/__init__.py mounts via
`app.include_router(router)`. Routes are mounted WITHOUT a prefix so paths
remain identical to the monolith (`/v1/documents`, `/v1/uploads`, …).

The `_UPLOAD_DIR` constant lives here (with its domain). The startup handler
in api/__init__.py lazy-imports it to call `.mkdir(...)` — that avoids a
circular import at module load (api package → api.documents → api).
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid as _uuid
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import chunked_uploads as _cu

# `from api import …` requires that api/__init__.py has executed past the
# definitions we depend on (`limiter`) before it runs
# `from api.documents import router`. __init__.py defines `limiter` well
# before its `from api.documents import …` line, so this resolves cleanly.
from api import limiter
from api._shared import (
    _CHAIN_STATE_FILE,
    AUDIT_LOG,
    _admin_auth,
    _auth,
    _chain_hmac,
    _chain_lock,
    _prev_hash,
    _write_audit,
)
from config import COLLECTION_NAME, pseudonymise_user
from config import NODE_ID as _NODE_ID
from ingest_queue import get_queue as _get_ingest_queue

log = logging.getLogger("api")

router = APIRouter()

# ── Document ingest ────────────────────────────────────────────────────────────
# Two upload paths feed one queue:
#   /v1/ingest         — single-shot, capped at 50 MiB. Kept for back-compat
#                        with worker-ui clients that haven't switched to
#                        the chunked protocol yet, and for ad-hoc curl use.
#   /v1/uploads/...    — chunked/resumable, supports gigabyte-scale corpora.
# Both write to storage/uploads/ and enqueue via ingest_queue.get_queue().
# PR-1: __file__ moved from `<repo>/api.py` to `<repo>/api/__init__.py`,
# so `.parent` is now the package dir; bump up to the repo root.
# The mkdir is deferred to the `_init_runtime_paths` startup handler in
# api/__init__.py (which lazy-imports this constant) so module import has
# zero filesystem side effects.
_UPLOAD_DIR       = Path(__file__).resolve().parent.parent / "storage" / "uploads"
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_ALLOWED_EXTS     = {".pdf", ".txt", ".md", ".docx"}


def _index_document(path: Path, source_name: str):
    """Compatibility shim: route through the queue rather than the old
    single-lock. /v1/ingest still calls this via background_tasks."""
    _get_ingest_queue().submit(path, source_name)


@router.get("/v1/documents")
@limiter.limit("60/minute")
def list_documents(request: Request, user: str = Depends(_auth)):
    """Return the documents the firm has ingested. Backs the worker UI's
    'Recent documents' panel so users can see at a glance what's in the
    corpus they're querying. Reads .ingest_state.json (the file-hash
    state file ingest.py maintains) — single source of truth for what
    has been indexed.

    Per-document fields: name, size_bytes, ingested_at (file mtime in
    data/), suffix (file type for the UI to pick an icon). We expose
    only the metadata needed for the UI; chunk text and vectors stay
    server-side."""
    from pathlib import Path
    state_path = Path(__file__).resolve().parent / ".ingest_state.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            state = {}

    # Files arrive via two paths and we surface both:
    #   - storage/uploads/  — UI-ingested files, named "<uuid>_<orig>"
    #     (UUID strip for display only; the on-disk + state-key name
    #     keeps the prefix to prevent same-name collisions).
    #   - data/             — seed/demo corpus + bulk-ingest path.
    # Both sources flow through the same ingest pipeline; from the
    # querying user's perspective they're one corpus.
    docs = []
    seen = set()
    base = Path(__file__).resolve().parent

    def add(p: Path, *, name: str, indexed_key: str):
        if name in seen:
            return
        try:
            st = p.stat()
        except OSError:
            return
        seen.add(name)
        docs.append({
            "name":         name,
            "size_bytes":   st.st_size,
            "ingested_at":  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
            "suffix":       p.suffix.lower().lstrip("."),
            "indexed":      indexed_key in state,
        })

    uploads_dir = base / "storage" / "uploads"
    # UUID prefix from /v1/ingest is uuid4 string form (36 chars, hyphens);
    # from /v1/uploads/.../complete it's uuid4().hex (32 chars). Match either.
    import re as _re
    uuid_prefix = _re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}_")
    if uploads_dir.exists():
        for p in uploads_dir.iterdir():
            if not p.is_file() or p.name.startswith("."):
                continue
            m = uuid_prefix.match(p.name)
            display = p.name[m.end():] if m else p.name
            add(p, name=display, indexed_key=p.name)

    data_dir = base / "data"
    if data_dir.exists():
        for p in data_dir.iterdir():
            if not p.is_file() or p.name.startswith("."):
                continue
            add(p, name=p.name, indexed_key=p.name)

    docs.sort(key=lambda d: d["ingested_at"], reverse=True)
    return {"object": "list", "data": docs, "count": len(docs)}


@router.delete("/v1/documents/{display_name}")
@limiter.limit("30/minute")
def delete_document(display_name: str, request: Request, key: str = Depends(_admin_auth)):
    """Forget a document everywhere it lives:
       1. The file in storage/uploads/ (UI-ingested) or data/ (seed).
       2. Every Qdrant point whose payload.source matches.
       3. The .ingest_state.json entry (so a future bulk re-ingest
          treats this filename as fresh if it reappears).
       4. Schedule a BM25 rebuild via the indexing queue (batched —
          if other deletes are in flight they share one rebuild).

    Admin-only (the manager UI's bearer is the LOCALLYAI_ADMIN_KEY).
    Worker-tier users cannot delete; this is a corpus-wide operation
    audited as such.

    The display_name comes from /v1/documents which strips the UUID
    prefix from chunked-upload files for readability. We map back to
    the on-disk name by either (a) finding a file in data/ with that
    exact name, or (b) finding a file in storage/uploads/ whose
    name ends with "_<display_name>" after the UUID prefix.
    """
    import re as _re
    from pathlib import Path
    base = Path(__file__).resolve().parent

    # Path-traversal hardening — same logic as upload validation.
    safe = Path(display_name).name
    if not safe or safe in (".", "..") or "/" in safe or "\\" in safe:
        raise HTTPException(400, "Invalid filename")
    if any(ord(c) < 32 for c in safe):
        raise HTTPException(400, "Invalid filename")

    # Locate the on-disk file. Prefer storage/uploads/ (live corpus)
    # over data/ (seed) because operators usually want to remove
    # client-uploaded material, not the demo set.
    uuid_re = _re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}_")
    on_disk: Path | None = None
    source_key: str | None = None  # the value stored in Qdrant payload.source

    uploads_dir = base / "storage" / "uploads"
    if uploads_dir.exists():
        for p in uploads_dir.iterdir():
            if not p.is_file() or p.name.startswith("."):
                continue
            m = uuid_re.match(p.name)
            stripped = p.name[m.end():] if m else p.name
            if stripped == safe:
                on_disk = p
                source_key = p.name
                break

    if on_disk is None:
        data_dir = base / "data"
        candidate = data_dir / safe
        if candidate.exists() and candidate.is_file():
            on_disk = candidate
            source_key = safe

    if on_disk is None:
        raise HTTPException(404, f"Document not found: {safe}")

    # Containment check — paranoid; safe was already sanitised above.
    allowed_roots = [(base / "storage" / "uploads").resolve(), (base / "data").resolve()]
    resolved = on_disk.resolve()
    if not any(str(resolved).startswith(str(r) + os.sep) or resolved == r for r in allowed_roots):
        raise HTTPException(400, "Path traversal detected")

    # 1. Drop every Qdrant point with payload.source == source_key.
    #    Filter-based delete is one round-trip and works whether the
    #    doc had 1 chunk or 10 000.
    qdrant_dropped = 0
    try:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

        from config import make_qdrant_client
        client = make_qdrant_client()
        flt = Filter(must=[FieldCondition(key="source", match=MatchValue(value=source_key))])
        # Count first so we can audit-log how much was removed.
        try:
            qdrant_dropped = client.count(collection_name=COLLECTION_NAME, count_filter=flt, exact=True).count
        except Exception:
            qdrant_dropped = -1  # unknown — collection may not exist yet
        client.delete(collection_name=COLLECTION_NAME, points_selector=FilterSelector(filter=flt))
    except Exception as exc:
        log.warning("Qdrant deletion failed for %s: %s", source_key, exc)

    # 2. Remove from .ingest_state.json so a re-upload of the same
    #    filename gets indexed as fresh (file_hash check would otherwise
    #    skip it as "unchanged").
    state_path = base / ".ingest_state.json"
    state_changed = False
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if source_key in state:
                del state[source_key]
                state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
                state_changed = True
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not update ingest state for %s: %s", source_key, exc)

    # 3. Delete the file. If the unlink fails, the operator gets a
    #    clear error and the UI can show what's wrong — we DON'T
    #    silently leave a half-deleted state.
    try:
        on_disk.unlink()
    except OSError as exc:
        raise HTTPException(500, f"Could not delete file on disk: {exc}")

    # 4. Mark BM25 dirty + queue a rebuild. The queue's quiet timer
    #    coalesces multiple deletes into one rebuild.
    try:
        _get_ingest_queue().flush()  # immediate rebuild — operator just deleted
    except Exception as exc:
        log.warning("BM25 rebuild after delete failed (will retry): %s", exc)

    # 5. Audit the deletion via the HMAC-chained log. GDPR Art. 5(1)(e)
    #    storage limitation + Art. 17 erasure both want a record of
    #    corpus-affecting deletions. ISO 27001 A.8.10 information
    #    deletion. Mirrors the chain-write pattern manage_users uses
    #    for admin_key_rotation — system event, no user_hash field.
    try:
        from config import DATA_REGION, current_salt_era
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        admin_entry = {
            "timestamp":             ts,
            "node_id":               _NODE_ID,
            "data_region":           DATA_REGION,
            "salt_era":              current_salt_era(),
            "event":                 "document_deleted",
            "deleted_by":            "admin",
            "filename":              safe,
            "stored_as":             source_key,
            "qdrant_points_removed": qdrant_dropped,
            "ingest_state_updated":  state_changed,
            "regulation":            "GDPR art. 5(1)(e) / art. 17, ISO 27001 A.8.10",
        }
        with _chain_lock:
            prev = _prev_hash()
            entry_json = json.dumps(admin_entry, sort_keys=True)
            chain = _chain_hmac(entry_json, prev)
            if chain:
                admin_entry["_chain_hmac"] = chain
            with open(AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(admin_entry) + "\n")
            if chain:
                _CHAIN_STATE_FILE.write_text(chain, encoding="utf-8")
    except Exception as exc:
        log.warning("Audit write for document_deleted failed: %s", exc)

    return {
        "status":                "deleted",
        "filename":              safe,
        "qdrant_points_removed": qdrant_dropped,
        "ingest_state_updated":  state_changed,
    }


# ── Per-document ACL endpoints ─────────────────────────────────────────────
# Per-doc access control. Backed by doc_acls.py (file at SHARED_DIR with
# fcntl.flock). When an ACL is set/changed via PUT, this endpoint also
# updates Qdrant payloads for every chunk of that document so dense
# retrieval can short-circuit at query time. The post-filter in
# retrieval.py is the canonical authority — Qdrant payload updates here
# are an optimisation, not a security boundary.
#
# Default policy: documents not in doc_acls.json are treated as
# allowed_users=["*"] (everyone in the firm). Set
# LOCALLYAI_DOC_ACL_DEFAULT=restricted in .env to flip the default to
# "no one until explicitly granted" for firms with strict access control.

@router.get("/v1/documents/{display_name}/acl")
@limiter.limit("60/minute")
def get_document_acl(display_name: str, request: Request, key: str = Depends(_admin_auth)):
    """Return the ACL for a single document. Returns the default-open
    policy when no explicit ACL has been set."""
    from doc_acls import get_acl as _get_acl
    return _get_acl(display_name)


@router.get("/v1/documents/acls")
@limiter.limit("60/minute")
def list_document_acls(request: Request, key: str = Depends(_admin_auth)):
    """Bulk: return every explicit ACL entry. Documents without an
    explicit entry don't appear here (they fall back to default-open)."""
    from doc_acls import list_acls as _list_acls
    return {"acls": _list_acls()}


class _AclSetReq(BaseModel):
    allowed_users: list[str] = Field(default_factory=list,
                                     description='List of usernames or "*" for everyone-in-firm')
    matter_code:   str       = Field(default="", max_length=64)
    ethical_wall:  list[str] = Field(default_factory=list,
                                     description="Optional ethical-wall group tags (informational)")


@router.put("/v1/documents/{display_name}/acl")
@limiter.limit("30/minute")
def set_document_acl(display_name: str, req: _AclSetReq, request: Request,
                     key: str = Depends(_admin_auth)):
    """Set/replace the ACL for a document. Updates Qdrant payloads for
    every chunk of that document so dense retrieval reflects the change
    immediately. The shared doc_acls.json is the source of truth; the
    Qdrant payloads are an optimisation."""
    from doc_acls import set_acl as _set_acl
    entry = _set_acl(
        source_name=display_name,
        allowed_users=req.allowed_users,
        matter_code=req.matter_code,
        ethical_wall=req.ethical_wall,
        set_by="admin",
    )
    # Push the change into Qdrant payloads for live retrieval
    chunks_updated = _update_chunk_acl_payloads(display_name, entry)
    # Audit-log the change so the DPO has provenance
    _write_audit(
        user="admin", model="-", sources=0, latency_ms=0,
        query_hash="", matter_code=req.matter_code,
    )
    return {
        "ok": True,
        "acl": entry,
        "chunks_updated": chunks_updated,
    }


@router.delete("/v1/documents/{display_name}/acl")
@limiter.limit("30/minute")
def delete_document_acl(display_name: str, request: Request, key: str = Depends(_admin_auth)):
    """Remove the explicit ACL — document falls back to default policy."""
    from doc_acls import delete_acl as _delete_acl
    from doc_acls import get_acl as _get_acl
    removed = _delete_acl(display_name)
    if removed:
        # Reset chunk payloads to the default policy
        default_entry = _get_acl(display_name)
        _update_chunk_acl_payloads(display_name, default_entry)
    return {"ok": True, "removed": removed}


def _update_chunk_acl_payloads(display_name: str, acl_entry: dict) -> int:
    """Update payload.allowed_users + payload.matter_code on every
    Qdrant chunk whose payload.source matches `display_name`. Used
    after an ACL change so live retrieval reflects the new policy
    without re-ingesting the document.

    Returns the number of chunks updated. Errors are non-fatal — the
    canonical ACL is in doc_acls.json; Qdrant payload is an optimisation
    and the post-filter in retrieval.py would still apply the policy."""
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        from config import COLLECTION_NAME, QDRANT_URL, STORAGE_DIR
        client = QdrantClient(url=QDRANT_URL) if QDRANT_URL else QdrantClient(path=str(STORAGE_DIR))
        flt = Filter(must=[FieldCondition(key="source", match=MatchValue(value=display_name))])
        # Count chunks that match (single scroll pass; we don't need the data)
        n = 0
        offset = None
        while True:
            res, offset = client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=flt,
                limit=500,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            n += len(res)
            if offset is None:
                break
        if n == 0:
            return 0
        client.set_payload(
            collection_name=COLLECTION_NAME,
            payload={
                "allowed_users": list(acl_entry.get("allowed_users", ["*"])),
                "matter_code":   acl_entry.get("matter_code", ""),
            },
            points_selector=flt,
        )
        return n
    except Exception as exc:
        log.warning(f"_update_chunk_acl_payloads failed for {display_name!r}: {exc}")
        return 0


# ── Conflict checks ────────────────────────────────────────────────────────
# New-matter intake conflict-of-interest checker. Backend in conflicts.py;
# this endpoint wraps with auth + audit + sanitisation.

class _ConflictParty(BaseModel):
    role: str = Field(default="interested", pattern="^(client|opposing|interested|opposing-counsel)$")
    name: str = Field(..., min_length=1, max_length=200)


class _ConflictCheckReq(BaseModel):
    parties:          list[_ConflictParty] = Field(..., min_length=1, max_length=20)
    description:      str = Field(default="", max_length=2000)
    opposing_counsel: list[str] = Field(default_factory=list, max_length=20)
    matter_id:        str | None = Field(default=None, max_length=64)


@router.post("/v1/conflicts/check")
@limiter.limit("30/minute")
def conflicts_check(req: _ConflictCheckReq, request: Request, key: str = Depends(_admin_auth)):
    """Run a conflict-of-interest check. Admin-only in v1 (likely
    partner-only in production once we add user roles)."""
    from conflicts import check as _check
    parties_dicts = [{"role": p.role, "name": p.name} for p in req.parties]
    result = _check(
        parties=parties_dicts,
        description=req.description,
        opposing_counsel=req.opposing_counsel,
        matter_id=req.matter_id,
        requester="admin",  # v1 — admin-only
    )
    # Audit log entry for the firm's compliance trail
    try:
        _write_audit(
            user="admin", model="-", sources=len(result.get("hits", [])),
            latency_ms=result.get("elapsed_ms", 0),
            query_hash="", matter_code=req.matter_id or "",
        )
    except Exception as exc:
        log.warning(f"audit log of conflict_check failed (non-fatal): {exc}")
    return result


@router.get("/v1/conflicts/recent")
@limiter.limit("60/minute")
def conflicts_recent(request: Request, key: str = Depends(_admin_auth), limit: int = 50):
    """List the most recent conflict checks (parties pseudonymised)."""
    if limit < 1 or limit > 500:
        raise HTTPException(400, "limit out of range")
    from conflicts import list_recent as _list_recent
    return {"checks": _list_recent(limit)}


# ── Document comparison ────────────────────────────────────────────────────
# Two-document input (either two ingested-doc display names, or two raw
# text bodies for paste-in cases) → structured diff + LLM-generated
# legal-significance commentary. ACL-gated on both docs (caller must be
# allowed to read both). Bounded at 200 KB per side — diff cost is
# O(n²) at the worst and legal docs over 200 KB are rare; for those,
# operators are expected to compare section-by-section.

_COMPARE_MAX_BYTES = 200 * 1024  # 200 KB per side


class _CompareReq(BaseModel):
    doc_a:   str | None = Field(default=None, max_length=512)
    doc_b:   str | None = Field(default=None, max_length=512)
    text_a:  str | None = None
    text_b:  str | None = None
    label_a: str | None = Field(default=None, max_length=200)
    label_b: str | None = Field(default=None, max_length=200)


def _read_doc_text_for_compare(display_name: str, user: str) -> tuple[str, str]:
    """Resolve + ACL-check + extract a document. Returns (text, label).
    Raises HTTPException with the right status for ACL / not-found / size."""
    on_disk = _resolve_doc_on_disk(display_name)
    if on_disk is None:
        raise HTTPException(status_code=404, detail=f"document not found: {display_name}")
    if user != "admin":
        from doc_acls import is_allowed as _is_allowed
        if not _is_allowed(display_name, user):
            raise HTTPException(status_code=403, detail=f"forbidden: {display_name}")
    try:
        if on_disk.stat().st_size > _COMPARE_MAX_BYTES * 4:
            raise HTTPException(status_code=413, detail=f"document too large to compare: {display_name}")
    except OSError:
        pass
    from ingest import extract as _extract
    pages = _extract(on_disk)
    text = "\n\n".join((p.get("text") or "") for p in pages).strip()
    if not text:
        raise HTTPException(status_code=415, detail=f"unsupported or empty document: {display_name}")
    if len(text.encode("utf-8")) > _COMPARE_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"document text exceeds {_COMPARE_MAX_BYTES // 1024} KB: {display_name}")
    return text, display_name


@router.post("/v1/documents/compare")
@limiter.limit("20/minute")
def documents_compare(req: _CompareReq, request: Request, user: str = Depends(_auth)):
    """Compare two documents (or two text bodies). Returns a section-level
    diff + per-significant-change LLM commentary on legal effect."""
    # Resolve both sides into (text, label) tuples
    if req.doc_a:
        text_a, label_a = _read_doc_text_for_compare(req.doc_a, user)
    elif req.text_a is not None:
        text_a = req.text_a
        label_a = req.label_a or "Document A"
    else:
        raise HTTPException(400, "doc_a or text_a is required")

    if req.doc_b:
        text_b, label_b = _read_doc_text_for_compare(req.doc_b, user)
    elif req.text_b is not None:
        text_b = req.text_b
        label_b = req.label_b or "Document B"
    else:
        raise HTTPException(400, "doc_b or text_b is required")

    # Bound text-only inputs the same way disk-resolved docs are bounded
    for name, t in (("text_a", text_a), ("text_b", text_b)):
        if len(t.encode("utf-8")) > _COMPARE_MAX_BYTES:
            raise HTTPException(413, f"{name} exceeds {_COMPARE_MAX_BYTES // 1024} KB")

    from documents_compare import compare as _compare_impl
    t0 = time.perf_counter()
    result = _compare_impl(text_a, text_b, label_a=label_a, label_b=label_b)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    result["elapsed_ms"] = elapsed_ms

    try:
        _write_audit(
            user=user, model="-",
            sources=2, latency_ms=elapsed_ms,
            query_hash="", matter_code="",
        )
    except Exception as exc:
        log.warning(f"audit log of document compare failed (non-fatal): {exc}")
    return result


# ── Citation verification ──────────────────────────────────────────────────
# Extract case-law / statute / decree citations from arbitrary text and
# verify each one against (a) the firm's corpus, (b) BAILII for UK,
# and (c) an LLM "on-point" check. Used by worker-ui's Verify-Citations
# button on assistant messages, and as a drafting helper for paste-in.

class _CitationVerifyReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=50_000)


@router.post("/v1/citations/verify")
@limiter.limit("20/minute")
def citations_verify(req: _CitationVerifyReq, request: Request, user: str = Depends(_auth)):
    """Verify every citation in `text`. Returns a list of structured
    citations with verification metadata."""
    from citations import verify as _cite_verify
    t0 = time.perf_counter()
    result = _cite_verify(req.text)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    try:
        _write_audit(
            user=user, model="-",
            sources=result.get("count", 0), latency_ms=elapsed_ms,
            query_hash="", matter_code="",
        )
    except Exception as exc:
        log.warning(f"audit log of citation verify failed (non-fatal): {exc}")
    return result


# ── Open-document file serve ───────────────────────────────────────────────
# When a chat response cites a source, the worker-ui's SourcesPanel
# shows file name + page + section header but historically the "Open
# document" button did nothing. This endpoint serves the raw file so
# the user can open it (PDFs anchor to #page=N in the browser; DOCX/
# TXT open in their associated app).
#
# Per-doc ACL is enforced — a user who can't retrieve from this doc
# also can't open it. Path-traversal is hardened the same way as
# the delete endpoint (Path(name).name + suffix allowlist).

_RAW_DOC_SUFFIX_ALLOW = {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf", ".odt", ".html", ".htm"}


def _resolve_doc_on_disk(display_name: str) -> Optional[Path]:
    """Locate the on-disk file for a display name. Mirrors the search
    in delete_document but tolerant of both UUID-prefixed and
    UUID-stripped names — chunk payloads written by different ingest
    paths use different conventions:

      - chunked upload (chunked_uploads.py)  → stores `display_name`
        (UUID-stripped) as Qdrant payload.source
      - single-shot /v1/ingest                → stores `dest.name`
        (UUID-prefixed) as Qdrant payload.source
      - bulk-ingest from data/                → stores plain filename

    The worker-ui SourcesPanel passes whatever was in the chunk's
    source field straight through, so we need to find a file under
    either convention. Returns None if not found.
    """
    import re as _re
    from pathlib import Path
    base = Path(__file__).resolve().parent
    safe = Path(display_name).name
    if not safe or safe in (".", "..") or "/" in safe or "\\" in safe:
        return None
    if any(ord(c) < 32 for c in safe):
        return None
    if Path(safe).suffix.lower() not in _RAW_DOC_SUFFIX_ALLOW:
        return None
    uuid_re = _re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}_")

    # Normalise the requested name — strip a leading UUID prefix if present
    safe_stripped = safe[uuid_re.match(safe).end():] if uuid_re.match(safe) else safe

    uploads_dir = base / "storage" / "uploads"
    if uploads_dir.exists():
        # First exact-name match (handles UUID-prefixed source values)
        exact = uploads_dir / safe
        if exact.exists() and exact.is_file():
            try:
                exact.resolve().relative_to(uploads_dir.resolve())
                return exact
            except (ValueError, OSError):
                pass
        # Then UUID-stripped match (handles clean source values)
        for p in uploads_dir.iterdir():
            if not p.is_file() or p.name.startswith("."):
                continue
            m = uuid_re.match(p.name)
            stripped = p.name[m.end():] if m else p.name
            if stripped == safe or stripped == safe_stripped:
                return p

    data_dir = base / "data"
    for name in (safe, safe_stripped):
        candidate = data_dir / name
        if candidate.exists() and candidate.is_file():
            try:
                candidate.resolve().relative_to(data_dir.resolve())
                return candidate
            except (ValueError, OSError):
                continue
    return None


@router.get("/v1/documents/{display_name}/raw")
@limiter.limit("60/minute")
def get_document_raw(display_name: str, request: Request, user: str = Depends(_auth)):
    """Stream the raw document file. Per-doc ACL gated. PDFs can be
    opened with `#page=N` in the URL fragment so the browser scrolls
    to the cited page."""
    on_disk = _resolve_doc_on_disk(display_name)
    if on_disk is None:
        raise HTTPException(status_code=404, detail="document not found")
    # Enforce ACL — admin bypasses (DPO audit). The display name is what
    # we keep in the ACL store + Qdrant payload (UUID prefix stripped).
    if user != "admin":
        from doc_acls import is_allowed as _is_allowed
        if not _is_allowed(display_name, user):
            raise HTTPException(status_code=403, detail="forbidden")
    # Pick a sensible Content-Type based on suffix (FileResponse infers
    # from extension; we just override for known legal-doc types).
    suffix = on_disk.suffix.lower()
    media = {
        ".pdf":  "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc":  "application/msword",
        ".txt":  "text/plain; charset=utf-8",
        ".md":   "text/markdown; charset=utf-8",
        ".rtf":  "application/rtf",
        ".odt":  "application/vnd.oasis.opendocument.text",
        ".html": "text/html; charset=utf-8",
        ".htm":  "text/html; charset=utf-8",
    }.get(suffix, "application/octet-stream")
    # `inline` so PDFs render in the browser; `filename` keeps the
    # display name (UUID prefix stripped) for if the user saves a copy.
    headers = {
        "Content-Disposition": f'inline; filename="{display_name}"',
        # The cited page is appended client-side as #page=N; that's a
        # URL fragment which never reaches the server. We just serve the file.
    }
    return FileResponse(
        path=str(on_disk),
        media_type=media,
        headers=headers,
    )


# ── Chunked / resumable upload protocol ─────────────────────────────────────
# Designed for gigabyte-scale corpora. See chunked_uploads.py for the wire
# format and security model. Worker-ui and manager-ui both call these.
class _UploadInitReq(BaseModel):
    filename:    str   = Field(..., min_length=1, max_length=512)
    total_bytes: int   = Field(..., ge=1)
    sha256:      str | None = Field(default=None, description="64-hex; verified on complete")


class _UploadCompleteReq(BaseModel):
    sha256: str | None = Field(default=None)


def _cu_err(exc: _cu.UploadError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@router.post("/v1/uploads")
@limiter.limit("60/minute")
def upload_init(req: _UploadInitReq, request: Request, user: str = Depends(_auth)):
    try:
        return _cu.init_upload(
            filename=req.filename,
            total_bytes=req.total_bytes,
            sha256=req.sha256,
            owner_user=user,
        )
    except _cu.UploadError as exc:
        raise _cu_err(exc)


@router.get("/v1/uploads/{upload_id}")
@limiter.limit("120/minute")
def upload_status(upload_id: str, request: Request, user: str = Depends(_auth)):
    try:
        return _cu.get_status(upload_id=upload_id, owner_user=user)
    except _cu.UploadError as exc:
        raise _cu_err(exc)


@router.patch("/v1/uploads/{upload_id}")
@limiter.limit("600/minute")
async def upload_chunk(upload_id: str, request: Request, user: str = Depends(_auth)):
    content_range = request.headers.get("Content-Range", "")
    body = await request.body()
    try:
        return _cu.append_chunk(
            upload_id=upload_id,
            content_range=content_range,
            data=body,
            owner_user=user,
        )
    except _cu.UploadError as exc:
        raise _cu_err(exc)


@router.post("/v1/uploads/{upload_id}/complete")
@limiter.limit("60/minute")
def upload_complete(
    upload_id: str,
    req: _UploadCompleteReq,
    request: Request,
    user: str = Depends(_auth),
):
    try:
        final_path, stored_as, n = _cu.complete_upload(
            upload_id=upload_id,
            sha256=req.sha256,
            owner_user=user,
        )
    except _cu.UploadError as exc:
        raise _cu_err(exc)
    log.info("Chunked upload complete: %s (%d bytes) by %s",
             stored_as, n, pseudonymise_user(user))
    _get_ingest_queue().submit(final_path, stored_as)
    return {"stored_as": stored_as, "bytes": n, "indexing": "queued"}


@router.delete("/v1/uploads/{upload_id}")
@limiter.limit("60/minute")
def upload_cancel(upload_id: str, request: Request, user: str = Depends(_auth)):
    try:
        _cu.cancel_upload(upload_id=upload_id, owner_user=user)
        return {"status": "cancelled", "upload_id": upload_id}
    except _cu.UploadError as exc:
        raise _cu_err(exc)


@router.get("/v1/ingest/status")
@limiter.limit("120/minute")
def ingest_status(request: Request, user: str = Depends(_auth)):
    """Live indexing queue depth — backs the 'Indexing N of M' UI ticker."""
    s = _get_ingest_queue().status()
    return {
        "in_flight":         s.in_flight,
        "queued":            s.queued,
        "completed_total":   s.completed_total,
        "failed_total":      s.failed_total,
        "bm25_pending":      s.bm25_pending,
        "last_completed_at": s.last_completed_at,
    }


@router.post("/v1/ingest/flush")
@limiter.limit("10/minute")
def ingest_flush(request: Request, user: str = Depends(_auth)):
    """Force a BM25 rebuild now (operator-clicked 'Done' after bulk load).
    No-op when nothing is queued or in flight."""
    _get_ingest_queue().flush()
    return {"status": "ok"}


@router.post("/v1/ingest")
@limiter.limit("10/minute")
async def ingest_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: str = Depends(_auth),
):
    safe_name = Path(file.filename).name if file.filename else ""
    if not safe_name or ".." in safe_name or safe_name.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in _ALLOWED_EXTS:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {suffix}")
    dest = _UPLOAD_DIR / f"{_uuid.uuid4()}_{safe_name}"
    if not str(dest.resolve()).startswith(str(_UPLOAD_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Path traversal detected")

    # Red-team finding 5.5: stream the upload to disk in 1 MiB chunks
    # rather than `await file.read()` of the whole body. Previously, an
    # attacker spamming 50 MB uploads at the 10/min rate limit burned
    # 500 MB/min of RAM. Streaming caps RAM usage at the chunk size and
    # short-circuits the moment the running total exceeds
    # _MAX_UPLOAD_BYTES. We delete the partial file on overflow so disk
    # isn't filled either.
    written = 0
    with open(dest, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > _MAX_UPLOAD_BYTES:
                out.close()
                try:
                    dest.unlink()
                except OSError:
                    pass
                raise HTTPException(status_code=413, detail="File too large (max 50 MB)")
            out.write(chunk)

    log.info(f"Document uploaded: {safe_name} ({written} bytes) by {pseudonymise_user(user)}")
    background_tasks.add_task(_index_document, dest, dest.name)
    return {"status": "uploaded", "stored_as": dest.name, "bytes": written, "indexing": "in_progress"}

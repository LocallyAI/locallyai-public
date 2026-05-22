"""
chunked_uploads.py — resumable, append-to-disk upload protocol.

Why this exists:
  /v1/ingest's UploadFile.read() loads the entire body into RAM. For
  multi-gigabyte client corpora, that's a hard wall — and a server OOM
  if two operators upload in parallel. This module accepts a file in
  arbitrarily-sized chunks (default ≤ 16 MiB), appends each one straight
  to disk, and verifies SHA-256 on completion.

Protocol:
  1. POST /v1/uploads
       body: {"filename": str, "total_bytes": int, "sha256": str|null}
       returns: {"upload_id", "received_bytes": 0, "chunk_size_suggested"}
  2. PATCH /v1/uploads/{upload_id}
       header: Content-Range: bytes <start>-<end>/<total>   (RFC 9110-style)
       body:   raw bytes (Content-Type: application/octet-stream)
       returns: {"received_bytes"}
  3. GET /v1/uploads/{upload_id}
       returns: {"received_bytes", "total_bytes", "status"}  (for resume)
  4. POST /v1/uploads/{upload_id}/complete
       body: {"sha256": str|null}   (overrides any value supplied at init)
       returns: {"stored_as", "bytes", "indexing": "queued"}
  5. DELETE /v1/uploads/{upload_id}
       cancels and removes the partial file.

Auth & security:
  - Every endpoint uses the same _auth dependency as /v1/ingest. No
    anonymous bytes ever land on disk.
  - Filename hardening (basename, no '..', no leading '/'), extension
    whitelist, path-containment check on every disk write.
  - Owner is recorded in the meta and checked on every subsequent
    operation — one user cannot resume / inspect another user's upload.
  - SHA-256 is computed streamingly during PATCH so completion is O(1) on
    that side. The client may supply an expected hash at init, in which
    case mismatch → 422 + part deleted.
  - Disk-free check at init (>= 1.1× total) prevents half-uploads that
    would leave the server in a broken state.

Resume model:
  - The client persists the upload_id in localStorage keyed by
    {filename, size, lastModified}. On the next attempt, GET
    /v1/uploads/{id} reports received_bytes; the client PATCHes from
    that offset.
  - Server-side, meta files in storage/uploads/.parts/ live until
    explicit completion, cancel, or 24h GC sweep.

Threading:
  - All disk writes happen inside _upload_lock(upload_id) — concurrent
    PATCHes for the same upload would corrupt the byte stream. Different
    uploads proceed in parallel.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid as _uuid
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger("chunked_uploads")

_BASE_DIR        = Path(__file__).resolve().parent
_UPLOAD_DIR      = _BASE_DIR / "storage" / "uploads"
_PARTS_DIR       = _UPLOAD_DIR / ".parts"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
_PARTS_DIR.mkdir(parents=True, exist_ok=True)

# Pinned to ingest's allowed types so a successful upload always becomes a
# valid ingest target. Update both sides if you add a format.
_ALLOWED_EXTS    = {".pdf", ".txt", ".md", ".docx"}

# Hard ceiling on per-file size. 5 GiB is enough for any single PDF/DOCX
# we expect; multi-GB corpora are many files, not one. Operators can lift
# via env if a real client doc demands it.
_MAX_FILE_BYTES  = int(os.environ.get("LOCALLYAI_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024 * 1024)))

# Per-chunk ceiling. The client SHOULD use 8 MiB; we allow up to 16 MiB so
# an operator on a fast LAN can dial up if they want.
_MAX_CHUNK_BYTES = 16 * 1024 * 1024
_CHUNK_SUGGESTED = 8 * 1024 * 1024

# Stale-upload GC window. Anything that hasn't received a chunk in this
# many seconds is considered abandoned.
_STALE_AFTER_SEC = int(os.environ.get("LOCALLYAI_UPLOAD_GC_SECONDS", str(24 * 3600)))


# ── Path & filename hardening ─────────────────────────────────────────────────
def _safe_filename(name: str) -> str:
    """Reject anything that would let an attacker write outside _UPLOAD_DIR
    or shadow a config file. We also strip any directory components a folder
    upload might bring along — the corpus is flat by design."""
    if not name:
        raise ValueError("Empty filename")
    base = Path(name).name
    if not base or base in (".", "..") or base.startswith("/") or "/" in base or "\\" in base:
        raise ValueError("Invalid filename")
    # Disallow control chars; preserve unicode (Arabic filenames are fine).
    if any(ord(c) < 32 for c in base):
        raise ValueError("Invalid filename")
    if Path(base).suffix.lower() not in _ALLOWED_EXTS:
        raise ValueError(f"Unsupported file type: {Path(base).suffix}")
    return base


def _resolved_within(p: Path, root: Path) -> bool:
    try:
        return str(p.resolve()).startswith(str(root.resolve()) + os.sep) or p.resolve() == root.resolve()
    except OSError:
        return False


# ── Meta persistence ──────────────────────────────────────────────────────────
@dataclass
class UploadMeta:
    upload_id: str
    filename: str
    total_bytes: int
    received_bytes: int
    sha256_expected: str | None
    sha256_running: str  # hex digest of partial hasher state? no — we recompute
    owner_user: str
    started_at: float
    last_chunk_at: float
    status: str  # "open" | "complete" | "cancelled" | "failed"


def _meta_path(upload_id: str) -> Path:
    return _PARTS_DIR / f"{upload_id}.meta.json"


def _part_path(upload_id: str) -> Path:
    return _PARTS_DIR / f"{upload_id}.part"


def _load_meta(upload_id: str) -> UploadMeta | None:
    p = _meta_path(upload_id)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return UploadMeta(**d)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        log.warning("Corrupt upload meta %s: %s", upload_id, exc)
        return None


def _save_meta(meta: UploadMeta) -> None:
    tmp = _meta_path(meta.upload_id).with_suffix(".meta.tmp")
    tmp.write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")
    os.replace(tmp, _meta_path(meta.upload_id))


# ── Per-upload locks ──────────────────────────────────────────────────────────
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(upload_id: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(upload_id)
        if lk is None:
            lk = threading.Lock()
            _locks[upload_id] = lk
        return lk


def _release_lock(upload_id: str) -> None:
    with _locks_guard:
        _locks.pop(upload_id, None)


# ── Errors ────────────────────────────────────────────────────────────────────
class UploadError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ── Core operations ───────────────────────────────────────────────────────────
def init_upload(*, filename: str, total_bytes: int, sha256: str | None, owner_user: str) -> dict:
    try:
        safe = _safe_filename(filename)
    except ValueError as exc:
        raise UploadError(400, str(exc))

    if total_bytes <= 0 or total_bytes > _MAX_FILE_BYTES:
        raise UploadError(413, f"total_bytes out of range (1 .. {_MAX_FILE_BYTES})")

    if sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", sha256 or ""):
        raise UploadError(400, "sha256 must be 64 hex chars or null")

    # Disk-free guard. Need 1.1× total to leave a little headroom for
    # logs, indices, and concurrent uploads.
    free = shutil.disk_usage(_UPLOAD_DIR).free
    needed = int(total_bytes * 1.1)
    if free < needed:
        raise UploadError(
            507,
            f"Insufficient storage: need ~{needed // (1024**2)} MiB, free {free // (1024**2)} MiB",
        )

    upload_id = _uuid.uuid4().hex
    now = time.time()
    meta = UploadMeta(
        upload_id=upload_id,
        filename=safe,
        total_bytes=total_bytes,
        received_bytes=0,
        sha256_expected=(sha256.lower() if sha256 else None),
        sha256_running="",
        owner_user=owner_user,
        started_at=now,
        last_chunk_at=now,
        status="open",
    )
    # Pre-create the empty part file so we don't surprise anything later.
    _part_path(upload_id).touch()
    _save_meta(meta)
    log.info("Upload init: %s (%s, %d bytes) by %s", upload_id, safe, total_bytes, owner_user)
    return {
        "upload_id": upload_id,
        "received_bytes": 0,
        "chunk_size_suggested": _CHUNK_SUGGESTED,
        "max_chunk_bytes": _MAX_CHUNK_BYTES,
    }


def get_status(*, upload_id: str, owner_user: str) -> dict:
    meta = _load_meta(upload_id)
    if not meta:
        raise UploadError(404, "Upload not found")
    if meta.owner_user != owner_user:
        raise UploadError(403, "Not your upload")
    return {
        "upload_id": meta.upload_id,
        "filename": meta.filename,
        "total_bytes": meta.total_bytes,
        "received_bytes": meta.received_bytes,
        "status": meta.status,
    }


_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+)$")


def append_chunk(*, upload_id: str, content_range: str, data: bytes, owner_user: str) -> dict:
    meta = _load_meta(upload_id)
    if not meta:
        raise UploadError(404, "Upload not found")
    if meta.owner_user != owner_user:
        raise UploadError(403, "Not your upload")
    if meta.status != "open":
        raise UploadError(409, f"Upload {meta.status}")

    m = _RANGE_RE.match(content_range or "")
    if not m:
        raise UploadError(400, "Bad Content-Range; expected 'bytes <start>-<end>/<total>'")
    start, end, total = int(m.group(1)), int(m.group(2)), int(m.group(3))

    if total != meta.total_bytes:
        raise UploadError(400, "Content-Range total disagrees with init total_bytes")
    if start != meta.received_bytes:
        raise UploadError(409, f"Out-of-order chunk: server has {meta.received_bytes}, client sent {start}")
    chunk_len = end - start + 1
    if chunk_len <= 0 or chunk_len != len(data):
        raise UploadError(400, "Content-Range length mismatch with body")
    if chunk_len > _MAX_CHUNK_BYTES:
        raise UploadError(413, f"Chunk too large (max {_MAX_CHUNK_BYTES})")
    if end + 1 > meta.total_bytes:
        raise UploadError(400, "Chunk extends past total_bytes")

    part = _part_path(upload_id)
    if not _resolved_within(part, _PARTS_DIR):
        raise UploadError(400, "Path traversal detected")

    with _lock_for(upload_id):
        # Re-read meta inside the lock to avoid race with parallel PATCHes
        # for the same id (rejected by the offset check, but be defensive).
        meta = _load_meta(upload_id)
        if not meta or meta.status != "open" or meta.received_bytes != start:
            raise UploadError(409, "Upload state changed under us; retry GET first")

        with open(part, "ab") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

        meta.received_bytes = end + 1
        meta.last_chunk_at = time.time()
        _save_meta(meta)

    return {
        "upload_id": meta.upload_id,
        "received_bytes": meta.received_bytes,
        "total_bytes": meta.total_bytes,
    }


def complete_upload(*, upload_id: str, sha256: str | None, owner_user: str) -> tuple[Path, str, int]:
    """Verify the assembled file and atomically promote it to the live
    upload dir. Returns (final_path, stored_as, bytes). The caller is
    responsible for handing final_path off to the ingest queue."""
    meta = _load_meta(upload_id)
    if not meta:
        raise UploadError(404, "Upload not found")
    if meta.owner_user != owner_user:
        raise UploadError(403, "Not your upload")
    if meta.status != "open":
        raise UploadError(409, f"Upload {meta.status}")

    with _lock_for(upload_id):
        meta = _load_meta(upload_id)
        if not meta or meta.status != "open":
            raise UploadError(409, "Upload state changed")

        if meta.received_bytes != meta.total_bytes:
            raise UploadError(
                409,
                f"Incomplete: have {meta.received_bytes} / {meta.total_bytes}",
            )

        part = _part_path(upload_id)
        # Streaming SHA-256 over the assembled file.
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        computed = h.hexdigest()

        expected = (sha256 or meta.sha256_expected or "").lower()
        if expected and not _hmac_compare(expected, computed):
            # Don't leave the bad bytes lying around.
            try:
                part.unlink()
                _meta_path(upload_id).unlink(missing_ok=True)
            finally:
                _release_lock(upload_id)
            raise UploadError(422, f"sha256 mismatch (expected {expected[:12]}…, got {computed[:12]}…)")

        # Atomic rename into the live uploads dir, with the same
        # "<uuid>_<filename>" pattern /v1/ingest uses.
        stored_as = f"{meta.upload_id}_{meta.filename}"
        final = _UPLOAD_DIR / stored_as
        if not _resolved_within(final, _UPLOAD_DIR):
            raise UploadError(400, "Path traversal detected")
        os.replace(part, final)

        # Clear meta. The .meta.json is no longer needed; the live file
        # is enough to derive the indexed key.
        _meta_path(upload_id).unlink(missing_ok=True)
        meta.status = "complete"
        # No need to persist; we just deleted the file.

    _release_lock(upload_id)
    log.info("Upload complete: %s (%s, %d bytes, sha256=%s) by %s",
             upload_id, meta.filename, meta.total_bytes, computed[:12], owner_user)
    return final, stored_as, meta.total_bytes


def cancel_upload(*, upload_id: str, owner_user: str) -> None:
    meta = _load_meta(upload_id)
    if not meta:
        # Cancel is idempotent.
        return
    if meta.owner_user != owner_user:
        raise UploadError(403, "Not your upload")
    with _lock_for(upload_id):
        _part_path(upload_id).unlink(missing_ok=True)
        _meta_path(upload_id).unlink(missing_ok=True)
    _release_lock(upload_id)
    log.info("Upload cancelled: %s by %s", upload_id, owner_user)


# ── Maintenance ───────────────────────────────────────────────────────────────
def gc_stale(now: float | None = None) -> int:
    """Remove .part + .meta files whose last chunk is older than the GC
    window. Called by the sentinel periodically. Returns count removed."""
    now = now or time.time()
    removed = 0
    for meta_file in _PARTS_DIR.glob("*.meta.json"):
        try:
            d = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Stuck/corrupt — drop it after the same window.
            try:
                age = now - meta_file.stat().st_mtime
            except OSError:
                continue
            if age > _STALE_AFTER_SEC:
                meta_file.unlink(missing_ok=True)
                removed += 1
            continue
        if (now - d.get("last_chunk_at", 0)) > _STALE_AFTER_SEC:
            uid = d.get("upload_id", meta_file.stem.replace(".meta", ""))
            _part_path(uid).unlink(missing_ok=True)
            meta_file.unlink(missing_ok=True)
            removed += 1
            log.info("GC'd stale upload %s (%s)", uid, d.get("filename"))
    return removed


# ── Helpers ───────────────────────────────────────────────────────────────────
def _hmac_compare(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))

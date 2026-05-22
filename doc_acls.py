"""doc_acls.py — per-document access-control list.

For firms above ~25 lawyers (or any litigation firm), some documents
are partner-only, matter-restricted, or behind ethical walls. The
default LocallyAI behaviour is "every authenticated user can retrieve
from every chunk in the corpus" — fine for small firms, untenable
above ~50k docs.

This module is the source-of-truth for which user is allowed to
retrieve from which document. ACLs are referenced both by:
  - Qdrant payload at ingest time (so dense retrieval filters at
    query time without an extra round-trip)
  - This file at query time (so BM25 — which has no payload filter —
    can post-filter, and so admins can audit/update ACLs without
    re-ingesting)

ACL shape per document (keyed by source filename):
  {
    "allowed_users":  ["alice", "bob", "*"],   # "*" = everyone in firm
    "matter_code":    "M-2026-0042",            # optional, audit + filtering
    "ethical_wall":   ["acquirer-team", "target-team"],  # optional
    "set_at":         "2026-05-14T...",
    "set_by":         "Admin",
    "version":        2,                        # bumped on each change
  }

Backwards compat: documents not in the ACL file are treated as
allowed_users=["*"] (everyone in the firm). New ingest defaults to
this for legacy installs; firms that adopt strict ACLs flip the
default via LOCALLYAI_DOC_ACL_DEFAULT=restricted.

Lives at SHARED_DIR/doc_acls.json so HA peers see the same ACL.
Atomic write via tmp+rename. fcntl.flock for concurrent updates.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from datetime import UTC, datetime

from config import SHARED_DIR

_ACL_FILE = SHARED_DIR / "doc_acls.json"
_LOCK_FILE = SHARED_DIR / ".doc_acls.lock"


def _default_open() -> bool:
    """Default ACL when a document has no explicit entry. True =
    'everyone in the firm can retrieve' (back-compat). Set
    LOCALLYAI_DOC_ACL_DEFAULT=restricted to flip the default to
    closed (firms with strict access controls)."""
    return os.environ.get("LOCALLYAI_DOC_ACL_DEFAULT", "open").lower() != "restricted"


@contextlib.contextmanager
def _lock():
    """Cross-process advisory lock — fleet HA peers may write
    concurrently when an ACL change propagates via Syncthing. The
    lock is short-lived; ACL writes are O(file size)."""
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    _LOCK_FILE.touch(exist_ok=True)
    fd = open(_LOCK_FILE, "rb+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fd.close()


def _load() -> dict:
    if not _ACL_FILE.exists():
        return {}
    try:
        d = json.loads(_ACL_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(acls: dict) -> None:
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _ACL_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(acls, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(_ACL_FILE)
    try:
        os.chmod(_ACL_FILE, 0o640)
    except OSError:
        pass


# ── Public API ──────────────────────────────────────────────────────────────

def get_acl(source_name: str) -> dict:
    """Return the ACL entry for a document (by source filename).
    If no entry exists, returns the default-open policy."""
    acls = _load()
    if source_name in acls:
        return acls[source_name]
    return {
        "allowed_users":  ["*"] if _default_open() else [],
        "matter_code":    "",
        "ethical_wall":   [],
        "set_at":         None,
        "set_by":         None,
        "version":        0,
        "default":        True,
    }


def list_acls() -> dict:
    """Return all ACL entries. UI uses this to render the ACL editor's list."""
    return _load()


def set_acl(source_name: str, allowed_users: list[str], matter_code: str = "",
            ethical_wall: list[str] | None = None, set_by: str = "admin") -> dict:
    """Set/replace the ACL for a document. Bumps version."""
    if not source_name:
        raise ValueError("source_name required")
    if not isinstance(allowed_users, list):
        raise ValueError("allowed_users must be a list")
    with _lock():
        acls = _load()
        existing = acls.get(source_name, {})
        new_version = int(existing.get("version", 0)) + 1
        entry = {
            "allowed_users":  list(dict.fromkeys(allowed_users)),  # de-dup, preserve order
            "matter_code":    matter_code or "",
            "ethical_wall":   list(ethical_wall or []),
            "set_at":         datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "set_by":         set_by,
            "version":        new_version,
        }
        acls[source_name] = entry
        _save(acls)
        return entry


def delete_acl(source_name: str) -> bool:
    """Drop an ACL entry — document falls back to default policy.
    Returns True if removed, False if nothing was set."""
    with _lock():
        acls = _load()
        if source_name not in acls:
            return False
        del acls[source_name]
        _save(acls)
        return True


def is_allowed(source_name: str, user: str) -> bool:
    """Fast check used by retrieval at query time. user is the
    authenticated username (per validate_key); 'admin' bypasses ACLs.
    Wildcard '*' in allowed_users means everyone in the firm."""
    if not user:
        return False
    if user == "admin":
        return True
    acl = get_acl(source_name)
    allowed = acl.get("allowed_users", [])
    if "*" in allowed:
        return True
    return user in allowed


def filter_chunks(chunks: list, user: str) -> list:
    """Post-filter a list of retrieved chunks (each carrying a
    `source` field) to only those the user is allowed to see.
    Used by BM25 retrieval (no payload filter) and as a defence-in-
    depth check after Qdrant filtering."""
    return [c for c in chunks if is_allowed(_chunk_source(c), user)]


def _chunk_source(chunk) -> str:
    """Extract the source filename from a chunk (handles both
    RetrievedChunk dataclass and plain dicts)."""
    if hasattr(chunk, "source"):
        return chunk.source
    if isinstance(chunk, dict):
        return chunk.get("source", "")
    return ""


def all_users_with_access(source_name: str) -> list[str]:
    """List of usernames that have access to a document — used by
    the audit + DPO surfaces. Returns ['*'] for default-open docs."""
    acl = get_acl(source_name)
    return list(acl.get("allowed_users", []))


def docs_visible_to(user: str, all_doc_sources: list[str]) -> list[str]:
    """Return the subset of doc sources visible to `user`. Used by
    the Documents UI to filter the "Recent documents" panel."""
    if user == "admin":
        return list(all_doc_sources)
    return [s for s in all_doc_sources if is_allowed(s, user)]

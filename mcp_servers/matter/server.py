"""In-process MCP server for read-only matter views + a sidecar
description store (`SHARED_DIR/matters_meta.json`).

Three tools:
  - list_matters    — distinct matter codes seen in audit.log + doc_acls.json
  - get_matter      — per-matter doc list + activity summary
  - describe_matter — write a sidecar human-readable description

A 60-second TTL cache fronts list_matters (rebuilding the cross-product of
audit.log + doc_acls on every call is unacceptable on a year-old log).
The cache is invalidated when describe_matter writes.
"""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import doc_acls
from config import SHARED_DIR
from shared_lock import shared_lock

DESCRIPTION = (
    "Read-only views of matters seen in the audit log + ACLs, plus a "
    "sidecar description store for operator-managed matter metadata."
)

_META_FILE = SHARED_DIR / "matters_meta.json"
_CACHE_TTL_SEC = 60.0

# Module-level cache state. Lazy-populated; never doubles up on import.
_CACHE: dict[str, Any] | None = None
_CACHE_ROOFTIME: float = 0.0


def _resolve_audit_log() -> Path:
    """Late-import the AUDIT_LOG path so tests can monkeypatch
    `api._shared.AUDIT_LOG` for the synthetic-log scenarios."""
    from api import _shared
    return _shared.AUDIT_LOG


def _load_meta() -> dict[str, dict[str, Any]]:
    if not _META_FILE.exists():
        return {}
    try:
        d = json.loads(_META_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_meta(meta: dict[str, dict[str, Any]]) -> None:
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    with shared_lock(_META_FILE, timeout=5.0):
        tmp = _META_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(_META_FILE)
        try:
            os.chmod(_META_FILE, 0o640)
        except OSError:
            pass


def _invalidate_cache() -> None:
    global _CACHE, _CACHE_ROOFTIME
    _CACHE = None
    _CACHE_ROOFTIME = 0.0


def _iter_audit_entries() -> list[dict[str, Any]]:
    """Single pass over audit.log, used by both list_matters and get_matter.
    Caller is expected to honour the cache."""
    path = _resolve_audit_log()
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _build_matter_index() -> dict[str, Any]:
    """Cross-reference doc_acls + audit.log. Returns the cached shape
    used by both list_matters and get_matter."""
    try:
        acls = doc_acls.list_acls() or {}
    except (OSError, json.JSONDecodeError):
        acls = {}

    by_matter: dict[str, dict[str, Any]] = {}

    # Documents from ACL store
    for source_name, entry in acls.items():
        if not isinstance(entry, dict):
            continue
        mc = (entry.get("matter_code") or "").strip()
        if not mc:
            continue
        bucket = by_matter.setdefault(mc, {
            "matter_code": mc,
            "documents": [],
            "audit_entries": [],
        })
        bucket["documents"].append({
            "display_name":  source_name,
            "allowed_users": list(entry.get("allowed_users", [])),
        })

    # Audit log activity
    for entry in _iter_audit_entries():
        mc = (entry.get("matter_code") or "").strip()
        if not mc:
            continue
        bucket = by_matter.setdefault(mc, {
            "matter_code": mc,
            "documents": [],
            "audit_entries": [],
        })
        bucket["audit_entries"].append(entry)

    return by_matter


def _get_or_build_index() -> dict[str, Any]:
    global _CACHE, _CACHE_ROOFTIME
    now = time.monotonic()
    if _CACHE is not None and (now - _CACHE_ROOFTIME) < _CACHE_TTL_SEC:
        return _CACHE
    _CACHE = _build_matter_index()
    _CACHE_ROOFTIME = now
    return _CACHE


def _activity_summary(audit_entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-matter activity rollup from the cached audit slice."""
    by_user: dict[str, int] = {}
    latest = ""
    for e in audit_entries:
        uh = (e.get("user_hash") or "")[:16] or "(none)"
        by_user[uh] = by_user.get(uh, 0) + 1
        ts = e.get("timestamp", "") or ""
        if ts > latest:
            latest = ts
    return {
        "total_turns":         len(audit_entries),
        "by_user_hash_prefix": dict(sorted(by_user.items(),
                                           key=lambda kv: kv[1], reverse=True)),
        "latest_activity":     latest or None,
    }


# ── Tool defs ─────────────────────────────────────────────────────────────

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_matters",
            "description": (
                "List every distinct matter_code seen across the document "
                "ACL store and the audit log, with document counts, audit "
                "event counts, and the sidecar description (if set)."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_matter",
            "description": (
                "Detailed view of a single matter: the ACL'd documents "
                "attached to it, plus an activity summary derived from "
                "the audit log (turns, top user_hash prefixes, latest "
                "activity timestamp)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "matter_code": {"type": "string"},
                },
                "required": ["matter_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_matter",
            "description": (
                "Write a human-readable description for a matter to the "
                "sidecar `matters_meta.json` store. Returns the persisted "
                "description and timestamp. Idempotent overwrite."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "matter_code": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["matter_code", "description"],
            },
        },
    },
]


# ── Dispatch wrappers ──────────────────────────────────────────────────────

def _list_matters(arguments: dict, *, user: str,
                  matter_code: str | None = None) -> dict:
    index = _get_or_build_index()
    meta = _load_meta()
    out = []
    for mc in sorted(index.keys()):
        bucket = index[mc]
        out.append({
            "matter_code":        mc,
            "doc_count":          len(bucket["documents"]),
            "audit_event_count":  len(bucket["audit_entries"]),
            "description":        (meta.get(mc) or {}).get("description"),
        })
    return {"matters": out, "count": len(out)}


def _get_matter(arguments: dict, *, user: str,
                matter_code: str | None = None) -> dict:
    requested = str(arguments.get("matter_code") or "").strip()
    if not requested:
        return {"error": "missing required argument: matter_code",
                "matter_code": "", "documents": [], "activity_summary": {},
                "description": None}
    index = _get_or_build_index()
    bucket = index.get(requested)
    meta = _load_meta()
    description = (meta.get(requested) or {}).get("description")
    if bucket is None:
        return {
            "matter_code": requested,
            "documents": [],
            "activity_summary": _activity_summary([]),
            "description": description,
        }
    return {
        "matter_code": requested,
        "documents": bucket["documents"],
        "activity_summary": _activity_summary(bucket["audit_entries"]),
        "description": description,
    }


def _describe_matter(arguments: dict, *, user: str,
                     matter_code: str | None = None) -> dict:
    requested = str(arguments.get("matter_code") or "").strip()
    description = str(arguments.get("description") or "").strip()
    if not requested:
        return {"error": "missing required argument: matter_code"}
    if not description:
        return {"error": "missing required argument: description"}
    # 8 KB cap protects the sidecar from a runaway model. The MCP layer
    # is the right place for this — the writer doesn't know it's an LLM
    # on the other end.
    if len(description) > 8000:
        description = description[:8000]
    meta = _load_meta()
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta[requested] = {
        "description": description,
        "updated_at":  now,
        "updated_by":  user,
    }
    _save_meta(meta)
    _invalidate_cache()
    return {
        "matter_code": requested,
        "description": description,
        "updated_at":  now,
    }


DISPATCH: dict[str, Callable[..., dict]] = {
    "list_matters":    _list_matters,
    "get_matter":      _get_matter,
    "describe_matter": _describe_matter,
}

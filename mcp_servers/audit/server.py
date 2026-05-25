"""In-process MCP server wrapping the LocallyAI audit log for read-only
forensic queries.

Lifted (with light adaptation) from `locallyai-audit-agent/tools.py`:
  - log_search       — case-insensitive substring across metadata fields
  - hmac_verify      — HMAC chain integrity walk
  - time_range_query — ISO-8601 window + structured filters
  - summary_stats    — per-bucket counts on a fixed group_by enum

Adaptations from the upstream agent:
  - Log path resolves from `api._shared.AUDIT_LOG` (not the agent's env
    var), so this server always reads the same file the writer produces.
  - HMAC key reads from `api._shared._AUDIT_HMAC_KEY` (already loaded
    from `LOCALLYAI_AUDIT_HMAC_KEY` at api startup); we don't reread the
    env var per call.
  - No tracing.py instrumentation — the chat handler does its own audit
    logging around tool-call dispatch.
"""
from __future__ import annotations

import datetime
import gzip
import hmac as _hmac
import json
from collections import Counter
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

DESCRIPTION = (
    "Forensic read access to the tamper-evident LocallyAI audit log "
    "(JSONL with HMAC chain). Search, time-range queries, HMAC "
    "verification, and per-bucket aggregations."
)


def _resolve_log_path() -> Path:
    """Late-import so module import has no side effects and so tests can
    monkeypatch `api._shared.AUDIT_LOG`."""
    from api import _shared
    return _shared.AUDIT_LOG


def _load_hmac_key() -> bytes:
    from api import _shared
    return _shared._AUDIT_HMAC_KEY


_SEARCHABLE_FIELDS = (
    "matter_code",
    "model",
    "backend",
    "data_region",
    "node_id",
    "user_hash",
    "salt_era",
    "query_hash",
    "timestamp",
)


def _open_jsonl(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


def _iter_entries_from(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with _open_jsonl(path) as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue


def _candidate_files(active: Path) -> list[Path]:
    files: list[Path] = []
    if active.exists():
        files.append(active)
    if active.parent.exists():
        rotations = sorted(
            active.parent.glob(f"{active.stem}-*.log.gz"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        files.extend(rotations)
    return files


# ── log_search ────────────────────────────────────────────────────────────

def _log_search_impl(query: str, max_results: int = 20) -> list[dict[str, Any]]:
    if not isinstance(max_results, int) or max_results < 1:
        max_results = 20
    if max_results > 500:
        max_results = 500
    active = _resolve_log_path()
    files = _candidate_files(active)
    needle = (query or "").lower()
    matches: list[dict[str, Any]] = []
    for f in files:
        for entry in _iter_entries_from(f):
            if needle:
                blob = " ".join(
                    str(entry.get(k, "")) for k in _SEARCHABLE_FIELDS
                ).lower()
                if needle not in blob:
                    continue
            matches.append(entry)
    matches.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return matches[:max_results]


# ── hmac_verify ────────────────────────────────────────────────────────────

_GENESIS_PREV = "0" * 64


def _expected_chain_hmac(entry_without_field: dict, prev: str, key: bytes) -> str:
    entry_json = json.dumps(entry_without_field, sort_keys=True)
    return _hmac.new(key, f"{prev}{entry_json}".encode(), sha256).hexdigest()


def _iter_log_entries_with_seq(active: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    if not active.parent.exists():
        return
    archives = sorted(active.parent.glob(f"{active.stem}-*.log.gz"))
    seq = 0
    for f in archives + ([active] if active.exists() else []):
        for entry in _iter_entries_from(f):
            seq += 1
            yield seq, entry


def _hmac_verify_impl(start_seq: int | None = None,
                      end_seq: int | None = None) -> dict[str, Any]:
    lo = max(1, int(start_seq)) if start_seq else 1
    hi = int(end_seq) if end_seq else None
    if hi is not None and hi < lo:
        return {
            "verified_count": 0, "total_count": 0,
            "first_failure_seq": None, "failures": [],
            "chain_intact": True,
            "error": f"end_seq ({hi}) < start_seq ({lo})",
        }
    key = _load_hmac_key()
    if not key:
        return {
            "verified_count": 0, "total_count": 0,
            "first_failure_seq": None, "failures": [],
            "chain_intact": False,
            "error": "LOCALLYAI_AUDIT_HMAC_KEY not configured at API startup.",
        }
    active = _resolve_log_path()
    prev = _GENESIS_PREV
    verified_count = 0
    total_count = 0
    first_failure_seq: int | None = None
    failures: list[dict[str, Any]] = []
    _FAILURE_CAP = 10
    for seq, entry in _iter_log_entries_with_seq(active):
        if hi is not None and seq > hi:
            break
        e = dict(entry)
        stored = e.pop("_chain_hmac", "")
        if not stored:
            continue
        in_range = seq >= lo
        expected = _expected_chain_hmac(e, prev, key)
        if in_range:
            total_count += 1
        match = _hmac.compare_digest(stored, expected)
        if match:
            if in_range:
                verified_count += 1
        else:
            if in_range:
                if first_failure_seq is None:
                    first_failure_seq = seq
                if len(failures) < _FAILURE_CAP:
                    failures.append({
                        "seq": seq,
                        "expected_hmac": expected,
                        "stored_hmac": stored,
                        "timestamp": entry.get("timestamp", ""),
                    })
        prev = stored
    return {
        "verified_count": verified_count,
        "total_count": total_count,
        "first_failure_seq": first_failure_seq,
        "failures": failures,
        "chain_intact": first_failure_seq is None,
    }


# ── time_range_query ───────────────────────────────────────────────────────

def _parse_iso(ts: str) -> datetime.datetime | None:
    if not ts:
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt


def _time_range_query_impl(
    start: str,
    end: str,
    event_type: str | None = None,
    user: str | None = None,
    max_results: int = 50,
) -> list[dict[str, Any]]:
    if not isinstance(max_results, int) or max_results < 1:
        max_results = 50
    if max_results > 500:
        max_results = 500
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    if start_dt is None or end_dt is None:
        return [{
            "error": "invalid_timestamp",
            "detail": f"start={start!r} end={end!r} — expected ISO-8601.",
        }]
    if end_dt < start_dt:
        return [{"error": "invalid_range",
                 "detail": f"end ({end}) precedes start ({start})."}]
    et = (event_type or "").strip().lower()
    user_needle = (user or "").strip().lower()
    active = _resolve_log_path()
    files = _candidate_files(active)
    matches: list[tuple[datetime.datetime, dict[str, Any]]] = []
    for f in files:
        for entry in _iter_entries_from(f):
            ts_dt = _parse_iso(entry.get("timestamp", ""))
            if ts_dt is None or ts_dt < start_dt or ts_dt > end_dt:
                continue
            if et:
                model = (entry.get("model") or "").strip()
                if et in ("chat",):
                    if model == "-" or not model:
                        continue
                elif et in ("non_chat", "non-chat", "admin"):
                    if model and model != "-":
                        continue
                else:
                    if et not in model.lower():
                        continue
            if user_needle:
                if user_needle not in (entry.get("user_hash") or "").lower():
                    continue
            matches.append((ts_dt, entry))
    matches.sort(key=lambda t: t[0])
    return [m[1] for m in matches[:max_results]]


# ── summary_stats ──────────────────────────────────────────────────────────

_VALID_GROUP_BY = ("user", "event_type", "hour_of_day", "day")


def _summary_stats_impl(
    group_by: str,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    gb = (group_by or "").strip().lower()
    if gb not in _VALID_GROUP_BY:
        return {
            "error": "invalid_group_by",
            "detail": (f"group_by={group_by!r} is not supported. "
                       f"Valid values: {list(_VALID_GROUP_BY)}."),
            "valid_group_by": list(_VALID_GROUP_BY),
        }
    start_dt = _parse_iso(start) if start else None
    end_dt = _parse_iso(end) if end else None
    if start and start_dt is None:
        return {"error": "invalid_timestamp",
                "detail": f"start={start!r} — expected ISO-8601."}
    if end and end_dt is None:
        return {"error": "invalid_timestamp",
                "detail": f"end={end!r} — expected ISO-8601."}
    active = _resolve_log_path()
    files = _candidate_files(active)
    counter: Counter[str] = Counter()
    total = 0
    for f in files:
        for entry in _iter_entries_from(f):
            ts_dt = _parse_iso(entry.get("timestamp", ""))
            if ts_dt is None:
                continue
            if start_dt and ts_dt < start_dt:
                continue
            if end_dt and ts_dt > end_dt:
                continue
            total += 1
            if gb == "user":
                key = (entry.get("user_hash") or "")[:16] or "(no user_hash)"
            elif gb == "event_type":
                model = (entry.get("model") or "").strip()
                key = "non_chat" if (model == "-" or not model) else "chat"
            elif gb == "hour_of_day":
                key = f"{ts_dt.hour:02d}"
            elif gb == "day":
                key = ts_dt.date().isoformat()
            else:                                        # pragma: no cover
                key = "(unreachable)"
            counter[key] += 1
    buckets = [{"key": k, "count": c} for k, c in counter.most_common()]
    return {
        "group_by":     gb,
        "total_events": total,
        "buckets":      buckets,
        "time_range":   {"start": start, "end": end},
    }


# ── Tool definitions (OpenAI function-call shape) ─────────────────────────

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "log_search",
            "description": (
                "Search LocallyAI's tamper-evident audit log by substring "
                "(case-insensitive) across the entry's metadata fields: "
                "matter_code, model, backend, data_region, node_id, "
                "user_hash, salt_era, query_hash, timestamp. "
                "The log is pseudonymised: usernames are SHA-256 hashes "
                "(field 'user_hash'); query text is never stored — only "
                "its SHA-256 ('query_hash'). Returns up to max_results "
                "entries newest-first. An empty query returns the most "
                "recent entries with no filter applied."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "Substring to match. Empty = most recent."},
                    "max_results": {"type": "integer", "default": 20,
                                    "minimum": 1, "maximum": 500},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hmac_verify",
            "description": (
                "Verify the cryptographic integrity of LocallyAI's audit "
                "log by recomputing the HMAC-SHA256 chain and comparing "
                "against the stored `_chain_hmac` per entry. Returns "
                "`chain_intact: true` if every entry's recomputed HMAC "
                "matches the stored value, or false with "
                "`first_failure_seq` pointing at the earliest break."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_seq": {"type": "integer", "minimum": 1},
                    "end_seq":   {"type": "integer", "minimum": 1},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "time_range_query",
            "description": (
                "Return audit-log entries whose `timestamp` falls inside "
                "a precise ISO-8601 window, optionally narrowed by "
                "event_type and user. event_type values: `chat` (model "
                "!= '-'), `non_chat`/`admin` (model == '-'), or any "
                "substring matched against the `model` field."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "string"},
                    "end":   {"type": "string"},
                    "event_type": {"type": "string"},
                    "user":  {"type": "string"},
                    "max_results": {"type": "integer", "default": 50,
                                    "minimum": 1, "maximum": 500},
                },
                "required": ["start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summary_stats",
            "description": (
                "Aggregate the audit log into per-bucket counts sorted "
                "descending. group_by is a fixed enum: `user`, "
                "`event_type`, `hour_of_day`, `day`. Optional start/end "
                "ISO-8601 timestamps narrow the window."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "group_by": {"type": "string",
                                 "enum": list(_VALID_GROUP_BY)},
                    "start": {"type": "string"},
                    "end":   {"type": "string"},
                },
                "required": ["group_by"],
            },
        },
    },
]


# ── Dispatch wrappers (chat-handler contract) ─────────────────────────────

def _log_search(arguments: dict, *, user: str,
                matter_code: str | None = None) -> dict:
    query = arguments.get("query", "")
    max_results = arguments.get("max_results", 20)
    results = _log_search_impl(query=str(query), max_results=max_results)
    return {"results": results, "count": len(results)}


def _hmac_verify(arguments: dict, *, user: str,
                 matter_code: str | None = None) -> dict:
    return _hmac_verify_impl(
        start_seq=arguments.get("start_seq"),
        end_seq=arguments.get("end_seq"),
    )


def _time_range_query(arguments: dict, *, user: str,
                      matter_code: str | None = None) -> dict:
    start = str(arguments.get("start", ""))
    end = str(arguments.get("end", ""))
    results = _time_range_query_impl(
        start=start,
        end=end,
        event_type=arguments.get("event_type"),
        user=arguments.get("user"),
        max_results=arguments.get("max_results", 50),
    )
    return {"results": results, "count": len(results)}


def _summary_stats(arguments: dict, *, user: str,
                   matter_code: str | None = None) -> dict:
    return _summary_stats_impl(
        group_by=str(arguments.get("group_by", "")),
        start=arguments.get("start"),
        end=arguments.get("end"),
    )


DISPATCH: dict[str, Callable[..., dict]] = {
    "log_search":       _log_search,
    "hmac_verify":      _hmac_verify,
    "time_range_query": _time_range_query,
    "summary_stats":    _summary_stats,
}

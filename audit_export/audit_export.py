import hashlib
import os, csv, io, logging
import sys as _sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse

# Ensure repo root is importable for the audit_reader helper.
_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in _sys.path:
    _sys.path.insert(0, str(_BASE))
from audit_reader import iter_filtered

log = logging.getLogger("audit_export")

router   = APIRouter(prefix="/export", tags=["Audit Export"])
BASE_DIR  = Path(__file__).resolve().parent.parent
LOG_FILE  = Path(os.environ.get("LOCALLYAI_LOG_DIR", str(BASE_DIR / "logs"))) / "audit.log"
MAX_RANGE_DAYS = 90

# Round-2: shared admin-auth dependency. Per-request env read so admin-key
# rotation propagates without restart, single source of truth for the
# guard so the four routers can't drift apart again.
from auth import admin_auth_dep
_auth = admin_auth_dep()

def _validate_dates(from_date: str, to_date: str):
    try:
        from_dt = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
        to_dt   = datetime.fromisoformat(to_date).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    if to_dt < from_dt:
        raise HTTPException(status_code=400, detail="to_date must be >= from_date.")
    delta = (to_dt - from_dt).days
    if delta > MAX_RANGE_DAYS:
        raise HTTPException(status_code=400, detail=f"Date range exceeds {MAX_RANGE_DAYS}-day maximum.")
    return from_dt, to_dt

def _hash_client(name: str) -> str:
    salt = os.environ.get("LOCALLYAI_AUDIT_SALT", "")
    return hashlib.sha256(f"{salt}:{name}".encode()).hexdigest()[:16]

def _iter_entries(from_dt, to_dt, client=None):
    """Stream-filter audit entries by date+client. Memory is bounded by
    one parsed entry at a time instead of preloading the whole file
    (round-2 B4)."""
    client_hash = _hash_client(client) if client else None

    def _predicate(e: dict) -> bool:
        ts_str = (e.get("timestamp", "") or "").strip()
        if not ts_str:
            return False
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        try:
            ts = datetime.fromisoformat(ts_str)
        except Exception:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if not (from_dt <= ts <= to_dt):
            return False
        if client_hash and e.get("user_hash", "") != client_hash:
            return False
        return True

    yield from iter_filtered(LOG_FILE, _predicate)

@router.get("/")
def export_csv(
    from_date: str = Query(..., description="YYYY-MM-DD"),
    to_date:   str = Query(..., description="YYYY-MM-DD"),
    client: Optional[str] = Query(None, max_length=128),
    _user: str = Depends(_auth),
):
    from_dt, to_dt = _validate_dates(from_date, to_date)
    def generate():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["timestamp","user_hash","model","sources","latency_ms","backend","query_hash","matter_code"])
        yield buf.getvalue()
        for e in _iter_entries(from_dt, to_dt, client):
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow([e.get("timestamp",""),e.get("user_hash",""),e.get("model",""),
                        e.get("sources",""),e.get("latency_ms",""),
                        e.get("backend",""),e.get("query_hash",""),e.get("matter_code","")])
            yield buf.getvalue()
    fname = "audit_" + from_date + "_" + to_date + ".csv"
    headers = {"Content-Disposition": "attachment; filename=" + fname}
    return StreamingResponse(generate(), media_type="text/csv", headers=headers)

@router.get("/summary")
def export_summary(
    from_date: str = Query(..., description="YYYY-MM-DD"),
    to_date:   str = Query(..., description="YYYY-MM-DD"),
    client: Optional[str] = Query(None, max_length=128),
    _user: str = Depends(_auth),
):
    from_dt, to_dt = _validate_dates(from_date, to_date)
    # System events (admin_key_rotation, salt_era_boundary, etc.) have no
    # user_hash because they're not user-attributable. Bucket them under
    # the SYSTEM_USER_HASH sentinel defined centrally in config.py so the
    # UI's hash-shaped rendering stays consistent and audit_export +
    # monitor agree on the same value (round-2 B9).
    from config import SYSTEM_USER_HASH
    by_user = {}
    total_queries = 0
    for e in _iter_entries(from_dt, to_dt, client):
        total_queries += 1
        u = e.get("user_hash") or SYSTEM_USER_HASH
        if u not in by_user:
            by_user[u] = {"queries": 0, "total_sources": 0, "total_latency_ms": 0.0, "matter_codes": set()}
        by_user[u]["queries"] += 1
        by_user[u]["total_sources"]   += int(e.get("sources", 0) or 0)
        by_user[u]["total_latency_ms"] += float(e.get("latency_ms", 0) or 0)
        mc = e.get("matter_code", "")
        if mc:
            by_user[u]["matter_codes"].add(mc)
    for u in by_user:
        q = by_user[u]["queries"]
        by_user[u]["avg_latency_ms"] = round(by_user[u].pop("total_latency_ms") / q, 2) if q else 0
        by_user[u]["matter_codes"]   = sorted(by_user[u]["matter_codes"])
    return {
        "period": {"from": from_date, "to": to_date},
        "total_queries": total_queries,
        "by_user": by_user,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

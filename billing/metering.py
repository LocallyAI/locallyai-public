import hmac as _hmac_mod
import json
import logging
import os
import sys as _sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query

# Ensure repo root is importable for the shared auth helper.
_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in _sys.path:
    _sys.path.insert(0, str(_BASE))
from auth import admin_auth_dep

log = logging.getLogger("billing.metering")

# Round-2 B12: verify the billing chain on read. Phase-2 F added the
# HMAC on writes; without read-side verification an attacker with
# file-level access could forge a billing entry and the invoice
# endpoint would return it as authoritative.
_AUDIT_HMAC_KEY = os.environ.get("LOCALLYAI_AUDIT_HMAC_KEY", "").encode("utf-8")


def _expected_chain_hmac(entry: dict, prev: str) -> str:
    if not _AUDIT_HMAC_KEY:
        return ""
    payload = {k: v for k, v in entry.items() if k != "_chain_hmac"}
    entry_json = json.dumps(payload, sort_keys=True)
    return _hmac_mod.new(_AUDIT_HMAC_KEY, f"{prev}{entry_json}".encode(), "sha256").hexdigest()

router = APIRouter(prefix="/billing", tags=["Billing"])

# Billing reads from billing.log (real user names for invoicing).
# audit.log stores only pseudonymised user hashes (GDPR compliance).
BILLING_LOG = (Path(__file__).resolve().parent.parent / "logs" / "billing.log")

_auth = admin_auth_dep()

def _parse_entries(from_date: str, to_date: str, client: Optional[str] = None):
    """Parse + chain-verify billing entries. The chain is verified
    cumulatively from the start of the file so a forged entry anywhere
    breaks all subsequent reads (round-2 B12). Filtered entries are
    still chain-verified; we just don't include them in the result.
    """
    if not BILLING_LOG.exists():
        return []
    try:
        from_dt = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
        to_dt = datetime.fromisoformat(to_date).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    entries: list = []
    prev_hash = "0" * 64
    line_no = 0
    with open(BILLING_LOG, encoding="utf-8", errors="replace") as _fh:
        for line in _fh:
            line_no += 1
            try:
                e = json.loads(line)
            except Exception:
                continue
            # Chain verification (cumulative). Skip entries that pre-date
            # the chain (missing _chain_hmac on an entry while the rest
            # of the file has chained means the chain was rebooted —
            # treat as informational only when the empty-key path is
            # active).
            stored = e.get("_chain_hmac", "")
            if _AUDIT_HMAC_KEY and stored:
                expected = _expected_chain_hmac(e, prev_hash)
                if stored != expected:
                    log.error(f"Billing chain mismatch at line {line_no}; refusing read")
                    raise HTTPException(status_code=500, detail=f"Billing chain broken at line {line_no}")
                prev_hash = stored
            elif _AUDIT_HMAC_KEY and not stored:
                # Key configured but this entry has no chain stamp — fail closed.
                log.error(f"Billing entry at line {line_no} missing _chain_hmac while HMAC key is set")
                raise HTTPException(status_code=500, detail=f"Billing chain broken at line {line_no}")
            ts_str = (e.get("timestamp", "") or "").strip()
            if ts_str.endswith("Z"):
                ts_str = ts_str[:-1] + "+00:00"
            try:
                ts = datetime.fromisoformat(ts_str)
            except Exception:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if not (from_dt <= ts <= to_dt):
                continue
            if client and e.get("user", "").lower() != client.lower():
                continue
            entries.append(e)
    return entries

@router.get("/usage")
def usage(
    from_date: str = Query(..., description="YYYY-MM-DD"),
    to_date: str = Query(..., description="YYYY-MM-DD"),
    client: Optional[str] = Query(None),
    key: str = Depends(_auth),
):
    entries = _parse_entries(from_date, to_date, client)
    clients = {}
    for e in entries:
        user = e.get("user", "unknown")
        if user not in clients:
            clients[user] = {"total_queries": 0, "first_query": None, "last_query": None, "models_used": {}, "_retrieval_times": []}
        c = clients[user]
        c["total_queries"] += 1
        ts = e.get("timestamp")
        if ts:
            if not c["first_query"] or ts < c["first_query"]:
                c["first_query"] = ts
            if not c["last_query"] or ts > c["last_query"]:
                c["last_query"] = ts
        model = e.get("model", "unknown")
        c["models_used"][model] = c["models_used"].get(model, 0) + 1
        rt = e.get("retrieval_ms")
        if rt is not None:
            c["_retrieval_times"].append(float(rt))
    result = {}
    for user, c in clients.items():
        rt_list = c.pop("_retrieval_times")
        c["avg_retrieval_ms"] = round(sum(rt_list) / len(rt_list), 2) if rt_list else None
        result[user] = c
    return {
        "period": {"from": from_date, "to": to_date},
        "clients": result,
        "total_queries": len(entries),
        "generated_at": datetime.now(timezone.utc).isoformat()
    }

@router.get("/invoice/{client_name}")
def invoice(
    client_name: str,
    from_date: str = Query(..., description="YYYY-MM-DD"),
    to_date: str = Query(..., description="YYYY-MM-DD"),
    monthly_fee: float = Query(..., description="Agreed monthly flat fee in GBP (excl. VAT)"),
    key: str = Depends(_auth),
):
    entries = _parse_entries(from_date, to_date, client_name)

    try:
        from_dt = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
        to_dt   = datetime.fromisoformat(to_date).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # Pro-rate for periods shorter or longer than one calendar month
    days   = (to_dt - from_dt).days + 1
    months = round(days / 30.4375, 4)  # average Gregorian month length

    subtotal = round(monthly_fee * months, 2)
    vat      = round(subtotal * 0.20, 2)
    total    = round(subtotal + vat, 2)

    # Usage breakdown — for client transparency only, not used for pricing
    daily = {}
    for e in entries:
        day = e.get("timestamp", "")[:10]
        daily[day] = daily.get(day, 0) + 1

    return {
        "client": client_name,
        "period": {"from": from_date, "to": to_date, "days": days, "months_prorated": months},
        "billing_model": "flat_fee",
        "monthly_fee_gbp": monthly_fee,
        "subtotal": subtotal,
        "vat_20_pct": vat,
        "total": total,
        "currency": "GBP",
        "usage_summary": {
            "total_queries": len(entries),
            "daily_breakdown": {d: daily[d] for d in sorted(daily)},
        },
        "note": "Flat-fee managed service. Usage summary is provided for transparency only and does not affect the invoice amount.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

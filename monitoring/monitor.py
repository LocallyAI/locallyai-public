import json
import os
import shutil
import sys as _sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends

# Ensure repo root is importable for the audit_reader helper + config.
_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in _sys.path:
    _sys.path.insert(0, str(_BASE))
from audit_reader import count_lines, iter_reversed_lines, tail
from auth import admin_auth_dep
from config import SYSTEM_USER_HASH

router = APIRouter(prefix="/monitor", tags=["Monitor"])

AUDIT_LOG = (Path(__file__).resolve().parent.parent / "logs" / "audit.log")
OLLAMA_URL = "http://localhost:11434/api/tags"

_auth = admin_auth_dep()


def _backend() -> str:
    return os.environ.get("LOCALLYAI_BACKEND", "").lower()


def _backend_check() -> tuple[bool, dict]:
    """Backend-aware health probe. The previous code blindly pinged
    Ollama on :11434 regardless of which backend was configured, so
    MLX deployments saw a permanent CRITICAL "Ollama unreachable"
    alert. This dispatches based on LOCALLYAI_BACKEND."""
    b = _backend()
    if b == "mlx":
        # MLX runs in-process. If the API responded to this request,
        # MLX successfully loaded at startup (a load failure would
        # have raised SystemExit). Report inference-gate state as the
        # backend health surface.
        try:
            from inference_gate import stats as _gate_stats
            return True, {"backend": "mlx", "gate": _gate_stats()}
        except Exception as e:
            return False, {"backend": "mlx", "error": str(e)}
    if b == "lmstudio":
        url = os.environ.get("LMSTUDIO_URL", "http://localhost:1234/v1/models")
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return True, {"backend": "lmstudio", "detail": "reachable"}
        except Exception as e:
            return False, {"backend": "lmstudio", "error": str(e)}
    if b == "ollama":
        try:
            with urllib.request.urlopen(OLLAMA_URL, timeout=3) as r:
                return True, {"backend": "ollama", "models": json.loads(r.read())}
        except Exception as e:
            return False, {"backend": "ollama", "error": str(e)}
    return True, {"backend": b or "unknown", "detail": "no probe for this backend"}

def _disk_free_gb(path=None):
    path = path or os.environ.get("LOCALLYAI_DISK_CHECK_PATH", "C:\\" if os.name == "nt" else "/")
    usage = shutil.disk_usage(path)
    return round(usage.free / (1024**3), 2)

def _audit_stats():
    """Tail the last 5 entries + report size, without preloading the
    file (round-2 B5). Line count via fs primitive (fast on large logs)."""
    if not AUDIT_LOG.exists():
        return None
    size = AUDIT_LOG.stat().st_size
    last5 = []
    for line in tail(AUDIT_LOG, 5):
        try:
            d = json.loads(line)
            if not d.get("user_hash"):
                d["user_hash"] = SYSTEM_USER_HASH
            last5.append(d)
        except Exception:
            last5.append({"raw": line})
    return {"line_count": count_lines(AUDIT_LOG), "size_bytes": size, "last_5": last5}

@router.get("/health/detailed")
def health_detailed(key: str = Depends(_auth)):
    backend_ok, backend_data = _backend_check()
    disk_gb = _disk_free_gb()
    audit = _audit_stats()

    # Watchdog agent status
    try:
        from watchdog import sentinel as _sent
        watchdog_status = _sent.status()
    except Exception as e:
        watchdog_status = {"error": str(e)}
    # Inference concurrency gate state. Surfaces in_flight + queued so an
    # operator can see queue pressure live and tune
    # LOCALLYAI_MAX_CONCURRENT_INFERENCE before users complain. The
    # fleet dashboard uses these counters to render per-node load.
    try:
        from inference_gate import stats as _gate_stats
        gate = _gate_stats()
    except Exception as e:
        gate = {"error": str(e)}
    # Retrieval timings — last call only; used to tune CANDIDATE_POOL,
    # TOP_K, and to confirm the cross-encoder reranker actually loaded
    # (rerank_ms == 0 means it's running in degraded RRF-only mode).
    try:
        from retrieval import get_last_retrieve_timings as _retr_timings
        retrieve_timings = _retr_timings()
    except Exception as _exc:
        retrieve_timings = {"error": str(_exc)}
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "backend": {"name": _backend(), "reachable": backend_ok, "detail": backend_data},
        "disk_free_gb": disk_gb,
        "audit_log": audit if audit else {"error": "audit.log not found"},
        "watchdog": watchdog_status,
        "inference_gate": gate,
        "retrieve_last_call": retrieve_timings,
    }

@router.get("/alerts")
def alerts(key: str = Depends(_auth)):
    result = []
    overall = "ok"
    # Backend-aware health check — Ollama alert no longer fires on
    # MLX / LM Studio deployments. The probe URL/endpoint dispatches
    # on LOCALLYAI_BACKEND so each backend reports its real state.
    backend_ok, backend_data = _backend_check()
    if not backend_ok:
        result.append({
            "level": "critical",
            "message": f"Inference backend ({_backend() or 'unknown'}) is unreachable: {backend_data.get('error', '?')}",
        })
        overall = "critical"
    if not AUDIT_LOG.exists():
        result.append({"level": "warning", "message": "audit.log not found"})
        if overall == "ok":
            overall = "degraded"
    else:
        disk_gb = _disk_free_gb()
        if disk_gb < 10:
            result.append({"level": "critical", "message": f"Low disk space: {disk_gb}GB free"})
            overall = "critical"
        # Round-2 B5: scan from EOF backwards, stop at first entry within
        # the 24h cutoff. Old code preloaded the whole audit.log via
        # read_text().splitlines() and only then reversed.
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        recent = False
        for line in iter_reversed_lines(AUDIT_LOG):
            try:
                entry = json.loads(line)
                ts_str = (entry.get("timestamp", "") or "").strip()
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if ts > cutoff:
                    recent = True
                    break
            except Exception:
                continue
        if not recent:
            result.append({"level": "info", "message": "No queries recorded in the last 24 hours"})

    # Merge Sentinel predictive alerts
    try:
        from watchdog.sentinel import get_alerts as _sent_alerts
        for sa in _sent_alerts():
            result.append({"level": sa["level"], "message": sa["message"]})
            if sa["level"] == "critical" and overall != "critical":
                overall = "critical"
            elif sa["level"] == "warning" and overall == "ok":
                overall = "degraded"
    except Exception:
        pass

    # Watchdog agent status. We intentionally invoke `_sent.status()`
    # for its side effects (warm-up + telemetry), but the return value
    # is not currently merged into /alerts. Tracked: surface it as
    # `status.watchdog` alongside the alerts in a later sitting.
    try:
        from watchdog import sentinel as _sent
        _sent.status()
    except Exception:
        pass
    return {"alerts": result, "status": overall}



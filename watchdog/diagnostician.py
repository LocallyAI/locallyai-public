# Agent 4: Diagnostician — crash signature matching + autonomous remediation
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends

# Ensure repo root is importable for the shared auth helper.
_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))
from auth import admin_auth_dep

_FIX_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

router   = APIRouter(prefix="/diagnostician", tags=["Diagnostician"])

LOG_DIR  = Path(__file__).resolve().parent.parent / "logs"
DIAG_LOG = LOG_DIR / "diagnostician.log"
PENDING  = LOG_DIR / "pending_fixes.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_auth = admin_auth_dep()

def _log(event: str, detail: str = ""):
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(),
             "event": event, "detail": detail}
    with open(DIAG_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

# ── Known remediations catalogue ─────────────────────────────────────────────
# Each entry: signature regex, description, autonomous (bool), fix callable or None
PRODUCTION_DIR = Path(__file__).resolve().parent.parent

def _fix_qdrant_lock():
    _storage_env = os.environ.get("LOCALLYAI_STORAGE_DIR", "")
    storage = Path(_storage_env) if _storage_env else PRODUCTION_DIR / "storage"
    lock = storage / ".lock"
    if lock.exists():
        lock.unlink()
        return f"Qdrant .lock file removed from {storage}"
    return "No lock file found"

def _fix_port_conflict():
    if sys.platform == "win32":
        result = os.popen("netstat -ano | findstr :8000").read()
        pids = re.findall(r"\s+(\d+)\s*$", result, re.MULTILINE)
        killed = []
        for pid in set(pids):
            try:
                subprocess.run(f"taskkill /PID {pid} /F >nul 2>&1", shell=False, timeout=30)
                killed.append(pid)
            except Exception:
                pass
        return f"Killed PIDs on :8000 — {killed}" if killed else "No blocking process found"
    return "Not on Windows"

def _fix_dep_version(package: str):
    reqs = PRODUCTION_DIR / "requirements.txt"
    if reqs.exists():
        return f"Check requirements.txt for {package} version pin"
    return f"requirements.txt not found; reinstall {package}"

REMEDIATIONS = [
    {
        "code": "QDRANT_LOCK",
        "pattern": re.compile(r"qdrant|\.lock|lock.*file", re.I),
        "description": "Qdrant file lock preventing startup",
        "autonomous": True,
        "fix": _fix_qdrant_lock,
    },
    {
        "code": "PORT_IN_USE",
        "pattern": re.compile(r"address already in use|Only one usage.*socket|port.*8000", re.I),
        "description": "Port 8000 already occupied by a prior process",
        "autonomous": True,
        "fix": _fix_port_conflict,
    },
    {
        "code": "IMPORT_ERROR",
        "pattern": re.compile(r"ModuleNotFoundError|ImportError|No module named", re.I),
        "description": "Missing or broken Python dependency",
        "autonomous": False,
        "fix": None,
        "suggestion": "Run: pip install -r requirements.txt",
    },
    {
        "code": "OOM",
        "pattern": re.compile(r"MemoryError|cannot allocate|out of memory", re.I),
        "description": "Out-of-memory error during model inference",
        "autonomous": False,
        "fix": None,
        "suggestion": "Reduce concurrent requests or switch to a smaller model variant",
    },
    {
        "code": "OLLAMA_TIMEOUT",
        "pattern": re.compile(r"timed out|timeout.*ollama|ollama.*timeout", re.I),
        "description": "Ollama inference timeout — model may be overloaded",
        "autonomous": False,
        "fix": None,
        "suggestion": "Restart Ollama service and check model size vs available VRAM",
    },
]

def _match_signature(text: str) -> dict | None:
    for rem in REMEDIATIONS:
        if rem["pattern"].search(text):
            return rem
    return None

def _load_pending() -> list:
    if PENDING.exists():
        try:
            return json.loads(PENDING.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def _save_pending(items: list):
    PENDING.write_text(json.dumps(items, indent=2), encoding="utf-8")

# ── API Endpoints ─────────────────────────────────────────────────────────────

@router.post("/analyse")
def analyse(body: dict, key: str = Depends(_auth)):
    """
    Accepts {"error_text": "..."} — matches against remediation catalogue.
    Autonomous fixes run immediately. Human-required fixes queued as pending.
    """
    error_text = body.get("error_text", "")
    if not error_text:
        raise HTTPException(status_code=400, detail="error_text required")

    match = _match_signature(error_text)
    if not match:
        _log("no_match", error_text[:200])
        return {"matched": False, "message": "No known remediation. Manual review required.", "error_preview": error_text[:300]}

    result = {
        "matched": True,
        "code": match["code"],
        "description": match["description"],
        "autonomous": match["autonomous"],
    }

    if match["autonomous"] and match.get("fix"):
        try:
            outcome = match["fix"]()
            _log("autonomous_fix_applied", f"{match['code']}: {outcome}")
            result["action"] = "fix_applied"
            result["outcome"] = outcome
        except Exception as e:
            _log("autonomous_fix_failed", str(e))
            result["action"] = "fix_failed"
            result["outcome"] = str(e)
    else:
        suggestion = match.get("suggestion", "Manual review required")
        pending = _load_pending()
        fix_id = f"{match['code']}_{int(time.time())}"
        pending.append({
            "id": fix_id,
            "code": match["code"],
            "description": match["description"],
            "suggestion": suggestion,
            "error_preview": error_text[:500],
            "status": "pending_approval",
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        _save_pending(pending)
        _log("queued_for_approval", f"{match['code']} id={fix_id}")
        result["action"] = "queued"
        result["fix_id"] = fix_id
        result["suggestion"] = suggestion
        result["message"] = "Fix queued for human approval. Review at GET /diagnostician/pending"

    return result

@router.get("/pending")
def pending_fixes(key: str = Depends(_auth)):
    return {"pending": _load_pending()}

@router.post("/approve/{fix_id}")
def approve_fix(fix_id: str, key: str = Depends(_auth)):
    items = _load_pending()
    item  = next((i for i in items if i["id"] == fix_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Fix ID not found")
    item["status"]      = "approved"
    item["approved_at"] = datetime.now(timezone.utc).isoformat()
    _save_pending(items)
    _log("fix_approved", fix_id)
    return {"approved": True, "fix_id": fix_id, "suggestion": item["suggestion"],
            "message": "Approved. Apply manually or trigger restart to take effect."}

@router.post("/reject/{fix_id}")
def reject_fix(fix_id: str, key: str = Depends(_auth)):
    items   = _load_pending()
    updated = [i for i in items if i["id"] != fix_id]
    _save_pending(updated)
    _log("fix_rejected", fix_id)
    return {"rejected": True, "fix_id": fix_id}

@router.get("/history")
def history(limit: int = 50, key: str = Depends(_auth)):
    if not DIAG_LOG.exists():
        return {"entries": []}
    lines = DIAG_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except Exception:
            entries.append({"raw": line})
    return {"entries": entries}
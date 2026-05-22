# Agent 3: Resurrector — staged recovery executor
import argparse
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

BASE_DIR  = Path(__file__).resolve().parent.parent
LOG_DIR   = BASE_DIR / "logs"
RES_LOG   = LOG_DIR / "resurrector.log"
LOG_DIR.mkdir(parents=True, exist_ok=True)

API_BASE   = os.environ.get("LOCALLYAI_API_BASE", "http://localhost:8000")
ADMIN_KEY  = os.environ.get("LOCALLYAI_ADMIN_KEY", "")
PID_FILE   = LOG_DIR / "api.pid"
# Sentinel read by supervisor.py — when present, the next API child is launched
# with SAFE_MODE=1. We don't spawn the API ourselves: the supervisor owns that.
SAFE_MODE_FLAG = LOG_DIR / "safe_mode.flag"

# Embedded-Qdrant lock file. Only relevant when QDRANT_URL is unset (no server).
_STORAGE_ENV = os.environ.get("LOCALLYAI_STORAGE_DIR", "")
QDRANT_URL   = os.environ.get("QDRANT_URL", "")
QDRANT_LOCK  = (
    Path(_STORAGE_ENV) if _STORAGE_ENV else BASE_DIR / "storage"
) / ".lock"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [RESURRECTOR] %(message)s")
log = logging.getLogger("resurrector")


def _log(event: str, stage: str, detail: str = ""):
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": event,
        "stage": stage,
        "detail": detail,
    }
    with open(RES_LOG, "a", encoding="utf-8") as f:
        f.write(__import__("json").dumps(entry) + "\n")
    log.info(f"[{stage}] {event}: {detail}")


import ssl

# Self-signed cert on loopback -- skip verification.
_PROBE_SSL_CTX = ssl.create_default_context()
_PROBE_SSL_CTX.check_hostname = False
_PROBE_SSL_CTX.verify_mode = ssl.CERT_NONE


def _probe(timeout: int = 10) -> bool:
    # /healthz is unauthenticated by design; the resurrector should not need
    # to hold an API key to verify post-restart health.
    try:
        req = urllib.request.Request(f"{API_BASE}/healthz")
        with urllib.request.urlopen(req, timeout=timeout, context=_PROBE_SSL_CTX) as r:
            return r.status == 200
    except Exception:
        return False


def _set_safe_mode_flag(on: bool):
    """Persist (or clear) the safe-mode sentinel that the supervisor reads on
    spawn. Sticky: stays in place across restarts until cleared, so we don't
    silently drop out of safe mode."""
    try:
        if on:
            SAFE_MODE_FLAG.write_text("1\n", encoding="utf-8")
            _log("safe_mode_set", "stage3", str(SAFE_MODE_FLAG))
        else:
            if SAFE_MODE_FLAG.exists():
                SAFE_MODE_FLAG.unlink()
                _log("safe_mode_cleared", "stage1", str(SAFE_MODE_FLAG))
    except Exception as exc:
        _log("safe_mode_flag_error", "stage", str(exc))


def _recycle_api(timeout: int = 90) -> bool:
    """Ask the supervisor to restart its API child by killing the current one.

    The supervisor's main loop notices `api_proc.poll() != None`, runs its
    own pre-flight `_kill_stale_listeners`, then spawns a new API child with
    the correct TLS flags. We never spawn API ourselves — that's what created
    plain-HTTP orphans squatting on :8000.

    Returns True once `/healthz` answers, False on timeout.
    """
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if sys.platform == "win32":
                subprocess.call(
                    ["taskkill", "/PID", str(pid), "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                )
            else:
                os.kill(pid, signal.SIGTERM)
            _log("recycle_signaled", "recycle", f"PID {pid}")
        except ProcessLookupError:
            _log("recycle_pid_gone", "recycle", str(pid))
        except Exception as exc:
            _log("recycle_failed", "recycle", str(exc))
            return False
    else:
        _log("recycle_no_pid_file", "recycle", str(PID_FILE))

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(2)
        if _probe(timeout=5):
            _log("recycle_recovered", "recycle", f"after {int(timeout - (deadline - time.monotonic()))}s")
            return True
    _log("recycle_timeout", "recycle", f"{timeout}s")
    return False


def _check_ollama() -> bool:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=10):
            return True
    except Exception:
        return False


def _restart_ollama():
    _log("restarting_ollama", "stage2")
    subprocess.Popen(
        ["ollama", "serve"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    time.sleep(5)


def _clear_qdrant_lock():
    # When QDRANT_URL is set, storage runs in a remote server and the local
    # .lock file is irrelevant (and may not even exist). Skip to avoid noise.
    if QDRANT_URL:
        _log("qdrant_lock_skipped", "stage2", "QDRANT_URL set — server mode")
        return
    if QDRANT_LOCK.exists():
        try:
            QDRANT_LOCK.unlink()
            _log("qdrant_lock_cleared", "stage2", str(QDRANT_LOCK))
        except Exception as exc:
            _log("qdrant_lock_clear_failed", "stage2", str(exc))
    else:
        _log("qdrant_lock_absent", "stage2")


def _send_alert(message: str, level: str = "warning"):
    try:
        from watchdog.alerting import send_alert
        send_alert(message, level)
    except Exception as exc:
        log.error(f"Alert send failed: {exc}")


def _diagnostic_dump(reason: str) -> str:
    lines = [
        f"RECOVERY FAILED — {datetime.now(UTC).isoformat()}",
        f"Trigger: {reason}",
    ]
    try:
        import psutil
        mem = psutil.virtual_memory()
        disk_path = "C:\\" if sys.platform == "win32" else "/"
        disk = psutil.disk_usage(disk_path)
        lines.append(f"RAM: {mem.percent}% used ({mem.available // 1024 // 1024}MB free)")
        lines.append(f"Disk: {disk.free // 1024 // 1024 // 1024}GB free")
    except ImportError:
        lines.append("psutil not available")
    audit = LOG_DIR / "audit.log"
    if audit.exists():
        tail = audit.read_text(encoding="utf-8", errors="replace").splitlines()[-10:]
        lines.append("Last 10 audit entries:")
        lines.extend(tail)
    dump_path = LOG_DIR / f"crash_dump_{int(time.time())}.txt"
    dump_path.write_text("\n".join(lines), encoding="utf-8")
    return str(dump_path)


def recover(reason: str):
    _log("recovery_started", "init", reason)
    # Always start a recovery cycle from a clean (non-safe) state. If we end
    # up needing safe mode, stage 3 sets the flag again.
    _set_safe_mode_flag(False)

    # Stage 1: soft restart — supervisor respawns with TLS.
    _log("stage1_begin", "stage1", "soft restart")
    if _recycle_api():
        _log("RECOVERED_SOFT", "stage1")
        _send_alert(f"Recovered after soft restart. Trigger: {reason}", "info")
        return

    # Stage 2: dependency check + restart.
    _log("stage2_begin", "stage2", "dependency check")
    if not _check_ollama():
        _restart_ollama()
    _clear_qdrant_lock()
    if _recycle_api():
        _log("RECOVERED_HARD", "stage2")
        _send_alert(
            f"Recovered after hard restart + dependency fix. Trigger: {reason}", "warning"
        )
        return

    # Stage 3: safe mode (vector search disabled, basic LLM only). The flag
    # is sticky — operator must clear logs/safe_mode.flag to leave safe mode.
    _log("stage3_begin", "stage3", "safe mode")
    _set_safe_mode_flag(True)
    if _recycle_api():
        _log("RECOVERED_DEGRADED", "stage3")
        _send_alert(
            f"Running in SAFE MODE — vector search disabled. Manual review required. "
            f"Remove {SAFE_MODE_FLAG} to leave safe mode. Trigger: {reason}",
            "warning",
        )
        return

    # Stage 4: all stages failed — clear the safe-mode flag (no point staying
    # in degraded mode if the API can't even bind), alert, and stand down.
    _set_safe_mode_flag(False)
    _log("RECOVERY_FAILED", "stage4")
    dump = _diagnostic_dump(reason)
    _send_alert(
        f"CRITICAL: All recovery stages failed. Manual intervention required. "
        f"Trigger: {reason}. Diagnostic dump: {dump}",
        "critical",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reason", default="unknown")
    args = parser.parse_args()
    recover(args.reason)

"""
telemetry.py — opt-in vendor health telemetry.

Each firm's office Mac periodically posts an anonymised health snapshot
to the vendor's monitoring Worker. Used by the LocallyAI on-call team
to spot incidents within the 4-hour SLA window. Off by default —
firms must explicitly enable in .env.

Privacy guarantees (anything beyond this list is a bug):
  WHAT GETS SENT
    firm_id           SHA-256 of LOCALLYAI_FIRM_NAME (one-way; not the name itself)
    node_id           NODE_ID (already in audit log; deployment identifier)
    version           Currently-applied release tag
    healthz_ok        bool — does /healthz return 200?
    sentinel_ok       bool — is the watchdog thread alive?
    backend           "mlx" | "ollama" | "lmstudio"
    region            "UK" | "KSA"
    uptime_seconds    process uptime
    api_pid_age_h     how long has the API been up (catches restart loops)
    free_disk_gb      gauge for disk-pressure alerts
    free_mem_gb       gauge for OOM alerts
    last_audit_event  category-only ("chat_completion" / "document_deleted") — never the content
    error_count_24h   counter
    self_heals_24h    {action_code: count}
    alert_codes       list of structured short codes if anything is firing
                      (e.g. ["healthz_failed", "ollama_runner_terminated"])
  WHAT NEVER GETS SENT
    firm name (only the hash); user names; document content; query text;
    chat responses; audit log entries; billing entries; secrets; IPs;
    LAN topology; conversation history; embeddings; file paths or
    filenames from the corpus.

The vendor receives a stream of {what's broken, how often, on which
firm} — never {what users are asking or what's in the documents}.

Configuration in .env (operator):
  LOCALLYAI_TELEMETRY=on              opt-in switch (default off)
  LOCALLYAI_TELEMETRY_URL=https://locallyai-monitor.<acct>.workers.dev/
  LOCALLYAI_TELEMETRY_TOKEN=...        per-firm auth token (vendor issues)
  LOCALLYAI_TELEMETRY_INTERVAL=300     heartbeat interval seconds (default 5 min)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import socket
import threading
import time
import urllib.request
import urllib.error
from collections import defaultdict
from typing import Optional


log = logging.getLogger("telemetry")

REPO_DIR = os.path.dirname(os.path.abspath(__file__))


_TRUTHY = {"1", "on", "true", "yes", "y", "t"}


def _enabled() -> bool:
    """Accept any common truthy value. The form/bootstrap writes "1";
    install.sh historically wrote "on"; humans editing .env type "true"
    or "yes". Honour all of them — refusing on a typo silently turns
    telemetry off, which is far worse than being permissive on input."""
    return os.environ.get("LOCALLYAI_TELEMETRY", "off").strip().lower() in _TRUTHY


def _firm_id() -> str:
    """SHA-256 of the firm name truncated to 16 hex. One-way: the
    vendor sees a stable identifier across heartbeats but cannot
    reverse-engineer the firm's name."""
    name = os.environ.get("LOCALLYAI_FIRM_NAME", "").strip()
    if not name:
        # Fall back to office hostname so untagged deployments still
        # produce stable IDs (vendor may not know the name yet).
        name = os.environ.get("LOCALLYAI_OFFICE_HOST", socket.gethostname())
    return hashlib.sha256(f"locallyai-firm:{name}".encode()).hexdigest()[:16]


# ── State (module-level — shared between heartbeat thread + alert calls) ─────
_state_lock = threading.Lock()
_self_heals_24h: dict[str, int] = defaultdict(int)
_error_count_24h = 0
_pending_alerts: list[dict] = []  # alerts queued for next heartbeat
_started_at = time.time()
_last_audit_event: str = ""
# Per-(code,severity) suppression window so a sticky condition (low disk,
# OOM, healthz-failed) doesn't fire one alert per 60s sentinel tick — the
# vendor email box was filling because every tick re-emailed the same
# critical. The alert still gets queued for the dashboard via the regular
# heartbeat; suppression only skips the immediate-post + the
# pending-queue duplicate within the window. Tunable via
# LOCALLYAI_ALERT_DEDUPE_SECONDS (default 14400 = 4 h).
#
# Persistence: state is loaded from storage/.alert_dedupe.json on
# import and rewritten atomically on every update. Without this, a
# process restart (uvicorn reload, crash + LaunchAgent kick) re-arms
# every code — sticky conditions then re-email on the next sentinel
# tick. The worker-side per-(firm,code) open-alert dedupe is the
# authoritative defence; this local layer is the bandwidth saver
# (no POST at all for repeat fires).
_DEDUPE_SECONDS = int(os.environ.get("LOCALLYAI_ALERT_DEDUPE_SECONDS", "14400"))
try:
    from config import STORAGE_DIR as _STORAGE_DIR  # type: ignore[attr-defined]
    _DEDUPE_FILE = _STORAGE_DIR / ".alert_dedupe.json"
except Exception:
    # Importable in tests / CI where the full app stack isn't present.
    _DEDUPE_FILE = None  # type: ignore[assignment]


def _load_dedupe() -> dict[str, float]:
    if _DEDUPE_FILE is None or not _DEDUPE_FILE.exists():
        return {}
    try:
        d = json.loads(_DEDUPE_FILE.read_text(encoding="utf-8"))
        return {k: float(v) for k, v in d.items() if isinstance(v, (int, float))}
    except Exception as exc:
        log.warning("Corrupt alert-dedupe state — resetting: %s", exc)
        return {}


def _save_dedupe(d: dict[str, float]) -> None:
    if _DEDUPE_FILE is None:
        return
    try:
        _DEDUPE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _DEDUPE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        tmp.replace(_DEDUPE_FILE)
    except OSError as exc:
        log.warning("Could not persist alert-dedupe state: %s", exc)


_last_alert_emitted: dict[str, float] = _load_dedupe()


def record_self_heal(action_code: str, success: bool) -> None:
    """Sentinel calls this when it auto-heals an issue. action_code is
    a short stable identifier ('rotate_logs', 'gc_uploads',
    'ollama_kickstart', etc.). The vendor dashboard groups by code."""
    if not _enabled():
        return
    with _state_lock:
        key = action_code if success else f"{action_code}_failed"
        _self_heals_24h[key] += 1
        # Also queue an event for the next heartbeat — keeps the
        # vendor in sync without waiting for the rolling-24h gauge.
        _pending_alerts.append({
            "code":     f"self_heal:{action_code}",
            "severity": "info" if success else "warning",
            "auto_healed": success,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })


def record_error() -> None:
    if not _enabled():
        return
    with _state_lock:
        global _error_count_24h
        _error_count_24h += 1


def record_audit_event(event_category: str) -> None:
    """Only the category, never the content. Used to surface "this
    deployment has been completely silent for 7 days" type signals."""
    if not _enabled():
        return
    with _state_lock:
        global _last_audit_event
        _last_audit_event = event_category


def emit_alert(code: str, severity: str = "critical", message: str = "",
               auto_healed: bool = False) -> None:
    """Sentinel calls this when something breaks that needs human
    attention. severity: "info" | "warning" | "critical". The
    vendor's notification routing decides what to do based on
    (severity, auto_healed) — critical+not-auto-healed pages
    immediately; warning emails; info logs only.

    Per-(code,severity) deduped via _last_alert_emitted: if the same
    code+severity fired within LOCALLYAI_ALERT_DEDUPE_SECONDS (default
    4 h), this call is a no-op. A sticky condition (e.g. low disk that
    nobody fixes for hours) thus produces ONE email at the start of the
    incident, not one per sentinel tick."""
    if not _enabled():
        return
    dedupe_key = f"{code}:{severity}"
    now = time.time()
    with _state_lock:
        last = _last_alert_emitted.get(dedupe_key, 0.0)
        if now - last < _DEDUPE_SECONDS:
            return
        _last_alert_emitted[dedupe_key] = now
        _save_dedupe(_last_alert_emitted)
        _pending_alerts.append({
            "code":        code,
            "severity":    severity,
            "message":     message[:200],   # cap to defend against accidental content leaks
            "auto_healed": auto_healed,
            "timestamp":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
    # Critical alerts get sent immediately rather than waiting for the
    # next heartbeat tick — operators want the page within seconds.
    if severity == "critical" and not auto_healed:
        threading.Thread(target=_post_immediate_alert, daemon=True).start()


# ── Heartbeat construction ──────────────────────────────────────────────────
def _gather_snapshot() -> dict:
    """Build the current health snapshot. Pure function — never reads
    audit log content, never reads document corpus."""
    snapshot: dict = {
        "schema_version": 1,
        "firm_id":   _firm_id(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    # Static-ish identifiers
    try:
        from config import NODE_ID, DATA_REGION
        snapshot["node_id"]     = NODE_ID
        snapshot["region"]      = DATA_REGION
    except Exception:
        snapshot["node_id"]     = ""
        snapshot["region"]      = ""
    snapshot["backend"]         = os.environ.get("LOCALLYAI_BACKEND", "")
    snapshot["channel"]         = os.environ.get("LOCALLYAI_UPDATE_CHANNEL", "stable")
    # Currently-applied LocallyAI version (best-effort)
    try:
        with open(os.path.join(REPO_DIR, "release_manifest.json")) as f:
            snapshot["version"] = json.load(f).get("version", "0.0.0")
    except Exception:
        snapshot["version"] = "unknown"

    # ── Operating environment versions ──────────────────────────────────
    # Vendor needs to detect firms running un-tested macOS / inference
    # backend / Python configurations BEFORE they cause an outage. Per
    # docs/sop/maintenance.md §macos-version-policy, the vendor publishes
    # a supported macOS band; this snapshot tells the dashboard which
    # band the firm is on so unsupported configs are flagged. None of
    # this reaches into document content / users / queries — pure
    # platform metadata.
    snapshot["macos_version"]   = _macos_version()
    snapshot["macos_build"]     = _macos_build()
    snapshot["python_version"]  = _python_version()
    snapshot["backend_version"] = _backend_version(snapshot["backend"])

    # Liveness signals
    snapshot["healthz_ok"]   = _probe_healthz()
    snapshot["sentinel_ok"]  = _probe_sentinel()
    snapshot["uptime_seconds"] = int(time.time() - _started_at)

    # Resource gauges
    snapshot["free_disk_gb"] = _free_disk_gb()
    snapshot["free_mem_gb"]  = _free_mem_gb()

    # Counters (rolling — telemetry agent resets them every 24 h)
    with _state_lock:
        snapshot["error_count_24h"] = _error_count_24h
        snapshot["self_heals_24h"]  = dict(_self_heals_24h)
        snapshot["last_audit_event"] = _last_audit_event
        snapshot["pending_alerts"]   = list(_pending_alerts)
        _pending_alerts.clear()      # alerts are flushed on each heartbeat

    # ── Churn early-warning aggregates ──────────────────────────────────
    # Three FIRM-LEVEL aggregates derived from the audit log. Strict
    # privacy discipline:
    #   - aggregate counts only, never individual events
    #   - user identity is the audit log's pseudonymised hash (salted
    #     LOCALLYAI_AUDIT_SALT, salt never leaves the deployment) — so
    #     `unique_users_7d` is "count distinct" of opaque hashes; the
    #     vendor cannot map back to individual employees
    #   - days_since_last_query is FIRM-LEVEL only (not per-user), to
    #     avoid the "single power-user firm" re-identification edge case
    #
    # The firm can opt out via LOCALLYAI_TELEMETRY_USAGE=off (the
    # operational telemetry — health, errors, heartbeats — keeps
    # flowing). Disclosed in the DPA telemetry clause + data-isolation
    # SOP. Default is on (legitimate-interest basis: vendor uses these
    # signals to detect declining engagement and intervene proactively).
    if os.environ.get("LOCALLYAI_TELEMETRY_USAGE", "on").lower() != "off":
        try:
            churn = _compute_churn_aggregates()
            snapshot["queries_24h"]            = churn["queries_24h"]
            snapshot["unique_users_7d"]        = churn["unique_users_7d"]
            snapshot["days_since_last_query"]  = churn["days_since_last_query"]
        except Exception as exc:
            log.warning(f"churn aggregates failed (non-fatal): {exc}")

    # Round-2 A5 / per-firm allowlist. The DPA template promises firms
    # they can request a partial-field heartbeat (e.g. exclude
    # backend_version). LOCALLYAI_TELEMETRY_FIELDS, if set, is the
    # comma-separated allowlist of fields the heartbeat may carry;
    # firm_id is always retained because the dashboard joins on it.
    allowlist_raw = os.environ.get("LOCALLYAI_TELEMETRY_FIELDS", "").strip()
    if allowlist_raw:
        allowed = {f.strip() for f in allowlist_raw.split(",") if f.strip()}
        allowed.add("firm_id")
        allowed.add("schema_version")
        allowed.add("timestamp")
        snapshot = {k: v for k, v in snapshot.items() if k in allowed}

    return snapshot


def _probe_healthz() -> bool:
    import ssl as _ssl
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    port = os.environ.get("LOCALLYAI_API_PORT", "8000")
    for scheme in ("https", "http"):
        try:
            with urllib.request.urlopen(f"{scheme}://localhost:{port}/healthz", timeout=2,
                                         context=ctx if scheme == "https" else None) as r:
                return r.status == 200
        except Exception:
            continue
    return False


def _probe_sentinel() -> bool:
    """Sentinel exposes a status() helper; if it errors or returns
    something obviously stale, we report unhealthy."""
    try:
        from watchdog import sentinel as _s
        s = _s.status()
        if not isinstance(s, dict):
            return False
        last_tick = s.get("last_tick_ts") or s.get("last_tick")
        if last_tick:
            age = time.time() - float(last_tick)
            return age < 600  # last tick within 10 min = healthy
        return True
    except Exception:
        return False


def _macos_version() -> str:
    """Marketing version (e.g. "14.4"). Empty string on non-macOS."""
    try:
        if os.uname().sysname != "Darwin":
            return ""
        import subprocess
        out = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True, timeout=2)
        return out.stdout.strip()
    except Exception:
        return ""


def _macos_build() -> str:
    """Build number (e.g. "23E214"). Catches CVE patch revisions
    that ship under the same marketing version."""
    try:
        if os.uname().sysname != "Darwin":
            return ""
        import subprocess
        out = subprocess.run(["sw_vers", "-buildVersion"], capture_output=True, text=True, timeout=2)
        return out.stdout.strip()
    except Exception:
        return ""


def _python_version() -> str:
    """e.g. "3.12.13". The launchd plist runs the venv's python; if the
    OS Python diverges materially that may hint at upstream Apple
    bumping the system Python under us."""
    import sys
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _backend_version(backend: str) -> str:
    """Best-effort version of the chosen inference backend.
    "" if the backend isn't installed / detection fails."""
    try:
        if backend == "mlx":
            import importlib.metadata as md
            return md.version("mlx-lm")
        if backend == "ollama":
            import subprocess
            out = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=2)
            return out.stdout.strip().split()[-1] if out.stdout else ""
        if backend == "lmstudio":
            import subprocess
            out = subprocess.run(["lms", "--version"], capture_output=True, text=True, timeout=2)
            return out.stdout.strip().split()[-1] if out.stdout else ""
    except Exception:
        return ""
    return ""


def _free_disk_gb() -> float:
    try:
        return round(shutil.disk_usage(REPO_DIR).free / (1024 ** 3), 2)
    except OSError:
        return -1.0


def _free_mem_gb() -> float:
    try:
        # vm_stat is macOS-native; on Linux fall back to /proc/meminfo.
        import subprocess
        if os.uname().sysname == "Darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2)
            for line in out.stdout.splitlines():
                if "Pages free" in line:
                    pages = int(line.rsplit(maxsplit=1)[1].rstrip("."))
                    return round(pages * 16384 / (1024 ** 3), 2)
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return round(int(line.split()[1]) / (1024 ** 2), 2)
    except Exception:
        pass
    return -1.0


# ── Posting ─────────────────────────────────────────────────────────────────
def _post_with_auth(payload: dict, path: str = "heartbeat") -> tuple[bool, str]:
    base = os.environ.get("LOCALLYAI_TELEMETRY_URL", "").rstrip("/")
    token = os.environ.get("LOCALLYAI_TELEMETRY_TOKEN", "")
    if not base or not token:
        return False, "telemetry URL or token not configured"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/{path}",
        data=body,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent":    "LocallyAI-telemetry/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status >= 400:
                return False, f"HTTP {r.status}: {r.read().decode('utf-8', errors='replace')[:200]}"
            return True, "ok"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:200]


def _post_immediate_alert() -> None:
    snap = _gather_snapshot()
    ok, detail = _post_with_auth(snap, path="heartbeat")
    if not ok:
        log.warning("Immediate alert post failed: %s", detail)


# ── Heartbeat loop ──────────────────────────────────────────────────────────
_heartbeat_thread: Optional[threading.Thread] = None


def start() -> None:
    """Start the background heartbeat thread. Idempotent: calling
    twice is a no-op. Sentinel calls this on its own startup tick;
    operator can also call manually for testing."""
    global _heartbeat_thread
    if not _enabled():
        return
    if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
        return
    interval = int(os.environ.get("LOCALLYAI_TELEMETRY_INTERVAL", "300"))

    def _loop():
        # First heartbeat right away so the dashboard sees the firm
        # come online within seconds of the API restart.
        time.sleep(2)
        last_24h_reset = time.time()
        while True:
            try:
                snap = _gather_snapshot()
                ok, detail = _post_with_auth(snap)
                if not ok:
                    log.warning("Heartbeat post failed: %s", detail)
                # Roll counters every 24 h
                if time.time() - last_24h_reset > 86400:
                    with _state_lock:
                        _self_heals_24h.clear()
                        global _error_count_24h
                        _error_count_24h = 0
                    last_24h_reset = time.time()
            except Exception as e:
                log.error("Heartbeat loop iteration failed: %s", e)
            time.sleep(interval)

    _heartbeat_thread = threading.Thread(target=_loop, daemon=True, name="telemetry-heartbeat")
    _heartbeat_thread.start()
    log.info("Telemetry heartbeat started (interval=%ds, url=%s)",
             interval, os.environ.get("LOCALLYAI_TELEMETRY_URL", "<unset>"))


# ── CLI ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(REPO_DIR, ".env"), override=True)
    except ImportError:
        pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    if cmd == "snapshot":
        print(json.dumps(_gather_snapshot(), indent=2))
    elif cmd == "post":
        snap = _gather_snapshot()
        print("Posting:", json.dumps(snap, indent=2))
        ok, detail = _post_with_auth(snap)
        print(f"Result: ok={ok} detail={detail}")
    elif cmd == "alert" and len(sys.argv) >= 3:
        emit_alert(sys.argv[2], severity=(sys.argv[3] if len(sys.argv) > 3 else "warning"),
                   message=" ".join(sys.argv[4:]))
        time.sleep(2)
        print("alert dispatched (immediate post if critical+not-auto-healed)")
    else:
        print("usage: python -m telemetry [snapshot | post | alert <code> [severity] [message...]]")

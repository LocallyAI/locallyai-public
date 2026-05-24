"""
api — LocallyAI FastAPI server (package)

PR-1 of the api.py → api/ refactor: this `__init__.py` holds everything that
used to live in api.py except for the shared primitives (audit-chain HMAC,
structured-log writers, auth helpers) which now live in `api/_shared.py`
and are re-exported below so external callers (`watchdog.sentinel` does
`import api as _api; _api._chain_lock`, `tests/ha_chaos.py` does
`api_mod._infer`) continue to find them as attributes of the `api` package.

Backend auto-selects via LOCALLYAI_BACKEND env var:
  mlx    -> Apple Silicon (MLX-LM, Metal)
  ollama -> any machine with Ollama running (default)
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import hashlib

# ── Shared primitives (audit chain, auth, log writers) ────────────────────────
# These used to be defined inline in api.py; in PR-1 they've moved to
# api/_shared.py. The first group is used by code that still lives in this
# file. The second group is pure re-export for external compatibility —
# `tests/ha_chaos.py` does `api_mod._infer`, watchdog/sentinel does
# `_api._chain_lock`, so they must remain attributes of the `api` package.
import hmac as _hmac_mod
import json
import logging
from datetime import UTC

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import lockout_store as _lockout  # noqa: F401  # external attr compatibility
from api import _shared
from api._shared import (
    _AUDIT_HMAC_KEY,
    AUDIT_LOG,
    SECURITY_LOG,
    _admin_auth,
    _open_chain_lock_fd,
    audit_verify_body,
    processing_record_body,
)
from api._shared import _BILLING_CHAIN_STATE_FILE as _BILLING_CHAIN_STATE_FILE  # noqa: F401
from api._shared import _CHAIN_LOCK_FILE as _CHAIN_LOCK_FILE  # noqa: F401
from api._shared import _CHAIN_STATE_FILE as _CHAIN_STATE_FILE  # noqa: F401
from api._shared import LOG_DIR as LOG_DIR  # noqa: F401  # external (sentinel) attr compat

# Pure re-exports (used only by external importers — see module docstring).
# `_client_ip` and `_write_security_log` are added in PR-2: they were
# imports-for-use before the chat handler moved out, and remain exported off
# the `api` package as a compatibility surface for any external caller.
from api._shared import _admin_security as _admin_security  # noqa: F401
from api._shared import (
    _atomic_write_billing_chain_state as _atomic_write_billing_chain_state,  # noqa: F401
)
from api._shared import _atomic_write_chain_state as _atomic_write_chain_state  # noqa: F401

# PR-2 / PR-3: _auth, _write_audit, _CHAIN_STATE_FILE consumed only by
# api.chat / api.documents now. Kept as `X as X` re-exports off the api
# package because external callers (and historical tests) may still
# reach for `api._auth` / `api._write_audit` / `api._CHAIN_STATE_FILE`.
from api._shared import _auth as _auth  # noqa: F401
from api._shared import _billing_prev_hash as _billing_prev_hash  # noqa: F401
from api._shared import _chain_hmac as _chain_hmac  # noqa: F401  # external (sentinel) attr compat
from api._shared import _chain_lock as _chain_lock  # noqa: F401  # external (sentinel) attr compat
from api._shared import _ChainLock as _ChainLock  # noqa: F401
from api._shared import _client_ip as _client_ip  # noqa: F401
from api._shared import _is_locked as _is_locked  # noqa: F401
from api._shared import _key_fingerprint as _key_fingerprint  # noqa: F401
from api._shared import _prev_hash as _prev_hash  # noqa: F401  # external (sentinel) attr compat
from api._shared import _record_failure as _record_failure  # noqa: F401
from api._shared import _record_success as _record_success  # noqa: F401
from api._shared import _write_audit as _write_audit  # noqa: F401
from api._shared import _write_security_log as _write_security_log  # noqa: F401
from api._shared import security as security  # noqa: F401
from audit_export.audit_export import router as audit_router
from billing.metering import router as billing_router
from config import (
    BASE_DIR,
    BILLING_LOG,
    COLLECTION_NAME,
)
from config import NODE_ID as _NODE_ID
from ingest import ensure_collection
from monitoring.monitor import router as monitor_router
from watchdog.diagnostician import router as diagnostician_router

# `_CHAIN_LOCK_FD` is intentionally NOT re-exported as a name here: the
# `_open_chain_lock_fd()` startup handler mutates `_shared._CHAIN_LOCK_FD`
# from None to an open fd, and any reader (incl. external code) should
# go through `api._shared._CHAIN_LOCK_FD` so they see the post-startup
# value rather than a stale None snapshot. The compatibility shim below
# at module level still satisfies `api._CHAIN_LOCK_FD` lookups via
# `__getattr__`.


def __getattr__(name: str):
    """Forward `api._CHAIN_LOCK_FD` (and any future deferred-init globals)
    to the live value on `api._shared`. Without this, code that does
    `import api as _api; _api._CHAIN_LOCK_FD` would see the None captured
    at import time rather than the open fd populated by the startup
    handler.
    """
    if name == "_CHAIN_LOCK_FD":
        return _shared._CHAIN_LOCK_FD
    raise AttributeError(f"module 'api' has no attribute {name!r}")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("api")

BACKEND = os.environ.get("LOCALLYAI_BACKEND", "ollama").lower()
log.info(f"Backend: {BACKEND}")

def _rate_limit_key(request: Request) -> str:
    """Per-API-key rate limit. Falls back to remote IP for unauth endpoints
    (/healthz, /docs). Without this, multiple users behind one NAT'd IP share
    a single budget — one noisy user could DoS the whole firm.
    Hashed so the bucket id never reveals the actual key in slowapi internals.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1].strip()
        if token:
            return "u:" + hashlib.sha256(token.encode()).hexdigest()[:16]
    return "ip:" + (get_remote_address(request) or "unknown")


limiter = Limiter(key_func=_rate_limit_key, default_limits=["200/hour"])

app = FastAPI(
    title="LocallyAI",
    version="1.0.0",
    description="On-premises AI for regulated industries",
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded."})


_DEFAULT_CORS = (
    "http://localhost,http://127.0.0.1,"
    "http://localhost:5173,http://127.0.0.1:5173,"   # manager-ui dev
    "http://localhost:5174,http://127.0.0.1:5174,"   # worker-ui dev
    "tauri://localhost,https://tauri.localhost"      # Tauri client apps
)
# Tauri webviews issue requests with origin "tauri://localhost" on macOS
# and "https://tauri.localhost" on Windows; both must be allowlisted
# explicitly because the wildcard "*" is rejected by hard-stop below
# (allow_credentials + "*" is the worst kind of CORS misconfig).
_ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("LOCALLYAI_CORS_ORIGINS", _DEFAULT_CORS).split(",") if o.strip()
]
# Hard-fail if anyone sets a wildcard. With allow_credentials=True (which we
# need so the browser sends Authorization), allow_origins=["*"] would let
# every page in the user's browser hit this API with their bearer token —
# the worst kind of mistake to make silently. ISO 27001 A.5.15 / GDPR art.32.
if any(o == "*" for o in _ALLOWED_ORIGINS):
    raise RuntimeError(
        "LOCALLYAI_CORS_ORIGINS must not contain '*'. List explicit origins "
        "(e.g. http://localhost:5174). Wildcard with credentials is unsafe."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    # PATCH required for chunked uploads (api.py:1241 PATCH /v1/uploads/{id}).
    # Red-team finding 5.1: missing PATCH meant browser-origin upload chunks
    # failed CORS preflight; Tauri webviews bypass CORS but conventional
    # browsers don't.
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(audit_router)
app.include_router(monitor_router)
app.include_router(billing_router)
app.include_router(diagnostician_router)

from watchdog import sentinel as _sentinel_mod

_sentinel_mod.start()


@app.on_event("startup")
def _init_runtime_paths():
    """PR-1: ensure on-disk directories exist and open the long-lived
    audit-chain lock fd. Deferred from module import so `import api`
    has zero filesystem side effects beyond what `load_dotenv()` does.

    Registered FIRST so any later startup handler that writes an audit
    entry (and so acquires _ChainLock) finds the fd populated.

    PR-3: `_UPLOAD_DIR` now lives in api/documents.py (with its domain).
    Lazy-imported here to keep the constant on its owning module without
    creating a circular import at package load (api → api.documents → api).
    """
    from api.documents import _UPLOAD_DIR
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _open_chain_lock_fd()


@app.on_event("startup")
def _bootstrap_collection():
    """Idempotently create the Qdrant collection so /v1/chat/completions works
    on first boot, even before the operator has run ingest.py."""
    try:
        from config import make_qdrant_client
        c = make_qdrant_client()
        try:
            ensure_collection(c)
        finally:
            c.close()
        log.info(f"Qdrant collection ready: {COLLECTION_NAME}")
    except Exception as exc:
        log.warning(f"Collection bootstrap skipped: {exc}")


@app.on_event("startup")
def _fail_closed_required_secrets():
    """Refuse to start when compliance-critical secrets aren't set.

    Red-team finding 2.1: audit chain silently produces no HMAC when
    LOCALLYAI_AUDIT_HMAC_KEY is empty, and the entries written during
    that window are indistinguishable from rotation tombstones. Closing
    that hole means failing-closed at boot, not warning-and-continuing.

    Red-team finding 11.1: a firm whose install never replaced the
    placeholder kill-switch URL polls a NXDOMAIN, and with
    LOCALLYAI_KILL_SWITCH_REQUIRED=1 (fail-closed default) cannot ever
    apply updates. The placeholder is operator error, not a security
    posture; refuse it with a clear message at startup.

    Both checks honour LOCALLYAI_ALLOW_INSECURE=1 as an explicit
    operator escape hatch for dev/test boxes that genuinely don't need
    these guarantees. Production installs never set that flag.
    """
    insecure_ok = os.environ.get("LOCALLYAI_ALLOW_INSECURE", "") == "1"

    audit_hmac = os.environ.get("LOCALLYAI_AUDIT_HMAC_KEY", "").strip()
    if not audit_hmac and not insecure_ok:
        log.error(
            "[startup-gate] LOCALLYAI_AUDIT_HMAC_KEY is empty. Audit-chain "
            "tamper-evidence is mandatory in production. Set it in .env to "
            "a 64-char hex secret (the installer does this automatically). "
            "Override with LOCALLYAI_ALLOW_INSECURE=1 for dev only."
        )
        raise SystemExit(2)

    ks_url = os.environ.get("LOCALLYAI_KILL_SWITCH_URL", "").strip()
    if ks_url == "https://updates.locallyai.app/status.json" and not insecure_ok:
        log.error(
            "[startup-gate] LOCALLYAI_KILL_SWITCH_URL is the placeholder default. "
            "An attacker who claims that domain could push 'kill_switch_active: false' "
            "to your firm. Set it to YOUR deployed Cloudflare Worker URL in .env. "
            "Override with LOCALLYAI_ALLOW_INSECURE=1 for dev only."
        )
        raise SystemExit(2)

    # Round-2 B6: empty / too-short AUDIT_SALT makes pseudonymised user
    # hashes publicly derivable (sha256(":<name>")). Require ≥32 chars.
    audit_salt = os.environ.get("LOCALLYAI_AUDIT_SALT", "").strip()
    if len(audit_salt) < 32 and not insecure_ok:
        log.error(
            "[startup-gate] LOCALLYAI_AUDIT_SALT is empty or too short "
            "(<32 chars). User pseudonymisation in the audit log becomes "
            "trivially reversible. Set it in .env to a 64-char random secret "
            "(the installer does this automatically). "
            "Override with LOCALLYAI_ALLOW_INSECURE=1 for dev only."
        )
        raise SystemExit(2)


@app.on_event("startup")
def _verify_pseudonym_key_material():
    """Run the GDPR Art. 32 / ISO 27001 A.8.24 checks on the salt + key
    material at startup. Surface every non-ok finding in the boot log
    so operators see them in launchd_error.log / service.log without
    having to remember to query an admin endpoint."""
    try:
        from config import verify_key_material
        for finding in verify_key_material():
            level = finding.get("level", "ok")
            msg = f"[key-material:{finding.get('code')}] {finding.get('message')}"
            if level == "fail":
                log.error(msg)
            elif level == "warn":
                log.warning(msg)
            else:
                log.info(msg)
    except Exception as exc:
        log.warning(f"key-material verification skipped: {exc}")


@app.on_event("startup")
def _register_fleet():
    """Insert this node into SHARED_DIR/fleet.json so peers can discover us.
    Best-effort: a registration failure must not stop the API from serving.
    For single-node deployments (SHARED_DIR == BASE_DIR) the file becomes a
    1-entry registry; harmless and useful for the fleet dashboard."""
    try:
        import fleet as _fleet
        scheme = "https" if (BASE_DIR / "tls" / "cert.pem").exists() else "http"
        api_url = os.environ.get("LOCALLYAI_API_URL") or f"{scheme}://{_NODE_ID}:{os.environ.get('PORT', '8000')}"
        _fleet.register(api_url=api_url, backend=BACKEND)
        log.info(f"Fleet: registered as {_NODE_ID} → {api_url}")
    except Exception as exc:
        log.warning(f"Fleet registration skipped: {exc}")


@app.on_event("shutdown")
def _deregister_fleet():
    try:
        import fleet as _fleet
        _fleet.deregister()
    except Exception:
        pass


# NOTE (PR-1): auth helpers (security, _client_ip, _is_locked,
# _record_failure, _record_success, _auth) and the lockout_store import
# moved to api/_shared.py and are re-exported at the top of this file.

# NOTE (PR-1): the log-path constants (LOG_DIR, AUDIT_LOG, SECURITY_LOG),
# audit-chain primitives (_AUDIT_HMAC_KEY, _CHAIN_STATE_FILE, _CHAIN_LOCK_FILE,
# _chain_lock, _ChainLock, _prev_hash, _atomic_write_chain_state, _chain_hmac,
# _BILLING_CHAIN_STATE_FILE, _billing_prev_hash, _atomic_write_billing_chain_state),
# writers (_write_audit, _key_fingerprint, _write_security_log), and the
# `_CHAIN_LOCK_FD` deferred-init handle all moved to api/_shared.py and are
# re-exported at the top of this file.

# NOTE (PR-2): chat-related code — the Message/ChatRequest models, the
# _infer/_stream_ollama/_list_models helpers, the RAG-context hardening
# (_INJECTION_PATTERNS, _sanitize_chunk, _looks_like_prompt_injection), the
# per-node idempotency cache, and the /healthz, /v1/branding, /v1/models,
# /v1/chat/completions, /, /v1/me routes — moved to api/chat.py and are
# mounted via app.include_router below. `_infer` is re-exported so
# `tests/ha_chaos.py` (which does `api_mod._infer = _fake_infer`) keeps
# working unchanged.
from api.chat import _infer as _infer  # noqa: F401, E402  — ha_chaos compat
from api.chat import router as _chat_router  # noqa: E402

app.include_router(_chat_router)

# ── Document / upload / ingest / conflict / compare / citations ──────────────
# Routes (and their helpers + the _UPLOAD_DIR/_MAX_UPLOAD_BYTES/_ALLOWED_EXTS/
# _COMPARE_MAX_BYTES/_RAW_DOC_SUFFIX_ALLOW constants) moved to api/documents.py
# in PR-3 and are mounted via app.include_router below. The startup handler
# `_init_runtime_paths` lazy-imports `_UPLOAD_DIR` from api.documents to call
# `.mkdir(...)`; this keeps the constant on its owning module without forcing
# api.documents to import at package-load time.
from api.documents import router as _documents_router  # noqa: E402

app.include_router(_documents_router)


# NOTE (PR-4): admin endpoints — /admin/reload-users, /admin/users CRUD,
# /admin/processing-record, /admin/audit-verify, /admin/training-records,
# /admin/backup-attestations, /admin/fleet/*, /admin/updates*,
# /admin/models*, /admin/installers* — moved to api/admin.py and are
# mounted via app.include_router below. The pure-body helpers
# `processing_record_body()` and `audit_verify_body()` moved to
# api/_shared.py so the /admin/compliance/snapshot route below can call
# them directly instead of doing handler-to-handler calls into admin.py.
from api.admin import router as _admin_router  # noqa: E402

app.include_router(_admin_router)


# ── DPO compliance snapshot ────────────────────────────────────────────────
# Single-document aggregation of every compliance-relevant signal, signed
# at the document level so the DPO can archive a copy monthly and prove to
# a regulator (or to internal audit) that the contents weren't altered.
# Replaces "the DPO trusts our docs" with "the DPO has a signed monthly
# artifact they can verify themselves."

# Sub-processor table mirrors DPA_DRAFT.md §6.2. Hard-extracted here
# because the DPA file format may change; this is the structured source
# of truth for the snapshot. Update both together.
_COMPLIANCE_SUB_PROCESSORS = [
    {"name": "Cloudflare", "role": "Worker hosting (vendor monitor + kill switch)",
     "observable": "Anonymised heartbeats: firm_id (SHA-256 hash), node_id, version, health gauges, alert codes",
     "client_data_exposure": "None",
     "soc2_url": "https://www.cloudflare.com/trust-hub/compliance-resources/",
     "soc2_last_reviewed": "2026-04-01"},
    {"name": "GitHub", "role": "Code repository hosting + signed release tag distribution",
     "observable": "Source code (no Client Data) + commit metadata (vendor identities)",
     "client_data_exposure": "None",
     "soc2_url": "https://github.com/security/trust",
     "soc2_last_reviewed": "2026-04-01"},
    {"name": "Hugging Face", "role": "Anonymous public model + embedder downloads at install time",
     "observable": "Source IP of the download (one transaction per install)",
     "client_data_exposure": "None",
     "soc2_url": "https://huggingface.co/security",
     "soc2_last_reviewed": "2026-04-01"},
    {"name": "Resend", "role": "Outbound email for vendor alerts",
     "observable": "Subject + body of vendor alert emails (firm_id hash + structured codes)",
     "client_data_exposure": "None",
     "soc2_url": "https://resend.com/security",
     "soc2_last_reviewed": "2026-04-01"},
    {"name": "Slack (optional)", "role": "Vendor on-call channel for alert echo",
     "observable": "Same content as Resend emails when sink enabled",
     "client_data_exposure": "None",
     "soc2_url": "https://slack.com/trust/compliance",
     "soc2_last_reviewed": "2026-04-01"},
    {"name": "Apple (future)", "role": "Code-signing certificate for client apps",
     "observable": "App bundle contents (no Client Data)",
     "client_data_exposure": "None",
     "soc2_url": "https://www.apple.com/legal/privacy/data/en/",
     "soc2_last_reviewed": "—"},
]

# Heartbeat field set as of this code version. Bumping the version means
# the vendor must re-disclose to opt-in firms before the bump ships.
_COMPLIANCE_TELEMETRY_FIELD_SET = {
    "version": "2026-05-12",
    "fields": [
        "firm_id", "node_id", "version", "healthz_ok", "sentinel_ok", "backend",
        "region", "uptime_seconds", "free_disk_gb", "free_mem_gb", "error_count_24h",
        "self_heals_24h", "last_audit_event", "pending_alerts",
        "macos_version", "macos_build", "python_version", "backend_version",
    ],
    "never_carries": [
        "firm name", "user names", "document content / filenames / paths",
        "chat queries or responses", "audit log entries (only category counts)",
        "billing entries", "conversation history", "TLS / admin / HMAC keys",
        "embeddings or vector data", "IP addresses (Worker sees but does not persist)",
    ],
}


def _compliance_retention_status() -> dict:
    """Per-stream retention horizons + oldest-entry timestamps so the DPO
    can confirm GDPR Art. 5(1)(e) storage-limitation in one glance."""
    out = {}
    streams = [
        ("audit", AUDIT_LOG, int(os.environ.get("LOCALLYAI_AUDIT_RETENTION_DAYS", "365"))),
        ("billing", BILLING_LOG, int(os.environ.get("LOCALLYAI_BILLING_RETENTION_DAYS", "2555"))),
        ("security", SECURITY_LOG, int(os.environ.get("LOCALLYAI_SECURITY_RETENTION_DAYS", "365"))),
    ]
    for name, path, days in streams:
        info = {"configured_days": days, "exists": path.exists()}
        if path.exists():
            info["size_bytes"] = path.stat().st_size
            try:
                # Read the first line for oldest-entry timestamp without preloading the file
                with open(path, encoding="utf-8", errors="replace") as fh:
                    first = fh.readline().strip()
                if first:
                    try:
                        e = json.loads(first)
                        info["oldest_entry_at"] = e.get("timestamp", "")
                    except Exception:
                        info["oldest_entry_at"] = "unparseable"
            except Exception:
                info["oldest_entry_at"] = ""
        out[name] = info
    return out


def _compliance_erasure_summary() -> dict:
    """Last 5 erasures + total count from the shared erasure ledger.
    Pseudonyms only — by design, the ledger never holds raw names."""
    from config import ERASURE_LOG
    if not ERASURE_LOG.exists():
        return {"total_erasures": 0, "last_5": []}
    from audit_reader import count_lines, iter_filtered
    total = count_lines(ERASURE_LOG)
    # Take the last 5 erasure events. iter_filtered streams the whole file —
    # acceptable here because erasure.log is ledger-paced, not query-paced.
    all_events = [e for e in iter_filtered(ERASURE_LOG, lambda e: e.get("event") == "erasure")]
    return {"total_erasures": total, "last_5": all_events[-5:]}


def _compliance_audit_log_sample(n: int = 30) -> list:
    """Last n audit entries — already pseudonymised + content-hash only,
    so safe to embed verbatim. Auditors want to see the SHAPE of what
    gets logged (timestamp, user_hash, model, sources, latency,
    matter_code, query_hash) — not just an "entries: N" count.
    """
    if not AUDIT_LOG.exists():
        return []
    from audit_reader import tail
    out: list = []
    for line in tail(AUDIT_LOG, n):
        try:
            e = json.loads(line)
            # Strip the chain HMAC so the sample is purely behavioural;
            # the chain integrity is reported separately by audit-verify.
            e.pop("_chain_hmac", None)
            out.append(e)
        except Exception:
            continue
    return out


def _compliance_incident_register(days: int = 90) -> list:
    """Full security.log entries from the last `days` days, ordered
    most-recent first. Auditors following a breach inspection want the
    actual records, not the bucketed counts in `breach_events_30d`.
    """
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    if not SECURITY_LOG.exists():
        return []
    cutoff = _dt.now(UTC) - _td(days=days)
    from audit_reader import iter_filtered

    def _within(e: dict) -> bool:
        ts_str = (e.get("timestamp", "") or "").strip()
        if not ts_str:
            return False
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        try:
            ts = _dt.fromisoformat(ts_str)
        except Exception:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts >= cutoff

    entries = list(iter_filtered(SECURITY_LOG, _within))
    # Most-recent first; cap at 100 to keep snapshot bounded.
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries[:100]


_TRAINING_RECORDS_FILE = BASE_DIR / "training_records.json"
_BACKUP_ATTESTATIONS_FILE = BASE_DIR / "backup_attestations.json"


def _compliance_training_records() -> dict:
    """Training-records summary for ISO 27001 A.6.3 (information
    security awareness). Reports unique users trained + last training
    event + per-topic counts. Records are added via
    /admin/training-records (separate route)."""
    if not _TRAINING_RECORDS_FILE.exists():
        return {"total_records": 0, "users_trained": 0, "topics": {}, "last_recorded_at": None}
    try:
        records = json.loads(_TRAINING_RECORDS_FILE.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            return {"total_records": 0, "users_trained": 0, "topics": {}, "last_recorded_at": None}
    except Exception:
        return {"total_records": 0, "users_trained": 0, "topics": {}, "last_recorded_at": None}
    users = {r.get("user", "") for r in records if r.get("user")}
    topics: dict = {}
    last_ts = ""
    for r in records:
        t = r.get("topic", "unspecified")
        topics[t] = topics.get(t, 0) + 1
        ts = r.get("completed_at", "")
        if ts > last_ts:
            last_ts = ts
    return {
        "total_records": len(records),
        "users_trained": len(users),
        "topics": topics,
        "last_recorded_at": last_ts or None,
    }


def _compliance_backup_attestations() -> dict:
    """Backup-restore test attestations for ISO 27001 A.8.13 / A.8.14.
    Operator records each successful restore-from-backup test (ad-hoc
    or scheduled) via /admin/backup-attestations. Snapshot reports the
    most recent 5 + the cadence."""
    if not _BACKUP_ATTESTATIONS_FILE.exists():
        return {"total": 0, "last_5": [], "last_test_at": None}
    try:
        records = json.loads(_BACKUP_ATTESTATIONS_FILE.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            return {"total": 0, "last_5": [], "last_test_at": None}
    except Exception:
        return {"total": 0, "last_5": [], "last_test_at": None}
    records.sort(key=lambda r: r.get("tested_at", ""), reverse=True)
    return {
        "total": len(records),
        "last_5": records[:5],
        "last_test_at": records[0].get("tested_at") if records else None,
    }


def _compliance_dpia(ropa: dict) -> dict:
    """Auto-generated DPIA per GDPR Art. 35 / KSA PDPL Art. 33. The
    template is auto-filled from the live RoPA where the answer is
    deterministic (lawful basis, recipients, transfers, retention,
    security measures); free-text fields are left for the firm's DPO
    to complete (necessity & proportionality assessment, risk-to-rights
    assessment, mitigations beyond defaults). Treat the auto-generated
    sections as the vendor's input; the firm-completed sections as
    the controller's input.
    """
    purposes = ropa.get("purposes", [])
    cats = ropa.get("categories_of_data", [])
    return {
        "version": "1.0",
        # Filled in at snapshot time by the caller — see compliance_snapshot()
        # which overwrites this with the actual generation timestamp.
        "generated_at": None,
        "regulation": "GDPR Art. 35 / UK DPA 2018 / KSA PDPL Art. 33 / UAE PDPL Art. 22",
        # Section A — context (auto from RoPA)
        "controller": ropa.get("controller", {}),
        "processing_purposes": purposes,
        "categories_of_data_subjects": ["lawyers / fee-earners (the firm's staff)",
                                         "clients of the firm (data subjects whose information appears in queries / documents)"],
        "categories_of_personal_data": cats,
        "recipients": ropa.get("recipients", []),
        "international_transfers": ropa.get("international_transfers", ""),
        "retention": ropa.get("retention", {}),

        # Section B — necessity & proportionality (auto where derivable;
        # firm-completed where judgment is required)
        "necessity_and_proportionality": {
            "lawful_basis_per_processing_activity": purposes,
            "purpose_limitation_assessment": (
                "Each processing activity has a single declared purpose; the "
                "audit log captures every query so any drift can be detected "
                "(GDPR Art. 5(1)(b) tamper-evidence)."
            ),
            "data_minimisation_assessment": (
                "Audit log stores SHA-256 query hash only, not query content. "
                "User identifiers in audit log are pseudonymised with rotating "
                "salt eras (GDPR Art. 25). Vendor heartbeats carry firm_id hash "
                "only; never carry document or chat content."
            ),
            "accuracy_assessment": "Firm completes — describes review processes for AI output.",
            "storage_limitation_assessment": (
                "Per-stream retention configured: audit "
                f"{ropa.get('retention',{}).get('audit_log_days','—')}d; billing "
                f"{ropa.get('retention',{}).get('billing_log_days','—')}d. Erasure "
                "ledger honoured across HA peers (manage_users.py erase)."
            ),
        },

        # Section C — risk identification (firm-driven; defaults sketched)
        "risks_to_rights_and_freedoms": [
            {"risk": "Unauthorised access to legally privileged content",
             "likelihood": "Low (LAN-only, mTLS, per-user keys, lockout)",
             "severity": "High (privilege loss; SRA implications)",
             "mitigations": [
                 "TLS 1.2+ in transit (RSA-4096 self-signed cert)",
                 "FileVault / BitLocker at-rest encryption",
                 "Per-user API keys with rate limiting + IP-based lockout",
                 "Pseudonymisation of user identifiers in audit log",
                 "HMAC-chained audit log (tamper-evident)",
             ]},
            {"risk": "Re-identification of audit subjects from pseudonyms",
             "likelihood": "Low (salt rotated on era boundaries; salt at rest under FileVault)",
             "severity": "Medium",
             "mitigations": [
                 "LOCALLYAI_AUDIT_SALT ≥ 32 chars enforced at startup",
                 "0o600 ACL on .env",
                 "Salt eras retained for subject-access; rotation on incident",
             ]},
            {"risk": "AI-generated output relied on without human review",
             "likelihood": "Medium (depends on firm training)",
             "severity": "High (professional indemnity, SRA Outcome 7)",
             "mitigations": [
                 "DPA Clause 9.4 disclaims vendor liability for unreviewed output",
                 "Persistent UI disclaimer on every AI response",
                 "Firm completes — describe training + supervision controls.",
             ]},
        ],

        # Section D — controller sign-off (firm fills)
        "controller_sign_off": {
            "dpo_name": "—",
            "dpo_signature_date": "—",
            "consultation_with_data_subjects": "—",
            "supervisory_authority_consultation_required": False,
        },
    }


def _compliance_breach_events_30d() -> list:
    """Tail security.log for events in the last 30 days. Bucketed by code+severity."""
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    if not SECURITY_LOG.exists():
        return []
    cutoff = _dt.now(UTC) - _td(days=30)
    from audit_reader import iter_filtered

    def _within_window(e: dict) -> bool:
        ts_str = (e.get("timestamp", "") or "").strip()
        if not ts_str:
            return False
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        try:
            ts = _dt.fromisoformat(ts_str)
        except Exception:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts >= cutoff

    buckets: dict = {}
    for e in iter_filtered(SECURITY_LOG, _within_window):
        code = e.get("code") or e.get("event") or "unknown"
        sev = e.get("severity") or e.get("level") or "info"
        k = f"{sev}:{code}"
        buckets[k] = buckets.get(k, 0) + 1
    return [{"severity_code": k, "count": v} for k, v in sorted(buckets.items())]


def _compliance_conflict_checks() -> dict:
    """Conflict-check counts for the DPO snapshot. Auditors look for
    evidence the firm runs checks systematically — count + status mix is
    enough; the underlying log entries pseudonymise party names already."""
    try:
        from conflicts import summary_for_compliance as _conf_summary
        return _conf_summary()
    except Exception as exc:
        return {"error": f"conflict-check log unreadable: {exc}",
                "total": 0, "last_30d": 0, "status_counts": {}}


@app.get("/admin/compliance/snapshot")
def compliance_snapshot(format: str = "json", key: str = Depends(_admin_auth)):
    """DPO monthly snapshot. Single signed document aggregating RoPA +
    audit-verify + key-material + sub-processors + telemetry disclosure +
    retention status + erasure summary + recent breach events.

    `format=json` (default) returns the raw bundle.
    `format=html` returns a printable single-page report — the DPO
    prints to PDF locally with Cmd-P and files the result for their
    monthly internal-audit cycle.

    The bundle is HMAC-signed at the document level using the same key
    that protects the audit chain. Saved copies can be verified offline
    with `scripts/verify_compliance_snapshot.py`.
    """
    import hashlib as _hl_compl
    from datetime import datetime as _dt

    from config import DATA_REGION as _data_region
    try:
        with open(BASE_DIR / "release_manifest.json", encoding="utf-8") as _mf:
            _release_version = json.load(_mf).get("version", "unknown")
    except Exception:
        _release_version = "unknown"
    deployment = {
        "deployment_id": os.environ.get("LOCALLYAI_DEPLOYMENT_ID", "locallyai"),
        "firm_id": _hl_compl.sha256(
            f"locallyai-firm:{os.environ.get('LOCALLYAI_FIRM_NAME', '').strip()}".encode()
        ).hexdigest()[:16],
        "node_id": _NODE_ID,
        "region": _data_region,
        "version": _release_version,
    }

    ropa = processing_record_body()
    dpia = _compliance_dpia(ropa)
    dpia["generated_at"] = _dt.now(UTC).isoformat()
    bundle = {
        "version": "1.1",
        "generated_at": _dt.now(UTC).isoformat(),
        "deployment": deployment,
        "ropa": ropa,
        "dpia": dpia,
        "audit_chain": audit_verify_body(),
        "audit_log_sample": _compliance_audit_log_sample(30),
        "key_material": __import__("config").verify_key_material(),
        "sub_processors": _COMPLIANCE_SUB_PROCESSORS,
        "telemetry_disclosure": {
            **_COMPLIANCE_TELEMETRY_FIELD_SET,
            "active_allowlist": [
                f.strip() for f in os.environ.get("LOCALLYAI_TELEMETRY_FIELDS", "").split(",") if f.strip()
            ],
        },
        "retention_status": _compliance_retention_status(),
        "erasure_log": _compliance_erasure_summary(),
        "training_records": _compliance_training_records(),
        "backup_attestations": _compliance_backup_attestations(),
        "incident_register_90d": _compliance_incident_register(90),
        "breach_events_30d": _compliance_breach_events_30d(),
        "conflict_checks":     _compliance_conflict_checks(),
    }

    # Document-level HMAC. Same key as the audit chain so the DPO doesn't
    # need a separate key to verify. Sort keys for deterministic signing.
    body_json = json.dumps(bundle, sort_keys=True, default=str)
    bundle["snapshot_hmac"] = (
        _hmac_mod.new(_AUDIT_HMAC_KEY, body_json.encode(), "sha256").hexdigest()
        if _AUDIT_HMAC_KEY else ""
    )

    if format == "html":
        ts = bundle["generated_at"][:10]
        dep = bundle["deployment"]["deployment_id"]
        fname = f"compliance-snapshot-{dep}-{ts}.html"
        return HTMLResponse(
            content=_render_compliance_snapshot_html(bundle),
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    return bundle


def _render_compliance_snapshot_html(bundle: dict) -> str:
    """Single-page printable HTML. Embeds the JSON bundle in a
    machine-parseable <script type=\"application/json\"> tag so
    verify_compliance_snapshot.py can extract + re-verify it."""
    import html as _html
    dep = bundle["deployment"]
    audit = bundle["audit_chain"]
    keymat = bundle["key_material"]
    retention = bundle["retention_status"]
    erasure = bundle["erasure_log"]
    breaches = bundle["breach_events_30d"]
    subs = bundle["sub_processors"]
    tele = bundle["telemetry_disclosure"]
    dpia = bundle.get("dpia", {})
    audit_sample = bundle.get("audit_log_sample", [])
    incidents = bundle.get("incident_register_90d", [])
    training = bundle.get("training_records", {})
    backups = bundle.get("backup_attestations", {})

    def _esc(x): return _html.escape(str(x))

    def _status_pill(level: str) -> str:
        color = {"ok": "#16a34a", "skipped": "#737373", "warn": "#d97706",
                 "fail": "#dc2626", "TAMPERED": "#dc2626"}.get(level, "#737373")
        return f'<span class="pill" style="background:{color}">{_esc(level)}</span>'

    keymat_rows = "".join(
        f"<tr><td>{_esc(f.get('code',''))}</td><td>{_status_pill(f.get('level','info'))}</td><td>{_esc(f.get('message',''))}</td></tr>"
        for f in keymat
    )
    sub_rows = "".join(
        f"<tr><td>{_esc(s['name'])}</td><td>{_esc(s['role'])}</td><td>{_esc(s['observable'])}</td>"
        f"<td>{_esc(s['client_data_exposure'])}</td>"
        f"<td>{_esc(s.get('soc2_last_reviewed','—'))}"
        + (f"<br/><a href=\"{_esc(s.get('soc2_url',''))}\">SOC2</a>" if s.get('soc2_url') else "")
        + "</td></tr>"
        for s in subs
    )
    audit_sample_rows = "".join(
        f"<tr><td>{_esc(e.get('timestamp',''))[:19]}</td>"
        f"<td><code>{_esc(e.get('user_hash','—'))[:16]}</code></td>"
        f"<td>{_esc(e.get('model','—'))[:30]}</td>"
        f"<td style='text-align:right'>{_esc(e.get('sources','—'))}</td>"
        f"<td style='text-align:right'>{_esc(e.get('latency_ms','—'))}</td>"
        f"<td><code>{_esc(e.get('query_hash','—'))[:12]}</code></td>"
        f"<td>{_esc(e.get('matter_code',''))}</td></tr>"
        for e in audit_sample
    ) or '<tr><td colspan="7" style="color:#737373">No audit entries yet.</td></tr>'
    incident_rows = "".join(
        f"<tr><td>{_esc(i.get('timestamp',''))[:19]}</td>"
        f"<td>{_esc(i.get('event','') or i.get('code','—'))}</td>"
        f"<td>{_esc(i.get('severity') or i.get('level','info'))}</td>"
        f"<td>{_esc((i.get('message') or i.get('detail',''))[:200])}</td></tr>"
        for i in incidents
    ) or '<tr><td colspan="4" style="color:#737373">No incidents recorded in the last 90 days.</td></tr>'
    dpia_risks = "".join(
        f"<tr><td>{_esc(r.get('risk',''))}</td>"
        f"<td>{_esc(r.get('likelihood',''))}</td>"
        f"<td>{_esc(r.get('severity',''))}</td>"
        f"<td><ul style='margin:0;padding-left:18px'>"
        + "".join(f"<li>{_esc(m)}</li>" for m in r.get('mitigations', []))
        + "</ul></td></tr>"
        for r in dpia.get("risks_to_rights_and_freedoms", [])
    )
    training_topic_rows = "".join(
        f"<tr><td>{_esc(t)}</td><td style='text-align:right'>{_esc(c)}</td></tr>"
        for t, c in training.get("topics", {}).items()
    ) or '<tr><td colspan="2" style="color:#737373">No training records yet.</td></tr>'
    backup_rows = "".join(
        f"<tr><td>{_esc(r.get('tested_at',''))[:19]}</td>"
        f"<td>{_esc(r.get('test_type',''))}</td>"
        f"<td>{_esc(r.get('result',''))}</td>"
        f"<td>{_esc(r.get('operator',''))}</td>"
        f"<td>{_esc(r.get('notes',''))[:80]}</td></tr>"
        for r in backups.get("last_5", [])
    ) or '<tr><td colspan="5" style="color:#737373">No backup tests attested yet.</td></tr>'
    retention_rows = "".join(
        f"<tr><td>{_esc(name)}</td><td>{_esc(info.get('configured_days',''))}d</td>"
        f"<td>{_esc(info.get('oldest_entry_at','—'))}</td>"
        f"<td>{_esc(info.get('size_bytes','—'))}</td></tr>"
        for name, info in retention.items()
    )
    erasure_rows = "".join(
        f"<tr><td>{_esc(e.get('timestamp',''))}</td><td><code>{_esc(e.get('pseudonym',''))[:16]}</code></td><td>{_esc(e.get('salt_era',''))}</td></tr>"
        for e in erasure.get("last_5", [])
    ) or '<tr><td colspan="3" style="color:#737373">No erasures recorded.</td></tr>'
    breach_rows = "".join(
        f"<tr><td>{_esc(b['severity_code'])}</td><td style=\"text-align:right\">{_esc(b['count'])}</td></tr>"
        for b in breaches
    ) or '<tr><td colspan="2" style="color:#737373">No breach events in the last 30 days.</td></tr>'

    conflicts_summary = bundle.get("conflict_checks", {}) or {}
    conflict_status_rows = "".join(
        f"<tr><td>{_esc(s)}</td><td style=\"text-align:right\">{_esc(c)}</td></tr>"
        for s, c in (conflicts_summary.get("status_counts") or {}).items()
    ) or '<tr><td colspan="2" style="color:#737373">No conflict checks recorded in the last 30 days.</td></tr>'

    embedded_json = json.dumps(bundle, sort_keys=True, default=str)
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>LocallyAI compliance snapshot — {_esc(dep['deployment_id'])} — {_esc(bundle['generated_at'][:10])}</title>
<style>
  body {{ font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; max-width: 980px; margin: 32px auto; padding: 0 24px; color: #18181b; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2 {{ font-size: 16px; margin: 32px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #e4e4e7; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
  th {{ font-weight: 600; color: #52525b; background: #fafafa; }}
  code {{ font: 12px ui-monospace, "SF Mono", monospace; background: #f4f4f5; padding: 1px 5px; border-radius: 3px; }}
  .meta {{ color: #71717a; font-size: 12px; }}
  .pill {{ display: inline-block; padding: 1px 8px; border-radius: 10px; color: white; font-size: 11px; font-weight: 600; }}
  .deck {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin: 12px 0; }}
  .stat {{ padding: 10px 12px; border: 1px solid #e4e4e7; border-radius: 6px; }}
  .stat .v {{ font-size: 18px; font-weight: 600; }}
  .stat .k {{ color: #71717a; font-size: 12px; }}
  .sig {{ margin-top: 32px; padding: 12px; background: #fafafa; border: 1px solid #e4e4e7; border-radius: 6px; font: 11px ui-monospace, monospace; word-break: break-all; }}
  .sig .label {{ font: 600 12px -apple-system; color: #52525b; margin-bottom: 4px; }}
  @media print {{ body {{ margin: 0; max-width: none; }} h2 {{ page-break-after: avoid; }} table {{ page-break-inside: avoid; }} }}
</style>
</head><body>
<h1>LocallyAI compliance snapshot</h1>
<div class="meta">
  Deployment: <code>{_esc(dep['deployment_id'])}</code>
  · Region: <code>{_esc(dep['region'])}</code>
  · firm_id: <code>{_esc(dep['firm_id'])}</code>
  · Node: <code>{_esc(dep['node_id'])}</code>
  · Version: <code>{_esc(dep['version'])}</code>
  · Generated: <code>{_esc(bundle['generated_at'])}</code>
</div>

<h2>At a glance</h2>
<div class="deck">
  <div class="stat"><div class="k">Audit chain</div><div class="v">{_status_pill(audit.get('status','?'))}</div></div>
  <div class="stat"><div class="k">Key-material findings</div><div class="v">{len([f for f in keymat if f.get('level') != 'ok'])} non-OK / {len(keymat)} total</div></div>
  <div class="stat"><div class="k">Erasures recorded</div><div class="v">{erasure.get('total_erasures', 0)}</div></div>
  <div class="stat"><div class="k">Breach events (30d)</div><div class="v">{sum(b['count'] for b in breaches)}</div></div>
</div>

<h2>Records of Processing Activities (RoPA)</h2>
<table>
  <tr><th>Field</th><th>Value</th></tr>
  <tr><td>RoPA version</td><td>{_esc(bundle['ropa'].get('version',''))}</td></tr>
  <tr><td>Controller deployment_id</td><td>{_esc(bundle['ropa'].get('controller',{}).get('deployment_id',''))}</td></tr>
  <tr><td>International transfers</td><td>{_esc(bundle['ropa'].get('international_transfers',''))}</td></tr>
  <tr><td>Erasure procedure</td><td>{_esc(bundle['ropa'].get('data_subject_rights',{}).get('erasure',''))}</td></tr>
</table>
<p class="meta">Full RoPA available via <code>GET /admin/processing-record</code>.</p>

<h2>Audit chain integrity</h2>
<table>
  <tr><th>Field</th><th>Value</th></tr>
  <tr><td>Status</td><td>{_status_pill(audit.get('status','?'))}</td></tr>
  <tr><td>Entries verified</td><td>{_esc(audit.get('entries','—'))}</td></tr>
  <tr><td>Node</td><td><code>{_esc(audit.get('node_id','—'))}</code></td></tr>
  {f'<tr><td>Reason</td><td>{_esc(audit.get("reason",""))}</td></tr>' if audit.get('status') != 'ok' else ''}
</table>

<h2>Key-material posture</h2>
<table>
  <tr><th>Code</th><th>Level</th><th>Message</th></tr>
  {keymat_rows or '<tr><td colspan="3" style="color:#737373">No findings.</td></tr>'}
</table>

<h2>Sub-processors (DPA Schedule §6.2)</h2>
<table>
  <tr><th>Name</th><th>Role</th><th>What they observe</th><th>Client data exposure</th><th>SOC2 reviewed</th></tr>
  {sub_rows}
</table>

<h2>DPIA (Data Protection Impact Assessment — GDPR Art. 35)</h2>
<p class="meta">Auto-generated from RoPA. Sections marked "—" are firm-completed (controller sign-off, training/supervision narrative). The vendor's inputs are deterministic; the firm's risk-to-rights assessment requires DPO judgement.</p>
<h3 style="font-size:13px;margin:10px 0 4px">Necessity & proportionality</h3>
<table>
  <tr><th>Aspect</th><th>Assessment</th></tr>
  <tr><td>Purpose limitation</td><td>{_esc(dpia.get('necessity_and_proportionality',{}).get('purpose_limitation_assessment',''))}</td></tr>
  <tr><td>Data minimisation</td><td>{_esc(dpia.get('necessity_and_proportionality',{}).get('data_minimisation_assessment',''))}</td></tr>
  <tr><td>Accuracy</td><td>{_esc(dpia.get('necessity_and_proportionality',{}).get('accuracy_assessment',''))}</td></tr>
  <tr><td>Storage limitation</td><td>{_esc(dpia.get('necessity_and_proportionality',{}).get('storage_limitation_assessment',''))}</td></tr>
</table>
<h3 style="font-size:13px;margin:14px 0 4px">Risks to rights & freedoms</h3>
<table>
  <tr><th>Risk</th><th>Likelihood</th><th>Severity</th><th>Mitigations</th></tr>
  {dpia_risks}
</table>
<p class="meta">Controller sign-off (firm-completed): DPO {_esc(dpia.get('controller_sign_off',{}).get('dpo_name','—'))} · signed {_esc(dpia.get('controller_sign_off',{}).get('dpo_signature_date','—'))}</p>

<h2>Audit-log sample — last 30 entries</h2>
<p class="meta">Pseudonymised + query-hash only (no content). Provided so auditors can see the SHAPE of what's logged, not just the integrity count.</p>
<table>
  <tr><th>Timestamp</th><th>User hash</th><th>Model</th><th style="text-align:right">Sources</th><th style="text-align:right">Latency ms</th><th>Query hash</th><th>Matter code</th></tr>
  {audit_sample_rows}
</table>

<h2>Incident register — last 90 days</h2>
<p class="meta">Source: <code>security.log</code>. Full entries; the bucketed summary below complements but does not replace this view.</p>
<table>
  <tr><th>Timestamp</th><th>Event / code</th><th>Severity</th><th>Message</th></tr>
  {incident_rows}
</table>

<h2>Training records (ISO 27001 A.6.3)</h2>
<p>Total records: {training.get('total_records', 0)} · unique users trained: {training.get('users_trained', 0)} · last recorded: <code>{_esc(training.get('last_recorded_at') or '—')}</code></p>
<table>
  <tr><th>Topic</th><th style="text-align:right">Records</th></tr>
  {training_topic_rows}
</table>

<h2>Backup test attestations (ISO 27001 A.8.13 / A.8.14)</h2>
<p>Total tests: {backups.get('total', 0)} · last test: <code>{_esc(backups.get('last_test_at') or '—')}</code></p>
<table>
  <tr><th>Tested at</th><th>Test type</th><th>Result</th><th>Operator</th><th>Notes</th></tr>
  {backup_rows}
</table>

<h2>Telemetry disclosure</h2>
<p>Heartbeat field-set version: <code>{_esc(tele['version'])}</code> · Active allowlist: <code>{_esc(tele['active_allowlist'] or 'all fields')}</code></p>
<table>
  <tr><th>Always carries</th><th>Never carries</th></tr>
  <tr>
    <td>{', '.join(_esc(f) for f in tele['fields'])}</td>
    <td>{', '.join(_esc(f) for f in tele['never_carries'])}</td>
  </tr>
</table>

<h2>Retention status</h2>
<table>
  <tr><th>Stream</th><th>Configured</th><th>Oldest entry</th><th>Size (bytes)</th></tr>
  {retention_rows}
</table>

<h2>Erasure log (last 5)</h2>
<table>
  <tr><th>Timestamp</th><th>Pseudonym</th><th>Salt era</th></tr>
  {erasure_rows}
</table>

<h2>Breach events (last 30 days, bucketed)</h2>
<table>
  <tr><th>Severity:Code</th><th style="text-align:right">Count</th></tr>
  {breach_rows}
</table>

<h2>Conflict checks</h2>
<p>Total recorded: {conflicts_summary.get('total', 0)} · last 30 days: {conflicts_summary.get('last_30d', 0)}</p>
<table>
  <tr><th>Status (last 30 days)</th><th style="text-align:right">Count</th></tr>
  {conflict_status_rows}
</table>
<p class="meta">Party names in <code>conflicts.log</code> are SHA-256 pseudonyms (same salt as audit-log user pseudonyms). The check itself is the audit-trail evidence; party identities live only in operator UI sessions.</p>

<div class="sig">
  <div class="label">Snapshot HMAC (verify with <code>python scripts/verify_compliance_snapshot.py &lt;file&gt;</code>):</div>
  {_esc(bundle.get('snapshot_hmac','(unsigned — LOCALLYAI_AUDIT_HMAC_KEY not set)'))}
</div>

<script type="application/json" id="locallyai-compliance-snapshot">
{embedded_json}
</script>
</body></html>"""


# NOTE (PR-4): the training-records CRUD, backup-attestations CRUD,
# fleet/*, updates/*, models/*, installers/* routes (and their loader /
# saver helpers, the _ModelSelectReq Pydantic, the kill_switch /
# system_updates / llm_models / client_installers module imports they
# pull in) all live in api/admin.py and are mounted via the
# `app.include_router(_admin_router)` above. The compliance helpers
# (_compliance_training_records / _compliance_backup_attestations) stay
# here and read `_TRAINING_RECORDS_FILE` / `_BACKUP_ATTESTATIONS_FILE`
# directly off the filesystem — no functional coupling to admin.py.


if __name__ == "__main__":
    # Red-team finding 4.1: the previous implementation hardcoded
    # host="0.0.0.0" and plain HTTP regardless of env/cert state. An
    # operator who ran `python api.py` directly silently exposed the
    # API in cleartext on every interface. Production launches via
    # supervisor.py which honours LOCALLYAI_BIND + tls/{cert,key}.pem.
    # Refuse the direct path with a clear pointer to the right entry.
    import sys as _sys
    _sys.stderr.write(
        "ERROR: api.py is not a runnable entry point.\n"
        "Use supervisor.py (which honours LOCALLYAI_BIND + TLS) instead:\n"
        "  .venv/bin/python supervisor.py\n"
        "Or, for one-off testing on loopback only, set the bind explicitly:\n"
        "  LOCALLYAI_BIND=127.0.0.1 .venv/bin/python -m uvicorn api:app "
        "--host 127.0.0.1 --port 8000 --ssl-keyfile tls/key.pem --ssl-certfile tls/cert.pem\n"
    )
    _sys.exit(2)

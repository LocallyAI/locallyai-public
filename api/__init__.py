"""
api — LocallyAI FastAPI server (package)

After PR-1…PR-5 of the api.py → api/ refactor, this `__init__.py` is just
the application shell: `app = FastAPI(...)`, middleware, startup/shutdown
handlers, the slowapi limiter, and `include_router` calls that mount the
domain modules:
  - api.chat        — /v1/chat/completions, /v1/models, /v1/branding, /healthz, /v1/me, /
  - api.documents   — /v1/docs, /v1/uploads, /v1/ingest, /v1/conflicts, /v1/compare, /v1/citations
  - api.admin       — /admin/users, /admin/audit-verify, /admin/processing-record,
                       /admin/training-records, /admin/backup-attestations,
                       /admin/fleet/*, /admin/updates*, /admin/models*, /admin/installers*
  - api.compliance  — /admin/compliance/snapshot (DPO monthly bundle, signed)
The shared primitives (audit-chain HMAC, structured-log writers, auth
helpers, the pure `processing_record_body()` / `audit_verify_body()` /
file-path constants `_TRAINING_RECORDS_FILE` / `_BACKUP_ATTESTATIONS_FILE`)
live in `api/_shared.py` and are re-exported below so external callers
(`watchdog.sentinel` does `import api as _api; _api._chain_lock`,
`tests/ha_chaos.py` does `api_mod._infer`) continue to find them as
attributes of the `api` package.

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
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import lockout_store as _lockout  # noqa: F401  # external attr compatibility
from api import _shared
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
from api._shared import _open_chain_lock_fd
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
def _load_plugins_startup():
    """Plugins step: scan PLUGIN_DIR (override via LOCALLYAI_PLUGIN_DIR) for
    claude-for-legal-format plugins and populate api.plugins._PLUGIN_REGISTRY.
    Loader is failure-tolerant: any single malformed plugin is logged and
    skipped without crashing startup. Default location BASE_DIR / "plugins"
    is silently absent on a fresh install — that's not an error."""
    import os as _os
    from pathlib import Path as _Path

    from api import plugins as _plugins
    from api._shared import BASE_DIR as _BASE_DIR
    override = _os.environ.get("LOCALLYAI_PLUGIN_DIR")
    plugin_dir = _Path(override) if override else _BASE_DIR / "plugins"
    _plugins.load_plugins_from_dir(plugin_dir)


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
# api/_shared.py so the /admin/compliance/snapshot route (now in
# api/compliance.py) can call them directly instead of doing
# handler-to-handler calls into admin.py.
from api.admin import router as _admin_router  # noqa: E402

app.include_router(_admin_router)


# NOTE (PR-5): the DPO compliance snapshot — /admin/compliance/snapshot,
# the _compliance_* helpers (training/backup/retention/erasure/audit-sample/
# incidents/dpia/breach/conflict), the _COMPLIANCE_SUB_PROCESSORS +
# _COMPLIANCE_TELEMETRY_FIELD_SET constants, and the printable HTML renderer
# (_render_compliance_snapshot_html) — moved to api/compliance.py and are
# mounted via app.include_router below. The training-records / backup-
# attestations file-path constants (_TRAINING_RECORDS_FILE,
# _BACKUP_ATTESTATIONS_FILE) live in api/_shared.py so api/admin.py
# (writer) and api/compliance.py (reader) share a single source of truth.
from api.compliance import router as _compliance_router  # noqa: E402

app.include_router(_compliance_router)


# Plugins (claude-for-legal-format SKILL.md loader + /v1/plugins endpoints).
# Registered after compliance so the include order matches the startup-event
# order (compliance helpers init first, plugins last).
from api.plugins import router as _plugins_router  # noqa: E402

app.include_router(_plugins_router)


# NOTE (PR-4): the training-records CRUD, backup-attestations CRUD,
# fleet/*, updates/*, models/*, installers/* routes (and their loader /
# saver helpers, the _ModelSelectReq Pydantic, the kill_switch /
# system_updates / llm_models / client_installers module imports they
# pull in) all live in api/admin.py and are mounted via the
# `app.include_router(_admin_router)` above. The compliance helpers
# (_compliance_training_records / _compliance_backup_attestations) live
# in api/compliance.py and read `_TRAINING_RECORDS_FILE` /
# `_BACKUP_ATTESTATIONS_FILE` (now in api/_shared.py) directly off the
# filesystem — no functional coupling to admin.py.


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


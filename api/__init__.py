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
import time
from datetime import UTC

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import lockout_store as _lockout  # noqa: F401  # external attr compatibility
from api import _shared
from api._shared import (
    _AUDIT_HMAC_KEY,
    AUDIT_LOG,
    LOG_DIR,
    SECURITY_LOG,
    _admin_auth,
    _chain_hmac,
    _chain_lock,
    _open_chain_lock_fd,
    _prev_hash,
)
from api._shared import _BILLING_CHAIN_STATE_FILE as _BILLING_CHAIN_STATE_FILE  # noqa: F401
from api._shared import _CHAIN_LOCK_FILE as _CHAIN_LOCK_FILE  # noqa: F401
from api._shared import _CHAIN_STATE_FILE as _CHAIN_STATE_FILE  # noqa: F401

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
from api._shared import _ChainLock as _ChainLock  # noqa: F401
from api._shared import _client_ip as _client_ip  # noqa: F401
from api._shared import _is_locked as _is_locked  # noqa: F401
from api._shared import _key_fingerprint as _key_fingerprint  # noqa: F401
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
    reload_users,
)
from config import NODE_ID as _NODE_ID
from ingest import ensure_collection
from manage_users import (
    add_user as _add_user,
)
from manage_users import (
    list_users as _list_users,
)
from manage_users import (
    remove_user as _remove_user,
)
from manage_users import (
    rotate_key as _rotate_key,
)
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


# ── Admin endpoints ────────────────────────────────────────────────────────────
@app.post("/admin/reload-users")
def reload_users_endpoint(key: str = Depends(_admin_auth)):
    """Hot-reload users.json without restarting. Call after manage_users.py changes."""
    count = reload_users()
    return {"status": "ok", "users_loaded": count}


class _UserCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


@app.get("/admin/users")
def admin_list_users(key: str = Depends(_admin_auth)):
    """Return the list of provisioned user names. Keys are NEVER returned —
    they exist only at creation time and after rotation."""
    return {"users": _list_users()}


@app.post("/admin/users")
def admin_create_user(req: _UserCreateRequest, key: str = Depends(_admin_auth)):
    """Create a user and return the freshly minted API key. The key is shown
    once and never again — the caller is responsible for handing it to the user
    over a secure channel."""
    try:
        new_key = _add_user(req.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    reload_users()
    return {"name": req.name, "api_key": new_key, "warning": "Store this key securely. It will not be shown again."}


@app.delete("/admin/users/{name}")
def admin_remove_user(name: str, key: str = Depends(_admin_auth)):
    try:
        _remove_user(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    reload_users()
    return {"removed": name}


@app.post("/admin/users/{name}/rotate")
def admin_rotate_key(name: str, key: str = Depends(_admin_auth)):
    try:
        new_key = _rotate_key(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    reload_users()
    return {"name": name, "api_key": new_key, "warning": "Store this key securely. It will not be shown again."}


@app.get("/admin/processing-record")
def processing_record(key: str = Depends(_admin_auth)):
    """Records of Processing Activities (GDPR art. 30, UAE PDPL art. 21,
    KSA PDPL art. 31). Returned as JSON so a DPO can pipe it into their
    register on demand. Reflects the deployment's actual configuration —
    if BACKEND or QDRANT_URL change, the record updates automatically."""
    deployment_id = os.environ.get("LOCALLYAI_DEPLOYMENT_ID", "locallyai-prod")
    retention_days = int(os.environ.get("LOCALLYAI_AUDIT_RETENTION_DAYS", "365"))
    qdrant_url = os.environ.get("QDRANT_URL", "")
    qdrant_urls = [u.strip() for u in os.environ.get("QDRANT_URLS", "").split(",") if u.strip()]
    ha_enabled = os.environ.get("LOCALLYAI_HA", "").strip() in ("1", "true", "yes")
    shared_dir = os.environ.get("LOCALLYAI_SHARED_DIR", "")
    try:
        import fleet as _fleet
        active_nodes = _fleet.active_nodes()
    except Exception:
        active_nodes = []
    ha_block = {
        "enabled": ha_enabled,
        "active_nodes": [n.get("node_id") for n in active_nodes],
        "shared_storage_path": shared_dir or "(single-node; SHARED_DIR == BASE_DIR)",
        "qdrant_topology": (
            f"{len(qdrant_urls)}-node cluster (replication_factor=2, write_consistency=2)"
            if ha_enabled and len(qdrant_urls) >= 2
            else "single Qdrant" + (f" at {qdrant_url}" if qdrant_url else " (embedded)")
        ),
        "audit_chain_model": "per-node (each node maintains its own HMAC chain; "
                             "fleet-wide verification via /admin/fleet/audit-verify)",
        "failover_model": (
            "Smart client (worker-ui) with mtime-cached health checks every 5s; "
            "in-flight requests retry on the next healthy node carrying the same "
            "client_request_id; per-node 120s idempotency cache prevents double "
            "billing or double audit when the first node actually completed."
        ),
        "sync_layer": "Syncthing replicates SHARED_DIR/{users.json,erasure.log,fleet.json} "
                      "between nodes; conflict files quarantined to SHARED_DIR/conflicts/ for "
                      "operator review (no silent merge of credential or erasure data).",
    }
    # Pseudonymisation key-material findings (GDPR Art. 4(5) / Art. 32,
    # ISO 27001 A.8.24, UAE PDPL art. 8(2), KSA PDPL art. 19). Surfaced
    # so a DPO sees the live posture and can act on warns.
    try:
        from config import current_salt_era, known_salt_eras, verify_key_material
        key_findings = verify_key_material()
        pseudonymity = {
            "current_salt_era":   current_salt_era(),
            "known_salt_eras":    known_salt_eras(),
            "key_material_state": key_findings,
        }
    except Exception as exc:
        pseudonymity = {"error": str(exc)}

    # Region-aware regulatory framing. Every entry below also surfaces in
    # audit/billing logs as `data_region`. KSA fleets foreground PDPL +
    # ISO 27001; UK fleets foreground UK GDPR + DPA 2018 + ISO 27001. The
    # full cross-jurisdiction list still appears under
    # `regulations_acknowledged` for DPOs operating across markets, but the
    # `applicable_regulations` field tells an auditor which framework
    # *governs* this specific deployment.
    from config import DATA_REGION as _data_region
    if _data_region == "KSA":
        applicable = [
            "KSA Personal Data Protection Law (Royal Decree M/19, 2023)",
            "ISO/IEC 27001:2022",
        ]
        breach_clause = "PDPL Art. 31 (notification to SDAIA + data subjects)"
        erasure_basis = "manage_users.py erase <name>  (PDPL art. 18 / UAE PDPL art. 14)"
        lawful_basis_user = "PDPL art. 5(1)(b) contract / employment relationship"
        lawful_basis_audit = "PDPL art. 5(1)(c) legal obligation (ISO 27001 A.8.15)"
        lawful_basis_billing = "PDPL art. 5(1)(b) contract (invoicing)"
        lawful_basis_corpus = "PDPL art. 5(1)(b)/(f) controller's own data"
    else:
        applicable = [
            "UK GDPR + DPA 2018",
            "EU GDPR (Regulation 2016/679)",
            "ISO/IEC 27001:2022",
        ]
        breach_clause = "GDPR Art. 33 (notification to ICO + data subjects)"
        erasure_basis = "manage_users.py erase <name>  (GDPR art. 17 / UAE PDPL art. 14 / KSA PDPL art. 18)"
        lawful_basis_user = "art.6(1)(b) contract / employment"
        lawful_basis_audit = "art.6(1)(c) legal obligation (ISO 27001 A.8.15)"
        lawful_basis_billing = "art.6(1)(b) contract (invoicing)"
        lawful_basis_corpus = "art.6(1)(b)/(f) controller's own data"

    return {
        "version": "1.3",
        "controller": {
            "deployment_id": deployment_id,
            "data_region":   _data_region,
            "note": "The deploying organisation is the controller; LocallyAI is on-prem software.",
        },
        "data_region": _data_region,
        "applicable_regulations": applicable,
        "breach_notification": breach_clause,
        "high_availability": ha_block,
        "pseudonymity": pseudonymity,
        "purposes": [
            "Retrieval-augmented question answering against the controller's own corpus",
            "Compliance auditing of model use",
            "Per-user usage measurement for internal billing",
        ],
        "data_categories": [
            {"name": "user_identifier", "lawful_basis": lawful_basis_user,
             "storage": "users.json (chmod 600)"},
            {"name": "audit_metadata",
             "fields": ["pseudonymised user hash", "data region", "model id",
                        "source-chunk count", "latency", "SHA-256 query hash"],
             "lawful_basis": lawful_basis_audit,
             "storage": f"logs/audit.log (chmod 640, HMAC-chained, {retention_days}d retention)"},
            {"name": "billing_metadata",
             "fields": ["real user name", "model", "latency", "matter code", "data region"],
             "lawful_basis": lawful_basis_billing,
             "storage": f"logs/billing.log (chmod 640, {retention_days}d retention)"},
            {"name": "document_corpus",
             "lawful_basis": lawful_basis_corpus,
             "storage": ("Qdrant server " + qdrant_url) if qdrant_url else "embedded Qdrant under storage/",
             "note": "Vector store + BM25 index of documents the controller chose to ingest."},
        ],
        "recipients": [
            "None. This deployment runs entirely on the controller's hardware. "
            "No outbound API calls. No subprocessor agreements required.",
        ],
        "international_transfers": (
            "None. Data stays on the deployment host (data_region=" + _data_region +
            "). Verifiable via firewall logs at the deployment site — no outbound API "
            "calls are made by LocallyAI after install. PDPL Art. 29 / GDPR Ch. V compliant."
        ),
        "retention": {
            "audit_log_days": retention_days,
            "billing_log_days": retention_days,
            "users_json": "until erasure request",
            "vector_store": "until controller deletes documents from data/",
        },
        "security_measures": [
            "TLS 1.2+ in transit (RSA-4096 self-signed cert; trusted in OS keychain)",
            "FileVault / BitLocker at-rest encryption (verified by audit_install.sh)",
            "Per-user API keys, IP-based lockout, per-key rate limiting",
            "HMAC-chained audit log (ISO 27001 A.8.15 tamper-evidence)",
            "Pseudonymisation of user identifiers in audit log "
              + ("(PDPL art. 19)" if _data_region == "KSA" else "(GDPR art. 25)"),
            f"Sentinel breach detector on logs/security.log ({breach_clause} readiness)",
        ],
        "data_subject_rights": {
            "erasure":   erasure_basis,
            "access":    "/admin/users + /v1/billing/<name> (admin endpoints behind admin key)",
            "rectification": "manage_users.py rotate <name>  (issues a fresh credential)",
        },
        "regulations_acknowledged": [
            "EU GDPR (Regulation 2016/679)",
            "UK GDPR + DPA 2018",
            "ISO/IEC 27001:2022",
            "UAE PDPL (Federal Decree-Law 45/2021)",
            "DIFC Data Protection Law DIFC Law No. 5 of 2020",
            "ADGM Data Protection Regulations 2021",
            "KSA Personal Data Protection Law (1 Royal Decree M/19, 2023)",
        ],
    }


@app.get("/admin/audit-verify")
def audit_verify(key: str = Depends(_admin_auth)):
    """Verify the HMAC chain integrity of audit.log. Returns TAMPERED if the chain is broken.

    Replays rotated archives (audit-YYYY-MM-DD.log.gz) in chronological order
    before the live log so the chain head preserved across rotations by the
    sentinel still validates — without that, the first post-rotation entry
    always looks TAMPERED to a verifier that only sees the truncated live log.

    Tail check: after replaying everything, the resulting head must equal
    .audit_chain. If it doesn't, entries were deleted from the tail (or the
    live log was wiped) — TAMPERED, even though no individual line failed.
    """
    if not _AUDIT_HMAC_KEY:
        return {"status": "skipped", "reason": "LOCALLYAI_AUDIT_HMAC_KEY not configured"}

    import gzip

    def _verify_lines(lines, prev):
        for i, line in enumerate(lines, start=1):
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            stored = entry.pop("_chain_hmac", "")
            if not stored:
                continue
            expected = _chain_hmac(json.dumps(entry, sort_keys=True), prev)
            if not _hmac_mod.compare_digest(stored, expected):
                return prev, i, False
            prev = stored
        return prev, 0, True

    # Snapshot live log + chain head atomically with respect to _write_audit so
    # the tail check below doesn't race a concurrent writer (writer appends to
    # audit.log AND updates .audit_chain inside _chain_lock — without taking
    # the same lock here, we could see audit.log post-write but .audit_chain
    # pre-write and falsely report TAMPERED). Archives are immutable once
    # rotated so they don't need the lock.
    with _chain_lock:
        live_text = AUDIT_LOG.read_text(encoding="utf-8", errors="replace") if AUDIT_LOG.exists() else ""
        expected_head = _prev_hash()

    prev = "0" * 64
    for arc in sorted(LOG_DIR.glob("audit-*.log.gz")):
        try:
            with gzip.open(arc, "rt", encoding="utf-8", errors="replace") as f:
                arc_lines = f.read().splitlines()
        except OSError as e:
            return {"status": "TAMPERED", "source": arc.name,
                    "reason": f"unreadable archive: {e}"}
        prev, broken, ok = _verify_lines(arc_lines, prev)
        if not ok:
            return {"status": "TAMPERED", "source": arc.name, "broken_at_line": broken}

    live_lines = live_text.splitlines()
    prev, broken, ok = _verify_lines(live_lines, prev)
    if not ok:
        return {"status": "TAMPERED", "source": "audit.log", "broken_at_line": broken}

    if expected_head and expected_head != "0" * 64 and prev != expected_head:
        return {"status": "TAMPERED", "source": "audit.log",
                "reason": "tail truncated: chain head does not match .audit_chain"}

    return {"status": "ok", "entries": len(live_lines), "node_id": _NODE_ID}


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

    ropa = processing_record(key=key)
    dpia = _compliance_dpia(ropa)
    dpia["generated_at"] = _dt.now(UTC).isoformat()
    bundle = {
        "version": "1.1",
        "generated_at": _dt.now(UTC).isoformat(),
        "deployment": deployment,
        "ropa": ropa,
        "dpia": dpia,
        "audit_chain": audit_verify(key=key),
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


# ── Training records (ISO 27001 A.6.3 information-security awareness) ──────
# Light file-backed CRUD. Each record: {id, user, topic, completed_at, notes}.
# Auditors want to see that users are trained on AI-output review, GDPR
# fundamentals, incident reporting, etc. The compliance snapshot summarises;
# these endpoints let the DPO maintain the underlying records.

def _load_training_records() -> list:
    if not _TRAINING_RECORDS_FILE.exists():
        return []
    try:
        d = json.loads(_TRAINING_RECORDS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save_training_records(records: list) -> None:
    tmp = _TRAINING_RECORDS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, indent=2), encoding="utf-8")
    tmp.replace(_TRAINING_RECORDS_FILE)
    try:
        os.chmod(_TRAINING_RECORDS_FILE, 0o640)
    except OSError:
        pass


@app.get("/admin/training-records")
def list_training_records(key: str = Depends(_admin_auth)):
    return {"records": _load_training_records()}


@app.post("/admin/training-records")
def add_training_record(body: dict, key: str = Depends(_admin_auth)):
    from datetime import datetime as _dt
    user = (body.get("user") or "").strip()
    topic = (body.get("topic") or "").strip()
    notes = (body.get("notes") or "").strip()
    completed_at = (body.get("completed_at") or "").strip()
    if not user or not topic:
        raise HTTPException(status_code=400, detail="user and topic are required")
    if not completed_at:
        completed_at = _dt.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    records = _load_training_records()
    next_id = (max((r.get("id", 0) for r in records), default=0)) + 1
    record = {"id": next_id, "user": user, "topic": topic,
              "completed_at": completed_at, "notes": notes}
    records.append(record)
    _save_training_records(records)
    return {"record": record}


@app.delete("/admin/training-records/{record_id}")
def delete_training_record(record_id: int, key: str = Depends(_admin_auth)):
    records = _load_training_records()
    new_records = [r for r in records if r.get("id") != record_id]
    if len(new_records) == len(records):
        raise HTTPException(status_code=404, detail="training record not found")
    _save_training_records(new_records)
    return {"deleted": True, "id": record_id}


# ── Backup test attestations (ISO 27001 A.8.13 / A.8.14) ───────────────────
# Operator records each successful restore-from-backup test. The compliance
# snapshot reports the most recent 5; auditors want to see that backups are
# tested (not just configured), and the cadence.

def _load_backup_attestations() -> list:
    if not _BACKUP_ATTESTATIONS_FILE.exists():
        return []
    try:
        d = json.loads(_BACKUP_ATTESTATIONS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save_backup_attestations(records: list) -> None:
    tmp = _BACKUP_ATTESTATIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, indent=2), encoding="utf-8")
    tmp.replace(_BACKUP_ATTESTATIONS_FILE)
    try:
        os.chmod(_BACKUP_ATTESTATIONS_FILE, 0o640)
    except OSError:
        pass


@app.get("/admin/backup-attestations")
def list_backup_attestations(key: str = Depends(_admin_auth)):
    return {"records": _load_backup_attestations()}


@app.post("/admin/backup-attestations")
def add_backup_attestation(body: dict, key: str = Depends(_admin_auth)):
    from datetime import datetime as _dt
    test_type = (body.get("test_type") or "").strip()  # e.g. "full restore", "partial", "smoke"
    result = (body.get("result") or "").strip()  # "passed" | "failed" | "partial"
    notes = (body.get("notes") or "").strip()
    operator = (body.get("operator") or "").strip()
    tested_at = (body.get("tested_at") or "").strip()
    if not test_type or not result:
        raise HTTPException(status_code=400, detail="test_type and result are required")
    if not tested_at:
        tested_at = _dt.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    records = _load_backup_attestations()
    next_id = (max((r.get("id", 0) for r in records), default=0)) + 1
    record = {"id": next_id, "test_type": test_type, "result": result,
              "operator": operator, "tested_at": tested_at, "notes": notes}
    records.append(record)
    _save_backup_attestations(records)
    return {"record": record}


@app.get("/admin/fleet/nodes")
def fleet_nodes(key: str = Depends(_admin_auth)):
    """Return the fleet.json registry plus liveness annotation. The fleet
    dashboard uses this as its master view."""
    import fleet as _fleet
    active = {n["node_id"] for n in _fleet.active_nodes()}
    nodes = []
    for n in _fleet.all_nodes():
        nodes.append({**n, "alive": n.get("node_id") in active})
    nodes.sort(key=lambda x: x.get("node_id", ""))
    return {"this_node": _NODE_ID, "active_count": len(active), "nodes": nodes}


@app.get("/admin/fleet/alerts")
def fleet_alerts(request: Request, key: str = Depends(_admin_auth)):
    """Aggregate monitor alerts from every active node so the dashboard
    can show fleet-wide alert state in one call."""
    import ssl
    import urllib.error
    import urllib.request

    import fleet as _fleet
    auth_header = request.headers.get("authorization", "")
    nodes = _fleet.active_nodes() or []
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    out = []
    for node in nodes:
        nid = node.get("node_id", "?")
        if nid == _NODE_ID:
            try:
                from monitoring.monitor import alerts as _local_alerts
                out.append({"node_id": nid, "alerts": _local_alerts() if callable(_local_alerts) else []})
            except Exception:
                out.append({"node_id": nid, "alerts": []})
            continue
        try:
            url = f"{node.get('api_url', '').rstrip('/')}/admin/monitor/alerts"
            req2 = urllib.request.Request(url, headers={"Authorization": auth_header})
            with urllib.request.urlopen(req2, timeout=3, context=ssl_ctx) as r:
                out.append({"node_id": nid, "alerts": json.loads(r.read().decode("utf-8"))})
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
            out.append({"node_id": nid, "alerts": [], "unreachable": str(e)[:160]})
    return {"nodes": out}


@app.get("/admin/fleet/sync-conflicts")
def fleet_sync_conflicts(key: str = Depends(_admin_auth)):
    """List Syncthing conflict files quarantined by the sentinel into
    SHARED_DIR/conflicts/. Operators reconcile via the dashboard rather
    than touching files directly."""
    from config import SHARED_DIR
    qdir = SHARED_DIR / "conflicts"
    if not qdir.exists():
        return {"shared_dir": str(SHARED_DIR), "conflicts": []}
    items = []
    for f in sorted(qdir.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        try:
            st = f.stat()
            items.append({
                "name": f.name,
                "size": st.st_size,
                "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
            })
        except OSError:
            continue
    return {"shared_dir": str(SHARED_DIR), "conflicts": items}


@app.get("/admin/fleet/qdrant-health")
def fleet_qdrant_health(key: str = Depends(_admin_auth)):
    """Report Qdrant cluster state from this node's perspective. Hits the
    local Qdrant /cluster endpoint and returns peer-id → status. The
    fleet dashboard aggregates per-node views to show fleet-wide cluster
    health (e.g. "Mac-A sees both peers; Mac-B sees only itself" → split
    brain).

    Single-node deployments (no QDRANT_URLS, embedded store) cleanly
    return mode:"single-node" — never errors.
    """
    import urllib.error
    import urllib.request

    from config import QDRANT_API_KEY, QDRANT_URL, QDRANT_URLS
    if not QDRANT_URLS and not QDRANT_URL:
        return {"node_id": _NODE_ID, "mode": "single-node",
                "reason": "QDRANT_URLS/QDRANT_URL unset; using embedded store"}
    target = (QDRANT_URLS or [QDRANT_URL])[0].rstrip("/")
    headers = {}
    if QDRANT_API_KEY:
        headers["api-key"] = QDRANT_API_KEY
    try:
        req = urllib.request.Request(f"{target}/cluster", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as r:
            body = json.loads(r.read().decode("utf-8"))
        result = body.get("result", {}) or {}
        peers = result.get("peers", {}) or {}
        return {
            "node_id":     _NODE_ID,
            "mode":        "cluster" if result.get("status") == "enabled" else "single",
            "raft_state":  result.get("raft_info", {}).get("role"),
            "peer_count":  len(peers),
            "peers":       [{"id": pid, "uri": p.get("uri")} for pid, p in peers.items()],
        }
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        return {"node_id": _NODE_ID, "mode": "unreachable",
                "reason": str(e)[:200], "target": target}


@app.get("/admin/fleet/gate")
def fleet_gate(request: Request, key: str = Depends(_admin_auth)):
    """Per-node inference-gate snapshot: max_inflight, in_flight, queued,
    peak_queue, total_admitted, total_rejected. Fan-out aggregates so
    the dashboard can show fleet-wide load."""
    import ssl
    import urllib.error
    import urllib.request

    import fleet as _fleet
    from inference_gate import stats as _gate_stats
    auth_header = request.headers.get("authorization", "")
    nodes = _fleet.active_nodes() or []
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    out = []
    for n in nodes:
        nid = n.get("node_id", "?")
        if nid == _NODE_ID:
            out.append({"node_id": nid, "gate": _gate_stats()})
            continue
        url = f"{n.get('api_url', '').rstrip('/')}/admin/monitor/health/detailed"
        try:
            req2 = urllib.request.Request(url, headers={"Authorization": auth_header})
            with urllib.request.urlopen(req2, timeout=3, context=ssl_ctx) as r:
                body = json.loads(r.read().decode("utf-8"))
            out.append({"node_id": nid, "gate": body.get("inference_gate", {})})
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
            out.append({"node_id": nid, "gate": {}, "unreachable": str(e)[:160]})
    if not out:
        # Single-node degenerate path
        out.append({"node_id": _NODE_ID, "gate": _gate_stats()})
    return {"nodes": out}


@app.post("/admin/fleet/refresh")
def fleet_refresh(key: str = Depends(_admin_auth)):
    """Force this node to re-read users.json + erasure.log right now,
    bypassing the 1-second mtime cache. Called by a coordinating peer
    after a privileged write (key rotation, erasure) on the shared store
    to close the propagation gap from the Syncthing interval (~10s) down
    to one network round-trip.

    Idempotent and cheap — a single stat + (if changed) a small JSON
    parse. Safe to call from any peer with a valid admin bearer."""
    import config as _config
    from config import _load_erased, reload_users
    reload_users()
    _config._ERASED = _load_erased()
    try:
        from config import ERASURE_LOG as _EL
        _config._ERASURE_MTIME = _EL.stat().st_mtime if _EL.exists() else 0.0
    except OSError:
        pass
    return {"status": "ok", "node_id": _NODE_ID,
            "users": len(_config.USERS), "erased": len(_config._ERASED)}


@app.get("/admin/fleet/audit-verify")
def fleet_audit_verify(request: Request, key: str = Depends(_admin_auth)):
    """Fan out /admin/audit-verify to every active node and aggregate the
    per-node results. Auditors verify the whole fleet from one call.

    Each node's chain is independent (per-node chains are a deliberate
    design choice — see docs/ha-2node-clients.md): we report each node's
    status separately and let the operator decide what "the fleet is
    healthy" means. fleet_status is "ok" iff every node reported "ok".

    The call is short-circuited for the local node (no HTTP hop). Peer
    calls re-use the bearer token the caller used here; if peer auth
    diverges the per-node entry will report status:"unreachable".
    """
    import ssl
    import urllib.error
    import urllib.request

    import fleet as _fleet

    auth_header = request.headers.get("authorization", "")
    nodes = _fleet.active_nodes() or []
    if not nodes:
        # No fleet entries at all — degenerate to single-node verify.
        local = audit_verify(key=key)
        local["node_id"] = _NODE_ID
        return {"fleet_status": local.get("status", "unknown"),
                "nodes": [local]}

    results = []
    overall_ok = True
    # Self-signed TLS in single-firm LANs — accept the peer's cert without
    # verification. The bearer token is the actual authentication; TLS is
    # for transit confidentiality, not peer identity (the LAN is trusted).
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    for node in nodes:
        node_id = node.get("node_id", "?")
        if node_id == _NODE_ID:
            local = audit_verify(key=key)
            local["node_id"] = _NODE_ID
            results.append(local)
            if local.get("status") != "ok":
                overall_ok = False
            continue

        url = f"{node.get('api_url', '').rstrip('/')}/admin/audit-verify"
        try:
            req = urllib.request.Request(url, headers={"Authorization": auth_header})
            with urllib.request.urlopen(req, timeout=5, context=ssl_ctx) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                payload["node_id"] = node_id
                results.append(payload)
                if payload.get("status") != "ok":
                    overall_ok = False
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
            results.append({"node_id": node_id, "status": "unreachable",
                            "reason": str(e)[:200]})
            overall_ok = False

    return {"fleet_status": "ok" if overall_ok else "degraded",
            "nodes": results}


# ── System updates (admin-only) ─────────────────────────────────────────────
# See system_updates.py + kill_switch.py + deploy.py for the
# defence-in-depth model: two channels (dev / stable), GPG-signed tags,
# SHA-256 manifest, OOB kill switch, atomic deploy + rollback.
import kill_switch as _ks_mod
import system_updates as _su_mod


@app.get("/admin/updates")
@limiter.limit("60/minute")
def admin_list_updates(request: Request, key: str = Depends(_admin_auth)):
    """Manager UI calls this to render the Updates page."""
    return {
        "channel_status":  _su_mod.status(),
        "kill_switch":     _ks_mod.status(),
        "available":       [_su_mod.to_dict(u) for u in _su_mod.list_available()],
    }


@app.post("/admin/updates/apply/{tag}")
@limiter.limit("10/minute")
def admin_apply_update(tag: str, request: Request, key: str = Depends(_admin_auth)):
    """Apply a specific tag. Re-verifies + atomic deploys + rolls back on
    health-check failure. Synchronous (returns when apply settles); the
    UI shows a spinner during the call (~30–90 s including healthz wait)."""
    import deploy as _dep
    return _dep.apply_tag(tag)


# ── LLM model picker (admin-only) ───────────────────────────────────────────
import llm_models as _llm_mod


@app.get("/admin/models")
@limiter.limit("60/minute")
def admin_list_models(request: Request, key: str = Depends(_admin_auth)):
    return {
        "current":  _llm_mod.current_model(),
        "models":   _llm_mod.list_models(),
        "download": _llm_mod.download_status(),
    }


class _ModelSelectReq(BaseModel):
    model_id: str = Field(..., min_length=1, max_length=200)


@app.post("/admin/models/select")
@limiter.limit("10/minute")
def admin_select_model(req: _ModelSelectReq, request: Request, key: str = Depends(_admin_auth)):
    """Kick off a background model download + .env swap + API restart.
    Returns immediately; UI polls /admin/models for download_status."""
    return _llm_mod.select(req.model_id)


# ── Client app installer distribution (admin-only) ──────────────────────────
# IT downloads the LocallyAI Worker / Manager .dmg / .msi from THIS server
# instead of GitHub directly — keeps the firm's perimeter intact (no
# GitHub accounts on staff devices). See client_installers.py for the
# pull mechanism (gh CLI against LocallyAI/locallyai's -clients tags)
# and docs/sop/client-install.md for the IT workflow.
import client_installers as _ci


@app.get("/admin/installers")
@limiter.limit("60/minute")
def admin_list_installers(request: Request, key: str = Depends(_admin_auth)):
    return {
        "files":  _ci.list_files(),
        "status": _ci.status(),
        "refresh_in_flight": _ci.is_refresh_in_flight(),
        "rebuild_in_flight": _ci.is_rebuild_in_flight(),
    }


@app.post("/admin/installers/refresh")
@limiter.limit("10/minute")
def admin_refresh_installers(request: Request, key: str = Depends(_admin_auth)):
    """Pull the newest -clients release from GitHub. Returns immediately;
    the actual download runs in a background thread (see refresh_async)
    so the UI doesn't hang for 30+ seconds on a slow link."""
    return _ci.refresh_async()


@app.post("/admin/installers/rebuild")
@limiter.limit("6/minute")
def admin_rebuild_installers(request: Request, key: str = Depends(_admin_auth)):
    """Rebuild the per-firm staff-laptop apps in-place by running
    scripts/build_staff_apps.sh. Different from /refresh — refresh
    pulls generic builds from GitHub Releases; rebuild regenerates
    locally-baked per-firm builds (the URL the WKWebView wrapper points
    at is this firm's office hostname). Triggered by IT after a
    `git pull` or hostname change. Returns immediately; the build
    runs in a background thread."""
    return _ci.rebuild_async()


@app.get("/admin/installers/{filename}")
@limiter.limit("60/minute")
def admin_download_installer(filename: str, request: Request, key: str = Depends(_admin_auth)):
    """Stream an installer file. Path-traversal hardened in resolve_file
    (rejects ./../ + restricts to known suffixes inside storage/installers/).
    """
    p = _ci.resolve_file(filename)
    if p is None:
        raise HTTPException(status_code=404, detail="Installer not found")
    return FileResponse(
        path=str(p),
        filename=p.name,
        media_type="application/octet-stream",
        # Browsers see this header and offer a Save dialog rather than
        # rendering. The double-quote handling matters because Tauri
        # filenames include spaces ("LocallyAI Worker_…").
        headers={"Content-Disposition": f'attachment; filename="{p.name}"'},
    )


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

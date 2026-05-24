"""Shared primitives: audit-chain HMAC, structured-log writers, auth,
fail-closed startup helpers. Imported by `api/__init__.py` and (in later
PRs) `api/chat.py`, `api/admin.py`, `api/compliance.py`, `api/documents.py`.

Notes:
  * Module import is side-effect-free beyond what the importer's environment
    already requires. The `_CHAIN_LOCK_FILE` touch and `_CHAIN_LOCK_FD = open(...)`
    that used to live at module level are deferred to `_open_chain_lock_fd()`,
    which `api/__init__.py` calls from a startup event.
  * `_ChainLock` accesses `_CHAIN_LOCK_FD` lazily (via module-global
    indirection) so importers don't pay for a file open they don't need;
    the startup event populates the fd before any writer reaches it.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac_mod
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import BinaryIO, Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import (
    BILLING_LOG,
    pseudonymise_user,
    validate_key,
)
from config import NODE_ID as _NODE_ID

log = logging.getLogger("api")

# ── Log paths ─────────────────────────────────────────────────────────────────
# Honour LOCALLYAI_LOG_DIR so the export, monitor, and billing routers (which
# already read from this env var) all see the same audit.log this writer
# produces. Without this the readers and writers diverge under non-default
# deployments and smoke tests.
_LOG_DIR_ENV = os.environ.get("LOCALLYAI_LOG_DIR", "")
LOG_DIR      = Path(_LOG_DIR_ENV) if _LOG_DIR_ENV else Path(__file__).resolve().parent.parent / "logs"
AUDIT_LOG    = LOG_DIR / "audit.log"
SECURITY_LOG = LOG_DIR / "security.log"

# HMAC chain makes audit.log tamper-evident (ISO 27001 A.12.4).
# Set LOCALLYAI_AUDIT_HMAC_KEY in .env to a random 64-char secret to enable.
_AUDIT_HMAC_KEY   = os.environ.get("LOCALLYAI_AUDIT_HMAC_KEY", "").encode()
_CHAIN_STATE_FILE = LOG_DIR / ".audit_chain"
_CHAIN_LOCK_FILE  = LOG_DIR / ".audit_chain.lock"
_chain_lock       = threading.Lock()

# Round-2 A4: keep a single fd alive for the process lifetime. Re-opening
# the lock file every acquire means a stray `rm logs/.audit_chain.lock`
# while the API is running creates a new inode; the next process to
# `open()` lands on the new inode and two processes can each hold flock
# on different inodes of the same path. Holding the fd pins the inode;
# even after `rm` the fd still points at the original vnode that
# everyone else originally fopened.
#
# PR-1: opening the fd is deferred from module import to startup so the
# package can be imported without filesystem side effects. The startup
# handler in `api/__init__.py` calls `_open_chain_lock_fd()`.
_CHAIN_LOCK_FD: Optional[BinaryIO] = None


def _open_chain_lock_fd() -> None:
    """Idempotent: ensure LOG_DIR exists, touch the lock file, and open
    the long-lived chain-lock file descriptor. Called from the API's
    startup event so module import remains side-effect-free."""
    global _CHAIN_LOCK_FD
    if _CHAIN_LOCK_FD is not None:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _CHAIN_LOCK_FILE.touch(exist_ok=True)
    _CHAIN_LOCK_FD = open(_CHAIN_LOCK_FILE, "rb+")


class _ChainLock:
    """Cross-process audit chain lock. Wraps threading.Lock + fcntl.flock
    so concurrent writers in (a) the same process across threads and
    (b) different processes (uvicorn workers, deploy.py, sentinel) are
    BOTH serialised through a single critical section.

    Red-team finding 2.3 + 9.2: the previous threading.Lock was
    process-local; a sentinel thread + a deploy.py invocation +
    multiple uvicorn workers could all race on audit.log writes
    producing chain forks invisible until verify-time.
    """
    def __enter__(self):
        _chain_lock.acquire()
        # Lazy module-attr lookup so the fd populated at startup is
        # always picked up, even if a writer imported the symbol
        # before startup ran.
        import fcntl as _fcntl  # noqa: I001  (lazy import: stdlib + intra-package)
        import api._shared as _self  # noqa: I001
        _fcntl.flock(_self._CHAIN_LOCK_FD.fileno(), _fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        import fcntl as _fcntl  # noqa: I001
        try:
            import api._shared as _self  # noqa: I001
            _fcntl.flock(_self._CHAIN_LOCK_FD.fileno(), _fcntl.LOCK_UN)
        finally:
            _chain_lock.release()


def _prev_hash() -> str:
    """Read chain head from disk. Read-only; caller holds the chain
    lock if they intend to write a follow-on entry."""
    if _CHAIN_STATE_FILE.exists():
        return _CHAIN_STATE_FILE.read_text(encoding="utf-8").strip()
    return "0" * 64


def _atomic_write_chain_state(chain: str) -> None:
    """Write the new chain head via tmp + rename so a crash between
    open() and write() can't leave an empty .audit_chain file (which
    on next read returns "0"*64 and silently rebases the chain).
    Red-team finding 2.2.
    """
    tmp = _CHAIN_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(chain, encoding="utf-8")
    tmp.replace(_CHAIN_STATE_FILE)


def _chain_hmac(entry_json: str, prev: str) -> str:
    if not _AUDIT_HMAC_KEY:
        return ""
    return _hmac_mod.new(_AUDIT_HMAC_KEY, f"{prev}{entry_json}".encode(), "sha256").hexdigest()


# Billing-side chain — same key as audit chain, separate prev-hash sidecar
# so the two log files can be independently verified.
_BILLING_CHAIN_STATE_FILE = LOG_DIR / ".billing_chain"


def _billing_prev_hash() -> str:
    if _BILLING_CHAIN_STATE_FILE.exists():
        return _BILLING_CHAIN_STATE_FILE.read_text(encoding="utf-8").strip()
    return "0" * 64


def _atomic_write_billing_chain_state(chain: str) -> None:
    tmp = _BILLING_CHAIN_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(chain, encoding="utf-8")
    tmp.replace(_BILLING_CHAIN_STATE_FILE)


def _write_audit(user: str, model: str, sources: int, latency_ms: float,
                 query_hash: str = "", matter_code: str = ""):
    """
    audit.log  — pseudonymised user hash only (GDPR Article 25 data minimisation).
    billing.log — real user name for invoicing; admin-access only.
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    user_hash = pseudonymise_user(user)
    # Defence in depth: if this user has been erased (from this node or
    # any peer via the shared erasure ledger), refuse to record a new
    # audit entry. validate_key already blocks the request earlier; this
    # closes the gap during the brief sync window after a peer erasure.
    from config import is_erased as _is_erased
    if _is_erased(user_hash):
        log.warning(f"Refusing audit write for erased pseudonym {user_hash[:8]}…")
        return

    # Stamp the writing node into every entry. The node_id participates in
    # the HMAC payload so a forged attribution would break the per-node
    # chain — auditors can attribute every line to a specific box. The
    # salt_era stamps which pseudonymisation salt produced user_hash so a
    # subject-access request finding an old entry can pick the right salt
    # for re-identification (GDPR Art. 15 / Art. 32 documented control
    # for cryptographic key rotation; ISO 27001 A.8.24).
    # BACKEND lives on the api package (set in api/__init__.py from env).
    # Late-import so module import order doesn't matter and so tests that
    # reassign `api.BACKEND` see the new value.
    from config import DATA_REGION, current_salt_era  # noqa: I001
    import api as _api_pkg  # noqa: I001
    _backend = getattr(_api_pkg, "BACKEND", "")
    entry = {
        "timestamp":   ts,
        "node_id":     _NODE_ID,
        "data_region": DATA_REGION,
        "user_hash":   user_hash,
        "salt_era":    current_salt_era(),
        "model": model,
        "sources": sources,
        "latency_ms": round(latency_ms, 2),
        "backend": _backend,
        "query_hash": query_hash,
        "matter_code": matter_code,
    }
    with _ChainLock():
        prev = _prev_hash()
        entry_json = json.dumps(entry, sort_keys=True)
        chain = _chain_hmac(entry_json, prev)
        if chain:
            entry["_chain_hmac"] = chain
        # fsync before chain-state write so the audit log is durable
        # before the chain head moves. Otherwise a crash between f.write
        # and the chain-state rename leaves audit.log holding a partial
        # entry but the chain head not yet advanced — verify catches it
        # but the operator now has to debug whether it's tampering or
        # a power-loss-during-write. Order = write entry, fsync, update
        # chain head atomically.
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if chain:
            _atomic_write_chain_state(chain)

    billing_entry = {
        "timestamp":   ts,
        "node_id":     _NODE_ID,
        "data_region": DATA_REGION,
        "user": user,
        "model": model,
        "sources": sources,
        "latency_ms": round(latency_ms, 2),
        "matter_code": matter_code,
    }
    # Billing log gets its own HMAC chain (red-team finding 2.4). Same
    # key, separate sidecar file — keeps the data-minimisation boundary
    # intact (audit.log stays pseudonymised; billing.log keeps real
    # names for invoicing; an attacker with file-level access can no
    # longer silently forge billing entries to inflate one user's
    # usage). The chain lock is shared with audit.log so the two
    # files are serialised together for any single writer thread.
    with _ChainLock():
        bprev = _billing_prev_hash()
        bentry_json = json.dumps(billing_entry, sort_keys=True)
        bchain = _chain_hmac(bentry_json, bprev)
        if bchain:
            billing_entry["_chain_hmac"] = bchain
        with open(BILLING_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(billing_entry) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if bchain:
            _atomic_write_billing_chain_state(bchain)


def _key_fingerprint(token: str | None) -> str:
    """Stable, non-reversible 8-char tag for an attempted API key.
    Salted with LOCALLYAI_AUDIT_SALT so it cannot be brute-forced offline
    against a known set of keys. Same key → same tag → breach detector can
    correlate repeated attempts without ever logging credential material.
    """
    if not token:
        return "-"
    salt = os.environ.get("LOCALLYAI_AUDIT_SALT", "")
    if not salt:
        return "-"
    return hashlib.sha256((salt + token).encode()).hexdigest()[:8]


def _write_security_log(event: str, ip: str, detail: str = "",
                        path: str | None = None, key_fp: str | None = None):
    """Append a JSON line to logs/security.log.

    ISO 27001 A.8.15 (logging) + A.8.16 (monitoring): every authentication
    decision that affects access (success-after-failure, failure, lockout,
    lockout-bypass-attempt) lands here. The breach detector tails it.
    """
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "ip": ip,
        "detail": detail,
    }
    if path:
        entry["path"] = path
    if key_fp:
        entry["key_fp"] = key_fp
    with open(SECURITY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ── Auth (user + admin) ──────────────────────────────────────────────────────
security = HTTPBearer()

# ── Failed login tracking (ISO 27001 A.9 — access control) ───────────────────
# Lockout state has moved to lockout_store.py (sqlite, cross-process safe).
# Red-team finding 1.3: the previous module-level dicts were per-uvicorn-worker,
# allowing an attacker to bypass lockouts by hitting different workers.
import lockout_store as _lockout


def _client_ip(request: Request) -> str:
    """Return the client IP. X-Forwarded-For is honoured ONLY when
    LOCALLYAI_TRUST_XFF=1 — the default is to ignore it (red-team
    finding 1.4). Single-Mac deployments have no upstream proxy and
    must NOT trust this header (any client can spoof it to forge
    apparent IP and poison the lockout table). Operators behind a
    real trusted reverse proxy enable the env flag explicitly.
    """
    if os.environ.get("LOCALLYAI_TRUST_XFF", "0") == "1":
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_locked(ip: str) -> bool:
    return _lockout.is_locked(ip)


def _record_failure(ip: str):
    triggered = _lockout.record_failure(ip)
    if triggered:
        log.warning(f"IP {ip} locked out after {_lockout._LOCKOUT_MAX} failed auth attempts")
        _write_security_log("lockout", ip, f"{_lockout._LOCKOUT_MAX} consecutive failures")


def _record_success(ip: str):
    _lockout.record_success(ip)


def _auth(request: Request, creds: HTTPAuthorizationCredentials = Depends(security)):
    ip = _client_ip(request)
    path = request.url.path if hasattr(request, "url") else None
    key_fp = _key_fingerprint(creds.credentials)
    if _is_locked(ip):
        # Don't silently 429 — the breach detector wants every attempt
        # *during* a lockout window so it can see sustained probing.
        _write_security_log("auth_locked_attempt", ip,
                            "Request rejected during lockout window",
                            path=path, key_fp=key_fp)
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")
    user = validate_key(creds.credentials)
    if not user:
        _record_failure(ip)
        _write_security_log("auth_failure", ip, "Invalid API key",
                            path=path, key_fp=key_fp)
        raise HTTPException(status_code=401, detail="Invalid API key")
    _record_success(ip)
    return user


_admin_security = HTTPBearer()


def _admin_auth(creds: HTTPAuthorizationCredentials = Depends(_admin_security)):
    """Admin auth — re-reads LOCALLYAI_ADMIN_KEY from os.environ on every
    request so a `manage_users.py rotate-admin` + a `set-env` cycle takes
    effect immediately without a process restart. Red-team finding 1.2:
    capturing the env var at import time meant rotations only worked
    after a launchctl kickstart, which firms forget to do.

    compare_digest needs both operands to be byte-equal length OR same
    type; we coerce to str on both sides."""
    admin_key = os.environ.get("LOCALLYAI_ADMIN_KEY", "")
    if not admin_key or not _hmac_mod.compare_digest(creds.credentials, admin_key):
        raise HTTPException(status_code=403, detail="Invalid admin key")
    return creds.credentials

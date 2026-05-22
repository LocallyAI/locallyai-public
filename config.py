import hashlib
import hmac as _hmac
import json
import os as _os
import socket as _socket
from pathlib import Path

BASE_DIR     = Path(__file__).parent

# ── Fleet identity ────────────────────────────────────────────────────────────
# Each node has a stable identifier stamped into every audit/billing entry
# and into fleet.json. Defaults to the machine hostname; override with
# LOCALLYAI_NODE_ID for predictable test fixtures or when the hostname is
# generic (e.g. "MacStudio.local"). Single-node deployments simply pick up
# the hostname and never look at it.
NODE_ID = _os.environ.get("LOCALLYAI_NODE_ID", "").strip() or _socket.gethostname()


# ── Data residency / compliance region ────────────────────────────────────────
# Tells the deployment which regulatory framework applies. Stamped into every
# audit + billing entry (inside the HMAC payload, so a forged region breaks
# the chain) and surfaced in /admin/processing-record so a DPO + auditor can
# verify the deployment self-identifies correctly.
#
# Defaults to "UK" for backwards compatibility — every existing deployment
# keeps working unchanged. New installs MUST pick via the install-script
# region picker (which writes to .env). The supported set is small on
# purpose; broaden only with regulatory review.
#
# UK   — UK GDPR / Data Protection Act 2018 / ICO posture.
# KSA  — Saudi Personal Data Protection Law (Royal Decree M/19, 2023);
#        breach reporting to SDAIA per PDPL Art. 31.
DATA_REGION = _os.environ.get("LOCALLYAI_DATA_REGION", "UK").strip().upper()
if DATA_REGION not in ("UK", "KSA"):
    import warnings as _warnings
    _warnings.warn(
        f"LOCALLYAI_DATA_REGION={DATA_REGION!r} is not one of {{UK, KSA}}; "
        "defaulting to UK. Run install.sh / install.ps1 region picker to set "
        "explicitly.", stacklevel=2,
    )
    DATA_REGION = "UK"


def is_ksa() -> bool:
    return DATA_REGION == "KSA"


def is_uk() -> bool:
    return DATA_REGION == "UK"


# ── Shared storage ────────────────────────────────────────────────────────────
# When set, files that must be visible to every node in the fleet
# (users.json, erasure.log, fleet.json) live under SHARED_DIR — typically a
# Syncthing-managed local directory in the 2-node Mac edition or an NFS
# mount once the firm has a NAS. When unset, we degenerate to single-node
# (everything under BASE_DIR) and the existing deployment keeps working
# unchanged. Per-node files (audit.log, .audit_chain, .last_rotate,
# billing.log) intentionally stay LOCAL — chains are per-node by design.
_SHARED_ENV = _os.environ.get("LOCALLYAI_SHARED_DIR", "").strip()
SHARED_DIR  = Path(_SHARED_ENV) if _SHARED_ENV else BASE_DIR

DATA_DIR     = BASE_DIR / "data"
# Honour LOCALLYAI_LOG_DIR so readers (audit_export, monitor, billing) and
# writers (api._write_audit) agree on the same path under non-default
# deployments and isolated test runs. LOG_DIR is per-node — never on the
# shared mount, so each node's audit chain is independent (per-node chains
# are a deliberate design choice; see docs/ha-2node-clients.md).
_LOG_DIR_ENV = _os.environ.get("LOCALLYAI_LOG_DIR", "")
LOG_DIR      = Path(_LOG_DIR_ENV) if _LOG_DIR_ENV else BASE_DIR / "logs"
AUDIT_LOG    = LOG_DIR / "audit.log"
BILLING_LOG  = LOG_DIR / "billing.log"
INGEST_STATE = BASE_DIR / ".ingest_state.json"

# users.json moves into SHARED_DIR so a key rotated on Mac-A is visible to
# Mac-B without an out-of-band sync. Single-node deployments still find it
# at BASE_DIR/users.json (SHARED_DIR == BASE_DIR by default).
USERS_FILE   = SHARED_DIR / "users.json"
FLEET_FILE   = SHARED_DIR / "fleet.json"
# erasure.log moves into SHARED_DIR so an Article-17 erasure performed on
# Mac-A is honoured by Mac-B without an out-of-band sync. Single-node
# falls back to BASE_DIR/erasure.log via SHARED_DIR == BASE_DIR.
ERASURE_LOG  = SHARED_DIR / "erasure.log"

_STORAGE_ENV = _os.environ.get("LOCALLYAI_STORAGE_DIR", "")
STORAGE_DIR  = Path(_STORAGE_ENV) if _STORAGE_ENV else BASE_DIR / "storage"

# When set, all QdrantClient instances connect to the Qdrant server at this URL
# instead of opening STORAGE_DIR as an embedded store. Required when more than
# one process needs concurrent access (e.g. api + ingest running together).
QDRANT_URL   = _os.environ.get("QDRANT_URL", "")
QDRANT_API_KEY = _os.environ.get("QDRANT_API_KEY", "")

# HA cluster: comma-separated list of Qdrant peer URLs. The client picks the
# first reachable peer for the request — every other peer in the cluster
# replicates the data internally (replication_factor=2 on collection
# create). Falls back to QDRANT_URL when unset, then to embedded.
QDRANT_URLS = [u.strip() for u in _os.environ.get("QDRANT_URLS", "").split(",") if u.strip()]

# When LOCALLYAI_HA=1, ensure_collection passes shard_number / replication_factor /
# write_consistency_factor so Qdrant gives us 2-node redundancy. Single-node
# deployments leave LOCALLYAI_HA unset and get the original single-shard layout.
HA_ENABLED = _os.environ.get("LOCALLYAI_HA", "").strip() in ("1", "true", "yes")


def _first_reachable_qdrant(urls: list[str]) -> str | None:
    """Return the first URL in `urls` whose /readyz responds within 1s.
    None if none reachable. Cheap probe used by make_qdrant_client at the
    rate of one call per request handler that needs Qdrant — handlers
    that hit Qdrant at sub-second cadence should cache the client."""
    if not urls:
        return None
    import urllib.error
    import urllib.request
    headers = {}
    if QDRANT_API_KEY:
        headers["api-key"] = QDRANT_API_KEY
    for u in urls:
        try:
            req = urllib.request.Request(f"{u.rstrip('/')}/readyz", headers=headers)
            with urllib.request.urlopen(req, timeout=1) as r:
                if r.status == 200:
                    return u
        except (urllib.error.URLError, OSError, TimeoutError):
            continue
    return None


def make_qdrant_client():
    """Build a QdrantClient pointing at the configured server, or fall back to
    the embedded store at STORAGE_DIR. Centralised so every caller agrees on
    transport and credentials.

    Order of preference:
      1. QDRANT_URLS (HA cluster) — pick first reachable peer.
      2. QDRANT_URL (single server, original behaviour).
      3. Embedded store at STORAGE_DIR (single-process dev).
    """
    from qdrant_client import QdrantClient
    if QDRANT_URLS:
        chosen = _first_reachable_qdrant(QDRANT_URLS) or QDRANT_URLS[0]
        return QdrantClient(url=chosen, api_key=QDRANT_API_KEY or None)
    if QDRANT_URL:
        return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
    return QdrantClient(path=str(STORAGE_DIR))

OLLAMA_BASE_URL = _os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# OpenAI-compatible base URL. Works with Ollama (default port 11434),
# LM Studio (default port 1234), vLLM, LocalAI, or OpenAI itself.
# Defaults to OLLAMA_BASE_URL for backward compatibility.
LLM_BASE_URL    = _os.environ.get("LLM_BASE_URL", OLLAMA_BASE_URL)
LLM_MODEL       = _os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")
# Embedding model. KSA fleets default to a multilingual model so Arabic
# documents retrieve well; nomic-embed-text is English-centric and degrades
# on Arabic. Operators can still override via EMBED_MODEL in .env.
# Documented Arabic-capable alternatives:
#   BAAI/bge-m3                       (1024-dim, recommend on capable hardware)
#   intfloat/multilingual-e5-large    (1024-dim, broader language coverage)
#   intfloat/multilingual-e5-base     (768-dim, matches existing VECTOR_SIZE)
EMBED_MODEL     = _os.environ.get(
    "EMBED_MODEL",
    "intfloat/multilingual-e5-base" if DATA_REGION == "KSA" else "nomic-embed-text:latest",
)

COLLECTION_NAME = "locallyai_legal_poc"
VECTOR_SIZE     = 768

CHUNK_SIZE     = 512
CHUNK_OVERLAP  = 64
TOP_K          = 5
CANDIDATE_POOL = 50
KEEP_ALIVE     = "0"

EXPAND_QUERIES  = False
RERANKER_MODEL  = "BAAI/bge-reranker-v2-m3"

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
SENTENCE_WINDOW  = 3
CLAUSE_MIN_WORDS = 30

API_HOST    = "0.0.0.0"
API_PORT    = 8000
API_TITLE   = "LocallyAI"
API_VERSION = "1.0.0"

# Salt for pseudonymising usernames in the audit log (GDPR Article 25 — data minimisation).
#
# We support multiple salts simultaneously via "eras". The CURRENT salt
# (LOCALLYAI_AUDIT_SALT) is what every new audit entry uses. Retired salts
# (LOCALLYAI_AUDIT_SALT_ERA_<N>=<salt>) are kept so we can re-identify a
# pseudonym from a historical entry — needed for GDPR Art. 15 subject-access
# and Art. 17 erasure that target old records. Salts never leave .env.
#
# Each entry gets a `salt_era` field — first 8 hex chars of SHA-256(salt).
# It identifies which salt the writer used WITHOUT exposing the salt itself
# (the era id is non-reversible to the salt under the same threat model that
# protects the pseudonym). The verifier and is_erased() consult the era to
# pick the right salt.
#
# Rotation: manage_users.py rotate-audit-salt generates a new salt, moves
# the old one into the next ERA_<N> slot, and stamps a salt_era_boundary
# entry into audit.log so an auditor can see exactly when the rotation
# happened. The HMAC chain is unbroken (the boundary is just another
# chained entry).
_AUDIT_SALT = _os.environ.get("LOCALLYAI_AUDIT_SALT", "")
if not _AUDIT_SALT:
    import warnings as _warnings
    _warnings.warn(
        "LOCALLYAI_AUDIT_SALT is not set. Audit log pseudonymisation uses no secret salt "
        "and can be reversed by anyone with a list of user names. Set this in .env.",
        stacklevel=2,
    )
elif len(_AUDIT_SALT) < 32:
    import warnings as _warnings
    _warnings.warn(
        f"LOCALLYAI_AUDIT_SALT is short ({len(_AUDIT_SALT)} chars). Use 64 hex chars "
        "(32 bytes of entropy) — `python -c 'import secrets; print(secrets.token_hex(32))'`. "
        "ISO 27001 A.8.24 / GDPR Art. 32: appropriate technical measures.",
        stacklevel=2,
    )


def _era_id(salt: str) -> str:
    """Non-reversible 8-hex-char identifier for a salt. Lands in audit
    entries as `salt_era` so the verifier picks the right salt without
    the salt itself appearing on disk."""
    if not salt:
        return ""
    return hashlib.sha256(f"era:{salt}".encode()).hexdigest()[:8]


def _load_salt_eras() -> list[tuple[str, str]]:
    """Return [(era_id, salt), ...] for the current salt and every retired
    era found in the env. Current first. Retired eras follow the pattern
    LOCALLYAI_AUDIT_SALT_ERA_1, _2, ... — consecutive integers; we stop
    at the first gap to avoid a stale ERA_99 from years ago confusing
    the lookup."""
    out: list[tuple[str, str]] = []
    if _AUDIT_SALT:
        out.append((_era_id(_AUDIT_SALT), _AUDIT_SALT))
    n = 1
    while True:
        retired = _os.environ.get(f"LOCALLYAI_AUDIT_SALT_ERA_{n}", "")
        if not retired:
            break
        out.append((_era_id(retired), retired))
        n += 1
    return out


_SALT_ERAS: list[tuple[str, str]] = _load_salt_eras()
_CURRENT_ERA: str = _SALT_ERAS[0][0] if _SALT_ERAS else ""


# ── User management ───────────────────────────────────────────────────────────

# Per-key metadata for expiry enforcement (GDPR art. 5(e), ISO 27001 A.8.5).
# Populated by _load_users(). Keyed by the API token; value is
# {"name": str, "expires_at": float | None}. expires_at == None means
# "never expires" — service accounts can opt in to that explicitly.
_KEY_META: dict = {}
# {sha256(key): (name, key)} — populated by _load_users(), drives
# validate_key()'s O(1) constant-time lookup. Storing the canonical key
# alongside the name lets validate_key avoid an O(N) tail loop just to
# find the matching key for compare_digest (red-team round-2 A1).
_KEY_BY_HASH: dict = {}

# Sentinel hash for audit entries that have no real user_hash — system
# events (admin_key_rotation, salt_era_boundary, etc.). Same shape as a
# pseudonymised user (16 hex chars) so the UI renders them consistently.
# Centralised here so audit_export + monitor agree (round-2 B9).
import hashlib as _hl

SYSTEM_USER_HASH = _hl.sha256(b"system_event").hexdigest()[:16]
del _hl


def _parse_iso(ts: str | None):
    if not ts:
        return None
    try:
        from datetime import datetime
        # Strip trailing 'Z' since fromisoformat doesn't accept it on 3.10.
        s = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


def _load_users() -> dict:
    """
    Load users from users.json. Returns {api_key: name} mapping.

    Two file shapes are accepted (backwards compatible):
      legacy:  {"Alice": "<api_key>"}
      v2:      {"Alice": {"key": "<api_key>", "created_at": "...",
                          "expires_at": "..." | null}}

    Empty dict if the file is missing or empty so the service can boot
    before the first user has been created — the installer adds the first
    user via manage_users.py immediately after launchd loads.
    """
    global _KEY_META, _KEY_BY_HASH
    _KEY_META = {}
    _KEY_BY_HASH = {}
    if not USERS_FILE.exists():
        return {}
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        import warnings as _w
        _w.warn(f"Failed to load users.json: {exc}", stacklevel=2)
        return {}
    if not data:
        return {}
    inverted: dict = {}
    import hashlib as _hl
    for name, entry in data.items():
        if isinstance(entry, str):
            key = entry
            expires_at = None
        elif isinstance(entry, dict):
            key = entry.get("key", "")
            expires_at = _parse_iso(entry.get("expires_at"))
        else:
            raise RuntimeError(f"users.json entry for {name!r} has unsupported type {type(entry).__name__}")
        if not isinstance(key, str) or len(key) < 32:
            raise RuntimeError(f"API key for {name!r} is too short or missing (minimum 32 characters).")
        inverted[key] = name
        _KEY_META[key] = {"name": name, "expires_at": expires_at}
        # Pre-compute SHA-256 of every valid key so validate_key() can do an
        # O(1) constant-time dict lookup. Red-team finding 1.1: the previous
        # per-key linear loop with hmac.compare_digest leaked timing info
        # proportional to user position in the dict. With a hash-map lookup,
        # the time taken is independent of whether the key is valid OR which
        # user it belongs to.
        _KEY_BY_HASH[_hl.sha256(key.encode()).hexdigest()] = (name, key)
    return inverted


USERS: dict = _load_users()

# mtime cache so a Syncthing-replicated users.json edit on Mac-A is picked
# up by Mac-B's auth check within ~1 second without a process restart.
# We rate-limit the stat call to once per second to avoid pounding the
# shared store on every API request.
import time as _time

_USERS_MTIME: float = USERS_FILE.stat().st_mtime if USERS_FILE.exists() else 0.0
_USERS_LAST_CHECK: float = 0.0
_USERS_CHECK_INTERVAL: float = 1.0


def _maybe_reload_users() -> None:
    """If users.json has changed on disk since the last check, reload it.
    Stat-rate-limited to once per _USERS_CHECK_INTERVAL seconds. Safe to
    call from any request handler — does no work the vast majority of the
    time. No-op if the file doesn't exist (single-node first-boot)."""
    global USERS, _USERS_MTIME, _USERS_LAST_CHECK
    now = _time.monotonic()
    if now - _USERS_LAST_CHECK < _USERS_CHECK_INTERVAL:
        return
    _USERS_LAST_CHECK = now
    try:
        m = USERS_FILE.stat().st_mtime
    except OSError:
        return
    if m != _USERS_MTIME:
        USERS = _load_users()
        _USERS_MTIME = m


def reload_users() -> int:
    """Hot-reload users from disk without restarting. Returns count of users loaded."""
    global USERS, _USERS_MTIME
    USERS = _load_users()
    try:
        _USERS_MTIME = USERS_FILE.stat().st_mtime
    except OSError:
        _USERS_MTIME = 0.0
    return len(USERS)


def validate_key(token: str) -> str | None:
    """Return username for a valid, non-expired API key, or None.

    Constant-time in TWO senses:
      1. Each individual comparison uses hmac.compare_digest.
      2. The lookup itself is O(1) (hash-map). The previous version did a
         linear scan with per-key compare_digest — each compare was
         constant-time but the *loop* leaked timing info proportional to
         the matching user's position in the dict (red-team 1.1).

    We hash the incoming token once, look up in _KEY_BY_HASH, then
    confirm with a final constant-time compare against the canonical
    stored key for that user (belt-and-braces against SHA-256 collisions
    in the lookup itself, though for 256-bit hashes this is paranoid).

    Expiry, erasure, and admin-key handling unchanged.
    """
    admin_key = _os.environ.get("LOCALLYAI_ADMIN_KEY", "")
    if admin_key and _hmac.compare_digest(token, admin_key):
        return "admin"
    _maybe_reload_users()
    if not _KEY_BY_HASH:
        return None
    import hashlib as _hl
    token_hash = _hl.sha256(token.encode()).hexdigest()
    hit = _KEY_BY_HASH.get(token_hash)
    if hit is None:
        return None
    stored_name, stored_key = hit
    # Final compare_digest against the canonical stored key — protects
    # against the theoretical case where two different keys collide on
    # SHA-256 (10**-77 odds; we still check). No O(N) loop: stored_key
    # comes directly from the hash-keyed dict.
    if not _hmac.compare_digest(token, stored_key):
        return None
    meta = _KEY_META.get(stored_key, {})
    expires_at = meta.get("expires_at")
    if expires_at is not None and expires_at < _time.time():
        return None
    if is_erased(pseudonymise_user(stored_name)):
        return None
    return stored_name


def pseudonymise_user(name: str, *, era: str | None = None) -> str:
    """
    One-way pseudonymisation of a username for GDPR-compliant audit
    records. The salt (LOCALLYAI_AUDIT_SALT or one of the retired
    LOCALLYAI_AUDIT_SALT_ERA_<N> values) must be kept secret — it is
    the only way to re-identify a user from their hash on a regulatory
    subject-access request.

    `era` selects which salt to use:
      * None (default)       — current salt; what new audit entries use
      * "<era_id>" (8 hex)   — the historical salt with that era id;
                                used by is_erased / re-identification
                                when reading entries from before a salt
                                rotation. Returns "" if the era is
                                unknown so callers can detect that the
                                old salt has been deliberately retired.
    """
    if era is None or era == _CURRENT_ERA:
        salt = _AUDIT_SALT
    else:
        for eid, s in _SALT_ERAS:
            if eid == era:
                salt = s
                break
        else:
            return ""
    return hashlib.sha256(f"{salt}:{name}".encode()).hexdigest()[:16]


def current_salt_era() -> str:
    """The 8-hex-char era id new audit entries should be stamped with.
    Empty string if no salt configured."""
    return _CURRENT_ERA


def known_salt_eras() -> list[str]:
    """Era ids the deployment can still re-identify against (current + all
    retired). Surfaced by /admin/processing-record so a DPO can see how
    many salt rotations have happened and audit the retention of retired
    salts (which are themselves regulated key material)."""
    return [eid for eid, _ in _SALT_ERAS]


def _full_disk_encryption_active() -> bool:
    """Best-effort check for FileVault (macOS) / BitLocker (Windows) on the
    boot volume. Returns False on any error so the verifier defaults to
    the cautious WARN posture if we can't confirm encryption."""
    import platform as _plat
    import subprocess as _sp
    sysname = _plat.system()
    try:
        if sysname == "Darwin":
            r = _sp.run(["fdesetup", "status"], capture_output=True, text=True, timeout=5)
            return r.returncode == 0 and "FileVault is On" in (r.stdout or "")
        if sysname == "Windows":
            # `manage-bde -status C:` returns "Protection On" when active.
            r = _sp.run(["manage-bde", "-status", "C:"], capture_output=True, text=True, timeout=8)
            return r.returncode == 0 and "Protection On" in (r.stdout or "")
        # Linux + others: unknown — treat as not-confirmed (WARN posture
        # is correct here because we can't verify dm-crypt automatically).
        return False
    except Exception:
        return False


def verify_key_material() -> list[dict]:
    """Run a battery of checks on the deployment's pseudonymisation key
    material against GDPR Art. 4(5) ("kept separately and subject to
    technical and organisational measures"), ISO 27001 A.8.24 (use of
    cryptography), UAE PDPL Federal Decree-Law 45/2021 art. 8(2), and
    KSA PDPL art. 19. Returns a list of {level: 'ok'|'warn'|'fail',
    code, message} entries. Levels:

      ok    — the control is in place, nothing to fix
      warn  — the deployment will function but is not compliance-best:
              flag for the operator at startup and in the dashboard
      fail  — refuse to start (or surface as a deployment-blocker)

    Currently no condition produces 'fail' on its own (we never crash
    a running production box on a tightening), but production playbooks
    can promote any of the warns to deploy-time failures by checking the
    return value from install.sh.
    """
    out: list[dict] = []

    # 1. Salt is set, and is at least 32 hex chars (≥ 16 bytes entropy).
    if not _AUDIT_SALT:
        out.append({"level": "fail", "code": "salt_missing",
                    "message": "LOCALLYAI_AUDIT_SALT is not set — pseudonymisation degenerates "
                               "to public SHA-256 and is reversible from any user-name list. "
                               "GDPR Art. 25 / Art. 32 violation. Set a 64-hex-char value in .env."})
    elif len(_AUDIT_SALT) < 32:
        out.append({"level": "warn", "code": "salt_short",
                    "message": f"LOCALLYAI_AUDIT_SALT is {len(_AUDIT_SALT)} chars; "
                               "regulators expect 64 hex chars (32 bytes entropy)."})
    else:
        out.append({"level": "ok", "code": "salt_present",
                    "message": f"audit salt configured ({len(_AUDIT_SALT)} chars, "
                               f"era {_CURRENT_ERA or '?'})"})

    # 2. .env exists and is mode 0o600 (owner only). On Windows we accept
    #    any state ACL-restricted by icacls; the platform_compat layer
    #    handles that.
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        try:
            mode = env_path.stat().st_mode & 0o777
            if _os.name == "posix" and mode & 0o077:
                out.append({"level": "warn", "code": "env_perms",
                            "message": f".env is mode {mode:#o}; should be 0o600 "
                                       "(owner-only). GDPR Art. 32 / ISO 27001 A.8.3."})
            else:
                out.append({"level": "ok", "code": "env_perms",
                            "message": f".env mode {mode:#o} (owner-only)"})
        except OSError as e:
            out.append({"level": "warn", "code": "env_perms_check",
                        "message": f"could not stat .env: {e}"})

    # 3. .env and users.json on the SAME partition is the deployment's
    #    realistic posture. The risk (file-level reader can re-identify
    #    pseudonyms) is fully mitigated by full-disk encryption +
    #    0o600 ACLs. When both mitigations are in place this is the
    #    intended single-deployment trust model — surface as 'ok' with
    #    a "colocated but mitigated" message so the operator knows.
    users_path = USERS_FILE
    if env_path.exists() and users_path.exists():
        try:
            same_dev = env_path.stat().st_dev == users_path.stat().st_dev
        except OSError:
            same_dev = True
        if not same_dev:
            out.append({"level": "ok", "code": "key_material_separated",
                        "message": ".env and users.json are on different partitions"})
        else:
            fde_on = _full_disk_encryption_active()
            env_owner_only = _os.name != "posix" or not (
                env_path.stat().st_mode & 0o077
            )
            if fde_on and env_owner_only:
                out.append({"level": "ok", "code": "key_material_colocated_mitigated",
                            "message": ".env + users.json colocated; mitigated by "
                                       "full-disk encryption + 0o600 ACLs (acceptable "
                                       "single-deployment trust model — ISO 27001 A.5.31 / "
                                       "GDPR Art. 4(5))."})
            else:
                missing = []
                if not fde_on: missing.append("full-disk encryption (FileVault/BitLocker)")
                if not env_owner_only: missing.append(".env 0o600 ACLs")
                out.append({"level": "warn", "code": "key_material_colocated",
                            "message": ".env (the salt) and users.json (the name list) "
                                       "are on the same partition. Mitigations missing: "
                                       + ", ".join(missing) +
                                       ". ISO 27001 A.5.31 / GDPR Art. 4(5)."})

    # 4. HMAC chain key for the audit log (separate concern from salt; A.8.15).
    if not _os.environ.get("LOCALLYAI_AUDIT_HMAC_KEY"):
        out.append({"level": "warn", "code": "audit_hmac_missing",
                    "message": "LOCALLYAI_AUDIT_HMAC_KEY not set — audit log is not "
                               "tamper-evident. ISO 27001 A.8.15 / A.5.33."})

    # 5. Retired salt eras still readable (subject-access on old entries
    #    needs the old salts available). Warn if there are no retired
    #    eras AND the deployment looks production-aged (audit.log >1MB).
    retired = max(0, len(_SALT_ERAS) - 1)
    audit_path = LOG_DIR / "audit.log"
    if retired == 0:
        try:
            big = audit_path.exists() and audit_path.stat().st_size > 1_000_000
        except OSError:
            big = False
        if big:
            out.append({"level": "warn", "code": "no_retired_eras",
                        "message": "audit.log is sizeable but no retired salt eras are "
                                   "configured. Consider running `manage_users.py "
                                   "rotate-audit-salt` periodically (GDPR Art. 32 / "
                                   "ISO 27001 A.8.24: cryptographic key rotation)."})
        else:
            out.append({"level": "ok", "code": "no_rotation_yet",
                        "message": "no retired salt eras (deployment is young — rotation "
                                   "becomes relevant after the first months of production)"})
    else:
        out.append({"level": "ok", "code": "retired_eras",
                    "message": f"{retired} retired salt era(s) retained for subject-access"})

    return out


# ── Erasure ledger (GDPR art. 17 / UAE PDPL art. 14 / KSA PDPL art. 18) ─────
# A pseudonym appears in this set after manage_users.py erase has tombstoned
# it. validate_key() rejects logins for the underlying user; _write_audit
# refuses to record new entries for the pseudonym (defence in depth — the
# user is also removed from users.json, but during the Syncthing sync gap a
# stale users.json on the peer could otherwise let one more entry through).
# Cache is mtime-rate-limited like _maybe_reload_users.

_ERASED: set[str] = set()
_ERASURE_MTIME: float = 0.0
_ERASURE_LAST_CHECK: float = 0.0


def _load_erased() -> set[str]:
    """Read the erasure ledger and return the set of erased pseudonyms.
    The on-disk format is one JSON object per line; we extract the
    `pseudonym` field. Tolerates partial / malformed lines so a single
    bad write doesn't blind the entire fleet."""
    if not ERASURE_LOG.exists():
        return set()
    out: set[str] = set()
    try:
        with open(ERASURE_LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                p = obj.get("pseudonym")
                if isinstance(p, str) and p:
                    out.add(p)
    except OSError:
        pass
    return out


def _maybe_reload_erased() -> None:
    global _ERASED, _ERASURE_MTIME, _ERASURE_LAST_CHECK
    now = _time.monotonic()
    if now - _ERASURE_LAST_CHECK < _USERS_CHECK_INTERVAL:
        return
    _ERASURE_LAST_CHECK = now
    try:
        m = ERASURE_LOG.stat().st_mtime
    except OSError:
        if _ERASED:
            _ERASED = set()
            _ERASURE_MTIME = 0.0
        return
    if m != _ERASURE_MTIME:
        _ERASED = _load_erased()
        _ERASURE_MTIME = m


def is_erased(pseudonym: str) -> bool:
    """True if this pseudonym appears in the erasure ledger. Used by
    _write_audit and validate_key to refuse work for erased identities
    even if a stale users.json on this node still lists them.

    Erasure tombstones written by manage_users.py erase store every
    era's pseudonym for the user (so the lookup hits regardless of which
    salt was active at the time of an audit-log entry the operator
    might re-process). For live-traffic blocking we only need the
    current-era hash; both forms land in _ERASED."""
    _maybe_reload_erased()
    return pseudonym in _ERASED


# Initial population so first request doesn't pay the load cost.
_ERASED = _load_erased()
_ERASURE_MTIME = ERASURE_LOG.stat().st_mtime if ERASURE_LOG.exists() else 0.0

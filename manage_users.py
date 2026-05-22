import hashlib
import json
import os
import re
import secrets
from pathlib import Path

# Load .env so erasure tombstones use the same LOCALLYAI_AUDIT_SALT the API
# uses; otherwise the pseudonym in the tombstone won't match audit.log.
_ENV_FILE = Path(__file__).resolve().parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

_NAME_RE = re.compile(r"^[\w][\w\s\-\&\.']{0,61}[\w\.']$|^\w$")

BASE_DIR   = Path(__file__).resolve().parent
# Use the same USERS_FILE config.py exposes so single-node deployments keep
# using ./users.json and HA deployments pick up SHARED_DIR/users.json.
import sys as _sys

_sys.path.insert(0, str(BASE_DIR))
from datetime import UTC

from config import USERS_FILE  # noqa: E402


def _load() -> dict:
    if USERS_FILE.exists():
        try:
            return json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save(users: dict):
    from platform_compat import chmod_safe
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(users, indent=2), encoding="utf-8")
    tmp.replace(USERS_FILE)
    chmod_safe(USERS_FILE, 0o600)

def _validate_key(key: str):
    if len(key) < 32:
        raise ValueError("API keys must be at least 32 characters.")


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expiry_iso(days: int | None = None) -> str | None:
    """Default key TTL via LOCALLYAI_KEY_TTL_DAYS (default 90).
    Pass days=0 explicitly for a never-expiring service account.
    GDPR art. 5(e) (storage limitation) and ISO 27001 A.8.5 (secure auth)
    both call for credential rotation; we make the default short and the
    'forever' choice explicit and auditable."""
    from datetime import datetime, timedelta
    if days is None:
        days = int(os.environ.get("LOCALLYAI_KEY_TTL_DAYS", "90"))
    if days <= 0:
        return None
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coerce_entry(value) -> dict:
    """Accept legacy string entries (just the key) or v2 dict entries."""
    if isinstance(value, str):
        return {"key": value, "created_at": None, "expires_at": None}
    if isinstance(value, dict):
        return {
            "key":         value.get("key", ""),
            "created_at":  value.get("created_at"),
            "expires_at":  value.get("expires_at"),
        }
    raise ValueError(f"Unsupported users.json entry type: {type(value).__name__}")

def add_user(name: str, key: str = None, ttl_days: int | None = None) -> str:
    if not name or not _NAME_RE.match(name):
        raise ValueError("User name must start and end with a letter, digit, or punctuation and contain only letters, digits, spaces, hyphens, ampersands, apostrophes, or dots (max 64 chars).")
    users = _load()
    if name in users:
        raise ValueError(f"User {name!r} already exists. Use rotate_key to change their key.")
    if key is None:
        key = secrets.token_hex(32)
    _validate_key(key)
    users[name] = {
        "key":         key,
        "created_at":  _now_iso(),
        "expires_at":  _expiry_iso(ttl_days),
    }
    _save(users)
    return key

def remove_user(name: str):
    users = _load()
    if name not in users:
        raise ValueError(f"User {name!r} not found.")
    del users[name]
    _save(users)

def rotate_key(name: str, ttl_days: int | None = None) -> str:
    users = _load()
    if name not in users:
        raise ValueError(f"User {name!r} not found.")
    entry = _coerce_entry(users[name])
    new_key = secrets.token_hex(32)
    entry["key"] = new_key
    entry["created_at"] = _now_iso()
    entry["expires_at"] = _expiry_iso(ttl_days)
    users[name] = entry
    _save(users)
    return new_key

def renew_key(name: str, ttl_days: int | None = None) -> dict:
    """Extend the expiry without rotating the key value. Useful for
    in-the-loop renewal where rotating would force a redeploy."""
    users = _load()
    if name not in users:
        raise ValueError(f"User {name!r} not found.")
    entry = _coerce_entry(users[name])
    entry["expires_at"] = _expiry_iso(ttl_days)
    users[name] = entry
    _save(users)
    return {"name": name, "expires_at": entry["expires_at"]}

def list_users() -> list:
    out = []
    for name, value in _load().items():
        e = _coerce_entry(value)
        out.append({
            "name":        name,
            "created_at":  e.get("created_at"),
            "expires_at":  e.get("expires_at"),
        })
    return out

def load_users() -> dict:
    return _load()


def erase_user(name: str) -> dict:
    """GDPR art. 17 / UAE PDPL art. 14 / KSA PDPL art. 18 right to erasure.

    Workflow that preserves the audit chain's integrity (ISO 27001 A.8.15
    requires we cannot tamper with prior log entries) while honouring the
    request:

      1. Remove the user from users.json (immediate revocation).
      2. Redact billing.log lines that reference this user — billing
         records carry the real name; they're rewritten to "(erased)".
      3. The audit log keeps the pseudonym entries (HMAC chain integrity)
         but a tombstone entry is appended noting the erasure with the
         pseudonym so a future DPO query can answer "yes, that pseudonym
         corresponds to an erasure request on <date>".

    Returns a summary dict for the CLI to print + the operator to file
    against the data-subject's erasure request as evidence of action.
    """
    import datetime
    users = _load()
    if name not in users:
        raise ValueError(f"User {name!r} not found.")

    # Compute the pseudonym under EVERY known salt era so the tombstone
    # blocks audit writes regardless of which salt was active when an
    # entry was originally chained. The current-era hash is also the
    # primary form for live-traffic blocking via is_erased().
    from config import current_salt_era, known_salt_eras, pseudonymise_user
    eras = known_salt_eras() or [""]
    pseudonyms = sorted({
        pseudonymise_user(name, era=e) for e in eras
        if pseudonymise_user(name, era=e)
    })
    pseudonym = pseudonymise_user(name, era=current_salt_era()) or (pseudonyms[0] if pseudonyms else "(no-salt)")

    # 1. Revoke access.
    del users[name]
    _save(users)

    # 2. Redact billing.log entries that name the user.
    billing = BASE_DIR / "logs" / "billing.log"
    redacted = 0
    if billing.exists():
        tmp = billing.with_suffix(".redacted")
        with open(billing, encoding="utf-8") as fin, \
             open(tmp,     "w", encoding="utf-8") as fout:
            for line in fin:
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    fout.write(line)
                    continue
                if rec.get("user") == name:
                    rec["user"] = "(erased)"
                    rec["erased"] = True
                    redacted += 1
                fout.write(json.dumps(rec) + "\n")
        tmp.replace(billing)
        from platform_compat import chmod_safe
        chmod_safe(billing, 0o640)

    # 3. Append a tombstone to the shared erasure ledger. Lives on
    #    SHARED_DIR so a Mac-A erasure is honoured by Mac-B (config.is_erased
    #    consults this file with a 1-second mtime cache). The audit log has
    #    its own HMAC chain (ISO 27001 A.8.15 tamper-evidence) and any line
    #    we wrote to it without computing the chain link would mark the
    #    chain TAMPERED — that's why erasure records get their own log.
    #    The shared_lock prevents two nodes appending concurrently and
    #    ending up with a Syncthing conflict file.
    from config import ERASURE_LOG
    from shared_lock import shared_lock
    ERASURE_LOG.parent.mkdir(exist_ok=True)
    # We write ONE line per pseudonym so config._load_erased's per-line
    # parser picks up every era. Same timestamp + same regulation tag so
    # they group in the operator's view. Single-era deployments produce
    # exactly one line, identical to the prior schema.
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with shared_lock(ERASURE_LOG, timeout=5.0):
        with open(ERASURE_LOG, "a", encoding="utf-8") as f:
            for p in pseudonyms:
                f.write(json.dumps({
                    "timestamp":              ts,
                    "event":                  "erasure",
                    "pseudonym":              p,
                    "salt_era":               next(
                        (e for e in eras
                         if pseudonymise_user(name, era=e) == p),
                        ""),
                    "billing_redacted_lines": redacted,
                    "regulation":             "GDPR art.17 / UAE PDPL art.14 / KSA PDPL art.18",
                }) + "\n")
    tombstone = {"timestamp": ts}  # only the timestamp is used downstream
    from platform_compat import chmod_safe
    chmod_safe(ERASURE_LOG, 0o640)

    # 4. Best-effort fan-out: ping every active peer's /admin/fleet/refresh
    #    so they re-read users.json + erasure.log immediately rather than
    #    waiting up to 10s for Syncthing to replicate the change AND up to
    #    1s more for their mtime cache. Single-node fleets short-circuit.
    peers_notified = _broadcast_fleet_refresh()

    return {
        "user": name,
        "pseudonym": pseudonym,
        "billing_redacted_lines": redacted,
        "users_json": "removed",
        "erasure_log_entry": tombstone["timestamp"],
        "peers_notified": peers_notified,
    }


def rotate_admin_key() -> dict:
    """Generate a new LOCALLYAI_ADMIN_KEY and rewrite .env in place.

    Why an in-place rewrite (and not just `sed`): we preserve comments
    and key order so downstream tooling (audit_install.sh, install.ps1)
    can keep parsing the file. The previous admin key is irrecoverably
    overwritten — there is no era machinery for the admin key (unlike
    the audit salt, where ERAs let you de-pseudonymise old log entries).
    Any operator with the old key loses access immediately on next API
    restart.

    Stamps an `admin_key_rotation` entry into the live audit chain so an
    auditor can see who rotated and when. The hash of the OLD key is
    recorded (not the key itself) so we can prove which key was retired
    without exposing it.

    Idempotent only in that calling twice issues two distinct rotations.
    No no-op path.
    """
    import secrets
    from datetime import datetime

    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        raise FileNotFoundError(
            f".env not found at {env_path}; nothing to rotate. Run install.sh first.")

    # Read existing .env line-by-line so we can rewrite preserving order
    # + comments. Same pattern as rotate_audit_salt.
    lines = env_path.read_text(encoding="utf-8").splitlines()
    out_lines: list[str] = []
    seen = False
    new_key = secrets.token_hex(32)
    old_key = ""
    for ln in lines:
        if "=" in ln and not ln.lstrip().startswith("#"):
            k, _, v = ln.partition("=")
            if k.strip() == "LOCALLYAI_ADMIN_KEY":
                old_key = v.strip()
                out_lines.append(f"LOCALLYAI_ADMIN_KEY={new_key}")
                seen = True
                continue
        out_lines.append(ln)
    if not seen:
        # Brand-new entry — append under a marker comment so it's findable.
        out_lines.append("")
        out_lines.append(f"# Admin key rotated {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}")
        out_lines.append(f"LOCALLYAI_ADMIN_KEY={new_key}")
    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    from platform_compat import chmod_safe
    chmod_safe(env_path, 0o600)

    # Stamp the rotation into the HMAC-chained audit log. We log the
    # SHA-256 of the old key (truncated) — proves which key was retired
    # without leaking the bytes themselves. Bypass the running api on
    # purpose: the operator restarts after rotation, and the entry must
    # be written under the current chain state.
    from config import LOG_DIR, NODE_ID
    audit_log = LOG_DIR / "audit.log"
    chain_state = LOG_DIR / ".audit_chain"
    hmac_key = os.environ.get("LOCALLYAI_AUDIT_HMAC_KEY", "").encode()
    entry = {
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "node_id":   NODE_ID,
        "event":     "admin_key_rotation",
        "previous_admin_key_sha256_prefix": (
            hashlib.sha256(old_key.encode()).hexdigest()[:16] if old_key else None),
        "new_admin_key_sha256_prefix": hashlib.sha256(new_key.encode()).hexdigest()[:16],
        "regulation": "GDPR art. 32 / ISO 27001 A.5.16 (privileged access management)",
    }
    if hmac_key and audit_log.exists():
        import hmac
        import json as _json
        prev = chain_state.read_text(encoding="utf-8").strip() if chain_state.exists() else ("0" * 64)
        entry_json = _json.dumps(entry, sort_keys=True)
        chain = hmac.new(hmac_key, (prev + entry_json).encode(), hashlib.sha256).hexdigest()
        entry["_chain_hmac"] = chain
        with open(audit_log, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry) + "\n")
        chain_state.write_text(chain, encoding="utf-8")
        chmod_safe(audit_log, 0o640)
        chmod_safe(chain_state, 0o640)

    return {
        "new_admin_key": new_key,
        "previous_admin_key_sha256_prefix": entry["previous_admin_key_sha256_prefix"],
        "audit_entry_at": entry["timestamp"],
    }


def rotate_audit_salt(*, keep_eras: int = 4) -> dict:
    """Rotate LOCALLYAI_AUDIT_SALT and demote the previous current salt
    to ERA_1, ERA_1 → ERA_2, etc. Drop any era beyond keep_eras (those
    historical pseudonyms become unrecoverable on subject-access — ISO
    27001 A.8.10 information deletion).

    Stamps a `salt_era_boundary` entry into the live audit chain so an
    auditor can see exactly when the rotation happened. Chain stays
    intact (the boundary is a regular HMAC-chained entry, not a special
    one).

    Idempotent ONLY in the sense that calling it twice creates two
    distinct rotations; there is no "no-op" path. Operators wanting a
    smoke-test should use a sandbox deployment.
    """
    import secrets
    from datetime import datetime

    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        raise FileNotFoundError(
            f".env not found at {env_path}; nothing to rotate. Run install.sh first.")

    # Read existing .env into an ordered list so we can rewrite preserving
    # comments + line order.
    lines = env_path.read_text(encoding="utf-8").splitlines()
    kv: dict[str, str] = {}
    line_keys: list[tuple[int, str | None]] = []
    for i, ln in enumerate(lines):
        if not ln.strip() or ln.lstrip().startswith("#") or "=" not in ln:
            line_keys.append((i, None))
            continue
        k, _, v = ln.partition("=")
        k = k.strip()
        kv[k] = v
        line_keys.append((i, k))

    old_current = kv.get("LOCALLYAI_AUDIT_SALT", "")
    if not old_current:
        raise RuntimeError(
            "LOCALLYAI_AUDIT_SALT is not set in .env. Cannot rotate from no salt — "
            "set one first via install.sh, then rotate.")

    # Collect retired eras in order.
    retired: list[str] = []
    n = 1
    while True:
        v = kv.get(f"LOCALLYAI_AUDIT_SALT_ERA_{n}", "")
        if not v:
            break
        retired.append(v)
        n += 1

    # Generate the new salt and shift everything down by one.
    new_current = secrets.token_hex(32)
    new_retired = [old_current] + retired
    if keep_eras >= 0:
        new_retired = new_retired[:keep_eras]

    # Compute era ids for the rotation record (do NOT log the salts).
    import hashlib as _hl
    def _eid(s: str) -> str:
        return _hl.sha256(f"era:{s}".encode()).hexdigest()[:8]
    new_era_id   = _eid(new_current)
    old_era_id   = _eid(old_current)
    dropped_eras = [_eid(s) for s in ([old_current] + retired)[keep_eras:]] if keep_eras >= 0 else []

    # Build the new .env content. Strategy: replace existing keys in place
    # (so comments/order survive); append any newly-introduced ERA keys at
    # the end of the file under a marker comment.
    new_kv = dict(kv)
    new_kv["LOCALLYAI_AUDIT_SALT"] = new_current
    # Wipe ALL existing ERA_N keys, then re-add the surviving ones.
    for k in list(new_kv):
        if k.startswith("LOCALLYAI_AUDIT_SALT_ERA_"):
            del new_kv[k]
    for i, s in enumerate(new_retired, start=1):
        new_kv[f"LOCALLYAI_AUDIT_SALT_ERA_{i}"] = s

    out_lines: list[str] = []
    seen: set[str] = set()
    for idx, key in line_keys:
        if key is None:
            out_lines.append(lines[idx])
            continue
        if key in new_kv:
            out_lines.append(f"{key}={new_kv[key]}")
            seen.add(key)
        # else: removed (an old ERA key that's no longer present)
    appended = [k for k in new_kv if k not in seen]
    if appended:
        out_lines.append("")
        out_lines.append(f"# Audit-salt eras (rotated {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')})")
        for k in appended:
            out_lines.append(f"{k}={new_kv[k]}")
    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    from platform_compat import chmod_safe
    chmod_safe(env_path, 0o600)

    # Stamp a salt_era_boundary entry into audit.log via a tiny standalone
    # writer. We intentionally bypass the running api process — the operator
    # is expected to restart the service after rotation, and the boundary
    # entry must be HMAC-chained under the OLD salt so the chain at the
    # moment of rotation stays valid. (New audit entries after restart will
    # use the new salt era; the verifier picks the right salt per entry.)
    from config import LOG_DIR, NODE_ID
    audit_log = LOG_DIR / "audit.log"
    chain_state = LOG_DIR / ".audit_chain"
    hmac_key = os.environ.get("LOCALLYAI_AUDIT_HMAC_KEY", "").encode()
    boundary = {
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "node_id":   NODE_ID,
        "event":     "salt_era_boundary",
        "previous_era": old_era_id,
        "new_era":      new_era_id,
        "retained_eras": [_eid(s) for s in new_retired],
        "dropped_eras":  dropped_eras,
        "salt_era":  old_era_id,  # the boundary itself uses the OLD salt
                                  # so the HMAC chain stays continuous
        "regulation": "GDPR art. 32 / ISO 27001 A.8.24 (key rotation)",
    }
    if hmac_key and audit_log.exists():
        import hmac
        import json as _json
        prev = chain_state.read_text(encoding="utf-8").strip() if chain_state.exists() else ("0" * 64)
        entry_json = _json.dumps(boundary, sort_keys=True)
        chain = hmac.new(hmac_key, (prev + entry_json).encode(), hashlib.sha256).hexdigest()
        boundary["_chain_hmac"] = chain
        with open(audit_log, "a", encoding="utf-8") as f:
            f.write(_json.dumps(boundary) + "\n")
        chain_state.write_text(chain, encoding="utf-8")
        chmod_safe(audit_log, 0o640)
        chmod_safe(chain_state, 0o640)

    return {
        "new_era":           new_era_id,
        "previous_era":      old_era_id,
        "retained_era_count": len(new_retired) + 1,  # current + retired
        "dropped_era_count":  len(dropped_eras),
        "audit_boundary_at":  boundary["timestamp"],
        "env_file":           str(env_path),
    }


def _broadcast_fleet_refresh() -> dict:
    """POST /admin/fleet/refresh to every active peer except this node.
    Returns {peer_id: status_string}. Failures are recorded but never
    raised — the shared store is the source of truth; the fan-out is a
    latency optimisation, not a correctness requirement."""
    import ssl
    import urllib.error
    import urllib.request
    try:
        import fleet as _fleet
        from config import NODE_ID
    except Exception:
        return {}
    admin = os.environ.get("LOCALLYAI_ADMIN_KEY", "")
    if not admin:
        return {"_warning": "LOCALLYAI_ADMIN_KEY not set; skipping peer fan-out"}
    nodes = _fleet.active_nodes() or []
    out: dict[str, str] = {}
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    for n in nodes:
        nid = n.get("node_id", "?")
        if nid == NODE_ID:
            continue
        url = f"{n.get('api_url', '').rstrip('/')}/admin/fleet/refresh"
        try:
            req = urllib.request.Request(url, method="POST",
                                         headers={"Authorization": f"Bearer {admin}"})
            with urllib.request.urlopen(req, timeout=3, context=ssl_ctx) as r:
                out[nid] = "ok" if r.status == 200 else f"http {r.status}"
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            out[nid] = f"unreachable: {str(e)[:120]}"
    return out

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="LocallyAI user management")
    sub = parser.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add",    help="Add a user")
    p_add.add_argument("name")
    p_add.add_argument("--key", default=None, help="Supply key (min 32 chars); auto-generated if omitted")
    p_add.add_argument("--ttl-days", type=int, default=None,
        help="Override LOCALLYAI_KEY_TTL_DAYS for this user. 0 = never expires (service account).")

    p_rm  = sub.add_parser("remove", help="Remove a user")
    p_rm.add_argument("name")

    p_rot = sub.add_parser("rotate", help="Rotate a user API key")
    p_rot.add_argument("name")
    p_rot.add_argument("--ttl-days", type=int, default=None,
        help="Reset expiry to this many days from now. 0 = never expires.")

    p_renew = sub.add_parser("renew",
        help="Extend expiry without rotating the key (no client redeploy needed)")
    p_renew.add_argument("name")
    p_renew.add_argument("--ttl-days", type=int, default=None)

    p_ls  = sub.add_parser("list",   help="List users")

    p_erase = sub.add_parser("erase",
        help="GDPR art.17 / PDPL erasure: remove user, redact billing log, append audit tombstone")
    p_erase.add_argument("name")

    p_rot_admin = sub.add_parser("rotate-admin-key",
        help="Generate a new LOCALLYAI_ADMIN_KEY, rewrite .env, stamp audit. "
             "Restart the API afterwards.")

    p_rot_salt = sub.add_parser("rotate-audit-salt",
        help="Generate a new pseudonymisation salt; retire the current one to ERA_<N>. "
             "GDPR art. 32 / ISO 27001 A.8.24: documented control for cryptographic key rotation.")
    p_rot_salt.add_argument("--keep-eras", type=int, default=4,
        help="How many retired eras to keep (so subject-access requests for old "
             "audit entries still work). Beyond this, the oldest era is dropped — "
             "those historical pseudonyms become unrecoverable, which is the right "
             "default for ISO 27001 A.8.10 information-deletion compliance. Set to "
             "0 to drop ALL old salts immediately (rare; only if a salt was leaked).")

    args = parser.parse_args()
    if args.cmd == "add":
        key = add_user(args.name, args.key, args.ttl_days)
        users = _load()
        exp = _coerce_entry(users[args.name]).get("expires_at")
        print(f"Added user {args.name!r}.")
        print(f"API key:   {key}")
        print(f"Expires:   {exp or 'never (service account)'}")
        print("Store this key securely — it will not be shown again.")
    elif args.cmd == "remove":
        remove_user(args.name)
        print(f"Removed user {args.name!r}.")
    elif args.cmd == "rotate":
        key = rotate_key(args.name, args.ttl_days)
        users = _load()
        exp = _coerce_entry(users[args.name]).get("expires_at")
        print(f"Rotated key for {args.name!r}.")
        print(f"New API key: {key}")
        print(f"Expires:     {exp or 'never (service account)'}")
        print("Store this key securely — it will not be shown again.")
    elif args.cmd == "renew":
        result = renew_key(args.name, args.ttl_days)
        print(f"Renewed expiry for {args.name!r}.")
        print(f"  expires_at: {result['expires_at'] or 'never (service account)'}")
    elif args.cmd == "list":
        users = list_users()
        if users:
            print(f"  {'name':32}  {'created':22}  {'expires':22}")
            print(f"  {'-'*32}  {'-'*22}  {'-'*22}")
            for u in users:
                print(f"  {u['name']:32}  {(u['created_at'] or '-'):22}  {(u['expires_at'] or 'never'):22}")
        else:
            print("No users configured.")
    elif args.cmd == "erase":
        result = erase_user(args.name)
        print(f"Erasure complete for {args.name!r}.")
        for k, v in result.items():
            print(f"  {k}: {v}")
        print("File this output against the data-subject's erasure request as evidence of action.")
    elif args.cmd == "rotate-admin-key":
        result = rotate_admin_key()
        print("Admin key rotated.")
        print()
        print(f"  New admin key:  {result['new_admin_key']}")
        print(f"  Audit stamp at: {result['audit_entry_at']}")
        print()
        print("Store this key securely — it will not be shown again.")
        print()
        print("ACTION REQUIRED: restart the API so it picks up the new key.")
        print("  Stop LocallyAI.app  (or)  bash scripts/stop_locallyai.sh")
        print("  LocallyAI Manager.app  (or)  bash scripts/start_locallyai.sh manager")
        print()
        print("Anyone holding the OLD admin key loses access on the restart.")
    elif args.cmd == "rotate-audit-salt":
        result = rotate_audit_salt(keep_eras=args.keep_eras)
        print("Audit salt rotated.")
        for k, v in result.items():
            print(f"  {k}: {v}")
        print()
        print("ACTION REQUIRED: restart the LocallyAI service so the new salt is")
        print("loaded by the API and sentinel processes:")
        print("  launchctl kickstart -k gui/$(id -u)/com.locallyai.server   # macOS")
        print("  Restart-Service LocallyAIServer                             # Windows")
    else:
        parser.print_help()

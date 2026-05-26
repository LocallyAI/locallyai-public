"""
kill_switch.py — out-of-band emergency stop for system updates.

The threat model: an attacker compromises the vendor's GitHub account
AND somehow obtains a valid release signature (insider threat, key
compromise). They push a malicious release through GPG-signed and
manifest-verified channels. Every other defence has failed.

This module is the last line. The vendor maintains a static JSON file
on infrastructure SEPARATE from GitHub (a different host, ideally a
different cloud provider entirely — say AWS S3 + CloudFront, or even
just GitHub Pages on a different account). Office Macs poll it before
applying any update. If `kill_switch_active: true` for a tag, or the
tag is on the blocklist, the office Mac refuses to apply.

This works because:
  - The kill-switch host is independently authenticated.
  - It's a tiny static file (cacheable, no auth complexity).
  - The vendor can update it within minutes of detecting a bad release.
  - Even if GitHub is fully compromised, the kill switch keeps working.

Configuration:
  LOCALLYAI_KILL_SWITCH_URL — the static JSON URL.
       Default placeholder: https://updates.locallyai.app/status.json
       (operator overrides for their actual deployment)
  LOCALLYAI_KILL_SWITCH_REQUIRED — if "1" (default), refuse to apply
       when the URL is unreachable. If "0", treat unreachable as "no
       kill switch active" (less secure but available).

Expected JSON shape:
  {
    "version": 1,
    "kill_switch_active": false,        // global stop — refuses ALL updates
    "blocklisted_tags": [               // pull these specific tags
      "v1.4.7-stable"
    ],
    "min_required_version": "1.0.0",    // firms below this MUST update
    "rollback_to_version": null,        // if non-null, force-revert to it
    "message": "..."                    // operator-facing notice
  }

The schema is versioned (`version: 1`) so we can extend without breaking
old office Macs.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

log = logging.getLogger("kill_switch")

KILL_SWITCH_URL = os.environ.get(
    "LOCALLYAI_KILL_SWITCH_URL",
    "https://updates.locallyai.app/status.json",
)
KILL_SWITCH_REQUIRED = os.environ.get("LOCALLYAI_KILL_SWITCH_REQUIRED", "1") == "1"
KILL_SWITCH_TIMEOUT_SEC = float(os.environ.get("LOCALLYAI_KILL_SWITCH_TIMEOUT", "5"))

# Cache the response briefly so consecutive update checks don't hammer
# the OOB host. Short enough that vendor's "STOP" reaches firms within
# minutes; long enough to avoid a flood when the manager UI polls.
_CACHE_TTL_SEC = 60
_cache: dict = {"fetched_at": 0.0, "payload": None, "error": None}


def _fetch() -> tuple[dict | None, str | None]:
    """Returns (payload, error). One of them is always None.

    Red-team finding 4.4 + 4.5: previously the payload was trusted
    solely on TLS cert chain. An attacker who compromised the CF
    account (or DNS, or CDN edge) could serve any JSON. There was no
    integrity check on the payload itself and no max-age, so a stale
    "kill_switch_active: false" payload could be replayed forever.

    Now:
      1. Fetch <KILL_SWITCH_URL> AND <KILL_SWITCH_URL>.sig.
      2. Verify the detached GPG signature against the same pinned
         release-signing key system_updates uses for release tags.
      3. Reject payloads older than max_age_seconds (default 86400 =
         24h; honour the payload's own max_age_seconds field if set,
         which lets the vendor explicitly extend during a planned
         outage).

    Failing any of these → return (None, error). With
    LOCALLYAI_KILL_SWITCH_REQUIRED=1 (default), the caller refuses
    the update — fail-closed. The vendor's release_kill_switch.sh
    script signs the payload before pushing it to the Worker."""
    now = time.time()
    if (now - _cache["fetched_at"]) < _CACHE_TTL_SEC and (_cache["payload"] or _cache["error"]):
        return _cache["payload"], _cache["error"]
    # Air-gap mode: never reach the vendor. Cached as a benign "no-op"
    # so callers in the same process don't re-evaluate every loop.
    # See config.AIR_GAP for the trade-off documentation.
    try:
        from config import AIR_GAP as _AIR_GAP
    except ImportError:
        _AIR_GAP = False
    if _AIR_GAP:
        _cache["payload"] = None
        _cache["error"] = "air-gap mode (LOCALLYAI_AIR_GAP=1)"
        _cache["fetched_at"] = now
        return None, _cache["error"]
    try:
        # 1) Fetch the payload.
        req = urllib.request.Request(KILL_SWITCH_URL, headers={
            "User-Agent": "LocallyAI/system_updates",
        })
        with urllib.request.urlopen(req, timeout=KILL_SWITCH_TIMEOUT_SEC) as resp:
            raw = resp.read()
        # 2) Fetch the detached signature.
        sig_url = KILL_SWITCH_URL + ".sig"
        sig_req = urllib.request.Request(sig_url, headers={
            "User-Agent": "LocallyAI/system_updates",
        })
        try:
            with urllib.request.urlopen(sig_req, timeout=KILL_SWITCH_TIMEOUT_SEC) as sresp:
                sig_raw = sresp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            if os.environ.get("LOCALLYAI_KILL_SWITCH_SIG_REQUIRED", "1") == "1":
                raise ValueError(f"kill-switch signature unreachable at {sig_url}: {exc}")
            sig_raw = None
        # 3) GPG-verify the signature against the payload bytes.
        # If LOCALLYAI_KILL_SWITCH_SIG_REQUIRED=0 (dev / demo), a failed
        # verification is logged but doesn't abort the fetch — useful
        # when the worker hasn't been wired through release_kill_switch.sh
        # yet. Production deployments leave the default (=1) which
        # raises and refuses the payload on any verification failure.
        if sig_raw is not None:
            ok, detail = _verify_signature(raw, sig_raw)
            if not ok:
                if os.environ.get("LOCALLYAI_KILL_SWITCH_SIG_REQUIRED", "1") == "1":
                    raise ValueError(f"kill-switch signature verification failed: {detail}")
                log.warning(f"kill-switch signature verification failed (sig not required): {detail}")
        # 4) Parse + schema-check.
        d = json.loads(raw.decode("utf-8"))
        if not isinstance(d, dict):
            raise ValueError("response is not a JSON object")
        if d.get("version") != 1:
            raise ValueError(f"unsupported kill-switch schema version: {d.get('version')}")
        # 5) Max-age check. Defaults to 24h; payload can override (up to 7d).
        issued_at = d.get("issued_at")
        if issued_at:
            import datetime as _dt
            try:
                ts = _dt.datetime.fromisoformat(issued_at.replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                raise ValueError(f"kill-switch issued_at is not ISO-8601: {issued_at!r}")
            max_age = int(d.get("max_age_seconds", 86400))
            if max_age > 7 * 86400:
                max_age = 7 * 86400  # cap to 7 days regardless of payload claim
            if (time.time() - ts) > max_age:
                raise ValueError(
                    f"kill-switch payload is stale (issued_at={issued_at}, "
                    f"max_age={max_age}s); refusing — replay attack defence."
                )
        _cache.update(fetched_at=now, payload=d, error=None)
        return d, None
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError, OSError) as exc:
        err = f"{type(exc).__name__}: {exc}"
        _cache.update(fetched_at=now, payload=None, error=err)
        log.warning("kill-switch fetch failed: %s", err)
        return None, err


def _verify_signature(payload_bytes: bytes, sig_bytes: bytes) -> tuple[bool, str]:
    """GPG-verify the detached signature against the payload. Pinned
    to the same vendor release-signing key system_updates uses."""
    import shutil as _shutil
    import subprocess as _sp
    import tempfile as _tf
    if not _shutil.which("gpg"):
        return False, "gpg not installed"
    try:
        # Resolve pinned fingerprint from system_updates so kill_switch
        # doesn't drift from release verification.
        from system_updates import _extract_pinned_fingerprint
        pinned_fp = _extract_pinned_fingerprint()
    except Exception:
        pinned_fp = os.environ.get("LOCALLYAI_RELEASE_KEY_FP", "").strip().upper().replace(" ", "") or None
    if not pinned_fp:
        return False, "no pinned release-signing fingerprint resolvable"
    with _tf.NamedTemporaryFile(suffix=".json", delete=False) as p_f, \
         _tf.NamedTemporaryFile(suffix=".sig", delete=False) as s_f:
        p_f.write(payload_bytes); p_f.flush()
        s_f.write(sig_bytes); s_f.flush()
        try:
            r = _sp.run(
                ["gpg", "--status-fd", "1", "--verify", s_f.name, p_f.name],
                capture_output=True, text=True, timeout=10,
            )
        except (_sp.TimeoutExpired, OSError) as exc:
            return False, f"gpg verify exec failed: {exc}"
        finally:
            try: os.unlink(p_f.name)
            except OSError: pass
            try: os.unlink(s_f.name)
            except OSError: pass
    if r.returncode != 0:
        return False, (r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "verify failed")[:200]
    # Parse VALIDSIG fingerprint from --status-fd output.
    sig_fp = None
    for line in (r.stdout + r.stderr).splitlines():
        if "VALIDSIG" in line:
            for tok in line.split():
                if len(tok) == 40 and all(c in "0123456789ABCDEFabcdef" for c in tok):
                    sig_fp = tok.upper()
                    break
            if sig_fp:
                break
    if not sig_fp:
        return False, "valid signature but fingerprint unparseable"
    if sig_fp != pinned_fp:
        return False, f"signature key {sig_fp[-16:]} != pinned {pinned_fp[-16:]}"
    return True, f"valid signature, pinned key {sig_fp[-16:]}"


def is_blocked(tag: str, version: str) -> tuple[bool, str]:
    """Should this tag be blocked from being applied?

    Returns (blocked, reason). Blocking reasons in priority order:
      1. Kill-switch URL unreachable AND required → block (fail-safe).
      2. Global kill_switch_active: true → block all updates.
      3. tag in blocklisted_tags → block this tag.
      4. version < min_required_version → fine for this check; the
         manager UI surfaces it separately.
    """
    payload, err = _fetch()
    if payload is None:
        if KILL_SWITCH_REQUIRED:
            return True, f"kill-switch unreachable + required ({err})"
        return False, ""  # operator opted into "fail-open"
    if payload.get("kill_switch_active"):
        msg = payload.get("message") or "global kill switch active"
        return True, f"kill switch: {msg}"
    bl = payload.get("blocklisted_tags") or []
    if isinstance(bl, list) and tag in bl:
        msg = payload.get("message") or "this tag is on the blocklist"
        return True, f"blocklisted: {msg}"
    return False, ""


def get_min_required_version() -> str | None:
    """The minimum version firms MUST update past. Returned to the
    manager UI so old deployments see a "you must update" banner."""
    payload, _ = _fetch()
    if not payload:
        return None
    v = payload.get("min_required_version")
    if isinstance(v, str) and re.match(r"^\d+\.\d+\.\d+$", v):
        return v
    return None


def status() -> dict:
    """Manager UI strip — operator can see whether the OOB channel is
    healthy."""
    payload, err = _fetch()
    return {
        "url": KILL_SWITCH_URL,
        "required": KILL_SWITCH_REQUIRED,
        "reachable": payload is not None,
        "kill_switch_active": (payload or {}).get("kill_switch_active", False),
        "blocklisted_tags": (payload or {}).get("blocklisted_tags", []),
        "min_required_version": (payload or {}).get("min_required_version"),
        "message": (payload or {}).get("message"),
        "error": err,
    }


# ── CLI for testing ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    # Load .env so the operator sees the same config the API does. The
    # module-level constants captured os.environ at import time, before
    # load_dotenv ran — re-resolve them here so the CLI matches reality.
    try:
        from pathlib import Path as _Path

        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_Path(__file__).resolve().parent / ".env", override=True)
        # Re-bind module constants from the freshly-loaded env.
        KILL_SWITCH_URL      = os.environ.get("LOCALLYAI_KILL_SWITCH_URL", KILL_SWITCH_URL)
        KILL_SWITCH_REQUIRED = os.environ.get("LOCALLYAI_KILL_SWITCH_REQUIRED", "1") == "1"
    except ImportError:
        pass  # python-dotenv missing — operator falls back to manual export
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(), indent=2))
    elif cmd == "is-blocked" and len(sys.argv) >= 4:
        b, r = is_blocked(sys.argv[2], sys.argv[3])
        print(f"BLOCKED={b}  REASON={r or '(none)'}")
    else:
        print("usage: python -m kill_switch [status | is-blocked <tag> <version>]")

"""
deploy.py — atomic in-place upgrade with health-check rollback.

The actual `git checkout vX.Y.Z-stable` machinery the system_updates
module hands off to. Kept separate from system_updates so:
  - The verification path (system_updates) has no side effects.
  - The deploy path can be triggered from a CLI for emergency manual
    apply, without going through the API.

Sequence:
  1. Verify (system_updates does this — we re-check defensively):
     - tag is on the right channel
     - GPG signature passes
     - manifest hashes pass
     - kill switch + soak window pass
  2. Snapshot CURRENT state: store the hash of HEAD as `previous_ref`
     so rollback is deterministic.
  3. `git stash` any uncommitted changes (preserves operator-side
     local mods like custom .env tweaks, then re-applied at the end).
  4. `git fetch + checkout <tag>`.
  5. `pip install -r requirements.txt --quiet` if requirements.txt
     changed between previous_ref and tag.
  6. Restart the API via launchctl kickstart.
  7. Poll /healthz for HEALTH_CHECK_TIMEOUT_SEC. If it fails to come
     back, ROLLBACK: checkout previous_ref, restart, fail loudly.
  8. Audit-log the apply (or rollback) with HMAC chain.

Idempotent? No. Each apply is a distinct event with a distinct
audit entry. Calling it twice in a row applies twice (which is a
no-op git-wise but produces two audit entries).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Optional


log = logging.getLogger("deploy")

REPO_DIR = Path(__file__).resolve().parent

# How long we give the restarted API to come back as healthy before we
# call the deploy a failure and roll back. 60 s comfortably covers a
# cold MLX-backed startup with a large model loaded; tweak via env if
# the firm runs a particularly heavy embedder.
HEALTH_CHECK_TIMEOUT_SEC = int(os.environ.get("LOCALLYAI_DEPLOY_HEALTH_TIMEOUT", "60"))
HEALTH_CHECK_INTERVAL_SEC = 2


def _git(args: list[str], **kw) -> subprocess.CompletedProcess:
    """Wrap subprocess.run with sensible defaults for git invocations
    inside REPO_DIR."""
    return subprocess.run(
        ["git", *args],
        cwd=REPO_DIR,
        capture_output=True, text=True,
        timeout=kw.pop("timeout", 60),
        **kw,
    )


def _current_ref() -> str:
    r = _git(["rev-parse", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else ""


def _restart_api() -> bool:
    """launchctl kickstart -k bounces the LaunchAgent. Returns True
    if the kickstart command succeeded (does NOT mean the app is
    healthy — that's the caller's job to verify)."""
    if not shutil.which("launchctl"):
        log.warning("launchctl not available — caller must restart manually")
        return False
    try:
        uid = os.getuid()
        r = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/app.locallyai.api"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            log.warning("launchctl kickstart non-zero: %s", r.stderr.strip())
            return False
        return True
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.error("kickstart failed: %s", exc)
        return False


def _wait_for_healthz() -> tuple[bool, str]:
    """Poll https://localhost:8000/healthz until 200 or timeout."""
    import ssl as _ssl
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE  # self-signed cert
    deadline = time.time() + HEALTH_CHECK_TIMEOUT_SEC
    last_err = "no probe attempted"
    api_port = os.environ.get("LOCALLYAI_API_PORT", "8000")
    # Try https first (TLS deployments), fall back to http (LOCALLYAI_ALLOW_HTTP=1).
    for scheme in ("https", "http"):
        url = f"{scheme}://localhost:{api_port}/healthz"
        while time.time() < deadline:
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=3, context=ctx if scheme == "https" else None) as r:
                    if r.status == 200:
                        return True, f"{scheme} healthz responding"
                    last_err = f"healthz HTTP {r.status}"
            except Exception as exc:
                last_err = str(exc)[:160]
            time.sleep(HEALTH_CHECK_INTERVAL_SEC)
        # If https never responded but http might, try http next.
    return False, last_err


def _audit(event: str, **fields):
    """Stamp an HMAC-chained entry into audit.log. Mirrors the writer
    in api.py so deploy events sit in the same chain as everything
    else."""
    try:
        from datetime import datetime, timezone
        import hashlib as _hl, hmac as _hmac
        from config import LOG_DIR, NODE_ID, DATA_REGION, current_salt_era
        log_path = LOG_DIR / "audit.log"
        chain_state = LOG_DIR / ".audit_chain"
        hmac_key = os.environ.get("LOCALLYAI_AUDIT_HMAC_KEY", "").encode()
        entry = {
            "timestamp":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "node_id":     NODE_ID,
            "data_region": DATA_REGION,
            "salt_era":    current_salt_era(),
            "event":       event,
            **fields,
            "regulation":  "ISO 27001 A.8.32 (change management) / GDPR art. 32",
        }
        if hmac_key and log_path.exists():
            prev = chain_state.read_text(encoding="utf-8").strip() if chain_state.exists() else ("0" * 64)
            entry_json = json.dumps(entry, sort_keys=True)
            chain = _hmac.new(hmac_key, (prev + entry_json).encode(), _hl.sha256).hexdigest()
            entry["_chain_hmac"] = chain
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            chain_state.write_text(chain, encoding="utf-8")
    except Exception as exc:
        log.error("audit write for %s failed: %s", event, exc)


def apply_tag(tag: str) -> dict:
    """Apply the given tag, with full verification + atomic deploy +
    rollback-on-fail. Returns a structured result the manager UI shows
    operators directly. Never raises; failures come back as
    ok=False with a `detail` field.
    """
    log.info("Apply requested for tag=%s", tag)
    result: dict = {"tag": tag, "ok": False, "detail": "", "rolled_back": False, "previous_ref": ""}

    # ── 1. Re-verify (system_updates does the heavy lifting) ────────────
    try:
        import system_updates as _su
    except Exception as exc:
        result["detail"] = f"system_updates unavailable: {exc}"
        return result

    avs = {a.tag: a for a in _su.list_available()}
    av = avs.get(tag)
    if not av:
        result["detail"] = f"tag {tag} not in available updates list — already applied or wrong channel?"
        return result
    if not av.gpg_verified:
        result["detail"] = f"GPG verification failed: {av.gpg_detail}"
        _audit("system_update_refused", tag=tag, reason=result["detail"])
        return result
    if not av.manifest_verified:
        result["detail"] = f"manifest hash mismatch: {av.manifest_detail}"
        _audit("system_update_refused", tag=tag, reason=result["detail"])
        return result
    if av.blocked_by_kill_switch:
        result["detail"] = f"blocked: {av.blocked_reason}"
        _audit("system_update_refused", tag=tag, reason=result["detail"])
        return result

    # ── 2. Snapshot ─────────────────────────────────────────────────────
    previous_ref = _current_ref()
    if not previous_ref:
        result["detail"] = "could not determine current git HEAD — refusing"
        return result
    result["previous_ref"] = previous_ref

    _audit("system_update_started", tag=tag, previous_ref=previous_ref,
           tier=av.manifest.tier, version=av.manifest.version)

    # ── 3. Stash uncommitted ────────────────────────────────────────────
    stash_pushed = False
    pre_status = _git(["status", "--porcelain"]).stdout
    if pre_status.strip():
        s = _git(["stash", "push", "-u", "-m", f"locallyai-pre-{tag}"])
        stash_pushed = s.returncode == 0
        if not stash_pushed:
            log.warning("stash failed (will deploy anyway): %s", s.stderr.strip())

    # ── 4. Checkout the tag ─────────────────────────────────────────────
    co = _git(["checkout", "-q", tag])
    if co.returncode != 0:
        result["detail"] = f"checkout failed: {co.stderr.strip()[:200]}"
        if stash_pushed: _git(["stash", "pop", "-q"])
        _audit("system_update_failed", tag=tag, stage="checkout", error=result["detail"])
        return result

    # ── 5. pip install if requirements.txt changed ──────────────────────
    diff = _git(["diff", "--name-only", previous_ref, "HEAD", "--", "requirements.txt"])
    if diff.stdout.strip():
        pip = subprocess.run(
            [str(REPO_DIR / ".venv/bin/pip"), "install", "-r", "requirements.txt", "--quiet"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=600,
        )
        if pip.returncode != 0:
            result["detail"] = f"pip install failed: {(pip.stderr or pip.stdout).strip()[:200]}"
            _rollback(previous_ref, stash_pushed, _audit_extra={"stage": "pip"})
            result["rolled_back"] = True
            return result

    # ── 6. Restart API ──────────────────────────────────────────────────
    if not _restart_api():
        result["detail"] = "API restart command failed"
        _rollback(previous_ref, stash_pushed, _audit_extra={"stage": "restart"})
        result["rolled_back"] = True
        return result

    # ── 7. Health check + auto-rollback ─────────────────────────────────
    healthy, hd = _wait_for_healthz()
    if not healthy:
        result["detail"] = f"healthz did not return 200 within {HEALTH_CHECK_TIMEOUT_SEC}s ({hd})"
        _rollback(previous_ref, stash_pushed, _audit_extra={"stage": "healthcheck", "error": hd})
        result["rolled_back"] = True
        return result

    if stash_pushed:
        _git(["stash", "pop", "-q"])

    result["ok"] = True
    result["detail"] = f"applied {tag} (tier {av.manifest.tier}, version {av.manifest.version})"
    _audit("system_update_applied", tag=tag, version=av.manifest.version,
           tier=av.manifest.tier, previous_ref=previous_ref)
    log.info("Apply succeeded: %s", tag)
    return result


def _rollback(previous_ref: str, stash_pushed: bool, _audit_extra: Optional[dict] = None):
    log.warning("Rolling back to %s", previous_ref)
    _git(["checkout", "-q", previous_ref])
    _restart_api()
    # Best-effort wait — if rollback ALSO doesn't come back, we have
    # bigger problems; the audit log will show both events.
    _wait_for_healthz()
    if stash_pushed:
        _git(["stash", "pop", "-q"])
    _audit("system_update_rolled_back", reverted_to=previous_ref,
           **(_audit_extra or {}))


# ── CLI ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if len(sys.argv) < 2:
        print("usage: python -m deploy <tag>")
        sys.exit(2)
    print(json.dumps(apply_tag(sys.argv[1]), indent=2))

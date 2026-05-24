"""Admin endpoints: users, audit-verify, processing-record, training-records,
backup-attestations, fleet, updates, models, installers.

PR-4 of the api.py → api/ refactor: extracted from api/__init__.py.

Exposes a `router = APIRouter()` that api/__init__.py mounts via
`app.include_router(router)`. Routes are mounted WITHOUT a prefix so paths
remain identical to the monolith (`/admin/users`, `/admin/fleet/nodes`, …).

The `/admin/compliance/snapshot` route is deliberately NOT here — it stays
in api/__init__.py for PR-5 along with its compliance helpers and the
sub-processor / telemetry constants. PR-4 broke the admin→compliance
handler-to-handler call by extracting `processing_record_body()` and
`audit_verify_body()` into api/_shared.py so both call sites depend on
shared bodies rather than the route handlers.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# `from api import …` requires that api/__init__.py has executed past the
# definitions we depend on (`limiter`) before it runs
# `from api.admin import router`. __init__.py defines `limiter` well before
# its `from api.admin import …` line, so this resolves cleanly.
from api import limiter
from api._shared import (
    _admin_auth,
    audit_verify_body,
    processing_record_body,
)
from config import (
    BASE_DIR,
    reload_users,
)
from config import NODE_ID as _NODE_ID
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

log = logging.getLogger("api")

router = APIRouter()


# ── Users (admin-only) ─────────────────────────────────────────────────────────
@router.post("/admin/reload-users")
def reload_users_endpoint(key: str = Depends(_admin_auth)):
    """Hot-reload users.json without restarting. Call after manage_users.py changes."""
    count = reload_users()
    return {"status": "ok", "users_loaded": count}


class _UserCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


@router.get("/admin/users")
def admin_list_users(key: str = Depends(_admin_auth)):
    """Return the list of provisioned user names. Keys are NEVER returned —
    they exist only at creation time and after rotation."""
    return {"users": _list_users()}


@router.post("/admin/users")
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


@router.delete("/admin/users/{name}")
def admin_remove_user(name: str, key: str = Depends(_admin_auth)):
    try:
        _remove_user(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    reload_users()
    return {"removed": name}


@router.post("/admin/users/{name}/rotate")
def admin_rotate_key(name: str, key: str = Depends(_admin_auth)):
    try:
        new_key = _rotate_key(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    reload_users()
    return {"name": name, "api_key": new_key, "warning": "Store this key securely. It will not be shown again."}


@router.get("/admin/processing-record")
def processing_record(key: str = Depends(_admin_auth)):
    """Records of Processing Activities (GDPR art. 30, UAE PDPL art. 21,
    KSA PDPL art. 31). Returned as JSON so a DPO can pipe it into their
    register on demand. Reflects the deployment's actual configuration —
    if BACKEND or QDRANT_URL change, the record updates automatically.

    Thin wrapper around `processing_record_body()` in api/_shared.py so the
    /admin/compliance/snapshot route (still in api/__init__.py) can call
    the same body without going through this auth'd handler."""
    return processing_record_body()


@router.get("/admin/audit-verify")
def audit_verify(key: str = Depends(_admin_auth)):
    """Verify the HMAC chain integrity of audit.log. Returns TAMPERED if the chain is broken.

    Thin wrapper around `audit_verify_body()` in api/_shared.py so the
    /admin/compliance/snapshot route (still in api/__init__.py) and the
    /admin/fleet/audit-verify aggregator can call the same body without
    going through this auth'd handler."""
    return audit_verify_body()


# ── Training records (ISO 27001 A.6.3 information-security awareness) ──────
# Light file-backed CRUD. Each record: {id, user, topic, completed_at, notes}.
# Auditors want to see that users are trained on AI-output review, GDPR
# fundamentals, incident reporting, etc. The compliance snapshot summarises;
# these endpoints let the DPO maintain the underlying records.
_TRAINING_RECORDS_FILE = BASE_DIR / "training_records.json"
_BACKUP_ATTESTATIONS_FILE = BASE_DIR / "backup_attestations.json"


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


@router.get("/admin/training-records")
def list_training_records(key: str = Depends(_admin_auth)):
    return {"records": _load_training_records()}


@router.post("/admin/training-records")
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


@router.delete("/admin/training-records/{record_id}")
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


@router.get("/admin/backup-attestations")
def list_backup_attestations(key: str = Depends(_admin_auth)):
    return {"records": _load_backup_attestations()}


@router.post("/admin/backup-attestations")
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


# ── Fleet (admin-only) ─────────────────────────────────────────────────────
@router.get("/admin/fleet/nodes")
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


@router.get("/admin/fleet/alerts")
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


@router.get("/admin/fleet/sync-conflicts")
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


@router.get("/admin/fleet/qdrant-health")
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


@router.get("/admin/fleet/gate")
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


@router.post("/admin/fleet/refresh")
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


@router.get("/admin/fleet/audit-verify")
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
        local = audit_verify_body()
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
            local = audit_verify_body()
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


@router.get("/admin/updates")
@limiter.limit("60/minute")
def admin_list_updates(request: Request, key: str = Depends(_admin_auth)):
    """Manager UI calls this to render the Updates page."""
    return {
        "channel_status":  _su_mod.status(),
        "kill_switch":     _ks_mod.status(),
        "available":       [_su_mod.to_dict(u) for u in _su_mod.list_available()],
    }


@router.post("/admin/updates/apply/{tag}")
@limiter.limit("10/minute")
def admin_apply_update(tag: str, request: Request, key: str = Depends(_admin_auth)):
    """Apply a specific tag. Re-verifies + atomic deploys + rolls back on
    health-check failure. Synchronous (returns when apply settles); the
    UI shows a spinner during the call (~30–90 s including healthz wait)."""
    import deploy as _dep
    return _dep.apply_tag(tag)


# ── LLM model picker (admin-only) ───────────────────────────────────────────
import llm_models as _llm_mod


@router.get("/admin/models")
@limiter.limit("60/minute")
def admin_list_models(request: Request, key: str = Depends(_admin_auth)):
    return {
        "current":  _llm_mod.current_model(),
        "models":   _llm_mod.list_models(),
        "download": _llm_mod.download_status(),
    }


class _ModelSelectReq(BaseModel):
    model_id: str = Field(..., min_length=1, max_length=200)


@router.post("/admin/models/select")
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


@router.get("/admin/installers")
@limiter.limit("60/minute")
def admin_list_installers(request: Request, key: str = Depends(_admin_auth)):
    return {
        "files":  _ci.list_files(),
        "status": _ci.status(),
        "refresh_in_flight": _ci.is_refresh_in_flight(),
        "rebuild_in_flight": _ci.is_rebuild_in_flight(),
    }


@router.post("/admin/installers/refresh")
@limiter.limit("10/minute")
def admin_refresh_installers(request: Request, key: str = Depends(_admin_auth)):
    """Pull the newest -clients release from GitHub. Returns immediately;
    the actual download runs in a background thread (see refresh_async)
    so the UI doesn't hang for 30+ seconds on a slow link."""
    return _ci.refresh_async()


@router.post("/admin/installers/rebuild")
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


@router.get("/admin/installers/{filename}")
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

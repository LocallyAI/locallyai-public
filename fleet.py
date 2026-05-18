"""
fleet.py

Fleet membership registry for the HA deployment. Each node writes its
own entry into SHARED_DIR/fleet.json on startup and refreshes
`last_seen` periodically via the heartbeat. Other nodes read the same
file to discover peers (for the audit-verify fan-out, the fleet
dashboard, and — later — smart-client routing fallbacks).

Schema (fleet.json):
{
  "schema_version": 1,
  "nodes": {
    "<node_id>": {
      "node_id":   "mac-studio-01",
      "hostname":  "MacStudio.local",
      "api_url":   "https://10.0.0.11:8000",
      "backend":   "mlx",
      "started_at":"2026-05-04T18:00:00Z",
      "last_seen": "2026-05-04T18:30:12Z"
    },
    ...
  }
}

Single-node deployments (SHARED_DIR == BASE_DIR, default) keep working
unchanged — they're just a 1-entry fleet that the dashboard / fan-out
endpoint short-circuit.
"""
from __future__ import annotations
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import FLEET_FILE, NODE_ID, BASE_DIR
from shared_lock import shared_lock
from platform_compat import chmod_safe

SCHEMA_VERSION = 1

# Nodes whose `last_seen` is older than this are considered offline.
# Heartbeat refreshes every 30s so 90s gives 2 missed heartbeats before
# we drop a node from the active fleet.
NODE_TTL_SECONDS = 90


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION, "nodes": {}}


def _read_unsafe() -> dict:
    """Read fleet.json without taking the lock. Caller must already hold
    it, or accept a torn read."""
    if not FLEET_FILE.exists():
        return _empty()
    try:
        data = json.loads(FLEET_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return _empty()
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return _empty()
    if not isinstance(data.get("nodes"), dict):
        data["nodes"] = {}
    return data


def _write_unsafe(data: dict) -> None:
    """Write fleet.json without taking the lock. Caller must already hold
    it. Atomic via tmp+rename."""
    FLEET_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FLEET_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(FLEET_FILE)
    chmod_safe(FLEET_FILE, 0o640)


def register(api_url: str, backend: str) -> dict:
    """Insert/refresh this node's entry. Called once at api startup and
    again from the heartbeat tick. Returns the node entry."""
    import socket
    entry = {
        "node_id":    NODE_ID,
        "hostname":   socket.gethostname(),
        "api_url":    api_url,
        "backend":    backend,
        "started_at": _now_iso(),
        "last_seen":  _now_iso(),
    }
    with shared_lock(FLEET_FILE, timeout=5.0):
        data = _read_unsafe()
        existing = data["nodes"].get(NODE_ID, {})
        # Preserve started_at across heartbeat refreshes.
        if existing.get("started_at"):
            entry["started_at"] = existing["started_at"]
        data["nodes"][NODE_ID] = entry
        _write_unsafe(data)
    return entry


def heartbeat() -> None:
    """Update only `last_seen` for this node. Cheaper than a full
    register() because it doesn't rebuild the entry."""
    with shared_lock(FLEET_FILE, timeout=5.0):
        data = _read_unsafe()
        node = data["nodes"].get(NODE_ID)
        if node is None:
            # We were never registered. Don't fabricate — let the next
            # register() call repopulate.
            return
        node["last_seen"] = _now_iso()
        _write_unsafe(data)


def deregister() -> None:
    """Remove this node from the fleet. Called on graceful shutdown so a
    deliberate reboot doesn't leave the node showing OFFLINE for 90s."""
    try:
        with shared_lock(FLEET_FILE, timeout=2.0):
            data = _read_unsafe()
            data["nodes"].pop(NODE_ID, None)
            _write_unsafe(data)
    except (TimeoutError, OSError):
        pass


def active_nodes() -> list[dict]:
    """Return entries whose last_seen is within NODE_TTL_SECONDS. Stale
    entries are NOT removed (a node that drops will reappear when it
    comes back); they're just filtered out of the active list."""
    if not FLEET_FILE.exists():
        return []
    try:
        with shared_lock(FLEET_FILE, timeout=2.0):
            data = _read_unsafe()
    except TimeoutError:
        # Lock contention: do an unsafe read. Worst case we see a partial
        # update — better than failing the dashboard / fan-out request.
        data = _read_unsafe()
    cutoff = time.time() - NODE_TTL_SECONDS
    fresh = []
    for node in data["nodes"].values():
        try:
            ts = datetime.strptime(node["last_seen"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        except (KeyError, ValueError):
            continue
        if ts >= cutoff:
            fresh.append(node)
    return fresh


def all_nodes() -> list[dict]:
    """Every node ever registered, regardless of last_seen. Used by the
    fleet dashboard so an operator can see "node-3 last seen 2h ago".
    """
    if not FLEET_FILE.exists():
        return []
    try:
        with shared_lock(FLEET_FILE, timeout=2.0):
            data = _read_unsafe()
    except TimeoutError:
        data = _read_unsafe()
    return list(data["nodes"].values())

"""
sync_conflicts.py

Syncthing conflict detection for the shared HA store.

When two nodes write the same path within the same sync window,
Syncthing renames the loser to:

    users.sync-conflict-20260504-180312-AABBCC.json
    erasure.sync-conflict-20260504-180312-DDEEFF.log

We never auto-merge — silent merging of credential or erasure data is
worse than the conflict itself. The sentinel calls scan_and_alert()
once per tick. Conflicts produce:
  - a security.log line ("sync_conflict" event with the path + winner)
  - an alert posted to the fleet dashboard
  - a tombstone moved into SHARED_DIR/conflicts/<file> so the operator
    can review without the file lingering on the live tree forever

The "winner" is whichever file currently lives at the unsuffixed path
(Syncthing's last-writer-wins). Operator decides whether to undo via
the dashboard.
"""
from __future__ import annotations
import os, json, logging, shutil
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger("sync_conflicts")

# Files we care about. Other shared files (e.g. fleet.json) are touched
# every minute by every node; conflicts there would just be noise — we
# accept Syncthing's last-write-wins for fleet.json since it's recoverable
# (heartbeat refreshes it within 60s).
_WATCHED_NAMES = ("users.json", "erasure.log")


def _conflict_pattern(name: str) -> str:
    # Syncthing's pattern: "<stem>.sync-conflict-<date>-<time>-<id><suffix>"
    stem, _, ext = name.partition(".")
    return f"{stem}.sync-conflict-"


def scan_and_alert(shared_dir: Path) -> list[dict]:
    """Return a list of conflicts found and quarantined this tick.
    Each entry: {path, original, ts, action}. Empty list when clean."""
    if not shared_dir.exists():
        return []
    quarantine = shared_dir / "conflicts"
    found: list[dict] = []
    for name in _WATCHED_NAMES:
        prefix = _conflict_pattern(name)
        for f in shared_dir.iterdir():
            if not f.is_file() or not f.name.startswith(prefix):
                continue
            try:
                quarantine.mkdir(parents=True, exist_ok=True)
                dest = quarantine / f.name
                shutil.move(str(f), str(dest))
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                found.append({
                    "timestamp": ts,
                    "event":     "sync_conflict",
                    "original":  name,
                    "conflict":  f.name,
                    "quarantined_to": str(dest.relative_to(shared_dir)),
                })
                _log.warning(
                    f"Sync conflict on {name}: quarantined {f.name} → conflicts/. "
                    f"Operator review required (data divergence between nodes).")
                # Round-2 B13: surface to the fleet dashboard so the
                # vendor doesn't depend on the firm exporting security.log.
                try:
                    import telemetry as _t
                    _t.emit_alert(
                        code="HA_SYNC_CONFLICT",
                        severity="critical",
                        message=f"Sync conflict on {name} quarantined as {f.name}",
                        auto_healed=False,
                    )
                except Exception:
                    pass
            except OSError as e:
                _log.error(f"Could not quarantine conflict {f}: {e}")
    return found


def write_security_events(events: list[dict], security_log: Path) -> None:
    """Append each conflict event to the per-node security.log so the
    breach detector + monitor surface it. Per-node so we keep the audit
    trail of who-saw-what-and-when on each box."""
    if not events:
        return
    security_log.parent.mkdir(parents=True, exist_ok=True)
    with open(security_log, "a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

"""lockout_store.py — sqlite-backed IP lockout state, cross-process safe.

Red-team finding 1.3: the previous lockout state was kept in module-level
dicts (`_failed`, `_locked`) inside `api.py`. Each uvicorn worker had its
own copy, so a flood-attacker hitting different workers bypassed the
lockout entirely. The dicts also had no lock around mutations.

This module exposes the same three operations the API needs:
  - is_locked(ip) -> bool
  - record_failure(ip) -> bool   (returns True if THIS failure triggered a lockout)
  - record_success(ip)           (clear failure history)

State lives in `logs/lockout.sqlite` (same directory as audit.log). sqlite
is WAL-mode by default for safe concurrent readers + a single writer at a
time; we use BEGIN IMMEDIATE for the read-modify-write paths so two workers
can't both decide "this is the 10th failure" simultaneously.

The schema is intentionally tiny — failure list per IP + current lockout
expiry. We don't keep historical data; the breach detector (sentinel) does
that separately by tailing security.log.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

_LOG_DIR = Path(os.environ.get("LOCALLYAI_LOG_DIR", "")) if os.environ.get("LOCALLYAI_LOG_DIR") else Path(__file__).resolve().parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH = _LOG_DIR / "lockout.sqlite"

# Configuration (mirrors the values api.py used to inline).
_LOCKOUT_MAX      = int(os.environ.get("LOCALLYAI_LOCKOUT_MAX", "10"))
_LOCKOUT_WINDOW   = int(os.environ.get("LOCALLYAI_LOCKOUT_WINDOW", "300"))
_LOCKOUT_DURATION = int(os.environ.get("LOCALLYAI_LOCKOUT_DURATION", "900"))

# Connection per thread (sqlite connections aren't thread-safe by default).
_local = threading.local()


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        c = sqlite3.connect(str(_DB_PATH), isolation_level=None, timeout=2.0)
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA synchronous=NORMAL;")
        # One row per IP. failures is a JSON array of unix-second floats;
        # locked_until is unix seconds (0 = not locked).
        c.execute("""
            CREATE TABLE IF NOT EXISTS lockout (
                ip TEXT PRIMARY KEY,
                failures TEXT NOT NULL DEFAULT '[]',
                locked_until REAL NOT NULL DEFAULT 0
            )
        """)
        _local.conn = c
    return _local.conn


def is_locked(ip: str) -> bool:
    row = _conn().execute("SELECT locked_until FROM lockout WHERE ip=?", (ip,)).fetchone()
    if not row:
        return False
    return time.time() < row[0]


def record_failure(ip: str) -> bool:
    """Record a failed auth attempt for `ip`. Returns True if THIS failure
    pushed the IP into a fresh lockout window (caller logs that)."""
    import json
    now = time.time()
    c = _conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        row = c.execute("SELECT failures, locked_until FROM lockout WHERE ip=?", (ip,)).fetchone()
        if row:
            try:
                failures = json.loads(row[0])
            except Exception:
                failures = []
            locked_until = row[1]
        else:
            failures = []
            locked_until = 0
        # Drop failures older than the rolling window.
        failures = [t for t in failures if now - t < _LOCKOUT_WINDOW]
        failures.append(now)
        triggered_lockout = False
        if len(failures) >= _LOCKOUT_MAX and now >= locked_until:
            locked_until = now + _LOCKOUT_DURATION
            triggered_lockout = True
        c.execute(
            "INSERT INTO lockout(ip, failures, locked_until) VALUES(?,?,?) "
            "ON CONFLICT(ip) DO UPDATE SET failures=excluded.failures, locked_until=excluded.locked_until",
            (ip, json.dumps(failures), locked_until),
        )
        c.execute("COMMIT")
        return triggered_lockout
    except Exception:
        c.execute("ROLLBACK")
        raise


def record_success(ip: str) -> None:
    """Successful auth clears the IP's failure history + any active lockout."""
    _conn().execute("DELETE FROM lockout WHERE ip=?", (ip,))


def reset() -> None:
    """Test-only: wipe the table. Production code never calls this."""
    _conn().execute("DELETE FROM lockout")

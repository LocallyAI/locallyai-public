"""
shared_lock.py

Cross-platform file lock for coordinating writes to files on shared
storage (Syncthing-managed dir today, NFS later). Prevents two nodes
from concurrently mutating fleet.json, users.json, erasure.log, etc.

Semantics:
    with shared_lock(path, timeout=5.0):
        ... read/modify/write path ...

POSIX uses fcntl.flock (LOCK_EX). Windows uses msvcrt.locking on a
sidecar `.lock` file (we can't lock a file we're about to truncate).
Timeout polls every 50ms; raises TimeoutError on expiry so callers
can fail fast rather than hang during NAS controller failover.

Lock is advisory — every writer must use this helper. Single-node
deployments still benefit (sentinel rotation vs subprocess writes).
"""
from __future__ import annotations
import os, time, errno
from contextlib import contextmanager
from pathlib import Path

_IS_WINDOWS = os.name == "nt"


@contextmanager
def shared_lock(path: str | Path, timeout: float = 5.0):
    """Acquire an exclusive advisory lock on `path`. Creates the file if
    missing. Releases on exit. Raises TimeoutError if not acquired in
    `timeout` seconds — callers should treat this as a transient
    storage failure and either retry or surface to the operator."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if _IS_WINDOWS:
        yield from _win_lock(path, timeout)
    else:
        yield from _posix_lock(path, timeout)


def _posix_lock(path: Path, timeout: float):
    import fcntl
    # Open in append mode so we don't truncate; create if missing.
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o640)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"shared_lock({path}) timed out after {timeout:.1f}s")
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(fd)


def _win_lock(path: Path, timeout: float):
    # Use a sidecar .lock file so we can lock a fixed byte without
    # interfering with reads/writes of the real file.
    import msvcrt
    sidecar = path.with_suffix(path.suffix + ".lock")
    fd = os.open(str(sidecar), os.O_RDWR | os.O_CREAT, 0o640)
    deadline = time.monotonic() + timeout
    locked = False
    try:
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"shared_lock({path}) timed out after {timeout:.1f}s")
                time.sleep(0.05)
        try:
            yield
        finally:
            if locked:
                try:
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
    finally:
        os.close(fd)

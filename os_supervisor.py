"""
os_supervisor.py

Cross-platform helpers for supervisor.py. The supervisor uses two
OS-specific operations: enumerate the PIDs listening on a TCP port, and
identify the command line of an arbitrary PID (so we can refuse to kill
non-Python listeners like AirPlay or a foreign dev server). Both have
trivial POSIX implementations (lsof + ps) and equally trivial Windows
implementations (netstat + tasklist).

Signal availability also varies — POSIX has SIGHUP, Windows doesn't —
so we expose a single `install_shutdown_handlers` helper that wires up
whichever signals exist on this platform plus, on Windows, a console
control handler so Ctrl+C / service-stop messages still drain cleanly.
"""
from __future__ import annotations
import os, signal, subprocess, sys
from typing import Callable

IS_WINDOWS = os.name == "nt"


def find_listener_pids(port: int, timeout: float = 5.0) -> list[int]:
    """Return PIDs of processes listening on TCP `port`. Empty list when
    nothing answers. Best-effort — if the OS tool is missing or hangs,
    we return [] and the caller assumes the port is free (which is what
    the prior single-platform code did)."""
    if IS_WINDOWS:
        return _find_listener_pids_windows(port, timeout)
    return _find_listener_pids_posix(port, timeout)


def cmdline_of(pid: int, timeout: float = 2.0) -> str:
    """Return a lower-cased command-line string for `pid`, or "" if the
    OS tool fails. The caller uses this to decide whether the listener
    is a Python/uvicorn (safe to kill) or a foreign holder (refuse and
    instruct the operator)."""
    if IS_WINDOWS:
        return _cmdline_windows(pid, timeout)
    return _cmdline_posix(pid, timeout)


def install_shutdown_handlers(handler: Callable[[int, object], None]) -> None:
    """Wire SIGTERM/SIGINT (and SIGHUP on POSIX) so launchctl stop, sc
    stop, and Ctrl+C all drain children. On Windows we also install a
    console control handler so service-stop messages reach us."""
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, handler)
    if IS_WINDOWS:
        try:
            import win32api  # type: ignore[import-not-found]
            # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT all map
            # through win32api so the handler fires on service-stop too.
            win32api.SetConsoleCtrlHandler(lambda evt: bool(handler(int(evt), None)) or True, True)
        except Exception:
            # pywin32 is optional; without it Ctrl+C still works via
            # signal.SIGINT. The service stop path uses TerminateProcess
            # which can't be intercepted anyway.
            pass


# ── POSIX ────────────────────────────────────────────────────────────────────

def _find_listener_pids_posix(port: int, timeout: float) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return [int(p) for p in result.stdout.split() if p.strip().isdigit()]


def _cmdline_posix(pid: int, timeout: float) -> str:
    try:
        return subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=timeout,
        ).stdout.strip().lower()
    except Exception:
        return ""


# ── Windows ──────────────────────────────────────────────────────────────────

def _find_listener_pids_windows(port: int, timeout: float) -> list[int]:
    """`netstat -ano` lists every TCP listener with its owning PID.
    Match the LISTENING state and the local-address port suffix; return
    every PID in column 5."""
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    pids: set[int] = set()
    suffix = f":{port}"
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        # Columns: Proto, LocalAddress, ForeignAddress, State, PID
        if parts[0].upper() != "TCP" or parts[3].upper() != "LISTENING":
            continue
        if not parts[1].endswith(suffix):
            continue
        try:
            pids.add(int(parts[4]))
        except ValueError:
            continue
    return list(pids)


def _cmdline_windows(pid: int, timeout: float) -> str:
    """tasklist /FI "PID eq N" /FO CSV /NH gives Image-Name,PID,Session,SessionN,Mem.
    We only need the image name to decide python/uvicorn vs foreign."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    line = result.stdout.strip()
    if not line or "INFO:" in line:
        return ""
    # CSV first cell, dequoted.
    first = line.split(",", 1)[0].strip().strip('"').lower()
    return first


# CREATE_NO_WINDOW prevents a console flash when subprocess fires a child
# from a service context. Defined as 0 on POSIX so the constant is safe to
# pass unconditionally (subprocess.run ignores creationflags off-Windows).
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

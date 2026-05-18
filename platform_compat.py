"""
platform_compat.py

Tiny cross-platform compat layer. The single-Mac codebase used POSIX
permissions everywhere; on Windows those calls are no-ops at best and
crash at worst. This module gives every other module one place to ask
"am I on Windows?" and one helper that does the right thing for file
ACLs without each caller having to care.

The Windows ACL story:
  os.chmod on Windows only toggles the read-only bit. To restrict a
  file to the owning user (the moral equivalent of chmod 600), we shell
  out to icacls — Windows' built-in ACL editor — to grant the current
  user full control and remove inherited permissions. This matches what
  install.sh achieves on macOS via mode 0o600 + FileVault.
"""
from __future__ import annotations
import os, subprocess, getpass, logging
from pathlib import Path

_log = logging.getLogger("platform_compat")

IS_WINDOWS = os.name == "nt"
IS_POSIX   = not IS_WINDOWS


def chmod_safe(path: str | Path, mode: int) -> None:
    """Apply POSIX-style mode bits where supported. On Windows, falls
    through to acl_restrict for sensitive modes (0o600/0o640/0o700);
    no-op for less sensitive modes. Never raises — best-effort, since
    permission tightening should not fail a write.
    """
    p = Path(path)
    if not p.exists():
        return
    if IS_POSIX:
        try:
            os.chmod(p, mode)
        except OSError as e:
            _log.warning(f"chmod {p} 0o{mode:o} failed: {e}")
        return
    # Windows path
    if mode in (0o600, 0o640, 0o700):
        acl_restrict(p)


def acl_restrict(path: str | Path) -> None:
    """Windows-only: restrict ACLs on `path` to the current user only.
    Uses icacls (built into Windows since Vista). Best-effort; logs a
    warning on failure rather than raising."""
    if not IS_WINDOWS:
        return
    p = Path(path)
    if not p.exists():
        return
    try:
        user = getpass.getuser()
        # /inheritance:r removes inherited ACEs; /grant gives full control to current user only.
        subprocess.run(
            ["icacls", str(p), "/inheritance:r", "/grant", f"{user}:F"],
            check=False, capture_output=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as e:
        _log.warning(f"icacls restrict {p} failed: {e}")


def normalise_path(path: str) -> str:
    """Normalise slashes and case for cross-platform comparisons. On
    Windows, paths are case-insensitive and may use either separator."""
    p = os.path.normpath(path)
    if IS_WINDOWS:
        p = p.lower()
    return p

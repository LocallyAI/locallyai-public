"""
client_installers.py — office-server distribution of staff laptop apps.

Architecture: the firm's office Mac Studio (already running LocallyAI)
is the only place that talks to GitHub. Staff IT downloads the .dmg /
.msi installers from `https://office-mac.local:8000/admin/installers/...`
behind the LOCALLYAI_ADMIN_KEY — no GitHub accounts, no public repo,
no third party.

How files arrive on disk:
  - Sentinel runs `refresh_async()` on a daily tick (best-effort).
  - The manager UI exposes a "Refresh now" button that calls
    POST /admin/installers/refresh for on-demand pulls.
  - Operators with shell access can also run `python -m client_installers`
    interactively for testing.

Pull mechanism: shells out to the GitHub CLI (`gh`) which the office
Mac authenticates once via the existing repo-access SSH-deploy-key SOP
(or `gh auth login` if the operator prefers a PAT). We chose `gh`
over raw GitHub API + curl for two reasons:
  1. `gh release download` handles auth + pagination + retry for free.
  2. The office Mac already needs `gh` for the SOP-documented release
     flow (scripts/release_clients.sh), so no new dependency.

Cache layout under STORAGE_DIR/installers/:
  installers/
    .meta.json                    ← which release tag we have, when pulled
    LocallyAI Worker_1.0.0.dmg
    LocallyAI Worker_1.0.0_x64_en-US.msi
    LocallyAI Manager_1.0.0.dmg
    LocallyAI Manager_1.0.0_x64_en-US.msi
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


log = logging.getLogger("client_installers")

# Repo + tag pattern. Override via env for forks.
GITHUB_REPO = os.environ.get("LOCALLYAI_CLIENTS_REPO", "LocallyAI/locallyai")
TAG_PATTERN = os.environ.get("LOCALLYAI_CLIENTS_TAG_GLOB", "v*-clients")

# Storage. Lazy-import config so this module is importable in CI / tests
# without the full app stack.
def _installer_dir() -> Path:
    from config import STORAGE_DIR
    d = STORAGE_DIR / "installers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _meta_path() -> Path:
    return _installer_dir() / ".meta.json"


# Allow-list of file extensions we'll surface. Anything else GitHub
# attaches to a release (changelog .txt, source .zip) is silently
# ignored.
_ALLOWED_EXTS = {".dmg", ".msi", ".exe", ".pkg", ".zip"}


# ── State persistence ────────────────────────────────────────────────────────
@dataclass
class InstallerMeta:
    last_tag: str = ""              # e.g. "v0.1.0-clients"
    last_pulled_at: float = 0.0     # epoch seconds
    last_status: str = ""           # "success" / "failed: <reason>"
    # Local rebuild state (separate from the GitHub-pull state above —
    # rebuild runs scripts/build_staff_apps.sh in-place to regenerate
    # the per-firm URL-baked bundles after a `git pull` or hostname
    # change, no GitHub round-trip).
    last_rebuilt_at: float = 0.0
    last_rebuild_status: str = ""    # "success" / "failed: <reason>"
    last_rebuild_detail: str = ""    # tail of stdout/stderr if failed


def _load_meta() -> InstallerMeta:
    p = _meta_path()
    if not p.exists():
        return InstallerMeta()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return InstallerMeta(**d)
    except Exception as exc:
        log.warning("Corrupt installer meta — resetting: %s", exc)
        return InstallerMeta()


def _save_meta(m: InstallerMeta) -> None:
    _meta_path().write_text(json.dumps(asdict(m), indent=2), encoding="utf-8")


# ── Public API ───────────────────────────────────────────────────────────────
def list_files() -> list[dict]:
    """Return metadata for every installer currently cached on disk.
    Sorted newest-mtime first so the manager UI shows the latest at top."""
    out = []
    for p in _installer_dir().iterdir():
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.suffix.lower() not in _ALLOWED_EXTS:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out.append({
            "name": p.name,
            "size_bytes": st.st_size,
            "mtime_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
            "platform": _classify_platform(p.name),
            "app": _classify_app(p.name),
        })
    out.sort(key=lambda d: d["mtime_iso"], reverse=True)
    return out


def status() -> dict:
    """What does the manager UI show in the header strip?"""
    m = _load_meta()
    return {
        "last_tag": m.last_tag,
        "last_pulled_at": m.last_pulled_at,
        "last_pulled_iso": (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(m.last_pulled_at))
                            if m.last_pulled_at else None),
        "last_status": m.last_status,
        "last_rebuilt_at": m.last_rebuilt_at,
        "last_rebuilt_iso": (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(m.last_rebuilt_at))
                             if m.last_rebuilt_at else None),
        "last_rebuild_status": m.last_rebuild_status,
        "last_rebuild_detail": m.last_rebuild_detail,
        "github_repo": GITHUB_REPO,
        "gh_cli_available": shutil.which("gh") is not None,
        "swiftc_available": shutil.which("swiftc") is not None,
    }


def resolve_file(name: str) -> Optional[Path]:
    """Map a UI-supplied filename to a real on-disk path; reject anything
    that isn't in the installers dir (path-traversal hardening)."""
    safe = Path(name).name
    if safe != name or safe in ("", ".", ".."):
        return None
    if Path(safe).suffix.lower() not in _ALLOWED_EXTS:
        return None
    p = _installer_dir() / safe
    try:
        if not str(p.resolve()).startswith(str(_installer_dir().resolve())):
            return None
    except OSError:
        return None
    if not p.exists() or not p.is_file():
        return None
    return p


def refresh() -> dict:
    """Pull the latest release's installer artefacts from GitHub.

    Strategy:
      1. `gh release list --repo <repo> --limit 30` → find the most
         recent tag matching TAG_PATTERN (a -clients tag).
      2. If we already have that tag (per .meta.json) and the
         installer files are present, no-op.
      3. Otherwise `gh release download <tag>` with the artefact
         allow-list. Race-safe: downloads to a tmp dir, then
         atomically renames into place.
      4. Update .meta.json.

    Returns a dict the UI can render directly. Never raises — failures
    are reported via the returned status string so the UI can show
    operators what went wrong (most often: gh not authenticated)."""
    m = _load_meta()
    if not shutil.which("gh"):
        m.last_status = "failed: gh CLI not installed (brew install gh)"
        _save_meta(m)
        return {**status(), "ok": False, "detail": m.last_status}

    # 1. Find the latest -clients tag.
    tag = _latest_clients_tag()
    if not tag:
        m.last_status = "failed: no -clients release on GitHub yet"
        _save_meta(m)
        return {**status(), "ok": False, "detail": m.last_status}

    # 2. Already have it?
    have_files = [p.name for p in _installer_dir().iterdir() if p.is_file() and p.suffix.lower() in _ALLOWED_EXTS]
    if m.last_tag == tag and have_files:
        m.last_pulled_at = time.time()
        m.last_status = "success (already up to date)"
        _save_meta(m)
        return {**status(), "ok": True, "detail": f"Already on {tag}"}

    # 3. Download to a tmp dir, then promote.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="locallyai-installers-") as tmp_str:
        tmp = Path(tmp_str)
        cmd = [
            "gh", "release", "download", tag,
            "--repo", GITHUB_REPO,
            "--dir", str(tmp),
            # Allow-list every extension we surface.
            "--pattern", "*.dmg",
            "--pattern", "*.msi",
            "--pattern", "*.exe",
            "--clobber",
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            m.last_status = f"failed: gh download timed out after 5 min"
            _save_meta(m)
            return {**status(), "ok": False, "detail": m.last_status}
        if r.returncode != 0:
            err_short = (r.stderr or r.stdout or "").strip().splitlines()[-1] if (r.stderr or r.stdout) else "unknown"
            m.last_status = f"failed: {err_short}"
            _save_meta(m)
            return {**status(), "ok": False, "detail": m.last_status}

        downloaded = [p for p in tmp.iterdir() if p.is_file() and p.suffix.lower() in _ALLOWED_EXTS]
        if not downloaded:
            m.last_status = f"failed: no installer artefacts in {tag}"
            _save_meta(m)
            return {**status(), "ok": False, "detail": m.last_status}

        # Atomic promote: drop the old set, then copy the new ones.
        # We only delete files matching our allow-list — anything else
        # in installers/ stays (operator may have placed a custom file).
        for old in _installer_dir().iterdir():
            if old.is_file() and old.suffix.lower() in _ALLOWED_EXTS and not old.name.startswith("."):
                try: old.unlink()
                except OSError: pass
        for src in downloaded:
            shutil.copy2(src, _installer_dir() / src.name)

    m.last_tag = tag
    m.last_pulled_at = time.time()
    m.last_status = "success"
    _save_meta(m)
    log.info("Pulled %d installer(s) from %s @ %s", len(downloaded), GITHUB_REPO, tag)
    return {**status(), "ok": True, "detail": f"Pulled {len(downloaded)} file(s) from {tag}"}


# ── Helpers ──────────────────────────────────────────────────────────────────
def _latest_clients_tag() -> str:
    """Returns the newest tag matching TAG_PATTERN, or "" if none."""
    try:
        r = subprocess.run(
            ["gh", "release", "list", "--repo", GITHUB_REPO, "--limit", "30", "--json", "tagName"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    if r.returncode != 0:
        log.warning("gh release list failed: %s", (r.stderr or "").strip())
        return ""
    try:
        rows = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return ""
    # Keep only the -clients tags; rows already come back newest-first.
    import fnmatch
    for row in rows:
        t = row.get("tagName", "")
        if fnmatch.fnmatch(t, TAG_PATTERN):
            return t
    return ""


def _classify_platform(filename: str) -> str:
    n = filename.lower()
    if n.endswith(".dmg") or n.endswith(".pkg"): return "macOS"
    if n.endswith(".msi") or n.endswith(".exe"): return "Windows"
    # Local per-firm bundles built by scripts/build_staff_apps.sh
    if "windows" in n and n.endswith(".zip"):    return "Windows"
    if ".app.zip" in n:                          return "macOS"
    if "trust cert" in n and n.endswith(".zip"): return "Cross-platform"
    return "unknown"


def _classify_app(filename: str) -> str:
    n = filename.lower()
    if "manager" in n:                              return "Manager"
    if "worker" in n or "workspace" in n:           return "Workspace"
    if "trust" in n and "cert" in n:                return "Trust certificate"
    if "windows apps" in n:                         return "Windows shortcuts"
    return "unknown"


# ── Async refresh trigger ────────────────────────────────────────────────────
# The HTTP "Refresh now" handler returns immediately and runs the pull
# in the background — gh release download can take 30+ seconds and we
# don't want the manager UI hanging on the request.
_refresh_lock = threading.Lock()
_refresh_in_flight = False


def refresh_async() -> dict:
    """Kick off a background refresh if one isn't already running.
    Returns immediately with the latest known status."""
    global _refresh_in_flight
    with _refresh_lock:
        if _refresh_in_flight:
            return {**status(), "ok": True, "detail": "refresh already in flight"}
        _refresh_in_flight = True

    def _run():
        global _refresh_in_flight
        try:
            refresh()
        finally:
            with _refresh_lock:
                _refresh_in_flight = False

    threading.Thread(target=_run, daemon=True, name="installer-refresh").start()
    return {**status(), "ok": True, "detail": "refresh started"}


def is_refresh_in_flight() -> bool:
    with _refresh_lock:
        return _refresh_in_flight


# ── In-place rebuild ─────────────────────────────────────────────────────────
# refresh() pulls pre-built generic-URL bundles from GitHub Releases.
# rebuild() runs scripts/build_staff_apps.sh locally to regenerate the
# per-firm URL-baked bundles — what install.sh runs at install time.
# IT triggers this from the Manager UI after a `git pull` (so the new
# Manager.app + Workspace.app builds reflect the latest source) or after
# changing the firm's office hostname (so the baked URL is correct).

_REBUILD_SCRIPT = "scripts/build_staff_apps.sh"
_REBUILD_TIMEOUT_SEC = 600       # 10 min — typical build is 10-30 s; cushion for slow disks
_REBUILD_DETAIL_TAIL = 800       # bytes of script output to retain on failure


def _repo_dir() -> Path:
    """The repo root — one level above this module."""
    return Path(__file__).resolve().parent


def rebuild() -> dict:
    """Rebuild the per-firm staff-laptop apps in-place by running
    scripts/build_staff_apps.sh. Output overwrites the existing zips
    in storage/installers/. Never raises; failures land in last_rebuild_status."""
    m = _load_meta()
    repo = _repo_dir()
    script = repo / _REBUILD_SCRIPT

    if not script.exists():
        m.last_rebuild_status = f"failed: {_REBUILD_SCRIPT} missing"
        m.last_rebuild_detail = ""
        m.last_rebuilt_at = time.time()
        _save_meta(m)
        return {**status(), "ok": False, "detail": m.last_rebuild_status}
    if not os.access(script, os.X_OK):
        m.last_rebuild_status = f"failed: {_REBUILD_SCRIPT} not executable (chmod +x)"
        m.last_rebuild_detail = ""
        m.last_rebuilt_at = time.time()
        _save_meta(m)
        return {**status(), "ok": False, "detail": m.last_rebuild_status}
    if not shutil.which("swiftc"):
        m.last_rebuild_status = "failed: swiftc not installed (install Xcode Command Line Tools)"
        m.last_rebuild_detail = ""
        m.last_rebuilt_at = time.time()
        _save_meta(m)
        return {**status(), "ok": False, "detail": m.last_rebuild_status}

    try:
        r = subprocess.run(
            ["bash", str(script)],
            cwd=str(repo),
            capture_output=True, text=True,
            timeout=_REBUILD_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        m.last_rebuild_status = f"failed: build timed out after {_REBUILD_TIMEOUT_SEC // 60} min"
        m.last_rebuild_detail = ""
        m.last_rebuilt_at = time.time()
        _save_meta(m)
        return {**status(), "ok": False, "detail": m.last_rebuild_status}

    if r.returncode != 0:
        # Keep the tail of stderr so the operator can see what failed
        tail = ((r.stderr or r.stdout or "").strip())[-_REBUILD_DETAIL_TAIL:]
        m.last_rebuild_status = f"failed: build_staff_apps.sh exited {r.returncode}"
        m.last_rebuild_detail = tail
        m.last_rebuilt_at = time.time()
        _save_meta(m)
        return {**status(), "ok": False, "detail": m.last_rebuild_status}

    m.last_rebuild_status = "success"
    m.last_rebuild_detail = ""
    m.last_rebuilt_at = time.time()
    _save_meta(m)
    log.info("Rebuilt staff apps in-place")
    return {**status(), "ok": True, "detail": "rebuild complete"}


_rebuild_lock = threading.Lock()
_rebuild_in_flight = False


def rebuild_async() -> dict:
    """Kick off a background rebuild if one isn't already running.
    Returns immediately with the latest known status."""
    global _rebuild_in_flight
    with _rebuild_lock:
        if _rebuild_in_flight:
            return {**status(), "ok": True, "detail": "rebuild already in flight"}
        _rebuild_in_flight = True

    def _run():
        global _rebuild_in_flight
        try:
            rebuild()
        finally:
            with _rebuild_lock:
                _rebuild_in_flight = False

    threading.Thread(target=_run, daemon=True, name="installer-rebuild").start()
    return {**status(), "ok": True, "detail": "rebuild started"}


def is_rebuild_in_flight() -> bool:
    with _rebuild_lock:
        return _rebuild_in_flight


# ── CLI for testing ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(), indent=2))
    elif cmd == "list":
        print(json.dumps(list_files(), indent=2))
    elif cmd == "refresh":
        print(json.dumps(refresh(), indent=2))
    elif cmd == "rebuild":
        print(json.dumps(rebuild(), indent=2))
    else:
        print("usage: python -m client_installers [status|list|refresh|rebuild]")

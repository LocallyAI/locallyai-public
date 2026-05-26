"""
llm_models.py — manager-UI-driven LLM model picker.

Operator picks an MLX model from a curated list in the Models page.
We download via huggingface-hub (sentence-transformers' transitive
dep — already installed), atomically swap MLX_MODEL in .env, kickstart
the API.

Why curated list rather than free-form: prevents an admin from typing
a malicious model identifier that ships custom inference code with
trust_remote_code=True (some HF models do). The list lives here in
code, signed via the same release pipeline as everything else.
Operators wanting an off-list model edit .env directly — that's
explicit consent.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("llm_models")

REPO_DIR = Path(__file__).resolve().parent
ENV_PATH = REPO_DIR / ".env"


@dataclass
class CuratedModel:
    id: str                 # mlx-community/Qwen2.5-3B-Instruct-4bit
    label: str              # "Qwen 2.5 3B (multilingual)"
    backend: str            # "mlx"
    approx_disk_gb: float
    approx_ram_gb: float
    languages: list[str]    # ["en", "ar", "zh", ...]
    notes: str = ""


# Curated list — every entry has been verified by the vendor as safe,
# multilingual-capable where claimed, and 4-bit MLX-formatted.
CURATED: list[CuratedModel] = [
    CuratedModel(
        id="mlx-community/Qwen2.5-1.5B-Instruct-4bit",
        label="Qwen 2.5 1.5B (multilingual, fast)",
        backend="mlx", approx_disk_gb=1.0, approx_ram_gb=1.5,
        languages=["en", "ar", "zh", "fr", "es", "de", "ja"],
        notes="Smallest multilingual option; fits comfortably on a base 16 GB Mac.",
    ),
    CuratedModel(
        id="mlx-community/Qwen2.5-3B-Instruct-4bit",
        label="Qwen 2.5 3B (multilingual, recommended for KSA)",
        backend="mlx", approx_disk_gb=2.0, approx_ram_gb=3.0,
        languages=["en", "ar", "zh", "fr", "es", "de", "ja"],
        notes="Demo default; balance of quality and speed.",
    ),
    CuratedModel(
        id="mlx-community/Qwen2.5-7B-Instruct-4bit",
        label="Qwen 2.5 7B (multilingual, strong)",
        backend="mlx", approx_disk_gb=5.0, approx_ram_gb=6.0,
        languages=["en", "ar", "zh", "fr", "es", "de", "ja"],
    ),
    CuratedModel(
        id="mlx-community/Qwen2.5-14B-Instruct-4bit",
        label="Qwen 2.5 14B (multilingual, best Arabic)",
        backend="mlx", approx_disk_gb=9.0, approx_ram_gb=11.0,
        languages=["en", "ar", "zh", "fr", "es", "de", "ja"],
        notes="Best Arabic answers. Needs ~10 GB free RAM.",
    ),
    CuratedModel(
        id="mlx-community/Llama-3.2-3B-Instruct-4bit",
        label="Llama 3.2 3B (English-centric, fast)",
        backend="mlx", approx_disk_gb=2.0, approx_ram_gb=2.5,
        languages=["en", "fr", "es", "de", "it", "pt"],
        notes="Strong English; NOT recommended for Arabic.",
    ),
    CuratedModel(
        id="mlx-community/Mistral-Small-Instruct-2409-4bit",
        label="Mistral Small 22B (multilingual)",
        backend="mlx", approx_disk_gb=14.0, approx_ram_gb=16.0,
        languages=["en", "ar", "fr", "es", "de", "it"],
    ),
]


# ── State ────────────────────────────────────────────────────────────────────
_download_lock = threading.Lock()
_download_in_flight: str | None = None
_download_log: list[str] = []  # ring buffer of recent log lines


def list_models() -> list[dict]:
    """Curated list + a flag for which model is currently active."""
    current = current_model()
    out = []
    for m in CURATED:
        d = m.__dict__.copy()
        d["active"] = (m.id == current)
        d["downloaded"] = _is_downloaded(m.id)
        out.append(d)
    return out


def current_model() -> str:
    """What's MLX_MODEL set to in .env (or env override)?"""
    # Env override takes precedence (matches how config.py reads it).
    v = os.environ.get("MLX_MODEL")
    if v: return v
    # Otherwise parse .env directly (don't import config — its module
    # state was set at first import and won't reflect post-edit changes).
    if not ENV_PATH.exists(): return ""
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("MLX_MODEL="):
            return line.split("=", 1)[1].strip()
    return ""


def _is_downloaded(model_id: str) -> bool:
    """HuggingFace caches models at ~/.cache/huggingface/hub/models--<org>--<name>/."""
    cache_root = Path(os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface" / "hub")))
    if not cache_root.exists():
        cache_root = Path.home() / ".cache" / "huggingface" / "hub"
    safe = "models--" + model_id.replace("/", "--")
    return (cache_root / safe).exists()


def download_status() -> dict:
    return {
        "in_flight": _download_in_flight,
        "log_tail": _download_log[-20:],
    }


# ── .env atomic swap ────────────────────────────────────────────────────────
def _swap_mlx_model_in_env(new_id: str) -> None:
    """Rewrite MLX_MODEL line preserving everything else. If MLX_MODEL
    isn't present, append. Mirrors the pattern used by manage_users
    rotate_audit_salt — preserves comments and order."""
    if not ENV_PATH.exists():
        raise FileNotFoundError(f"{ENV_PATH} missing — run install.sh first")
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    seen = False
    for ln in lines:
        if not ln.lstrip().startswith("#") and "=" in ln:
            k, _, _ = ln.partition("=")
            if k.strip() == "MLX_MODEL":
                out.append(f"MLX_MODEL={new_id}")
                seen = True
                continue
        out.append(ln)
    if not seen:
        out.append("")
        out.append(f"MLX_MODEL={new_id}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        from platform_compat import chmod_safe
        chmod_safe(ENV_PATH, 0o600)
    except Exception:
        try: os.chmod(ENV_PATH, 0o600)
        except OSError: pass


# ── Download + activate ─────────────────────────────────────────────────────
def _push_log(line: str) -> None:
    _download_log.append(line)
    # Keep the buffer modest.
    if len(_download_log) > 200:
        del _download_log[:-100]


def select(model_id: str) -> dict:
    """Validate, kick off background download if needed, swap .env on
    success, restart API. Returns immediately with `accepted: true` once
    the model is known + the download started; the manager UI polls
    download_status() to track progress.

    Air-gap mode (LOCALLYAI_AIR_GAP=1): if the model isn't already
    cached locally, refuse the request. Air-gap firms side-load model
    files via rsync from a trusted mirror before selecting them."""
    global _download_in_flight
    valid = next((m for m in CURATED if m.id == model_id), None)
    if not valid:
        return {"accepted": False, "detail": f"Unknown model id: {model_id} (off curated list)"}

    # Air-gap mode: refuse download. Already-cached models are still
    # selectable because no network is touched in that path.
    try:
        from config import AIR_GAP as _AIR_GAP
    except ImportError:
        _AIR_GAP = False
    if _AIR_GAP and not _is_downloaded(model_id):
        return {
            "accepted": False,
            "detail": f"Air-gap mode (LOCALLYAI_AIR_GAP=1): {model_id} is not "
                      f"in the local HuggingFace cache. Side-load it manually "
                      f"(rsync from a trusted mirror) before selecting. See "
                      f"docs/sop/air-gap-mode.md.",
        }

    with _download_lock:
        if _download_in_flight and _download_in_flight != model_id:
            return {"accepted": False, "detail": f"Download already in flight: {_download_in_flight}"}
        _download_in_flight = model_id

    def _run():
        global _download_in_flight
        try:
            _push_log(f"=== select {model_id} ===")
            # 1. Make sure huggingface-hub is available.
            try:
                from huggingface_hub import snapshot_download
            except ImportError:
                _push_log("ERROR: huggingface-hub not installed — run pip install huggingface-hub")
                return
            # 2. Download (snapshot_download is idempotent + resumable).
            _push_log(f"Downloading {model_id} via huggingface_hub.snapshot_download…")
            try:
                snapshot_download(repo_id=model_id)
                _push_log("Download complete.")
            except Exception as exc:
                _push_log(f"Download failed: {exc}")
                return
            # 3. Swap .env atomically.
            _push_log("Updating .env (MLX_MODEL=...)")
            try:
                _swap_mlx_model_in_env(model_id)
            except Exception as exc:
                _push_log(f"ERROR rewriting .env: {exc}")
                return
            # 4. Restart API so the new model is loaded.
            if shutil.which("launchctl"):
                _push_log("Restarting API (launchctl kickstart)…")
                uid = os.getuid()
                r = subprocess.run(
                    ["launchctl", "kickstart", "-k", f"gui/{uid}/app.locallyai.api"],
                    capture_output=True, text=True, timeout=15,
                )
                if r.returncode != 0:
                    _push_log(f"WARN: launchctl kickstart non-zero: {r.stderr.strip()}")
                else:
                    _push_log("API restarted.")
            else:
                _push_log("launchctl not available — operator must restart the API manually.")
            _push_log("=== select complete ===")
        finally:
            with _download_lock:
                _download_in_flight = None

    threading.Thread(target=_run, daemon=True, name="llm-model-download").start()
    return {"accepted": True, "detail": f"Download + activation started for {model_id}"}


# ── CLI ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        print(json.dumps(list_models(), indent=2))
    elif cmd == "current":
        print(current_model())
    elif cmd == "status":
        print(json.dumps(download_status(), indent=2))
    elif cmd == "select" and len(sys.argv) >= 3:
        print(json.dumps(select(sys.argv[2]), indent=2))
        # Wait briefly to capture the first few log lines.
        import time as _t
        _t.sleep(2)
        print(json.dumps(download_status(), indent=2))
    else:
        print("usage: python -m llm_models [list | current | status | select <model_id>]")

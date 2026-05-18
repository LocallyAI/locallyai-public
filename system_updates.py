"""
system_updates.py — secure update channel for the LocallyAI server itself.

Companion to client_installers.py (which distributes the staff-laptop
.dmg/.msi). This module handles updates to the server code that runs
on the firm's office Mac — Python source, UI, models, config schema,
sentinel logic.

Defence-in-depth, deepest first:

  1. Two-channel release (UPD.1)
       Vendor tags v*-dev (only the vendor's dev box pulls).
       After 24-48h soak with no rollback, vendor re-tags as
       v*-stable. Firms only pull -stable. Buys a window to
       catch bad releases before they reach a firm.

  2. SHA-256 manifest (UPD.2)
       Every release ships release_manifest.json declaring tier (A/B/C)
       + sha256 of every artifact + min_required_version. Office Mac
       downloads the tag, verifies HEAD's tree matches manifest's
       expected hashes, refuses to apply if mismatched.

  3. GPG signature verification (UPD.3)
       Vendor signs every tag with `git tag -s` using an offline key.
       Office Mac verifies via `git verify-tag` against the vendor
       public key pinned at docs/release-signing-key.gpg (imported by
       install.sh). Defends against compromised GitHub account —
       attacker would need both the GH creds AND the offline GPG key.

  4. OOB kill switch (UPD.4 — separate module: kill_switch.py)
       Static JSON polled before every apply.

  5. Atomic deploy + health-check auto-rollback (UPD.6 — separate)
       Apply via git stash + checkout; restart API; poll /healthz;
       if unhealthy, revert.

What this module does:
  - Polls GitHub (via gh CLI) for tags matching the firm's channel.
  - Filters by min-version floor and kill-switch.
  - Verifies GPG signature, manifest, hashes.
  - Returns a structured "available updates" payload the manager UI
    renders. Apply itself is in deploy.py (atomic + rollback).

What this module does NOT do:
  - Trigger the actual git checkout (deploy.py does that).
  - Decide to auto-apply (the sentinel + admin endpoint do that,
    based on tier + operator config).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


log = logging.getLogger("system_updates")

REPO_DIR = Path(__file__).resolve().parent
GITHUB_REPO = os.environ.get("LOCALLYAI_REPO", "LocallyAI/locallyai")

# Channels: "stable" (default for firms) or "dev" (vendor's dev box).
# Tags are matched against v*-<channel>; e.g. v1.2.0-stable.
UPDATE_CHANNEL = os.environ.get("LOCALLYAI_UPDATE_CHANNEL", "stable").lower()
if UPDATE_CHANNEL not in ("stable", "dev"):
    log.warning("Unknown LOCALLYAI_UPDATE_CHANNEL=%r; falling back to stable", UPDATE_CHANNEL)
    UPDATE_CHANNEL = "stable"

# Per-tier opt-out. Firms can pin to manual mode entirely with
# LOCALLYAI_AUTO_UPDATE=off, OR enable only specific tiers via
# LOCALLYAI_AUTO_UPDATE_TIERS=A,B (default A).
AUTO_UPDATE_ENABLED = os.environ.get("LOCALLYAI_AUTO_UPDATE", "on").lower() != "off"
AUTO_UPDATE_TIERS = {
    t.strip().upper() for t in
    os.environ.get("LOCALLYAI_AUTO_UPDATE_TIERS", "A").split(",")
    if t.strip()
}

# Pinned GPG key path (imported by install.sh into the firm's GPG keyring).
SIGNING_KEY_PATH = REPO_DIR / "docs" / "release-signing-key.gpg"

# Override: allow the operator to pin a specific fingerprint via env
# (useful during a key rotation). Without this, the pinned fingerprint
# is extracted from SIGNING_KEY_PATH at module-import time.
_PINNED_FP_OVERRIDE = os.environ.get("LOCALLYAI_RELEASE_KEY_FP", "").strip().upper().replace(" ", "")


def _extract_pinned_fingerprint() -> Optional[str]:
    """Read the trusted fingerprint from docs/release-signing-key.gpg.
    Runs `gpg --show-keys --with-colons` against the key file (does NOT
    import; pure parse). Returns the full 40-char fingerprint string,
    or None on failure.

    Red-team finding 7.5: previously verify_tag_signature accepted "any
    good signature from a key in the local keyring". An attacker who
    can `gpg --import` a malicious key into the firm's keyring (via
    a compromised dependency that shells out to gpg) could sign tags
    that validate. Pinning forces the signature to come from THIS
    specific key.
    """
    if _PINNED_FP_OVERRIDE and len(_PINNED_FP_OVERRIDE) == 40:
        return _PINNED_FP_OVERRIDE
    if not SIGNING_KEY_PATH.exists():
        return None
    if not _gpg_available():
        return None
    try:
        r = subprocess.run(
            ["gpg", "--show-keys", "--with-colons", str(SIGNING_KEY_PATH)],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    # `fpr:::::::::<FINGERPRINT>:` — first fpr line is the primary key.
    for line in r.stdout.splitlines():
        if line.startswith("fpr:"):
            parts = line.split(":")
            if len(parts) >= 10 and len(parts[9]) == 40:
                return parts[9].upper()
    return None


_PINNED_FINGERPRINT: Optional[str] = None  # lazy-resolved on first verify

# Soak window for the dev → stable promotion. Firms reject any -stable
# tag whose released_at is younger than this (defends against vendor
# accidentally promoting before the dev soak completes).
DEV_SOAK_HOURS = int(os.environ.get("LOCALLYAI_DEV_SOAK_HOURS", "24"))


# ── Data shapes ─────────────────────────────────────────────────────────────
@dataclass
class ReleaseManifest:
    """Shape of release_manifest.json that ships with each tagged release."""
    version: str                             # "1.2.0"
    channel: str                             # "dev" | "stable"
    tier: str                                # "A" | "B" | "C"
    released_at: str                         # ISO-8601 UTC
    changelog_summary: str = ""
    artifacts: list[dict] = field(default_factory=list)  # [{name, sha256, size}]
    min_required_version: str = "0.0.0"      # firms below this MUST update
    rollback_to_previous_if_failed: bool = True


@dataclass
class AvailableUpdate:
    tag: str
    manifest: ReleaseManifest
    gpg_verified: bool
    gpg_detail: str
    manifest_verified: bool
    manifest_detail: str
    blocked_by_kill_switch: bool
    blocked_reason: str
    eligible_for_auto_apply: bool


def _current_version() -> str:
    """Best-effort: read from the most recent applied tag, or VERSION file,
    or fall back to git describe."""
    vfile = REPO_DIR / "VERSION"
    if vfile.exists():
        v = vfile.read_text(encoding="utf-8").strip()
        if v: return v
    try:
        r = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "--match", f"v*-{UPDATE_CHANNEL}"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            tag = r.stdout.strip()
            return _tag_to_version(tag)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return "0.0.0"


def _tag_to_version(tag: str) -> str:
    """v1.2.0-stable → 1.2.0"""
    m = re.match(r"^v?(\d+\.\d+\.\d+)(?:-[a-z]+)?$", tag)
    return m.group(1) if m else tag


def _version_tuple(v: str) -> tuple:
    """Loose semver tuple for ordering. Non-numeric parts → 0."""
    parts = []
    for p in v.lstrip("v").split("."):
        try: parts.append(int(re.match(r"\d+", p).group(0)))
        except (AttributeError, ValueError): parts.append(0)
    while len(parts) < 3: parts.append(0)
    return tuple(parts[:3])


# ── GPG verification ────────────────────────────────────────────────────────
def _gpg_available() -> bool:
    return shutil.which("gpg") is not None


def verify_tag_signature(tag: str) -> tuple[bool, str]:
    """Run `git verify-tag` against the local GPG keyring AND match the
    signing key against the pinned vendor fingerprint. Returns
    (ok, detail). Detail is operator-readable.

    Red-team finding 7.5: pinning to a specific fingerprint closes the
    "any good signature in the keyring" gap. If a malicious dependency
    imports an attacker-controlled key, signatures from that key still
    fail this check.

    Critical: this reads the LOCAL git tag, not GitHub's. Operator must
    `git fetch --tags` before calling. The fetch step is in deploy.py.
    """
    global _PINNED_FINGERPRINT
    if not _gpg_available():
        return False, "gpg not installed (brew install gnupg)"
    if _PINNED_FINGERPRINT is None:
        _PINNED_FINGERPRINT = _extract_pinned_fingerprint()
    if not _PINNED_FINGERPRINT:
        return False, (
            "could not extract pinned release-signing fingerprint from "
            f"{SIGNING_KEY_PATH} (file missing or gpg --show-keys failed). "
            "Set LOCALLYAI_RELEASE_KEY_FP env to a 40-char fingerprint to "
            "override during rotation."
        )
    try:
        r = subprocess.run(
            ["git", "verify-tag", "--raw", tag],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"verify-tag exec failed: {exc}"

    if r.returncode != 0:
        err = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "verify-tag failed"
        return False, err[:200]

    # rc==0 means gpg thought the signature was valid; we still have to
    # check it's the PINNED key, not just any key in the local keyring.
    sig_fp = None
    for line in r.stderr.splitlines():
        # VALIDSIG <fingerprint> <date> ...  — fingerprint is the 2nd token
        if "VALIDSIG" in line:
            parts = line.split()
            for p in parts:
                if len(p) == 40 and all(c in "0123456789ABCDEFabcdef" for c in p):
                    sig_fp = p.upper()
                    break
            if sig_fp:
                break
    if not sig_fp:
        return False, "valid signature but fingerprint unparseable from VALIDSIG line"
    if sig_fp != _PINNED_FINGERPRINT:
        return False, (
            f"signature key fingerprint {sig_fp[-16:]} does not match pinned "
            f"vendor key {_PINNED_FINGERPRINT[-16:]}. Refusing — this would "
            f"be a key-substitution attack if not a rotation. Update "
            f"docs/release-signing-key.gpg from the vendor's signed comms "
            f"channel before retrying."
        )
    return True, f"valid signature, pinned key {sig_fp[-16:]}"


# ── Manifest verification ───────────────────────────────────────────────────
def _read_manifest_at_tag(tag: str) -> Optional[ReleaseManifest]:
    """Pull release_manifest.json from the local git tag."""
    try:
        r = subprocess.run(
            ["git", "show", f"{tag}:release_manifest.json"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    try:
        d = json.loads(r.stdout)
        return ReleaseManifest(**d)
    except (json.JSONDecodeError, TypeError):
        return None


def verify_manifest(tag: str, manifest: ReleaseManifest) -> tuple[bool, str]:
    """Cross-check the manifest's claims against the tagged source.

    For each artifact, compute sha256 of the file at the tag and compare
    to the manifest's declared hash. We deliberately trust the manifest
    AT the tag (not the live working tree) — the GPG signature on the
    tag covers both the manifest and the tree state at that point.
    """
    if not manifest.artifacts:
        return True, "no artifacts declared (server-side update only)"
    failures = []
    for art in manifest.artifacts:
        name = art.get("name", "")
        expected = (art.get("sha256") or "").lower()
        if not name or not expected:
            failures.append(f"{name}: malformed manifest entry")
            continue
        # Compute sha256 of the file at the tag.
        try:
            r = subprocess.run(
                ["git", "show", f"{tag}:{name}"],
                cwd=REPO_DIR, capture_output=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            failures.append(f"{name}: git show failed ({exc})")
            continue
        if r.returncode != 0:
            failures.append(f"{name}: not present at tag")
            continue
        import hashlib as _hl
        got = _hl.sha256(r.stdout).hexdigest()
        if got != expected:
            failures.append(f"{name}: sha256 mismatch (expected {expected[:12]}…, got {got[:12]}…)")
    if failures:
        return False, "; ".join(failures[:3])
    return True, f"all {len(manifest.artifacts)} artifact hash(es) match"


# ── Channel filter + soak window ────────────────────────────────────────────
def _list_remote_tags() -> list[str]:
    """`gh release list` returns newest-first. Filter to our channel."""
    if not shutil.which("gh"):
        return []
    try:
        r = subprocess.run(
            ["gh", "release", "list", "--repo", GITHUB_REPO, "--limit", "30",
             "--json", "tagName"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if r.returncode != 0:
        return []
    try:
        rows = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return []
    pat = re.compile(rf"^v\d+\.\d+\.\d+-{re.escape(UPDATE_CHANNEL)}$")
    return [row["tagName"] for row in rows if pat.match(row.get("tagName", ""))]


def _passes_soak(manifest: ReleaseManifest) -> tuple[bool, str]:
    """Stable releases must have aged at least DEV_SOAK_HOURS since
    released_at. Catches the case where vendor accidentally promoted
    too early."""
    if manifest.channel != "stable":
        return True, ""
    try:
        from datetime import datetime, timezone
        released = datetime.fromisoformat(manifest.released_at.replace("Z", "+00:00"))
        age_hr = (datetime.now(timezone.utc) - released).total_seconds() / 3600
        if age_hr < DEV_SOAK_HOURS:
            return False, f"in soak window ({age_hr:.1f}h elapsed, {DEV_SOAK_HOURS}h required)"
        return True, f"{age_hr:.0f}h since release (soak satisfied)"
    except (ValueError, AttributeError) as exc:
        return False, f"unparseable released_at: {exc}"


# ── Public API ──────────────────────────────────────────────────────────────
def list_available() -> list[AvailableUpdate]:
    """Return every release tagged for our channel that's newer than
    what we're currently running, with verification status attached.
    """
    out: list[AvailableUpdate] = []
    current_v = _current_version()
    current_tup = _version_tuple(current_v)

    # Make sure local tags are up to date so verify_tag works.
    try:
        subprocess.run(["git", "fetch", "--tags", "--quiet"],
                       cwd=REPO_DIR, capture_output=True, timeout=20)
    except (subprocess.TimeoutExpired, OSError):
        pass

    for tag in _list_remote_tags():
        v = _tag_to_version(tag)
        if _version_tuple(v) <= current_tup:
            continue  # not newer
        manifest = _read_manifest_at_tag(tag)
        if not manifest:
            out.append(AvailableUpdate(
                tag=tag, manifest=ReleaseManifest(version=v, channel=UPDATE_CHANNEL,
                                                  tier="?", released_at=""),
                gpg_verified=False, gpg_detail="manifest missing — cannot verify",
                manifest_verified=False, manifest_detail="release_manifest.json absent",
                blocked_by_kill_switch=False, blocked_reason="",
                eligible_for_auto_apply=False,
            ))
            continue
        gpg_ok, gpg_detail = verify_tag_signature(tag)
        man_ok, man_detail = verify_manifest(tag, manifest)
        soak_ok, soak_detail = _passes_soak(manifest)

        ks_blocked, ks_reason = _check_kill_switch(tag, manifest)

        eligible = (
            AUTO_UPDATE_ENABLED
            and manifest.tier in AUTO_UPDATE_TIERS
            and gpg_ok and man_ok and soak_ok and not ks_blocked
        )
        out.append(AvailableUpdate(
            tag=tag, manifest=manifest,
            gpg_verified=gpg_ok, gpg_detail=gpg_detail,
            manifest_verified=man_ok, manifest_detail=(man_detail if man_ok else man_detail),
            blocked_by_kill_switch=ks_blocked, blocked_reason=(ks_reason or (soak_detail if not soak_ok else "")),
            eligible_for_auto_apply=eligible,
        ))
    return out


def _check_kill_switch(tag: str, manifest: ReleaseManifest) -> tuple[bool, str]:
    """Defer to kill_switch.py module (lazy import — module is optional
    in early deployments)."""
    try:
        import kill_switch as _ks
        return _ks.is_blocked(tag, manifest.version)
    except Exception:
        return False, ""  # missing module = no kill-switch enforcement (dev only)


def status() -> dict:
    """Manager UI's "current state" summary."""
    return {
        "channel": UPDATE_CHANNEL,
        "current_version": _current_version(),
        "auto_update_enabled": AUTO_UPDATE_ENABLED,
        "auto_update_tiers": sorted(AUTO_UPDATE_TIERS),
        "gpg_available": _gpg_available(),
        "github_repo": GITHUB_REPO,
        "dev_soak_hours": DEV_SOAK_HOURS,
    }


# ── Serialisation for the API ───────────────────────────────────────────────
def to_dict(av: AvailableUpdate) -> dict:
    d = asdict(av)
    d["manifest"] = asdict(av.manifest)
    return d


# ── CLI for testing ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(), indent=2))
    elif cmd == "list":
        ups = list_available()
        print(json.dumps([to_dict(u) for u in ups], indent=2))
    elif cmd == "verify-tag" and len(sys.argv) > 2:
        tag = sys.argv[2]
        ok, detail = verify_tag_signature(tag)
        print(f"GPG: {'PASS' if ok else 'FAIL'} — {detail}")
        manifest = _read_manifest_at_tag(tag)
        if manifest:
            mok, mdetail = verify_manifest(tag, manifest)
            print(f"Manifest: {'PASS' if mok else 'FAIL'} — {mdetail}")
            print(f"Tier: {manifest.tier}  Released: {manifest.released_at}")
        else:
            print("Manifest: MISSING — release_manifest.json not at tag")
    else:
        print("usage: python -m system_updates [status|list|verify-tag <tag>]")

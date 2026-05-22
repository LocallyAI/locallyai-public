#!/usr/bin/env python3
"""Verify a saved DPO compliance snapshot.

The compliance endpoint emits an HTML report with the JSON bundle embedded
in a <script type="application/json" id="locallyai-compliance-snapshot">
tag and an HMAC signature over the bundle (less the signature itself)
computed with LOCALLYAI_AUDIT_HMAC_KEY.

This script extracts the JSON, recomputes the HMAC against the same key,
and reports VERIFIED / MISMATCH. A DPO (or a regulator) can run this
against any saved snapshot to prove the contents weren't altered.

Usage:
    python scripts/verify_compliance_snapshot.py <snapshot.html>

Exit codes:
    0 — verified
    1 — mismatch (file altered or wrong key)
    2 — usage / file errors
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import re
import sys
from pathlib import Path

_EMBED_RE = re.compile(
    r'<script[^>]+id="locallyai-compliance-snapshot"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _extract_bundle(html_text: str) -> dict:
    m = _EMBED_RE.search(html_text)
    if not m:
        raise SystemExit("Could not find embedded snapshot JSON in file. Wrong file type?")
    raw = m.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Embedded JSON is malformed: {exc}")


def _hmac_key() -> bytes:
    key = os.environ.get("LOCALLYAI_AUDIT_HMAC_KEY", "").strip()
    if not key:
        raise SystemExit(
            "LOCALLYAI_AUDIT_HMAC_KEY is not set. Verify with the same key the\n"
            "snapshot was signed with — usually the deployment's audit-chain key."
        )
    return key.encode("utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 2

    html_text = path.read_text(encoding="utf-8", errors="replace")
    bundle = _extract_bundle(html_text)
    stored_sig = bundle.pop("snapshot_hmac", "")
    if not stored_sig:
        print("Snapshot is unsigned (snapshot_hmac field empty/missing).")
        print("Treat as untrusted — the deployment did not have LOCALLYAI_AUDIT_HMAC_KEY set when generated.")
        return 1

    body = json.dumps(bundle, sort_keys=True, default=str).encode("utf-8")
    expected = _hmac.new(_hmac_key(), body, hashlib.sha256).hexdigest()

    if _hmac.compare_digest(stored_sig, expected):
        gen_at = bundle.get("generated_at", "?")
        dep = bundle.get("deployment", {})
        print(f"VERIFIED. Snapshot from {dep.get('deployment_id', '?')} ({dep.get('region', '?')})")
        print(f"          generated_at  = {gen_at}")
        print(f"          firm_id       = {dep.get('firm_id', '?')}")
        print(f"          node_id       = {dep.get('node_id', '?')}")
        print(f"          version       = {dep.get('version', '?')}")
        return 0

    print("MISMATCH — snapshot has been altered OR was signed with a different key.")
    print(f"  stored:   {stored_sig}")
    print(f"  expected: {expected}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

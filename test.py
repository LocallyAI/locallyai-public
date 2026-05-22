"""
LocallyAI — Production Smoke Tests

Verifies the live install: /healthz, auth wall, /v1/models, /v1/chat/completions
with retrieval. Auto-detects HTTP vs HTTPS based on whether the cert exists.

Usage:
    python test.py --key YOUR_API_KEY
    python test.py --key YOUR_API_KEY --base https://localhost:8000

Exit code 0 if all tests pass, 1 otherwise.
"""

import argparse
import os
import sys
from pathlib import Path

import requests

# Suppress the self-signed-cert warning from urllib3 — install.sh generates a
# self-signed cert by design (LAN-only deployment).
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _default_base() -> str:
    """Pick https://localhost:8000 if a cert is present, else http://."""
    tls = Path(__file__).resolve().parent / "tls" / "cert.pem"
    return "https://localhost:8000" if tls.exists() else "http://localhost:8000"


def check(label: str, ok: bool, detail: str = "") -> int:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    return 1 if ok else 0


def run_tests(key: str, base: str) -> tuple[int, int]:
    print(f"\n── LocallyAI smoke tests against {base} ──\n")
    headers = {"Authorization": f"Bearer {key}"}
    passed = 0
    total = 0

    # 1. /healthz — unauth liveness
    total += 1
    try:
        r = requests.get(f"{base}/healthz", timeout=10, verify=False)
        data = r.json()
        ok = r.status_code == 200 and data.get("ok") is True
        passed += check("/healthz", ok, f"backend={data.get('backend')}")
    except Exception as e:
        passed += check("/healthz", False, str(e))

    # 2. Auth wall — invalid key rejected
    total += 1
    try:
        r = requests.get(
            f"{base}/v1/models",
            headers={"Authorization": "Bearer invalid"},
            timeout=10,
            verify=False,
        )
        passed += check("Auth wall — invalid key rejected", r.status_code == 401)
    except Exception as e:
        passed += check("Auth wall — invalid key rejected", False, str(e))

    # 3. /v1/models — valid key
    total += 1
    available_models: list[str] = []
    try:
        r = requests.get(f"{base}/v1/models", headers=headers, timeout=10, verify=False)
        ok = r.status_code == 200 and "data" in r.json()
        if ok:
            available_models = [m.get("id", "") for m in r.json().get("data", [])]
        passed += check("/v1/models — valid key", ok, f"models={available_models}")
    except Exception as e:
        passed += check("/v1/models — valid key", False, str(e))

    # 4. Chat completion with retrieval — uses default model from server config
    total += 1
    try:
        payload = {
            "messages": [
                {"role": "user", "content": "What is LocallyAI? Answer in one sentence."}
            ],
            "max_tokens": 200,
            "temperature": 0.1,
        }
        r = requests.post(
            f"{base}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=180,
            verify=False,
        )
        data = r.json() if r.status_code == 200 else {}
        choices = data.get("choices", [])
        answer = choices[0].get("message", {}).get("content", "") if choices else ""
        sources = data.get("usage", {}).get("sources_retrieved", 0)
        ok = r.status_code == 200 and bool(answer)
        passed += check(
            "/v1/chat/completions",
            ok,
            f"sources_retrieved={sources}, preview='{answer[:100]}...'",
        )
    except Exception as e:
        passed += check("/v1/chat/completions", False, str(e))

    print(f"\n── Results: {passed}/{total} passed ──\n")
    return passed, total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LocallyAI smoke tests")
    parser.add_argument("--key", required=True, help="API key (user or admin)")
    parser.add_argument("--base", default=os.environ.get("LOCALLYAI_API_BASE", _default_base()))
    args = parser.parse_args()

    passed, total = run_tests(args.key, args.base.rstrip("/"))
    sys.exit(0 if passed == total else 1)

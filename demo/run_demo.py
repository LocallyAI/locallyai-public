"""
run_demo.py — End-to-end demonstration of LocallyAI on the seeded corpus.

Sends 5 representative queries that each exercise one of the demo documents
(NDA, GDPR policy, conflict-check procedure, lease clauses, engagement letter)
and prints the answer + retrieved-source count. Use this immediately after
`bash install.sh` (in demo mode) to show that the full RAG path works.

Usage:
    python demo/run_demo.py --key YOUR_API_KEY

Run from inside the production/ folder. Auto-detects HTTP vs HTTPS based on
the presence of tls/cert.pem.
"""

import argparse
import os
import sys
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


GREEN  = "\033[32m"
CYAN   = "\033[36m"
YELLOW = "\033[33m"
RED    = "\033[31m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


def _default_base() -> str:
    # demo/ sits one level below production/
    tls = Path(__file__).resolve().parent.parent / "tls" / "cert.pem"
    return "https://localhost:8000" if tls.exists() else "http://localhost:8000"


# Each demo query is designed to trigger retrieval from one specific seeded doc.
DEMO_QUERIES = [
    (
        "NDA — duration & exclusions",
        "Under our standard mutual NDA, how long does the confidentiality "
        "obligation last after disclosure, and what categories of information "
        "are excluded from the obligation?",
    ),
    (
        "GDPR — data-subject rights process",
        "What is the process for handling a data-subject access request under "
        "our UK GDPR policy? Specifically, what's our acknowledgement and "
        "response timeline?",
    ),
    (
        "Conflict check — traffic-light classification",
        "When the conflict-check team returns an 'Amber' classification, what "
        "is the required next step before opening the matter?",
    ),
    (
        "Lease — rent review mechanism",
        "On a standard FRI lease, how is rent reviewed if landlord and tenant "
        "cannot agree the open-market rent within three months of the review "
        "date?",
    ),
    (
        "Engagement — AML requirements",
        "What anti-money-laundering verification documents do we require from "
        "an individual client before substantively acting on a matter?",
    ),
]


def _ask(base: str, key: str, question: str) -> tuple[str, int]:
    payload = {
        "messages": [{"role": "user", "content": question}],
        "max_tokens": 512,
        "temperature": 0.1,
    }
    r = requests.post(
        f"{base}/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=300,
        verify=False,
    )
    if r.status_code != 200:
        return f"[error {r.status_code}: {r.text[:200]}]", 0
    data = r.json()
    answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    sources = data.get("usage", {}).get("sources_retrieved", 0)
    return answer, sources


def main() -> None:
    parser = argparse.ArgumentParser(description="LocallyAI end-to-end demo")
    parser.add_argument("--key", required=True, help="API key (printed by install.sh)")
    parser.add_argument(
        "--base",
        default=os.environ.get("LOCALLYAI_API_BASE", _default_base()),
    )
    args = parser.parse_args()
    base = args.base.rstrip("/")
    key = args.key

    # Connectivity check
    try:
        h = requests.get(f"{base}/healthz", timeout=5, verify=False)
        if h.status_code != 200:
            print(f"{RED}[error] {base}/healthz returned {h.status_code}.{RESET}", file=sys.stderr)
            sys.exit(2)
    except Exception as e:
        print(f"{RED}[error] Cannot reach {base}: {e}{RESET}", file=sys.stderr)
        print(f"{YELLOW}Service running? launchctl list | grep com.locallyai.server{RESET}", file=sys.stderr)
        sys.exit(2)

    print(f"\n{BOLD}LocallyAI demo{RESET} — {base}")
    print(f"{DIM}Sending {len(DEMO_QUERIES)} representative queries against the seeded corpus.{RESET}")
    print(f"{DIM}Each should retrieve >0 source chunks if the demo install ingested correctly.{RESET}\n")

    grand_total_sources = 0
    failures = 0
    for i, (title, question) in enumerate(DEMO_QUERIES, start=1):
        print(f"{BOLD}── Query {i}/{len(DEMO_QUERIES)}: {title} ─────────────────────────{RESET}")
        print(f"{CYAN}Q:{RESET} {question}")
        t0 = time.monotonic()
        try:
            answer, sources = _ask(base, key, question)
        except requests.exceptions.Timeout:
            print(f"{RED}[error] timed out after 5 min{RESET}\n")
            failures += 1
            continue
        except Exception as e:
            print(f"{RED}[error] {e}{RESET}\n")
            failures += 1
            continue
        elapsed = time.monotonic() - t0

        if answer.startswith("[error"):
            print(f"{RED}{answer}{RESET}\n")
            failures += 1
            continue

        print(f"{GREEN}A:{RESET} {answer}")
        marker = GREEN if sources > 0 else YELLOW
        print(f"{marker}   sources_retrieved={sources}{RESET}  {DIM}({elapsed:.1f}s){RESET}\n")
        grand_total_sources += sources

    print(f"{BOLD}── Summary ──{RESET}")
    print(f"  Queries run:           {len(DEMO_QUERIES)}")
    print(f"  Failed:                {failures}")
    print(f"  Total source chunks:   {grand_total_sources}")
    if failures == 0 and grand_total_sources > 0:
        print(f"\n  {GREEN}{BOLD}Demo PASSED.{RESET} RAG pipeline is live end-to-end.\n")
        sys.exit(0)
    else:
        print(f"\n  {YELLOW}{BOLD}Demo INCOMPLETE.{RESET}")
        print(f"  {YELLOW}From the production folder root, re-ingest using the venv's python:{RESET}")
        print(f"  {YELLOW}    .venv/bin/python ingest.py --force{RESET}")
        print(f"  {YELLOW}If 'sources_retrieved=0' on every query, the embedding backend isn't")
        print("  responding. Check the right thing for your backend:")
        backend_hints = {
            "mlx":      "EMBED_BACKEND=local — verify sentence-transformers can load nomic-ai/nomic-embed-text-v1.5",
            "ollama":   "curl -s http://localhost:11434/api/tags",
            "lmstudio": "curl -s http://localhost:1234/v1/models",
        }
        backend = os.environ.get("LOCALLYAI_BACKEND", "ollama").lower()
        print(f"    {backend_hints.get(backend, backend_hints['ollama'])}{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()

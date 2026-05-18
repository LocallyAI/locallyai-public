"""
chat.py — Interactive REPL for LocallyAI

A single-file terminal client that talks to the local /v1/chat/completions
endpoint. Multi-turn conversation, slash commands, source-count display,
TLS auto-detect, no external deps beyond `requests` (already in requirements.txt).

Usage:
    python chat.py --key YOUR_API_KEY
    python chat.py --key YOUR_API_KEY --model qwen2.5:7b
    python chat.py --key YOUR_API_KEY --base https://10.0.0.42:8000

Slash commands:
    /help            Show help
    /models          List installed Ollama models
    /model <name>    Switch model (must already be `ollama pull`-ed)
    /clear           Clear conversation history
    /sources         Show sources retrieved on the last response
    /quit            Exit (also Ctrl+D)
"""

import argparse
import os
import sys
from pathlib import Path

import requests
import urllib3

# Self-signed cert is by design (LAN-only deployment) — silence the warning.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ── ANSI ──────────────────────────────────────────────────────────────────────
GREEN  = "\033[32m"
CYAN   = "\033[36m"
YELLOW = "\033[33m"
RED    = "\033[31m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


def _default_base() -> str:
    """Pick https://localhost:8000 if a cert is present, else http://."""
    tls = Path(__file__).resolve().parent / "tls" / "cert.pem"
    return "https://localhost:8000" if tls.exists() else "http://localhost:8000"


def _list_models(base: str, key: str) -> list[str]:
    try:
        r = requests.get(
            f"{base}/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
            verify=False,
        )
        if r.status_code != 200:
            return []
        return [m.get("id", "") for m in r.json().get("data", [])]
    except Exception:
        return []


def _chat(base: str, key: str, model: str | None, messages: list[dict]) -> tuple[str, int, dict]:
    payload: dict = {"messages": messages, "max_tokens": 2048, "temperature": 0.1}
    if model:
        payload["model"] = model
    r = requests.post(
        f"{base}/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=300,
        verify=False,
    )
    if r.status_code != 200:
        return f"[error {r.status_code}: {r.text[:300]}]", 0, {}
    data = r.json()
    answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    sources = data.get("usage", {}).get("sources_retrieved", 0)
    return answer, sources, data


HELP = f"""
{BOLD}LocallyAI REPL{RESET}

Just type to chat. Slash commands:
  {CYAN}/help{RESET}            Show this help
  {CYAN}/models{RESET}          List installed models
  {CYAN}/model <name>{RESET}    Switch model (must be installed via `ollama pull`)
  {CYAN}/clear{RESET}           Clear conversation history
  {CYAN}/sources{RESET}         Show sources retrieved on the last response
  {CYAN}/quit{RESET}            Exit (also Ctrl+D)
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="LocallyAI interactive REPL")
    parser.add_argument("--key", required=True, help="API key (printed by install.sh)")
    parser.add_argument(
        "--base",
        default=os.environ.get("LOCALLYAI_API_BASE", _default_base()),
        help="Base URL (default: auto-detect HTTPS/HTTP)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the server's default model (e.g. qwen2.5:7b, llama3.3:70b)",
    )
    args = parser.parse_args()
    base = args.base.rstrip("/")
    key = args.key
    model = args.model

    # Connectivity check — fail fast with a useful pointer if the service is down
    try:
        h = requests.get(f"{base}/healthz", timeout=5, verify=False)
        if h.status_code != 200:
            print(
                f"{RED}[error] {base}/healthz returned {h.status_code}.{RESET}",
                file=sys.stderr,
            )
            sys.exit(2)
        backend = h.json().get("backend", "?")
    except Exception as e:
        print(f"{RED}[error] Cannot reach {base}: {e}{RESET}", file=sys.stderr)
        print(
            f"{YELLOW}Check the service: launchctl list | grep com.locallyai.server{RESET}",
            file=sys.stderr,
        )
        sys.exit(2)

    # Auth probe — friendlier than dying on the first chat
    available = _list_models(base, key)
    if not available:
        try:
            r = requests.get(
                f"{base}/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=5,
                verify=False,
            )
            if r.status_code == 401:
                print(
                    f"{RED}[error] Invalid API key.{RESET} "
                    f"Add a user with: {CYAN}python manage_users.py add <Name>{RESET}",
                    file=sys.stderr,
                )
                sys.exit(3)
        except Exception:
            pass

    print(f"\n{BOLD}LocallyAI{RESET} — connected to {GREEN}{base}{RESET} "
          f"(backend: {backend})")
    if available:
        print(f"  Models installed: {', '.join(available)}")
    if model:
        print(f"  Active model: {GREEN}{model}{RESET}")
    else:
        print(f"  Active model: {DIM}(server default){RESET}")
    print(f"  Type {CYAN}/help{RESET} for commands.")

    messages: list[dict] = []
    last_response: dict = {}

    try:
        while True:
            try:
                line = input(f"\n{BOLD}you>{RESET} ").strip()
            except EOFError:
                print()
                break
            if not line:
                continue

            # Slash commands
            if line in ("/quit", "/exit"):
                break
            if line == "/help":
                print(HELP)
                continue
            if line == "/clear":
                messages.clear()
                last_response = {}
                print(f"{DIM}(history cleared){RESET}")
                continue
            if line == "/models":
                models = _list_models(base, key)
                if models:
                    for m in models:
                        marker = f"{GREEN}*{RESET}" if m == model else " "
                        print(f"  {marker} {m}")
                else:
                    print(f"{YELLOW}(no models installed or auth failed){RESET}")
                continue
            if line.startswith("/model "):
                model = line.split(maxsplit=1)[1].strip()
                print(f"{DIM}(model set to {model}){RESET}")
                continue
            if line == "/sources":
                if not last_response:
                    print(f"{DIM}(no response yet){RESET}")
                else:
                    n = last_response.get("usage", {}).get("sources_retrieved", 0)
                    print(f"{DIM}sources_retrieved={n}{RESET}")
                continue
            if line.startswith("/"):
                print(f"{YELLOW}Unknown command: {line}. Try /help.{RESET}")
                continue

            # Normal chat
            messages.append({"role": "user", "content": line})
            print(f"{DIM}…thinking…{RESET}", end="\r", flush=True)
            try:
                answer, sources, data = _chat(base, key, model, messages)
            except requests.exceptions.Timeout:
                print(
                    f"\033[K{RED}[error] Request timed out (5 min). "
                    f"Larger models on smaller boxes can be very slow.{RESET}"
                )
                messages.pop()
                continue
            except Exception as e:
                print(f"\033[K{RED}[error] {e}{RESET}")
                messages.pop()
                continue
            last_response = data
            print(f"\033[K{GREEN}assistant>{RESET} {answer}")
            if sources:
                plural = "s" if sources != 1 else ""
                print(f"  {DIM}({sources} source chunk{plural} retrieved){RESET}")
            messages.append({"role": "assistant", "content": answer})
    except KeyboardInterrupt:
        print()

    print(f"\n{DIM}Goodbye.{RESET}")


if __name__ == "__main__":
    main()

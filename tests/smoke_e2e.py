"""
End-to-end smoke test for LocallyAI.

Spins up:
  1. A fake OpenAI-compatible LLM/embeddings server on localhost (so the
     test doesn't need Ollama or the real model files).
  2. The real FastAPI backend on a random free port, pointed at the fake
     LLM and at an isolated storage directory.

Then exercises every endpoint the UIs depend on and asserts that
/v1/chat/completions returns a non-empty `sources` array with the expected
citation shape.

Run from the repo root:
    python tests/smoke_e2e.py
"""
import http.server
import json
import os
import random
import secrets
import shutil
import socket
import socketserver
import string
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_fake_llm(port: int) -> threading.Thread:
    """OpenAI-compatible stub. Deterministic dummy embedding for
    /v1/embeddings; echo answer for /v1/chat/completions."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a, **_k):
            return

        def _read_body(self):
            n = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(n)) if n else {}

        def _send(self, payload, status=200):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/v1/models":
                self._send({"data": [{"id": "fake-llm", "object": "model"}]})
            else:
                self._send({"detail": "not found"}, 404)

        def do_POST(self):
            body = self._read_body()
            if self.path == "/v1/embeddings":
                text = body.get("input", "") or body.get("prompt", "")
                rng = random.Random(hash(text) & 0xFFFFFFFF)
                self._send({"data": [{"embedding": [rng.uniform(-1, 1) for _ in range(768)]}]})
            elif self.path == "/api/embeddings":
                text = body.get("prompt", "")
                rng = random.Random(hash(text) & 0xFFFFFFFF)
                self._send({"embedding": [rng.uniform(-1, 1) for _ in range(768)]})
            elif self.path == "/v1/chat/completions":
                msg = body.get("messages", [{}])[-1].get("content", "")
                self._send({
                    "choices": [{
                        "message": {"role": "assistant", "content": f"FAKE-LLM ANSWER for: {msg[:120]}"},
                        "finish_reason": "stop",
                    }]
                })
            else:
                self._send({"detail": "not found"}, 404)

    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    setattr(t, "_server", server)
    return t


def start_backend(env: dict, port: int):
    import importlib
    for name in list(sys.modules):
        if name.split(".")[0] in {
            "api", "config", "ingest", "retrieval", "bm25",
            "audit_export", "monitoring", "billing", "watchdog", "manage_users",
        }:
            del sys.modules[name]
    os.environ.update(env)
    api = importlib.import_module("api")
    import uvicorn

    config = uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as r:
                if r.status == 200:
                    return server, thread, api
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("backend did not come up")


def http_get(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, json.loads(r.read())


def http_post(url, body, token=None):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, json.loads(r.read())


def http_post_multipart(url, file_path: Path, token=None):
    boundary = "----lai" + "".join(random.choice(string.hexdigits) for _ in range(16))
    body = []
    body.append(f"--{boundary}\r\n".encode())
    body.append(f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode())
    body.append(b"Content-Type: text/markdown\r\n\r\n")
    body.append(file_path.read_bytes())
    body.append(f"\r\n--{boundary}--\r\n".encode())
    payload = b"".join(body)
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.loads(r.read())


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="locallyai_e2e_"))
    print(f"[e2e] workdir: {work}")

    storage = work / "storage"
    logs = work / "logs"
    storage.mkdir(parents=True)
    logs.mkdir(parents=True)

    repo_users = REPO_ROOT / "users.json"
    backup = repo_users.read_bytes() if repo_users.exists() else None
    repo_users.write_text("{}", encoding="utf-8")

    fake_llm_port = _free_port()
    backend_port = _free_port()
    fake_thread = make_fake_llm(fake_llm_port)
    print(f"[e2e] fake LLM on http://127.0.0.1:{fake_llm_port}")

    admin_key = secrets.token_hex(32)
    env = {
        "LOCALLYAI_BACKEND": "ollama",
        "LOCALLYAI_ADMIN_KEY": admin_key,
        "LOCALLYAI_AUDIT_SALT": secrets.token_hex(32),
        "LOCALLYAI_AUDIT_HMAC_KEY": secrets.token_hex(32),
        "LOCALLYAI_STORAGE_DIR": str(storage),
        "LOCALLYAI_LOG_DIR": str(logs),
        "LLM_BASE_URL": f"http://127.0.0.1:{fake_llm_port}",
        "OLLAMA_BASE_URL": f"http://127.0.0.1:{fake_llm_port}",
        "OLLAMA_MODEL": "fake-llm",
        "EMBED_MODEL": "fake-embed",
        "EMBED_BACKEND": "http",
        "PORT": str(backend_port),
        "LOCALLYAI_DISK_CHECK_PATH": str(work),
    }

    failures: list[str] = []

    def assert_eq(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")
            print(f"  FAIL {name}: {got!r} != {want!r}")
        else:
            print(f"  OK   {name}")

    def assert_truthy(name, value, hint=""):
        if not value:
            failures.append(f"{name}: falsey {value!r} {hint}")
            print(f"  FAIL {name}: {value!r} {hint}")
        else:
            print(f"  OK   {name}")

    server = thread = api = None
    try:
        server, thread, api = start_backend(env, backend_port)
        base = f"http://127.0.0.1:{backend_port}"
        print(f"[e2e] backend up at {base}")

        s, body = http_get(f"{base}/healthz")
        assert_eq("healthz status", s, 200)
        assert_eq("healthz ok flag", body.get("ok"), True)

        s, body = http_get(f"{base}/v1/me", token=admin_key)
        assert_eq("me admin status", s, 200)
        assert_eq("me admin user", body.get("user"), "admin")
        assert_eq("me admin flag", body.get("is_admin"), True)

        s, body = http_post(f"{base}/admin/users", {"name": "Sarah Chen"}, token=admin_key)
        assert_eq("create user status", s, 200)
        user_key = body.get("api_key", "")
        assert_truthy("create user key length", len(user_key) >= 32)

        s, body = http_get(f"{base}/v1/me", token=user_key)
        assert_eq("me user status", s, 200)
        assert_eq("me user name", body.get("user"), "Sarah Chen")
        assert_eq("me user flag", body.get("is_admin"), False)

        s, body = http_get(f"{base}/admin/users", token=admin_key)
        assert_eq("list users status", s, 200)
        assert_truthy("list users contains", "Sarah Chen" in body.get("users", []))

        sample = work / "policy.md"
        sample.write_text(
            "# Indemnification Policy\n\n"
            "Acme indemnifies the Customer for material breaches of "
            "confidentiality, with an aggregate cap of 12 months of fees.\n\n"
            "Termination for convenience requires 30 days written notice.\n",
            encoding="utf-8",
        )
        s, body = http_post_multipart(f"{base}/v1/ingest", sample, token=user_key)
        assert_eq("ingest status", s, 200)
        assert_eq("ingest stage", body.get("indexing"), "in_progress")

        for _ in range(40):
            if (storage / "bm25_index.json").exists():
                break
            time.sleep(0.5)
        assert_truthy("bm25 index built", (storage / "bm25_index.json").exists())

        s, body = http_post(
            f"{base}/v1/chat/completions",
            {"messages": [{"role": "user", "content": "What is the indemnification cap?"}]},
            token=user_key,
        )
        assert_eq("chat status", s, 200)
        answer = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        assert_truthy("chat answer non-empty", answer.strip())
        assert_truthy("chat answer routed via fake LLM", "FAKE-LLM ANSWER" in answer)
        sources = body.get("sources", [])
        assert_truthy("chat sources is list", isinstance(sources, list))
        assert_truthy(
            "chat sources non-empty",
            len(sources) > 0,
            hint=f"(usage.sources_retrieved={body.get('usage', {}).get('sources_retrieved')})",
        )
        if sources:
            first = sources[0]
            for field in ("chunk_id", "source", "snippet", "score"):
                assert_truthy(f"chat source has '{field}'", field in first)
            assert_truthy("chat source.snippet non-empty", first.get("snippet"))

        s, body = http_get(f"{base}/v1/models", token=user_key)
        assert_eq("models status", s, 200)
        ids = [m.get("id") for m in body.get("data", [])]
        assert_truthy("models contains fake-llm", "fake-llm" in ids)

        s, body = http_get(f"{base}/monitor/health/detailed", token=admin_key)
        assert_eq("monitor health status", s, 200)
        assert_truthy("monitor health has audit_log", "audit_log" in body)

        s, body = http_get(f"{base}/monitor/alerts", token=admin_key)
        assert_eq("monitor alerts status", s, 200)
        assert_truthy("monitor alerts has status", body.get("status") in {"ok", "degraded", "critical"})

        today = date.today().isoformat()
        last_week = (date.today() - timedelta(days=7)).isoformat()
        s, body = http_get(f"{base}/export/summary?from_date={last_week}&to_date={today}", token=admin_key)
        assert_eq("audit summary status", s, 200)
        assert_truthy("audit summary total_queries >= 1", body.get("total_queries", 0) >= 1)
    except urllib.error.HTTPError as e:
        failures.append(f"HTTPError: {e.code} {e.reason}: {e.read()[:300]!r}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        failures.append(f"unhandled exception: {e}")
    finally:
        if backup is not None:
            repo_users.write_bytes(backup)
        else:
            try:
                repo_users.unlink()
            except FileNotFoundError:
                pass
        if server is not None:
            server.should_exit = True
        getattr(fake_thread, "_server", None) and fake_thread._server.shutdown()
        try:
            shutil.rmtree(work)
        except Exception:
            pass

    if failures:
        print("\n[e2e] FAILED")
        for f in failures:
            print("   - " + f)
        return 1
    print("\n[e2e] PASS — every wired endpoint returned the expected shape, including citations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

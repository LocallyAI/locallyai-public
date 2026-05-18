"""
serve_ui.py — Tiny static server for a built Vite SPA.

Used by the per-app launch scripts. Differences vs `python -m http.server`:

  - SPA fallback: any non-file route returns index.html so TanStack Router
    deep links work after refresh.
  - Auto-picks a free port if the requested one is busy, prints the chosen
    URL, and (optionally) opens it in the default browser.
  - Single-shot: terminates cleanly on Ctrl+C with no zombie threads.

Usage:
    python serve_ui.py <dist_dir> [--port 5173] [--no-open]
"""
import argparse
import http.server
import os
import socket
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path


class SpaHandler(http.server.SimpleHTTPRequestHandler):
    """Serves files from `directory`; falls back to index.html for unknown
    routes so client-side routes (e.g. /audit, /system) don't 404 on refresh."""

    def __init__(self, *args, directory: str = "", **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self):  # noqa: N802 (stdlib naming)
        path = self.translate_path(self.path)
        if not os.path.exists(path) or os.path.isdir(path):
            requested = self.path.split("?", 1)[0]
            if not requested.startswith(("/assets/", "/favicon")):
                self.path = "/index.html"
        return super().do_GET()

    def log_message(self, format: str, *args):  # quieter default logging
        sys.stderr.write("[serve_ui] %s - %s\n" % (self.address_string(), format % args))


def _free_port(preferred: int) -> int:
    """Return `preferred` if available, otherwise an OS-assigned free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve a built Vite SPA on localhost.")
    parser.add_argument("dist_dir", help="Path to the dist/ directory of the built app")
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--no-open", action="store_true", help="Do not launch a browser")
    args = parser.parse_args()

    dist = Path(args.dist_dir).resolve()
    if not (dist / "index.html").exists():
        print(f"[serve_ui] error: {dist}/index.html not found. Did you run `bun run build`?", file=sys.stderr)
        return 2

    port = _free_port(args.port)
    handler = lambda *a, **kw: SpaHandler(*a, directory=str(dist), **kw)  # noqa: E731

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://localhost:{port}"
        print(f"[serve_ui] serving {dist} at {url}")
        if not args.no_open:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[serve_ui] shutting down")
            httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())

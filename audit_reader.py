"""Streaming helpers for append-only JSON-lines logs.

Round-2 B4 / B5: audit_export and monitor both did
`AUDIT_LOG.read_text().splitlines()` for every request — fine on 1 MB
files, lethal at 500 MB once retention runs hot for a year. These
helpers do the same job without preloading.

Used by audit_export (CSV stream) and monitoring/monitor (last-5 +
recent-timestamp checks). Future readers should use these too.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Iterator


def tail(path: Path, n: int, chunk_size: int = 8192) -> list[str]:
    """Return the last n complete lines of path as a list of strings.

    Seeks from EOF backwards in chunk_size blocks, collecting newlines
    until n are found or the file is exhausted. Memory is bounded by
    ~n * average-line-length + chunk_size.
    """
    if n <= 0 or not path.exists():
        return []
    size = path.stat().st_size
    if size == 0:
        return []
    with open(path, "rb") as fh:
        buf = b""
        pos = size
        lines_seen = 0
        while pos > 0 and lines_seen <= n:
            read_size = min(chunk_size, pos)
            pos -= read_size
            fh.seek(pos)
            buf = fh.read(read_size) + buf
            lines_seen = buf.count(b"\n")
        # Split on lines, drop the final empty entry if the file ends with \n,
        # and keep only the last n complete lines.
        raw_lines = buf.split(b"\n")
        if raw_lines and raw_lines[-1] == b"":
            raw_lines = raw_lines[:-1]
        last = raw_lines[-n:] if len(raw_lines) > n else raw_lines
        return [line.decode("utf-8", errors="replace") for line in last]


def iter_filtered(path: Path, predicate: Callable[[dict], bool]) -> Iterator[dict]:
    """Yield parsed JSON entries from path where predicate(entry) is truthy.

    Lines that fail to parse are skipped silently — same behaviour as the
    inline read loops that this helper replaces.
    """
    if not path.exists():
        return
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if predicate(entry):
                yield entry


def count_lines(path: Path) -> int:
    """Fast line count. Avoids loading the file into memory."""
    if not path.exists():
        return 0
    if os.name != "nt":
        try:
            import subprocess
            res = subprocess.run(
                ["wc", "-l", str(path)],
                capture_output=True, text=True, timeout=30, check=True,
            )
            return int(res.stdout.strip().split()[0])
        except Exception:
            pass
    # Windows fallback / wc absent: stream the file
    count = 0
    with open(path, "rb") as fh:
        for _ in fh:
            count += 1
    return count


def iter_reversed_lines(path: Path, chunk_size: int = 8192) -> Iterator[str]:
    """Yield non-empty lines from path in reverse order, without preloading.

    Used by monitor.alerts to find the most-recent timestamp without
    reading the whole file. Stops early at the first match upstream.
    """
    if not path.exists():
        return
    size = path.stat().st_size
    if size == 0:
        return
    with open(path, "rb") as fh:
        buf = b""
        pos = size
        while pos > 0:
            read_size = min(chunk_size, pos)
            pos -= read_size
            fh.seek(pos)
            buf = fh.read(read_size) + buf
            # Split out complete lines from the end, leaving any partial
            # leading line for the next iteration.
            parts = buf.split(b"\n")
            buf = parts[0]  # potentially partial — keep for next round
            for line in reversed(parts[1:]):
                if line:
                    yield line.decode("utf-8", errors="replace")
        if buf:
            yield buf.decode("utf-8", errors="replace")

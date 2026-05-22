"""
ingest_queue.py — bounded background indexing queue with batched BM25 rebuild.

Why this exists:
  Until now, _index_document held a process-wide lock and rebuilt BM25 after
  every single file. For bulk corpus loading (gigabytes, hundreds of files)
  that's O(n²) — every rebuild rescans every point in Qdrant.

What this module does:
  - Runs ingestion on a small ThreadPoolExecutor (default 2 workers). Picked
    to keep the embed backend busy without thrashing GPU memory.
  - Tracks in-flight + completed counts so the UI can render an
    "Indexing N of M" indicator.
  - Defers BM25 rebuild until the queue has been quiet for QUIET_SECONDS
    (default 30s). One last rebuild always lands; calling flush() forces it
    immediately.
  - Single instance per process. The /healthz handler can read .status() to
    surface queue depth on the operator dashboard.

Threading model:
  - submit() is called from request handlers (FastAPI's threadpool).
  - The executor's worker threads run _index_one and call _on_complete.
  - A single coordinator thread sleeps QUIET_SECONDS then triggers BM25 if
    the queue has been idle that whole time. It's reset each time a file
    completes.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("ingest_queue")


@dataclass
class IngestJob:
    path: Path
    source_name: str
    submitted_at: float = field(default_factory=time.time)


@dataclass
class QueueStatus:
    in_flight: int
    queued: int
    completed_total: int
    failed_total: int
    last_completed_at: float | None
    bm25_pending: bool


class IngestQueue:
    def __init__(self, *, max_workers: int = 2, quiet_seconds: float = 30.0):
        self._max_workers = max_workers
        self._quiet_seconds = quiet_seconds
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ingest")

        self._lock = threading.Lock()
        self._in_flight = 0
        self._queued = 0
        self._completed = 0
        self._failed = 0
        self._last_completed_at: float | None = None
        self._last_event_at: float = time.time()
        self._bm25_pending = False
        self._stop = threading.Event()

        # Coordinator thread for the BM25 quiet-rebuild logic.
        self._coordinator = threading.Thread(
            target=self._coordinator_loop, name="ingest-coordinator", daemon=True
        )
        self._coordinator.start()

    # ── Public API ────────────────────────────────────────────────────────────
    def submit(self, path: Path, source_name: str) -> None:
        """Enqueue a file for indexing. Returns immediately."""
        with self._lock:
            self._queued += 1
            self._last_event_at = time.time()
            self._bm25_pending = True
        self._executor.submit(self._run, IngestJob(path=path, source_name=source_name))

    def status(self) -> QueueStatus:
        with self._lock:
            return QueueStatus(
                in_flight=self._in_flight,
                queued=self._queued,
                completed_total=self._completed,
                failed_total=self._failed,
                last_completed_at=self._last_completed_at,
                bm25_pending=self._bm25_pending,
            )

    def flush(self) -> None:
        """Force a BM25 rebuild now (operator-triggered "Done" button).
        Safe to call any time; if there's no work, it's a no-op."""
        with self._lock:
            need = self._bm25_pending and self._in_flight == 0 and self._queued == 0
        if need:
            self._do_bm25_rebuild()

    def shutdown(self, wait: bool = True) -> None:
        self._stop.set()
        self._executor.shutdown(wait=wait)

    # ── Worker path ───────────────────────────────────────────────────────────
    def _run(self, job: IngestJob) -> None:
        with self._lock:
            self._queued -= 1
            self._in_flight += 1
        try:
            self._index_one(job)
            with self._lock:
                self._completed += 1
                self._last_completed_at = time.time()
        except Exception as exc:
            log.error("Ingest failed for %s: %s", job.source_name, exc, exc_info=True)
            with self._lock:
                self._failed += 1
        finally:
            with self._lock:
                self._in_flight -= 1
                self._last_event_at = time.time()

    def _index_one(self, job: IngestJob) -> None:
        # Local imports keep the module importable without the full app stack.
        from config import make_qdrant_client
        from ingest import ensure_collection, file_hash, ingest_file, load_state, save_state

        client = make_qdrant_client()
        ensure_collection(client)
        n = ingest_file(client, job.path, job.source_name)
        state = load_state()
        state[job.source_name] = file_hash(job.path)
        save_state(state)
        log.info("Indexed %s (%d vectors)", job.source_name, n)

    # ── Coordinator ───────────────────────────────────────────────────────────
    def _coordinator_loop(self) -> None:
        """Sleep, then check whether the queue has been quiet long enough
        to fire BM25. Tight 1s tick — cheap because we mostly do nothing."""
        while not self._stop.is_set():
            time.sleep(1.0)
            with self._lock:
                idle_for = time.time() - self._last_event_at
                ready = (
                    self._bm25_pending
                    and self._in_flight == 0
                    and self._queued == 0
                    and idle_for >= self._quiet_seconds
                )
            if ready:
                self._do_bm25_rebuild()

    def _do_bm25_rebuild(self) -> None:
        try:
            from config import make_qdrant_client
            from ingest import rebuild_bm25
            client = make_qdrant_client()
            t0 = time.time()
            rebuild_bm25(client)
            log.info("BM25 batch rebuild complete in %.1fs", time.time() - t0)
        except Exception as exc:
            log.error("BM25 batch rebuild failed: %s", exc, exc_info=True)
        finally:
            with self._lock:
                self._bm25_pending = False


# Process-wide singleton. Built lazily so the import has no side effects.
_singleton: IngestQueue | None = None
_singleton_lock = threading.Lock()


def get_queue() -> IngestQueue:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                import os
                workers = int(os.environ.get("LOCALLYAI_INGEST_WORKERS", "2"))
                quiet = float(os.environ.get("LOCALLYAI_INGEST_QUIET_SECONDS", "30"))
                _singleton = IngestQueue(max_workers=workers, quiet_seconds=quiet)
                log.info("IngestQueue started: workers=%d quiet=%.0fs", workers, quiet)
    return _singleton

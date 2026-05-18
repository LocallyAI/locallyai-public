"""
inference_gate.py

Bounded concurrency gate for chat completions. Without one, a burst of
N simultaneous users pushes N model contexts into unified memory at the
same time and the host OOMs — exactly the failure mode you do not want
during a client demo or a Monday morning.

The gate is two limits, not one:

    LOCALLYAI_MAX_CONCURRENT_INFERENCE  (default 6)
        How many requests may be EXECUTING inference at any moment.
        For MLX this is also bounded by the dedicated worker thread,
        but we still gate at the API layer so streaming requests
        (which hold the slot for the duration of generation) can't
        pile up unbounded behind the worker.

    LOCALLYAI_INFERENCE_QUEUE_MAX       (default 24)
        How many requests may be WAITING for a slot. Beyond this, the
        gate refuses with 503 + Retry-After. The smart client takes
        503 as a retry-on-peer signal — much better than queuing for
        minutes and timing out.

Why these defaults: a Mac Studio with 64 GB unified memory comfortably
holds ~6 active 7B contexts at full max_tokens without paging. Operators
on bigger boxes can raise the limit; on smaller ones they should lower
it. The queue cap is 4× the inflight cap because in-flight requests
for short prompts complete in a couple of seconds; a 24-deep queue
drains in roughly 8 seconds at full throughput.
"""
from __future__ import annotations
import os, threading, time
from contextlib import contextmanager

_MAX_INFLIGHT = max(1, int(os.environ.get("LOCALLYAI_MAX_CONCURRENT_INFERENCE", "6")))
_MAX_QUEUE    = max(0, int(os.environ.get("LOCALLYAI_INFERENCE_QUEUE_MAX", "24")))

# threading.BoundedSemaphore is the right primitive: chat handlers run on
# FastAPI's sync thread pool, so an asyncio.Semaphore would be wrong. The
# bounded variant raises if release() is called more times than acquire(),
# catching off-by-one bugs in the gate plumbing.
_sem = threading.BoundedSemaphore(_MAX_INFLIGHT)
_state_lock = threading.Lock()
_in_flight  = 0
_queued     = 0
_peak_queue = 0
_total_admitted = 0
_total_rejected = 0


class GateBusy(Exception):
    """Raised when the queue cap is reached; the chat handler turns this
    into a 503 with Retry-After so the smart client retries on a peer."""


@contextmanager
def slot(timeout: float = 30.0):
    """Acquire one inference slot or raise GateBusy if the queue is full.
    Holds the slot for the duration of the with-block; releases on exit
    even when the body raises. `timeout` bounds how long a queued
    request waits — beyond that we treat it as if the queue were full.
    """
    global _in_flight, _queued, _peak_queue, _total_admitted, _total_rejected

    # Reject early if the queue would exceed the cap. Doing the check
    # under _state_lock prevents a thundering herd from all observing
    # _queued < _MAX_QUEUE at the same instant.
    with _state_lock:
        if _queued >= _MAX_QUEUE:
            _total_rejected += 1
            raise GateBusy(
                f"inference queue full ({_queued}/{_MAX_QUEUE}); "
                f"in_flight={_in_flight}/{_MAX_INFLIGHT}")
        _queued += 1
        if _queued > _peak_queue:
            _peak_queue = _queued

    try:
        acquired = _sem.acquire(timeout=timeout)
        if not acquired:
            with _state_lock:
                _total_rejected += 1
            raise GateBusy(
                f"inference slot wait exceeded {timeout:.0f}s; "
                f"in_flight={_in_flight}/{_MAX_INFLIGHT}")
    finally:
        # We're either now holding the semaphore (admitted) or we raised;
        # in both cases this request is no longer queued.
        with _state_lock:
            _queued -= 1

    with _state_lock:
        _in_flight += 1
        _total_admitted += 1
    try:
        yield
    finally:
        with _state_lock:
            _in_flight -= 1
        _sem.release()


def stats() -> dict:
    """Snapshot of the gate state — fed into /admin/monitor and the
    fleet dashboard so operators can see queue pressure live."""
    with _state_lock:
        return {
            "max_inflight":   _MAX_INFLIGHT,
            "max_queue":      _MAX_QUEUE,
            "in_flight":      _in_flight,
            "queued":         _queued,
            "peak_queue":     _peak_queue,
            "total_admitted": _total_admitted,
            "total_rejected": _total_rejected,
        }


def configure_for_tests(*, max_inflight: int, max_queue: int) -> None:
    """Re-initialise the gate for a test run. Production code never calls
    this; the chaos suite uses it to verify queueing behaviour with a
    deliberately tiny inflight cap."""
    global _MAX_INFLIGHT, _MAX_QUEUE, _sem
    global _in_flight, _queued, _peak_queue, _total_admitted, _total_rejected
    _MAX_INFLIGHT = max(1, max_inflight)
    _MAX_QUEUE    = max(0, max_queue)
    _sem = threading.BoundedSemaphore(_MAX_INFLIGHT)
    _in_flight = 0
    _queued = 0
    _peak_queue = 0
    _total_admitted = 0
    _total_rejected = 0

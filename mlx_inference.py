"""
mlx_inference.py
MLX-LM inference backend for Apple Silicon Mac Studio.
Exposes the same interface as the Ollama client used in api.py:
  generate(prompt, model, stream) -> str | Generator[str]

MLX-LM must be installed: pip install mlx-lm
Model loaded once at startup; stays in unified memory.

Threading: MLX binds GPU streams to the thread that creates them. If the
model is loaded on one thread and `mx.eval` runs on another, you get
"RuntimeError: There is no Stream(gpu, N) in current thread." FastAPI
serves sync handlers from a thread pool with no thread affinity, so we
funnel ALL MLX work (load + every generate call) through a single
dedicated worker thread. The worker is started lazily on first use.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import uuid
from typing import Iterator

log = logging.getLogger("mlx_inference")

_model     = None
_tokenizer = None
_lock      = threading.Lock()

# Single dedicated MLX worker thread + a job queue. All MLX calls (load
# and generate) run on this thread so GPU streams stay valid.
_mlx_queue: queue.Queue[tuple] = queue.Queue()
_mlx_thread: threading.Thread | None = None
_mlx_thread_lock = threading.Lock()


def _mlx_worker_loop():
    while True:
        fn, args, kwargs, result_q = _mlx_queue.get()
        try:
            result_q.put(("ok", fn(*args, **kwargs)))
        except BaseException as e:  # noqa: BLE001 — re-raised on caller thread
            result_q.put(("err", e))


def _ensure_worker():
    global _mlx_thread
    with _mlx_thread_lock:
        if _mlx_thread is None or not _mlx_thread.is_alive():
            _mlx_thread = threading.Thread(target=_mlx_worker_loop,
                                           name="mlx-worker", daemon=True)
            _mlx_thread.start()


def _run_on_mlx_thread(fn, *args, **kwargs):
    """Submit fn to the MLX worker thread, block for the result, re-raise
    any exception on the caller's thread."""
    _ensure_worker()
    result_q: queue.Queue[tuple] = queue.Queue(maxsize=1)
    _mlx_queue.put((fn, args, kwargs, result_q))
    status, payload = result_q.get()
    if status == "err":
        raise payload
    return payload

# Model to load — override with env var MLX_MODEL
DEFAULT_MODEL = os.environ.get(
    "MLX_MODEL",
    "mlx-community/Mistral-7B-Instruct-v0.3-4bit"   # safe default; swap for 70B on 256GB
)

def _load_model(model_id: str):
    """Load the MLX model. If a pin file (.model_lock) exists for this
    model, verify the resolved HuggingFace commit matches; warn loudly on
    mismatch (ISO 27001 A.8.30 supply-chain integrity).

    .model_lock format (TOML-ish, hand-written for portability):
        [mlx-community/Llama-3.2-1B-Instruct-4bit]
        commit = "abc123def456..."
        pinned_at = "2026-05-04T..."
    """
    global _model, _tokenizer
    from mlx_lm import load
    log.info(f"Loading MLX model: {model_id}")

    expected_commit = _read_pin(model_id)
    _model, _tokenizer = load(model_id)
    log.info("Model loaded into unified memory")

    if expected_commit:
        actual = _resolve_commit(model_id)
        if actual and actual != expected_commit:
            # Round-2 B10: a warning is not a control. Refuse to start
            # unless the operator explicitly acknowledges the drift.
            # An attacker who poisoned the HF cache (or compromised the
            # upstream account) gets blocked by default; the operator
            # has to re-pin or explicitly opt into the new commit.
            if os.environ.get("LOCALLYAI_MODEL_DRIFT_ACK") != "1":
                raise RuntimeError(
                    f"MODEL INTEGRITY DRIFT: {model_id} pinned to "
                    f"{expected_commit[:12]}… but loaded {actual[:12]}…. "
                    f"Refusing to start. Review the upstream commit, update "
                    f".model_lock if intended, or set LOCALLYAI_MODEL_DRIFT_ACK=1 "
                    f"to acknowledge this load."
                )
            log.warning(
                f"MODEL INTEGRITY DRIFT acknowledged via LOCALLYAI_MODEL_DRIFT_ACK=1: "
                f"{model_id} pinned to {expected_commit[:12]}… loaded {actual[:12]}…."
            )
        elif not actual:
            log.warning(
                f"MODEL INTEGRITY: pin present for {model_id} but commit could not be resolved. "
                f"Run: huggingface-cli show-rev <model> to verify."
            )
        else:
            log.info(f"MODEL INTEGRITY: commit matches pin ({actual[:12]}…)")
    else:
        log.info(
            f"MODEL INTEGRITY: no pin for {model_id}. To pin, write to .model_lock — "
            "see mlx_inference._read_pin docstring."
        )


_PIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".model_lock")


def _read_pin(model_id: str) -> str | None:
    """Tiny TOML-ish parser — avoids adding a tomllib dependency for one file."""
    if not os.path.exists(_PIN_FILE):
        return None
    try:
        section = None
        with open(_PIN_FILE, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if s.startswith("[") and s.endswith("]"):
                    section = s[1:-1].strip()
                    continue
                if section == model_id and s.startswith("commit"):
                    _, _, rhs = s.partition("=")
                    return rhs.strip().strip('"\'')
    except OSError:
        return None
    return None


def _resolve_commit(model_id: str) -> str | None:
    """Return the HuggingFace commit SHA for the local cache of `model_id`.

    huggingface_hub stores resolved snapshots under
    ~/.cache/huggingface/hub/models--<repo>/snapshots/<commit>/. We read the
    `refs/main` file which contains the commit SHA the snapshot resolved to.
    """
    try:
        cache_root = os.path.expanduser(
            os.environ.get("HF_HOME", "~/.cache/huggingface")
        )
        # huggingface_hub layout
        repo_dir = os.path.join(
            cache_root, "hub", f"models--{model_id.replace('/', '--')}"
        )
        ref = os.path.join(repo_dir, "refs", "main")
        if os.path.isfile(ref):
            with open(ref, encoding="utf-8") as f:
                return f.read().strip()
    except OSError:
        pass
    return None

def _ensure_loaded_sync(model_id: str | None = None):
    global _model, _tokenizer
    target = model_id or DEFAULT_MODEL
    with _lock:
        if _model is None:
            _load_model(target)


def ensure_loaded(model_id: str | None = None):
    """Public entry point. Loads on the dedicated MLX worker thread so
    streams and the model are owned by the same thread."""
    _run_on_mlx_thread(_ensure_loaded_sync, model_id)


# Qwen 2.5 / Hermes / Llama-3.x tool-call output convention. The model
# emits `<tool_call>\n{...}\n</tool_call>` blocks (Qwen 2.5 native format,
# also re-used by Hermes and many fine-tunes). Tolerant regex — accepts
# whitespace variations and DOTALL across multi-line JSON arguments.
_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def _parse_tool_calls(text: str) -> tuple[str | None, list[dict] | None]:
    """Extract OpenAI-shaped tool_calls from a raw model output string.

    Returns ``(content_without_tool_blocks, tool_calls_list)``. Either
    side can be None. If no tool blocks are present, returns the original
    text and None — callers should treat the result the same as a plain
    string response.
    """
    if not text or "<tool_call>" not in text:
        return text or None, None
    calls: list[dict] = []
    for raw in _TOOL_CALL_RE.findall(text):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # The model emitted a malformed tool_call block — skip it
            # rather than 500ing. The caller will see only the prose.
            continue
        name = parsed.get("name")
        if not name:
            continue
        # OpenAI requires `arguments` to be a JSON-encoded STRING (not a
        # JSON object). Re-serialise whatever the model emitted.
        args = parsed.get("arguments", {})
        if not isinstance(args, str):
            args = json.dumps(args)
        calls.append({
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {"name": name, "arguments": args},
        })
    # Strip the tool_call blocks from the prose. Whitespace cleanup keeps
    # the residual content readable for the audit-agent.
    residual = _TOOL_CALL_RE.sub("", text).strip()
    return (residual or None), (calls or None)


def _generate_sync(messages, model: str | None, max_tokens: int, temperature: float,
                   tools: list[dict] | None = None,
                   tool_choice: str | dict | None = None):
    """Run a single MLX generation. Returns a dict
    ``{"content": str | None, "tool_calls": list | None}``.

    When ``tools`` is None, ``tool_calls`` will be None and ``content``
    carries the model's text — semantically identical to the pre-tools
    behaviour (string output, just wrapped in a dict). ``tool_choice`` is
    currently advisory: Qwen's chat template honours the presence of
    tools but does not surface a separate tool_choice slot. We forward
    it to apply_chat_template only if the tokenizer signals support, so
    future tokenizer upgrades pick it up automatically.
    """
    _ensure_loaded_sync(model)
    from mlx_lm import generate as mlx_generate
    from mlx_lm.sample_utils import make_logits_processors, make_sampler

    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    fallback_text = messages[-1].get("content", "") if messages else ""
    try:
        template_kwargs: dict = {"tokenize": False, "add_generation_prompt": True}
        if tools:
            # Qwen 2.5's tokenizer supports `tools=` natively — it renders
            # the per-tool JSON schema into the system prompt and primes
            # the <tool_call>...</tool_call> response convention.
            template_kwargs["tools"] = tools
        formatted = _tokenizer.apply_chat_template(messages, **template_kwargs)
    except TypeError:
        # The active tokenizer doesn't recognise the `tools` kwarg.
        # Inline a minimal tools description into the system message so
        # the model still has something to call against, and fall back
        # to the no-kwarg template path so generation proceeds. This
        # keeps audit-agent unblocked on older tokenizers.
        if tools:
            tool_lines = []
            for t in tools:
                fn = (t or {}).get("function") or {}
                tool_lines.append(json.dumps({
                    "name":        fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters":  fn.get("parameters", {}),
                }))
            sys_msg = {
                "role": "system",
                "content": (
                    "You have access to the following tools. To call a tool, "
                    "emit a JSON object inside <tool_call>...</tool_call> "
                    "tags with `name` and `arguments` fields.\n\n"
                    + "\n".join(tool_lines)
                ),
            }
            messages = [sys_msg] + list(messages)
        try:
            formatted = _tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        except Exception:
            formatted = fallback_text
    except Exception:
        formatted = fallback_text

    sampler = make_sampler(temp=temperature, top_p=0.9)
    logits_processors = make_logits_processors(repetition_penalty=1.1)
    text = mlx_generate(
        _model, _tokenizer, prompt=formatted, verbose=False,
        max_tokens=max_tokens,
        sampler=sampler,
        logits_processors=logits_processors,
    )
    if not tools:
        # Preserve the legacy "content is the full text" contract for
        # tool-less callers. content is the raw text, tool_calls is None.
        return {"content": text, "tool_calls": None}
    content, tool_calls = _parse_tool_calls(text)
    return {"content": content, "tool_calls": tool_calls}


def _stream_pump_sync(messages, model: str | None, max_tokens: int,
                      temperature: float, out_q: queue.Queue,
                      abort_event: threading.Event):
    """Run streaming generation on the MLX worker thread, pushing each
    token onto out_q as it is produced. Pushes the sentinel `("done",
    None)` when generation completes; exceptions are pushed as
    `("err", exc)` so the consumer can re-raise on the request thread.

    `abort_event` is the consumer's "I gave up" signal. The consumer
    sets it when its generator is closed (Starlette closes the SSE
    response on client disconnect → GeneratorExit → finally clause).
    The producer checks the flag between every token push so a
    closed consumer halts generation within one polling window
    (≤ 0.5 s) — without this, a full out_q (maxsize=64) wedges the
    sole MLX worker thread and locks out every subsequent request.

    Tokens go to a queue rather than a list so the SSE response can
    pump them out as they arrive — chat feels live, and the user
    can see the answer regenerating after a mid-stream node failover.
    """
    _ensure_loaded_sync(model)
    from mlx_lm import stream_generate
    from mlx_lm.sample_utils import make_logits_processors, make_sampler

    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    fallback_text = messages[-1].get("content", "") if messages else ""
    try:
        formatted = _tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
    except Exception:
        formatted = fallback_text

    sampler = make_sampler(temp=temperature, top_p=0.9)
    logits_processors = make_logits_processors(repetition_penalty=1.1)

    def _put_or_abort(item) -> bool:
        """Block-pushing wrapper that wakes every 0.5 s to re-check
        abort_event. Returns False if the consumer aborted (caller
        should stop generation), True if the put succeeded."""
        while True:
            if abort_event.is_set():
                return False
            try:
                out_q.put(item, timeout=0.5)
                return True
            except queue.Full:
                continue  # re-check abort, then retry put

    try:
        for response in stream_generate(
            _model, _tokenizer, prompt=formatted,
            max_tokens=max_tokens,
            sampler=sampler,
            logits_processors=logits_processors,
        ):
            if not _put_or_abort(("token", response.text)):
                # Consumer is gone. Break out of the mlx_lm generator —
                # GC frees the prompt cache and we exit cleanly.
                break
        else:
            # for-else: ran to completion without an abort.
            _put_or_abort(("done", None))
    except BaseException as e:  # noqa: BLE001
        if not abort_event.is_set():
            try:
                out_q.put(("err", e), timeout=0.5)
            except queue.Full:
                pass


def stream(messages, model: str | None = None,
           max_tokens: int = 2048, temperature: float = 0.1) -> Iterator[str]:
    """Token-by-token generator. Internally schedules a job on the MLX
    worker thread that pushes tokens onto a queue; this generator pumps
    the queue and yields each token to the caller (typically the SSE
    response writer in api.chat). Re-raises worker exceptions on the
    consumer thread.

    Cleanup contract: when the consumer abandons us (Starlette closing
    the SSE response on client disconnect raises GeneratorExit at the
    next yield), the finally clause sets abort_event and drains out_q.
    The producer notices abort_event within one polling window and
    exits the mlx_lm generator, freeing its prompt cache. Without this
    contract a closed consumer wedges the worker thread on a full
    out_q.put() call.
    """
    out_q: queue.Queue = queue.Queue(maxsize=64)
    abort_event = threading.Event()

    _ensure_worker()
    # We bypass _run_on_mlx_thread because we don't want the helper to
    # block on a result_q (the streaming function returns None).
    _mlx_queue.put((
        _stream_pump_sync,
        (messages, model, max_tokens, temperature, out_q, abort_event),
        {},
        queue.Queue(maxsize=1),
    ))

    try:
        while True:
            kind, payload = out_q.get()
            if kind == "token":
                yield payload
            elif kind == "done":
                return
            elif kind == "err":
                raise payload
    finally:
        # Tell the producer to stop, and drain anything queued so the
        # producer's in-flight put() unblocks immediately.
        abort_event.set()
        while True:
            try:
                out_q.get_nowait()
            except queue.Empty:
                break


def generate(messages, model: str | None = None, stream: bool = False,
             max_tokens: int = 2048, temperature: float = 0.1,
             tools: list[dict] | None = None,
             tool_choice: str | dict | None = None):
    """`messages` is the OpenAI-style chat list (system / user / assistant).
    A bare string is still accepted for backwards compatibility with old
    callers and is treated as a single user turn.

    Return shape:
      * stream=True  → Iterator[str] of text tokens (legacy contract).
        Streaming-with-tools is intentionally NOT supported here — the
        chat handler raises 501 before reaching this path. If a future
        caller invokes generate(stream=True, tools=...) directly, the
        tool blocks will simply pass through in the token stream and
        the caller must parse them itself.
      * stream=False → dict ``{"content": str | None,
                               "tool_calls": list | None}``. When
        ``tools`` is None, ``tool_calls`` is None and ``content`` is
        the full text — semantically the same as the pre-tools string
        return, just wrapped in a dict.
    """
    if stream:
        # Streaming path is text-only; tools/tool_choice are ignored.
        # The chat handler refuses stream=True with tools at the route
        # boundary, so we should never reach here with both set.
        return stream_tokens(messages, model, max_tokens, temperature)
    return _run_on_mlx_thread(
        _generate_sync, messages, model, max_tokens, temperature,
        tools=tools, tool_choice=tool_choice)


# Public alias — `stream` shadows the kw-arg name in generate(); callers that
# want token-by-token MLX streaming should use stream_tokens directly.
stream_tokens = stream

def list_models() -> list[dict]:
    """Return model list in OpenAI /v1/models format."""
    return [{"id": DEFAULT_MODEL, "object": "model",
             "owned_by": "locallyai", "backend": "mlx"}]

"""
Tests for OpenAI tools / tool_choice support on /v1/chat/completions.

Covers Task 4 of the api/ refactor: the request models accept the
OpenAI tool-calling schema (tools, tool_choice on the request; tool_calls,
tool_call_id, name, role="tool" on messages) and the chat handler
correctly relays tool_calls returned by _infer into the OpenAI envelope
shape (message.tool_calls + finish_reason="tool_calls").

These tests stub _infer and the inference gate; they DO NOT spin up MLX
or talk to Ollama/LM Studio. The MLX tool-call parsing is unit-tested
separately at the _parse_tool_calls level so we don't need a real model.
"""
from __future__ import annotations

import os

import pytest

# Match the smoke-test pattern: set the audit salt before any LocallyAI
# import. config.py warns at import time without it, polluting test output.
os.environ.setdefault("LOCALLYAI_AUDIT_SALT",  "t" * 64)
os.environ.setdefault("LOCALLYAI_AUDIT_HMAC_KEY", "k" * 64)
os.environ.setdefault("LOCALLYAI_ADMIN_KEY",   "a" * 64)
# Skip the startup gates that refuse boot when these aren't set explicitly
# — for in-process tests with TestClient we want the api package to load
# without forcing a real kill-switch URL.
os.environ.setdefault("LOCALLYAI_ALLOW_INSECURE", "1")
os.environ.setdefault("LOCALLYAI_KILL_SWITCH_URL",
                      "https://example.invalid/status.json")


# ── 1) Schema validation: ChatRequest accepts tools / tool_choice ────────

def test_chat_request_accepts_tools_field():
    from api.chat import ChatRequest, Message
    tools = [{
        "type": "function",
        "function": {
            "name":        "x",
            "description": "y",
            "parameters":  {"type": "object"},
        },
    }]
    req = ChatRequest(
        model="x",
        messages=[Message(role="user", content="hi")],
        tools=tools,
        tool_choice="auto",
    )
    assert req.tools == tools
    assert req.tool_choice == "auto"


def test_chat_request_tool_choice_can_be_object():
    from api.chat import ChatRequest, Message
    tc = {"type": "function", "function": {"name": "x"}}
    req = ChatRequest(
        messages=[Message(role="user", content="hi")],
        tools=[{"type": "function",
                "function": {"name": "x", "parameters": {"type": "object"}}}],
        tool_choice=tc,
    )
    assert req.tool_choice == tc


def test_chat_request_tools_optional_default_unchanged():
    """Pre-existing chat requests must still validate identically — tools
    defaults to None, tool_choice defaults to 'auto' but neither is required."""
    from api.chat import ChatRequest, Message
    req = ChatRequest(messages=[Message(role="user", content="hi")])
    assert req.tools is None
    assert req.tool_choice == "auto"


# ── 2) Schema validation: Message accepts role="tool" + tool fields ──────

def test_message_accepts_role_tool_with_tool_call_id_and_name():
    from api.chat import Message
    m = Message(role="tool", tool_call_id="x", name="y", content="z")
    assert m.role         == "tool"
    assert m.tool_call_id == "x"
    assert m.name         == "y"
    assert m.content      == "z"


def test_message_accepts_assistant_tool_calls_with_none_content():
    """When the assistant emits only tool_calls (no prose), content is None."""
    from api.chat import Message
    m = Message(
        role="assistant",
        content=None,
        tool_calls=[{
            "id":   "call_abc",
            "type": "function",
            "function": {"name": "foo", "arguments": "{}"},
        }],
    )
    assert m.content is None
    assert m.tool_calls and m.tool_calls[0]["id"] == "call_abc"


# ── 3) End-to-end: handler relays tool_calls into the response envelope ──

@pytest.fixture
def client(monkeypatch):
    """Build a TestClient and stub the inference path so the test never
    talks to a real backend. The `with TestClient(...) as c` form is
    required so FastAPI's @app.on_event("startup") handlers actually
    fire — without that, the audit-chain lock fd stays None and the
    handler hits an AttributeError on the first write."""
    from fastapi.testclient import TestClient

    import api as api_mod
    from api import chat as chat_mod

    def _fake_infer(messages, model, stream, max_tokens, temperature,
                    tools=None, tool_choice=None):
        # When the test passes tools, return the canonical tool-call shape.
        # When tools is None, return a plain content dict — same as the
        # real _infer's contract for non-tools requests.
        if tools:
            return {
                "content": None,
                "tool_calls": [{
                    "id":   "c1",
                    "type": "function",
                    "function": {"name": "foo", "arguments": "{}"},
                }],
            }
        return {"content": "ok", "tool_calls": None}

    monkeypatch.setattr(api_mod, "_infer", _fake_infer)
    # Bypass retrieval (avoids spinning up Qdrant for the test).
    monkeypatch.setattr(chat_mod, "_sanitize_chunk", lambda c: c)
    monkeypatch.setattr("retrieval.retrieve", lambda *a, **kw: [])

    with TestClient(api_mod.app) as c:
        yield c


def _post_chat(client, body):
    return client.post(
        "/v1/chat/completions",
        json=body,
        headers={"Authorization": "Bearer " + ("a" * 64)},
    )


def test_handler_relays_tool_calls_and_sets_finish_reason(client):
    body = {
        "messages": [{"role": "user", "content": "call the foo tool please"}],
        "tools": [{
            "type": "function",
            "function": {
                "name":        "foo",
                "description": "calls foo",
                "parameters":  {"type": "object"},
            },
        }],
        "tool_choice": "auto",
        "max_tokens": 32,
    }
    r = _post_chat(client, body)
    assert r.status_code == 200, r.text
    payload = r.json()
    choice = payload["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    message = choice["message"]
    assert message["role"] == "assistant"
    assert message.get("content") is None
    assert message["tool_calls"][0]["function"]["name"] == "foo"
    assert message["tool_calls"][0]["function"]["arguments"] == "{}"


def test_handler_without_tools_uses_stop_finish_reason(client):
    """Regression guard: pre-existing tool-less chat requests must behave
    identically to before this feature landed."""
    body = {
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 8,
    }
    r = _post_chat(client, body)
    assert r.status_code == 200, r.text
    payload = r.json()
    choice = payload["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["role"] == "assistant"
    assert choice["message"]["content"] == "ok"
    assert "tool_calls" not in choice["message"]


def test_handler_string_returning_infer_still_works(client, monkeypatch):
    """ha_chaos.py compat: when a test monkey-patches _infer with a stub
    that returns a plain string (the old contract), the handler must
    coerce it back into the dict envelope rather than 500ing."""
    import api as api_mod

    def _string_infer(messages, model, stream, max_tokens, temperature,
                      tools=None, tool_choice=None):
        return "plain-string-answer"

    monkeypatch.setattr(api_mod, "_infer", _string_infer)
    r = _post_chat(client, {
        "messages": [{"role": "user", "content": "x"}],
        "max_tokens": 8,
    })
    assert r.status_code == 200, r.text
    msg = r.json()["choices"][0]["message"]
    assert msg["content"] == "plain-string-answer"
    assert "tool_calls" not in msg


def test_handler_streaming_with_tools_returns_501(client):
    """Streaming + tools is intentionally not implemented; the handler
    must refuse rather than silently drop tool calls from the stream."""
    r = _post_chat(client, {
        "messages": [{"role": "user", "content": "x"}],
        "tools": [{"type": "function",
                   "function": {"name": "f", "parameters": {"type": "object"}}}],
        "stream": True,
    })
    assert r.status_code == 501, r.text


# ── 4) Unit: MLX tool-call parser handles the Qwen 2.5 wire format ───────

def test_mlx_parse_tool_calls_extracts_qwen_format():
    """Direct unit test of the parser used inside _generate_sync. Avoids
    importing mlx_lm so the test runs on Ubuntu CI without MLX deps."""
    try:
        from mlx_inference import _parse_tool_calls
    except ImportError:
        pytest.skip("mlx_inference not importable (no MLX deps)")
    raw = (
        "Sure, I'll call the tool.\n"
        '<tool_call>\n{"name": "get_weather", "arguments": {"city": "Riyadh"}}\n</tool_call>\n'
        "Done."
    )
    content, calls = _parse_tool_calls(raw)
    assert calls is not None and len(calls) == 1
    call = calls[0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "get_weather"
    # OpenAI requires arguments to be a STRING, not a dict.
    assert isinstance(call["function"]["arguments"], str)
    assert call["function"]["arguments"] == '{"city": "Riyadh"}'
    assert call["id"].startswith("call_")
    # Residual prose stripped of tool blocks.
    assert "Sure" in content and "Done" in content
    assert "<tool_call>" not in content


def test_mlx_parse_tool_calls_no_tools_returns_text_unchanged():
    try:
        from mlx_inference import _parse_tool_calls
    except ImportError:
        pytest.skip("mlx_inference not importable (no MLX deps)")
    content, calls = _parse_tool_calls("plain text answer")
    assert content == "plain text answer"
    assert calls is None

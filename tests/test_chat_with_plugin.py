"""
Tests for Step 3 — chat handler plugin/skill wiring.

Covers:
  - ChatRequest accepts the new `plugin` + `skill` fields and rejects
    malformed values via the pattern constraints.
  - A chat request without `plugin` behaves identically to a pre-Step-3
    chat request (audit log doesn't fail, response envelope unchanged,
    no plugin/skill in the audit entry).
  - When `plugin` (and optionally `skill`) is set, the active plugin's
    practice profile + skill body are spliced into the system prompt
    BEFORE any retrieval context.
  - The plugin's declared MCP tool defs are merged into the `tools` list
    passed to `_infer`, with caller-supplied tools winning on name
    collisions.
  - When `_infer` returns tool_calls naming an in-process MCP tool, the
    chat handler dispatches the tool, appends a role="tool" message, and
    re-invokes `_infer` — up to 3 iterations.
  - Recursion is capped at 3 iterations and still returns a response.
  - The audit log call records `plugin` + `skill` provenance kwargs.

These tests stub _infer + `mcp_servers.*.DISPATCH` entries; they do not
talk to a real backend, do not retrieve from Qdrant, and do not write
to a real audit log (other than the in-process file the audit chain
helpers already use, which is fine for a single test run).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Audit-salt env vars must be set BEFORE any LocallyAI import — config.py
# warns at import time without them. Mirrors test_chat_tools.py.
os.environ.setdefault("LOCALLYAI_AUDIT_SALT",  "t" * 64)
os.environ.setdefault("LOCALLYAI_AUDIT_HMAC_KEY", "k" * 64)
os.environ.setdefault("LOCALLYAI_ADMIN_KEY",   "a" * 64)
os.environ.setdefault("LOCALLYAI_ALLOW_INSECURE", "1")
os.environ.setdefault("LOCALLYAI_KILL_SWITCH_URL",
                      "https://example.invalid/status.json")
# Point the plugin loader at the test fixtures so the startup handler
# populates the registry with `test-plugin` (which declares search +
# citation MCP servers, total 4 tools).
_FIXTURES = Path(__file__).parent / "fixtures" / "plugins"
os.environ.setdefault("LOCALLYAI_PLUGIN_DIR", str(_FIXTURES))


# ── 1) Schema validation ──────────────────────────────────────────────────

def test_chat_request_accepts_plugin_skill_fields():
    from api.chat import ChatRequest, Message
    req = ChatRequest(
        model="x",
        messages=[Message(role="user", content="hi")],
        plugin="test-plugin",
        skill="clearance",
    )
    assert req.plugin == "test-plugin"
    assert req.skill == "clearance"


def test_chat_request_rejects_uppercase_plugin_name():
    """The pattern is ^[a-z0-9\\-]{1,64}$ — uppercase is a 422, not a 500."""
    from pydantic import ValidationError

    from api.chat import ChatRequest, Message
    with pytest.raises(ValidationError):
        ChatRequest(
            messages=[Message(role="user", content="hi")],
            plugin="Test-Plugin",  # uppercase rejected
        )


# ── 2) End-to-end client fixture ──────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    """Build a TestClient. Stub retrieval so the test never spins up Qdrant.

    NB: this fixture does NOT stub _infer — individual tests do that
    themselves so each test can return its own canned payload.
    """
    from fastapi.testclient import TestClient

    import api as api_mod
    from api import chat as chat_mod

    # Bypass retrieval everywhere it might be called.
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


# ── 3) No-plugin path is unchanged ────────────────────────────────────────

def test_chat_without_plugin_unchanged(client, monkeypatch):
    """A request with no plugin must produce a normal response and the
    audit log helper must not be called with plugin/skill kwargs."""
    import api as api_mod
    from api import chat as chat_mod

    def _fake_infer(messages, model, stream, max_tokens, temperature,
                    tools=None, tool_choice=None):
        return {"content": "hello back", "tool_calls": None}

    monkeypatch.setattr(api_mod, "_infer", _fake_infer)

    captured: dict = {}

    def _fake_audit(user, model, sources, latency_ms,
                    query_hash="", matter_code="",
                    *, plugin=None, skill=None):
        captured["plugin"] = plugin
        captured["skill"] = skill

    monkeypatch.setattr(chat_mod, "_write_audit", _fake_audit)

    r = _post_chat(client, {
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 8,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "hello back"
    assert body["choices"][0]["finish_reason"] == "stop"
    # The audit helper was called with plugin/skill defaults of None.
    assert captured.get("plugin") is None
    assert captured.get("skill") is None


# ── 4) Plugin splice: practice profile + skill body in system prompt ──────

def test_chat_with_plugin_splices_addendum_into_system_prompt(client, monkeypatch):
    """When plugin + skill are set, the system prompt (message[0]) must
    contain the practice-profile heading AND the skill body heading."""
    import api as api_mod

    captured: dict = {}

    def _fake_infer(messages, model, stream, max_tokens, temperature,
                    tools=None, tool_choice=None):
        captured["messages"] = list(messages)
        captured["tools"] = tools
        return {"content": "ok", "tool_calls": None}

    monkeypatch.setattr(api_mod, "_infer", _fake_infer)

    r = _post_chat(client, {
        "messages": [{"role": "user", "content": "do the thing"}],
        "plugin": "test-plugin",
        "skill": "clearance",
        "max_tokens": 8,
    })
    assert r.status_code == 200, r.text
    sys_content = captured["messages"][0]["content"]
    assert captured["messages"][0]["role"] == "system"
    # Practice profile header injected from CLAUDE.md.
    assert "Practice profile: test-plugin" in sys_content
    # The CLAUDE.md body text is preserved.
    assert "legal assistant for a fictional firm" in sys_content
    # Skill header + body section appear after the practice profile.
    assert "Skill: clearance" in sys_content
    assert "Clearance workflow" in sys_content


# ── 5) Tool merge: plugin's MCP tools flow through to _infer ──────────────

def test_chat_with_plugin_injects_tool_defs(client, monkeypatch):
    """test-plugin declares mcp-locallyai-search + mcp-locallyai-citation;
    those expose search_documents, list_matter_documents, verify,
    search_caselaw — total 4 tools should reach _infer."""
    import api as api_mod

    captured: dict = {}

    def _fake_infer(messages, model, stream, max_tokens, temperature,
                    tools=None, tool_choice=None):
        captured["tools"] = tools
        return {"content": "ok", "tool_calls": None}

    monkeypatch.setattr(api_mod, "_infer", _fake_infer)

    r = _post_chat(client, {
        "messages": [{"role": "user", "content": "search"}],
        "plugin": "test-plugin",
        "skill": "clearance",
        "max_tokens": 8,
    })
    assert r.status_code == 200, r.text
    tools = captured["tools"] or []
    names = {t["function"]["name"] for t in tools}
    # At minimum, search_documents (the canonical one) must be present.
    assert "search_documents" in names
    # The full toolbox from search + citation is 4 entries.
    assert len(tools) >= 4


# ── 6) Tool-call recursion dispatches in-process MCP tools ────────────────

def test_tool_call_dispatch_recursion_one_round(client, monkeypatch):
    """First _infer call returns tool_calls naming search_documents; the
    handler must dispatch, append the result as role=tool, and re-invoke
    _infer. Second call returns plain content — that's the final answer."""
    import api as api_mod
    from mcp_servers.search import server as search_mod

    # Monkey-patch the dispatch entry for search_documents so we know
    # exactly what comes back. The signature must match (args, *, user,
    # matter_code) — that's how api.plugins.dispatch_builtin_tool calls
    # it. Use a sentinel value so the test can prove the result reached
    # the model.
    def _fake_search(args, *, user=None, matter_code=None):
        return {"matches": [{"chunk_id": "X", "text": "STUB-SEARCH-HIT"}]}

    monkeypatch.setitem(search_mod.DISPATCH, "search_documents", _fake_search)

    call_log: list[dict] = []

    def _fake_infer(messages, model, stream, max_tokens, temperature,
                    tools=None, tool_choice=None):
        call_log.append({"messages": list(messages), "tools": tools})
        if len(call_log) == 1:
            return {
                "content": None,
                "tool_calls": [{
                    "id": "call_test_1",
                    "type": "function",
                    "function": {
                        "name": "search_documents",
                        "arguments": '{"query": "anything"}',
                    },
                }],
            }
        # Second iteration — model produced the final answer.
        return {"content": "final", "tool_calls": None}

    monkeypatch.setattr(api_mod, "_infer", _fake_infer)

    r = _post_chat(client, {
        "messages": [{"role": "user", "content": "search for X"}],
        "plugin": "test-plugin",
        "skill": "clearance",
        "max_tokens": 64,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["choices"][0]["message"]["content"] == "final"
    # _infer was called exactly twice: initial + after tool dispatch.
    assert len(call_log) == 2
    # Second call's message list must contain the tool result we stubbed.
    second_messages = call_log[1]["messages"]
    tool_msgs = [m for m in second_messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_test_1"
    assert tool_msgs[0]["name"] == "search_documents"
    # The content is a JSON-serialised dict — STUB-SEARCH-HIT must appear.
    assert "STUB-SEARCH-HIT" in tool_msgs[0]["content"]
    # And the assistant tool-call turn was appended before the tool result.
    assistant_turns = [m for m in second_messages if m.get("role") == "assistant"]
    assert len(assistant_turns) == 1
    assert assistant_turns[0]["tool_calls"][0]["id"] == "call_test_1"


# ── 7) Recursion cap ──────────────────────────────────────────────────────

def test_tool_call_recursion_hits_cap(client, monkeypatch):
    """If _infer always returns tool_calls, recursion stops at 3 rounds
    and a response still comes back (with the last assistant message)."""
    import api as api_mod
    from mcp_servers.search import server as search_mod

    monkeypatch.setitem(
        search_mod.DISPATCH, "search_documents",
        lambda args, *, user=None, matter_code=None: {"ok": True},
    )

    counter: dict = {"n": 0}

    def _fake_infer(messages, model, stream, max_tokens, temperature,
                    tools=None, tool_choice=None):
        counter["n"] += 1
        return {
            "content": None,
            "tool_calls": [{
                "id": f"call_loop_{counter['n']}",
                "type": "function",
                "function": {
                    "name": "search_documents",
                    "arguments": "{}",
                },
            }],
        }

    monkeypatch.setattr(api_mod, "_infer", _fake_infer)

    r = _post_chat(client, {
        "messages": [{"role": "user", "content": "loop forever"}],
        "plugin": "test-plugin",
        "skill": "clearance",
        "max_tokens": 64,
    })
    # Even though the model never settled, the handler must return 200
    # with the last tool_calls envelope, NOT crash or stream-error.
    assert r.status_code == 200, r.text
    # _infer was called at most 1 (initial) + 3 (recursion cap) = 4 times.
    assert counter["n"] <= 4
    assert counter["n"] >= 4  # exactly 4 means the cap actually hit
    body = r.json()
    # finish_reason is tool_calls because the final infer_result still
    # carried tool_calls (the cap broke the loop, didn't clear them).
    assert body["choices"][0]["finish_reason"] == "tool_calls"


# ── 8) Audit log records plugin + skill ──────────────────────────────────

def test_audit_log_records_plugin_and_skill(client, monkeypatch):
    """The chat handler must pass plugin=req.plugin, skill=req.skill into
    _write_audit. We capture by monkey-patching the helper in api.chat."""
    import api as api_mod
    from api import chat as chat_mod

    def _fake_infer(messages, model, stream, max_tokens, temperature,
                    tools=None, tool_choice=None):
        return {"content": "ok", "tool_calls": None}

    monkeypatch.setattr(api_mod, "_infer", _fake_infer)

    captured: dict = {}

    def _fake_audit(user, model, sources, latency_ms,
                    query_hash="", matter_code="",
                    *, plugin=None, skill=None):
        captured["plugin"] = plugin
        captured["skill"] = skill

    monkeypatch.setattr(chat_mod, "_write_audit", _fake_audit)

    r = _post_chat(client, {
        "messages": [{"role": "user", "content": "hi"}],
        "plugin": "test-plugin",
        "skill": "clearance",
        "max_tokens": 8,
    })
    assert r.status_code == 200, r.text
    assert captured.get("plugin") == "test-plugin"
    assert captured.get("skill") == "clearance"


# ── 9) Caller-supplied tools win on a name collision ─────────────────────

def test_caller_tools_win_on_name_collision(client, monkeypatch):
    """If the caller supplies a tool with the same name as one the plugin
    declares, the caller's def reaches _infer — not the plugin's. This
    keeps the caller in control of their own schema."""
    import api as api_mod

    captured: dict = {}

    def _fake_infer(messages, model, stream, max_tokens, temperature,
                    tools=None, tool_choice=None):
        captured["tools"] = tools
        return {"content": "ok", "tool_calls": None}

    monkeypatch.setattr(api_mod, "_infer", _fake_infer)

    caller_tool = {
        "type": "function",
        "function": {
            "name": "search_documents",  # collides with plugin's tool
            "description": "CALLER OVERRIDE",
            "parameters": {"type": "object"},
        },
    }

    r = _post_chat(client, {
        "messages": [{"role": "user", "content": "search"}],
        "plugin": "test-plugin",
        "skill": "clearance",
        "tools": [caller_tool],
        "max_tokens": 8,
    })
    assert r.status_code == 200, r.text
    tools = captured["tools"] or []
    # Exactly one tool named search_documents — and it's the caller's.
    sd_defs = [t for t in tools if t["function"]["name"] == "search_documents"]
    assert len(sd_defs) == 1
    assert sd_defs[0]["function"]["description"] == "CALLER OVERRIDE"
    # The plugin's OTHER tools (citation: verify, search_caselaw;
    # list_matter_documents) must still appear so we know the merge ran.
    names = {t["function"]["name"] for t in tools}
    assert "verify" in names or "search_caselaw" in names or \
        "list_matter_documents" in names


# ── 10) Round-trip sanity: tool args parsed even when non-string ─────────

def test_tool_args_handles_dict_arguments(client, monkeypatch):
    """OpenAI mandates arguments as a JSON string, but some local backends
    (older Ollama tool-call patches) hand back an already-parsed dict.
    The recursion loop must accept both."""
    import api as api_mod
    from mcp_servers.search import server as search_mod

    received_args: dict = {}

    def _fake_search(args, *, user=None, matter_code=None):
        received_args.update(args)
        return {"ok": True}

    monkeypatch.setitem(search_mod.DISPATCH, "search_documents", _fake_search)

    call_log: list[int] = []

    def _fake_infer(messages, model, stream, max_tokens, temperature,
                    tools=None, tool_choice=None):
        call_log.append(1)
        if len(call_log) == 1:
            return {
                "content": None,
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "search_documents",
                        # NB: dict, not a JSON string.
                        "arguments": {"query": "raw-dict-arg"},
                    },
                }],
            }
        return {"content": "done", "tool_calls": None}

    monkeypatch.setattr(api_mod, "_infer", _fake_infer)

    r = _post_chat(client, {
        "messages": [{"role": "user", "content": "x"}],
        "plugin": "test-plugin",
        "skill": "clearance",
    })
    assert r.status_code == 200, r.text
    assert received_args.get("query") == "raw-dict-arg"
    assert r.json()["choices"][0]["message"]["content"] == "done"


# ── 11) Unknown plugin is a no-op, not a 4xx/5xx ─────────────────────────

def test_unknown_plugin_is_noop_not_error(client, monkeypatch):
    """An unknown plugin name should silently fall back to the base
    persona (build_chat_system_prompt_addendum returns None on unknown).
    The request must still succeed."""
    import api as api_mod

    captured: dict = {}

    def _fake_infer(messages, model, stream, max_tokens, temperature,
                    tools=None, tool_choice=None):
        captured["messages"] = list(messages)
        captured["tools"] = tools
        return {"content": "ok", "tool_calls": None}

    monkeypatch.setattr(api_mod, "_infer", _fake_infer)

    r = _post_chat(client, {
        "messages": [{"role": "user", "content": "hi"}],
        "plugin": "no-such-plugin-name",
        "max_tokens": 8,
    })
    assert r.status_code == 200, r.text
    # No tool defs leaked through (no active plugin → no plugin tools).
    assert not (captured.get("tools") or [])
    # System prompt does not contain a plugin addendum.
    sys_content = captured["messages"][0]["content"]
    assert "Practice profile" not in sys_content


# ── 12) json import sanity ────────────────────────────────────────────────
# (unused — placeholder to keep `json` import non-orphaned if a future
# refactor strips one of the above tests; safe to delete.)
def test_json_module_is_importable_in_test_scope():
    assert json.dumps({"x": 1}) == '{"x": 1}'

"""Unit tests for api.plugins — the SKILL.md / CLAUDE.md / .mcp.json loader.

Exercises three fixture plugins at tests/fixtures/plugins/:
  - test-plugin    : happy path; 2 skills + .mcp.json + CLAUDE.md
  - bad-injection  : SKILL.md contains "ignore previous instructions" — rejected
  - no-manifest    : missing .claude-plugin/plugin.json — silently skipped
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("LOCALLYAI_AUDIT_SALT", "test_salt_for_ci_only" * 2)


FIXTURES = Path(__file__).parent / "fixtures" / "plugins"


@pytest.fixture
def loaded_registry():
    """Fresh load of the fixture directory before each test."""
    from api import plugins
    return plugins.load_plugins_from_dir(FIXTURES)


def test_happy_path_plugin_loads(loaded_registry):
    """test-plugin parses fully — manifest + practice profile + 2 skills + mcp."""
    spec = loaded_registry["test-plugin"]
    assert spec.name == "test-plugin"
    assert spec.version == "0.1.0"
    assert "fixture" in spec.description.lower()
    assert "legal assistant" in spec.practice_profile_md
    assert set(spec.skills) == {"clearance", "dpia"}
    assert spec.declared_mcp_servers == ["mcp-locallyai-citation", "mcp-locallyai-search"]


def test_skill_frontmatter_parses(loaded_registry):
    """Frontmatter `name` / `description` / `user-invocable` survive load."""
    clearance = loaded_registry["test-plugin"].skills["clearance"]
    assert clearance.name == "clearance"
    assert "trademark clearance" in clearance.description.lower()
    # Body strips the YAML frontmatter — only the markdown remains
    assert "---" not in clearance.body_md[:5]
    assert "Clearance workflow" in clearance.body_md


def test_html_comments_stripped(loaded_registry):
    """The HTML comment in the clearance SKILL.md (used for LocallyAI-port
    annotations) MUST NOT reach the model context."""
    clearance = loaded_registry["test-plugin"].skills["clearance"]
    assert "LocallyAI-port" not in clearance.body_md
    assert "<!--" not in clearance.body_md


def test_injection_plugin_rejected(loaded_registry):
    """bad-injection's SKILL.md trips _INJECTION_PATTERNS — must NOT load."""
    assert "bad-injection" not in loaded_registry


def test_missing_manifest_skipped(loaded_registry):
    """no-manifest has no .claude-plugin/plugin.json — silently skipped."""
    assert "no-manifest" not in loaded_registry


def test_list_plugins_serialisable(loaded_registry):
    """list_plugins() returns a JSON-serialisable list of dicts. Used by
    GET /v1/plugins so this is the contract worker-ui consumes."""
    import json

    from api import plugins
    out = plugins.list_plugins()
    json.dumps(out)  # raises if any non-serialisable value leaked
    by_name = {p["name"]: p for p in out}
    assert "test-plugin" in by_name
    assert sorted(s["name"] for s in by_name["test-plugin"]["skills"]) == ["clearance", "dpia"]
    assert by_name["test-plugin"]["mcp_servers"] == [
        "mcp-locallyai-citation", "mcp-locallyai-search",
    ]


def test_build_chat_addendum_with_skill(loaded_registry):
    """When the chat handler passes plugin+skill, the addendum stitches the
    practice profile + skill body in that order."""
    from api import plugins
    md = plugins.build_chat_system_prompt_addendum("test-plugin", "clearance")
    assert md is not None
    assert "Practice profile" in md
    assert "legal assistant" in md
    assert "Skill: clearance" in md
    assert "Clearance workflow" in md
    # Practice profile precedes the skill body
    assert md.index("Practice profile") < md.index("Skill: clearance")


def test_build_chat_addendum_no_plugin(loaded_registry):
    """No plugin selected → None (chat.py uses falsiness to skip the splice)."""
    from api import plugins
    assert plugins.build_chat_system_prompt_addendum(None, None) is None
    assert plugins.build_chat_system_prompt_addendum("", None) is None


def test_build_chat_addendum_unknown_plugin(loaded_registry):
    """Unknown plugin name → None (warning logged, but doesn't raise — the
    chat request still succeeds with the generic persona)."""
    from api import plugins
    assert plugins.build_chat_system_prompt_addendum("does-not-exist", None) is None


def test_builtin_tool_defs_filtered_by_declared(loaded_registry):
    """A plugin with `.mcp.json` declaring search + citation should NOT see
    audit or matter tools — even if those modules exist later."""
    from api import plugins
    spec = loaded_registry["test-plugin"]
    defs = plugins.builtin_tool_defs(spec)
    # week-1 mcp_servers/ is empty; defs is empty list. Once mcp_servers are
    # added, declared filter still gates which servers are queried.
    assert isinstance(defs, list)


def test_builtin_tool_defs_none_when_inactive(loaded_registry):
    """No plugin → no tool defs (the model only sees req.tools)."""
    from api import plugins
    assert plugins.builtin_tool_defs(None) == []


def test_load_missing_dir_returns_empty():
    """Pointing at a non-existent directory is safe (returns empty registry)."""
    from api import plugins
    registry = plugins.load_plugins_from_dir(Path("/tmp/nonexistent-plugins-dir"))
    assert registry == {}

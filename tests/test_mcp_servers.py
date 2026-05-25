"""Unit tests for the 4 in-process MCP servers (mcp_servers/{search,
audit,matter,citation}).

Pattern mirrors `tests/test_plugins_loader.py`: each server gets a
fixture that imports the module + returns its (TOOL_DEFS, DISPATCH)
tuple, and per-tool we exercise at least one happy path via
monkeypatch-stubbed primitives so the tests don't depend on Qdrant /
the live audit log / network access.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("LOCALLYAI_AUDIT_SALT", "t" * 32)
os.environ.setdefault("LOCALLYAI_AUDIT_HMAC_KEY", "k" * 32)


# ── Shared helpers ────────────────────────────────────────────────────────


def _assert_tool_def_shape(d: dict) -> None:
    """Every tool def must match the OpenAI function-call spec the chat
    handler hands to the model."""
    assert d.get("type") == "function"
    fn = d.get("function")
    assert isinstance(fn, dict)
    assert isinstance(fn.get("name"), str) and fn["name"]
    assert isinstance(fn.get("description"), str) and fn["description"]
    params = fn.get("parameters")
    assert isinstance(params, dict)
    assert params.get("type") == "object"
    assert isinstance(params.get("properties"), dict)
    assert isinstance(params.get("required"), list)


def _assert_dispatch_matches(tool_defs: list[dict], dispatch: dict) -> None:
    names = {d["function"]["name"] for d in tool_defs}
    assert names == set(dispatch.keys()), (
        f"TOOL_DEFS names {names} != DISPATCH keys {set(dispatch.keys())}"
    )
    for fn in dispatch.values():
        assert callable(fn)


# ── search server ────────────────────────────────────────────────────────


@pytest.fixture
def search_mod():
    from mcp_servers.search import server
    return server


def test_search_shape(search_mod):
    assert isinstance(search_mod.TOOL_DEFS, list) and search_mod.TOOL_DEFS
    for d in search_mod.TOOL_DEFS:
        _assert_tool_def_shape(d)
    _assert_dispatch_matches(search_mod.TOOL_DEFS, search_mod.DISPATCH)


def test_search_documents_passes_through(search_mod, monkeypatch):
    """Stub retrieval.retrieve and assert the wrapper forwards args + shapes."""
    captured: dict = {}

    def fake_retrieve(query, user=None, matter_code=None):
        captured["query"] = query
        captured["user"] = user
        captured["matter_code"] = matter_code
        return [{"chunk_id": "c1", "text": "hello", "source": "doc.pdf",
                 "score": 0.9, "section": "I", "page": 1}]

    monkeypatch.setattr("mcp_servers.search.server.retrieval.retrieve",
                        fake_retrieve)
    out = search_mod.DISPATCH["search_documents"](
        {"query": "test", "matter_code": "M-1", "k": 3},
        user="alice", matter_code="M-fallback",
    )
    assert out["count"] == 1
    assert out["results"][0]["chunk_id"] == "c1"
    assert captured["user"] == "alice"
    # Caller-supplied matter_code wins over the request-level fallback.
    assert captured["matter_code"] == "M-1"


def test_search_documents_falls_back_to_request_matter(search_mod, monkeypatch):
    captured: dict = {}

    def fake_retrieve(query, user=None, matter_code=None):
        captured["matter_code"] = matter_code
        return []

    monkeypatch.setattr("mcp_servers.search.server.retrieval.retrieve",
                        fake_retrieve)
    search_mod.DISPATCH["search_documents"](
        {"query": "x"}, user="bob", matter_code="M-2",
    )
    assert captured["matter_code"] == "M-2"


def test_list_matter_documents_scans_acls(search_mod, monkeypatch):
    monkeypatch.setattr("mcp_servers.search.server.doc_acls.list_acls",
                        lambda: {
                            "a.pdf": {"matter_code": "M-1",
                                      "allowed_users": ["alice"]},
                            "b.pdf": {"matter_code": "M-2",
                                      "allowed_users": ["*"]},
                            "c.pdf": {"matter_code": "M-1",
                                      "allowed_users": ["alice", "bob"]},
                        })
    out = search_mod.DISPATCH["list_matter_documents"](
        {"matter_code": "M-1"}, user="alice",
    )
    names = sorted(d["display_name"] for d in out["documents"])
    assert names == ["a.pdf", "c.pdf"]
    assert out["count"] == 2


# ── audit server ────────────────────────────────────────────────────────


@pytest.fixture
def audit_mod(tmp_path, monkeypatch):
    """Point the audit server at a tmp_path log and seed 3 entries."""
    log_path = tmp_path / "audit.log"
    entries = [
        {"timestamp": "2026-05-20T09:00:00Z", "user_hash": "abc123",
         "model": "qwen-2.5", "matter_code": "M-1", "backend": "mlx"},
        {"timestamp": "2026-05-20T10:00:00Z", "user_hash": "def456",
         "model": "-", "matter_code": "M-1", "backend": ""},
        {"timestamp": "2026-05-20T11:00:00Z", "user_hash": "abc123",
         "model": "qwen-2.5", "matter_code": "M-2", "backend": "mlx"},
    ]
    log_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n",
                        encoding="utf-8")
    from api import _shared
    monkeypatch.setattr(_shared, "AUDIT_LOG", log_path)
    from mcp_servers.audit import server
    return server


def test_audit_shape(audit_mod):
    assert len(audit_mod.TOOL_DEFS) == 4
    for d in audit_mod.TOOL_DEFS:
        _assert_tool_def_shape(d)
    _assert_dispatch_matches(audit_mod.TOOL_DEFS, audit_mod.DISPATCH)


def test_audit_log_search_finds_matter(audit_mod):
    out = audit_mod.DISPATCH["log_search"](
        {"query": "M-1", "max_results": 10}, user="admin",
    )
    assert out["count"] == 2
    codes = {e["matter_code"] for e in out["results"]}
    assert codes == {"M-1"}


def test_audit_log_search_empty_query_returns_all(audit_mod):
    out = audit_mod.DISPATCH["log_search"](
        {"query": "", "max_results": 10}, user="admin",
    )
    assert out["count"] == 3
    # Newest-first
    assert out["results"][0]["timestamp"] == "2026-05-20T11:00:00Z"


def test_audit_time_range_query_filters(audit_mod):
    out = audit_mod.DISPATCH["time_range_query"](
        {"start": "2026-05-20T09:30:00Z",
         "end":   "2026-05-20T10:30:00Z"},
        user="admin",
    )
    assert out["count"] == 1
    assert out["results"][0]["user_hash"] == "def456"


def test_audit_summary_stats_user(audit_mod):
    out = audit_mod.DISPATCH["summary_stats"](
        {"group_by": "user"}, user="admin",
    )
    assert out["total_events"] == 3
    bk = {b["key"]: b["count"] for b in out["buckets"]}
    # `abc123` appears twice, `def456` once
    assert bk["abc123"] == 2
    assert bk["def456"] == 1


# ── matter server ────────────────────────────────────────────────────────


@pytest.fixture
def matter_mod(tmp_path, monkeypatch):
    """Stand up a synthetic audit log + ACL store + sidecar dir for the
    matter server. Each test gets a fresh tmp_path + cache reset."""
    log_path = tmp_path / "audit.log"
    entries = [
        {"timestamp": "2026-05-20T09:00:00Z", "user_hash": "abcdef1234567890",
         "model": "qwen-2.5", "matter_code": "M-1", "backend": "mlx"},
        {"timestamp": "2026-05-20T10:00:00Z", "user_hash": "0011223344556677",
         "model": "qwen-2.5", "matter_code": "M-1", "backend": "mlx"},
        {"timestamp": "2026-05-20T11:00:00Z", "user_hash": "abcdef1234567890",
         "model": "qwen-2.5", "matter_code": "M-2", "backend": "mlx"},
    ]
    log_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n",
                        encoding="utf-8")
    from api import _shared
    monkeypatch.setattr(_shared, "AUDIT_LOG", log_path)

    # Redirect SHARED_DIR + the sidecar file at the tmp_path
    shared = tmp_path / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    from mcp_servers.matter import server
    monkeypatch.setattr(server, "SHARED_DIR", shared)
    monkeypatch.setattr(server, "_META_FILE", shared / "matters_meta.json")

    monkeypatch.setattr(server.doc_acls, "list_acls", lambda: {
        "a.pdf": {"matter_code": "M-1", "allowed_users": ["alice"]},
        "b.pdf": {"matter_code": "M-1", "allowed_users": ["*"]},
        "c.pdf": {"matter_code": "M-3", "allowed_users": ["alice"]},
    })

    # Wipe the module-level cache so tests don't leak state.
    server._CACHE = None
    server._CACHE_ROOFTIME = 0.0
    return server


def test_matter_shape(matter_mod):
    assert len(matter_mod.TOOL_DEFS) == 3
    for d in matter_mod.TOOL_DEFS:
        _assert_tool_def_shape(d)
    _assert_dispatch_matches(matter_mod.TOOL_DEFS, matter_mod.DISPATCH)


def test_matter_list_matters_unions_acls_and_audit(matter_mod):
    out = matter_mod.DISPATCH["list_matters"]({}, user="admin")
    by_code = {m["matter_code"]: m for m in out["matters"]}
    # M-1 surfaces both via ACLs (2 docs) and audit (2 events)
    assert by_code["M-1"]["doc_count"] == 2
    assert by_code["M-1"]["audit_event_count"] == 2
    # M-2 is audit-only
    assert by_code["M-2"]["doc_count"] == 0
    assert by_code["M-2"]["audit_event_count"] == 1
    # M-3 is ACL-only
    assert by_code["M-3"]["doc_count"] == 1
    assert by_code["M-3"]["audit_event_count"] == 0


def test_matter_describe_and_get_roundtrip(matter_mod):
    res = matter_mod.DISPATCH["describe_matter"](
        {"matter_code": "M-1",
         "description": "Acme v Beta breach-of-contract."},
        user="alice",
    )
    assert res["matter_code"] == "M-1"
    assert "Acme" in res["description"]

    got = matter_mod.DISPATCH["get_matter"](
        {"matter_code": "M-1"}, user="alice",
    )
    assert got["description"] == "Acme v Beta breach-of-contract."
    assert got["activity_summary"]["total_turns"] == 2
    assert sorted(d["display_name"] for d in got["documents"]) == ["a.pdf", "b.pdf"]


# ── citation server ─────────────────────────────────────────────────────


@pytest.fixture
def citation_mod():
    from mcp_servers.citation import server
    return server


def test_citation_shape(citation_mod):
    assert len(citation_mod.TOOL_DEFS) == 2
    for d in citation_mod.TOOL_DEFS:
        _assert_tool_def_shape(d)
    _assert_dispatch_matches(citation_mod.TOOL_DEFS, citation_mod.DISPATCH)


def test_citation_verify_passthrough(citation_mod, monkeypatch):
    sentinel = {"citations": [{"cite": "[2026] UKSC 1", "verified": True}],
                "count": 1, "elapsed_ms": 7}
    monkeypatch.setattr("mcp_servers.citation.server.citations.verify",
                        lambda text: sentinel)
    out = citation_mod.DISPATCH["verify"](
        {"text": "see [2026] UKSC 1"}, user="alice",
    )
    assert out is sentinel


def test_citation_search_caselaw_filters_to_cases(citation_mod, monkeypatch):
    def fake_retrieve(query, user=None, matter_code=None):
        return [
            {"chunk_id": "1", "source": "R v Smith [2024] EWCA Crim 5.pdf",
             "text": "...", "score": 0.9, "section": "", "page": 1},
            {"chunk_id": "2", "source": "internal_memo.docx",
             "text": "...", "score": 0.8, "section": "", "page": 1},
            {"chunk_id": "3", "source": "Doe v Roe [2026] UKSC 1.txt",
             "text": "...", "score": 0.7, "section": "", "page": 1},
        ]

    monkeypatch.setattr("mcp_servers.citation.server.retrieval.retrieve",
                        fake_retrieve)
    out = citation_mod.DISPATCH["search_caselaw"](
        {"query": "contract"}, user="alice",
    )
    assert out["count"] == 2
    assert all("internal_memo" not in r["source"] for r in out["results"])


def test_citation_search_caselaw_non_uk_jurisdiction(citation_mod):
    out = citation_mod.DISPATCH["search_caselaw"](
        {"query": "tort", "jurisdiction": "US"}, user="alice",
    )
    assert out["count"] == 0
    assert "not yet implemented" in out.get("note", "")


# ── end-to-end live import probe (mirrors the verification step) ─────────


def test_plugins_builtin_tool_defs_surfaces_servers(monkeypatch):
    """Mimic the live probe in the step plan: load the test-plugin and
    confirm builtin_tool_defs surfaces the search + citation tools."""
    fixture_dir = Path(__file__).parent / "fixtures" / "plugins"
    from api import plugins
    plugins.load_plugins_from_dir(fixture_dir)
    spec = plugins.get_plugin("test-plugin")
    assert spec is not None
    defs = plugins.builtin_tool_defs(spec)
    names = {d["function"]["name"] for d in defs}
    # test-plugin declares search + citation servers
    assert {"search_documents", "list_matter_documents"} <= names
    assert {"verify", "search_caselaw"} <= names
    # ... and explicitly NOT the audit / matter ones
    assert "log_search" not in names
    assert "list_matters" not in names

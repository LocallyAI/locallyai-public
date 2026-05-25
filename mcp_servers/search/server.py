"""In-process MCP server wrapping LocallyAI's hybrid Qdrant + BM25 + RRF
retriever and the doc_acls.json store.

Two tools surface here:
  - search_documents     → `retrieval.retrieve(query, user, matter_code)`
  - list_matter_documents → scan of `SHARED_DIR/doc_acls.json`
"""
from __future__ import annotations

import json
from typing import Any, Callable

import doc_acls
import retrieval

DESCRIPTION = (
    "Hybrid retrieval over the firm's ingested document corpus "
    "(Qdrant dense + BM25 sparse fused via RRF), plus a read-only view "
    "of per-document ACLs scoped to a matter."
)

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search the firm's ingested document corpus for chunks "
                "relevant to a query. Returns the top-k passages with "
                "source filename, score, section heading, and page "
                "number. Use this BEFORE answering any question that "
                "could be grounded in the firm's own files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language search query.",
                    },
                    "matter_code": {
                        "type": "string",
                        "description": (
                            "Optional matter code to scope the search "
                            "(e.g. 'M-2026-0042'). If omitted, the "
                            "matter_code attached to the chat request "
                            "(if any) is used as a fallback."
                        ),
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of chunks to return (default 5).",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 25,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_matter_documents",
            "description": (
                "List documents whose ACL entry attaches them to the "
                "given matter_code. Returns the document display name, "
                "allowed users, and the matter code itself. Use this to "
                "ground 'what files do we have on matter X?' questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "matter_code": {
                        "type": "string",
                        "description": "Matter code to filter by (exact match).",
                    },
                },
                "required": ["matter_code"],
            },
        },
    },
]


def _search_documents(arguments: dict, *, user: str,
                      matter_code: str | None = None) -> dict:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return {"error": "missing required argument: query", "results": [], "count": 0}
    k = arguments.get("k", 5)
    try:
        k = int(k)
    except (TypeError, ValueError):
        k = 5
    k = max(1, min(25, k))
    # Caller-supplied matter_code beats the request-level fallback.
    effective_matter = arguments.get("matter_code") or matter_code or None
    results = retrieval.retrieve(query, user=user, matter_code=effective_matter)
    if not isinstance(results, list):
        results = []
    return {"results": results[:k], "count": min(len(results), k),
            "query": query, "matter_code": effective_matter}


def _list_matter_documents(arguments: dict, *, user: str,
                           matter_code: str | None = None) -> dict:
    requested = str(arguments.get("matter_code") or "").strip()
    if not requested:
        return {"error": "missing required argument: matter_code",
                "documents": [], "count": 0}
    # doc_acls doesn't expose a public matter-filter helper — scan the raw
    # ACL dict (this is the single source of truth that the rest of the
    # codebase reads through `doc_acls.list_acls()`).
    try:
        acls = doc_acls.list_acls() or {}
    except (OSError, json.JSONDecodeError):
        acls = {}
    docs: list[dict[str, Any]] = []
    for source_name, entry in sorted(acls.items()):
        if not isinstance(entry, dict):
            continue
        if entry.get("matter_code") != requested:
            continue
        docs.append({
            "display_name":  source_name,
            "allowed_users": list(entry.get("allowed_users", [])),
            "matter_code":   entry.get("matter_code", ""),
        })
    return {"documents": docs, "count": len(docs), "matter_code": requested}


DISPATCH: dict[str, Callable[..., dict]] = {
    "search_documents":      _search_documents,
    "list_matter_documents": _list_matter_documents,
}

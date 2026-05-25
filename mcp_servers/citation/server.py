"""In-process MCP server wrapping LocallyAI's citation verifier.

Two tools surface here:
  - verify          → `citations.verify(text)` passthrough
  - search_caselaw  → corpus-scoped search filtered to case-like sources
                      (UK only for now; external BAILII path is a later
                      iteration).
"""
from __future__ import annotations

import re
from typing import Any, Callable

import citations
import retrieval

DESCRIPTION = (
    "Citation verification (in-corpus + external lookup) and an "
    "in-corpus caselaw search shim. Use this for any legal-citation "
    "claim the model is about to make."
)

# Heuristic: a chunk is "case-like" if the source filename matches the
# common UK citation patterns (e.g. `[2026] UKSC 1`, `R v Smith [2024]
# EWCA Crim 5`) OR ends with `.case.txt`/`.case.md`. This is intentionally
# loose; the model already gets the chunk text back to filter further.
_CASE_SOURCE_PATTERNS = (
    re.compile(r"\[\d{4}\]"),                 # [2026] (year in brackets)
    re.compile(r"\bUKSC\b|\bEWCA\b|\bEWHC\b"),  # UK court abbrevs
    re.compile(r"\bAC\b|\bWLR\b|\bAll ER\b"),   # UK report series
    re.compile(r"\.case\.(md|txt|json)$", re.IGNORECASE),
)


def _looks_like_case(source: str) -> bool:
    if not source:
        return False
    for pat in _CASE_SOURCE_PATTERNS:
        if pat.search(source):
            return True
    return False


TOOL_DEFS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "verify",
            "description": (
                "Verify every legal citation in a block of text. Runs "
                "in-corpus lookup + external lookup + on-point check. "
                "Returns per-citation `verified`, `found_in_corpus`, "
                "`found_external`, and `on_point` flags. USE THIS BEFORE "
                "asserting any citation in an answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text containing citations to verify.",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_caselaw",
            "description": (
                "Search for caselaw matching a query. For jurisdiction "
                "'UK' (default), runs an in-corpus retrieval filtered to "
                "chunks whose source looks like a UK case citation. For "
                "any other jurisdiction, returns a 'not yet implemented' "
                "marker so the calling skill can fall back to a web "
                "lookup later."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "jurisdiction": {
                        "type": "string",
                        "description": "ISO-style jurisdiction code (default 'UK').",
                        "default": "UK",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def _verify(arguments: dict, *, user: str,
            matter_code: str | None = None) -> dict:
    text = str(arguments.get("text") or "")
    if not text:
        return {"error": "missing required argument: text",
                "citations": [], "count": 0, "elapsed_ms": 0}
    return citations.verify(text)


def _search_caselaw(arguments: dict, *, user: str,
                    matter_code: str | None = None) -> dict:
    query = str(arguments.get("query") or "").strip()
    jurisdiction = str(arguments.get("jurisdiction") or "UK").strip().upper()
    if not query:
        return {"error": "missing required argument: query",
                "results": [], "count": 0, "jurisdiction": jurisdiction}
    if jurisdiction != "UK":
        return {
            "results": [],
            "count": 0,
            "jurisdiction": jurisdiction,
            "note": (f"external lookup not yet implemented for "
                     f"jurisdiction={jurisdiction!r}"),
        }
    candidates = retrieval.retrieve(query, user=user, matter_code=None)
    if not isinstance(candidates, list):
        candidates = []
    cases = [c for c in candidates if _looks_like_case(c.get("source", ""))]
    return {
        "results":      cases,
        "count":        len(cases),
        "jurisdiction": jurisdiction,
        "query":        query,
    }


DISPATCH: dict[str, Callable[..., dict]] = {
    "verify":         _verify,
    "search_caselaw": _search_caselaw,
}

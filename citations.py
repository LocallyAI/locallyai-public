"""citations.py — citation extraction + verification.

Lawyers cite case law and statutes constantly. AI-generated drafts
have a well-known habit of inventing plausible-looking citations
("hallucinated case law"). This module:

  1. Extracts citations from arbitrary text using a regex catalogue
  2. For each citation, verifies it via:
     - in-corpus search (the firm's own documents)
     - external check — UK only in v1 (BAILII)
     - LLM "on-point" check — given citation + surrounding context,
       is the cited authority actually being used correctly?

The output is a list of structured citations with verification flags.
The worker-ui decorates each citation in chat output as ✓ / ? / ✗.

Caveats baked in:
- BAILII is rate-limited; positive matches cache for 30 days in
  storage/citations_cache.json
- KSA shariah-court citations have no public database — extraction
  works, external verification is "unavailable"
- The on-point check is the LLM's opinion; surfaced as opinion, not
  fact, in the SOP and the UI
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from config import BASE_DIR  # type: ignore[attr-defined]

log = logging.getLogger("citations")

_CACHE_FILE = BASE_DIR / "storage" / "citations_cache.json"
_CACHE_TTL_DAYS = 30
_CACHE_LOCK = threading.Lock()


# ── Regex catalogue ────────────────────────────────────────────────────────
# Each entry: (pattern, jurisdiction, kind). Patterns are ordered most-specific
# first so we don't double-match (e.g. UKSC must be tried before generic AC).

_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # UK neutral citations (post-2001)
    (re.compile(r"\[(?P<year>\d{4})\]\s+UKSC\s+(?P<num>\d+)"), "UK", "case"),
    (re.compile(r"\[(?P<year>\d{4})\]\s+UKHL\s+(?P<num>\d+)"), "UK", "case"),
    (re.compile(r"\[(?P<year>\d{4})\]\s+UKPC\s+(?P<num>\d+)"), "UK", "case"),
    (re.compile(r"\[(?P<year>\d{4})\]\s+EWCA\s+(?P<court>Civ|Crim)\s+(?P<num>\d+)"), "UK", "case"),
    (re.compile(r"\[(?P<year>\d{4})\]\s+EWHC\s+(?P<num>\d+)(?:\s*\((?P<div>[A-Za-z]+)\))?"), "UK", "case"),
    # UK pre-neutral and reporter citations
    (re.compile(r"\[(?P<year>\d{4})\]\s+(?P<vol>\d*)\s*AC\s+(?P<page>\d+)"), "UK", "case"),
    (re.compile(r"\[(?P<year>\d{4})\]\s+(?P<vol>\d+)?\s*WLR\s+(?P<page>\d+)"), "UK", "case"),
    (re.compile(r"\[(?P<year>\d{4})\]\s+(?P<vol>\d+)?\s*All\s+ER\s+(?P<page>\d+)"), "UK", "case"),
    (re.compile(r"\((?P<year>\d{4})\)\s+(?P<vol>\d+)\s+WLR\s+(?P<page>\d+)"), "UK", "case"),
    # US federal
    (re.compile(r"(?P<vol>\d+)\s+U\.?\s?S\.?\s+(?P<page>\d+)"), "US", "case"),
    (re.compile(r"(?P<vol>\d+)\s+F\.?\s?(?P<series>2d|3d|4th)\s+(?P<page>\d+)"), "US", "case"),
    (re.compile(r"(?P<vol>\d+)\s+S\.?\s?Ct\.?\s+(?P<page>\d+)"), "US", "case"),
    # UK statutes — Act + year [+ optional section]
    (re.compile(r"(?P<act>[A-Z][A-Za-z'\-&]+(?:\s+[A-Z][A-Za-z'\-&]+){0,5}\s+Act)\s+(?P<year>\d{4})(?:[,\s]+s(?:ection|\.)?\s*(?P<section>\d+(?:\([\w]+\))?))?"),
     "UK", "statute"),
    # KSA shariah / royal-decree citations: نظام / مرسوم ملكي + Hijri year
    (re.compile(r"(?:Royal\s+Decree|نظام|مرسوم\s+ملكي)\s+(?:No\.?|رقم)\s+(?P<num>[\w/\-]+)\s+(?:dated|بتاريخ)\s+(?P<date>[\w/\-]+)?"),
     "KSA", "decree"),
]


# ── Citation dataclass ─────────────────────────────────────────────────────

class Citation(dict):
    """Dict-shaped so it serializes to JSON without converters. Carries:
       cite, jurisdiction, kind, year, parsed (regex group dict), span
       (start, end in source text), context (~200 chars around the cite)."""


def extract(text: str) -> list[Citation]:
    """Extract structured citations from arbitrary text. Returns a list
    in source order; ranges are deduplicated by overlap (most-specific
    pattern wins via order)."""
    if not text:
        return []
    seen_spans: list[tuple[int, int]] = []
    out: list[Citation] = []
    for pattern, jurisdiction, kind in _PATTERNS:
        for m in pattern.finditer(text):
            span = (m.start(), m.end())
            if any(_overlap(span, existing) for existing in seen_spans):
                continue
            seen_spans.append(span)
            ctx_start = max(0, span[0] - 200)
            ctx_end = min(len(text), span[1] + 200)
            out.append(Citation(
                cite=m.group(0),
                jurisdiction=jurisdiction,
                kind=kind,
                year=m.groupdict().get("year"),
                parsed=m.groupdict(),
                span=list(span),
                context=text[ctx_start:ctx_end],
            ))
    out.sort(key=lambda c: c["span"][0])
    return out


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0])


# ── Cache ──────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    tmp.replace(_CACHE_FILE)


def _cache_get(key: str) -> Optional[dict]:
    with _CACHE_LOCK:
        c = _load_cache()
    entry = c.get(key)
    if not entry:
        return None
    cached_at = entry.get("cached_at")
    try:
        when = datetime.fromisoformat(cached_at)
    except Exception:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - when > timedelta(days=_CACHE_TTL_DAYS):
        return None
    return entry.get("value")


def _cache_set(key: str, value: dict) -> None:
    with _CACHE_LOCK:
        c = _load_cache()
        c[key] = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "value": value,
        }
        _save_cache(c)


# ── In-corpus search ───────────────────────────────────────────────────────

def _check_in_corpus(cite: str) -> dict:
    """BM25-search the firm corpus for the citation string. Returns the
    top hit (source + score + snippet) or {found: false}."""
    try:
        from retrieval import _get_retriever, _embed_query
    except Exception:
        return {"found": False, "reason": "retrieval unavailable"}
    retriever = _get_retriever()
    if retriever is None:
        return {"found": False, "reason": "retrieval not initialised"}
    vec = _embed_query(cite)
    if vec is None:
        return {"found": False, "reason": "embedding unavailable"}
    try:
        chunks = retriever.retrieve(cite, vec, user="admin")
    except Exception as exc:
        return {"found": False, "reason": f"retrieval failed: {exc}"}
    if not chunks:
        return {"found": False}
    top = chunks[0]
    if (top.score or 0) < 0.5:
        return {"found": False, "best_score": float(top.score or 0)}
    return {
        "found": True,
        "source": top.source,
        "score": float(top.score or 0),
        "snippet": (top.text or "")[:300],
    }


# ── External check (UK only — BAILII) ──────────────────────────────────────

_BAILII_BASE = "https://www.bailii.org/cgi-bin/lucy_search_1.cgi"
_BAILII_TIMEOUT = 5.0
_BAILII_USER_AGENT = "LocallyAI/1.0 citations-verifier (+https://locallyai.app)"


def _check_external_uk(cite: str) -> dict:
    """Query BAILII for the citation. Cached aggressively (30d) — BAILII
    is a free public service and we shouldn't hammer it. Returns
    {found: bool, url?: str, snippet?: str}."""
    cache_key = f"bailii:{cite}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return {**cached, "from_cache": True}

    if os.environ.get("LOCALLYAI_CITATIONS_NO_EXTERNAL") == "1":
        return {"found": False, "reason": "external lookups disabled by config"}

    params = urllib.parse.urlencode({"querytext": cite, "method": "boolean"})
    url = f"{_BAILII_BASE}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _BAILII_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_BAILII_TIMEOUT) as r:
            body = r.read().decode("utf-8", errors="replace")
    except Exception as exc:
        # Don't cache failures — transient network blips shouldn't poison the cache
        return {"found": False, "reason": f"BAILII unreachable: {type(exc).__name__}"}

    # BAILII returns an HTML results page. We don't parse it deeply — we
    # just check whether it contains a result link to the citation. A
    # genuine match always contains an `<a href="/uk/cases/.../...html">`.
    has_match = bool(re.search(r'<a\s+href="(/[a-z]{2,4}/[\w/\-]+\.html)"', body))
    snippet_m = re.search(
        r'<a\s+href="(/[a-z]{2,4}/[\w/\-]+\.html)">([^<]{0,200})</a>', body)
    if has_match:
        result = {
            "found": True,
            "url": "https://www.bailii.org" + (snippet_m.group(1) if snippet_m else ""),
            "snippet": (snippet_m.group(2).strip() if snippet_m else "")[:200],
        }
    else:
        result = {"found": False, "reason": "no match in BAILII results"}
    _cache_set(cache_key, result)
    return result


def _check_external(citation: Citation) -> dict:
    """Dispatch external check by jurisdiction."""
    j = citation.get("jurisdiction")
    if j == "UK":
        return _check_external_uk(citation["cite"])
    if j == "US":
        return {"found": False, "reason": "US external lookup not implemented in v1"}
    if j == "KSA":
        return {"found": False, "reason": "no public KSA case database"}
    return {"found": False, "reason": "unknown jurisdiction"}


# ── On-point LLM check ─────────────────────────────────────────────────────

_ON_POINT_PROMPT = """You are a citation-quality checker. You will be given:
  - A citation (case or statute reference)
  - The surrounding context (~400 chars) from the document where it appears
  - Whether the citation was found in the firm's corpus and/or in BAILII

Decide: is the citation being used CORRECTLY for the proposition it supports?

Output STRICT JSON (no markdown fences, no commentary outside the JSON):
{
  "on_point": true | false | null,
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one or two sentences>",
  "suggestion": "<one sentence — empty string if on_point=true>"
}

on_point:
  - true  = the citation supports the proposition in the context
  - false = the citation is wrong/inapposite for the proposition
  - null  = insufficient information to judge (cite not found, context too thin)

Be conservative. If the cite is not found in either the corpus or BAILII, set on_point=null.
"""


def _check_on_point(citation: Citation, in_corpus: dict, external: dict) -> dict:
    """Single LLM call asking whether the citation is being used correctly."""
    try:
        from mlx_inference import generate as _mlx_generate
    except Exception:
        return {"on_point": None, "confidence": "low",
                "reasoning": "LLM backend unavailable.", "suggestion": ""}
    if not (in_corpus.get("found") or external.get("found")):
        return {"on_point": None, "confidence": "high",
                "reasoning": "Citation not found in firm corpus or external sources; cannot judge on-point.",
                "suggestion": "Verify the citation manually before relying on it."}
    payload = {
        "citation":         citation["cite"],
        "context":          citation.get("context", ""),
        "in_corpus":        bool(in_corpus.get("found")),
        "in_corpus_source": in_corpus.get("source"),
        "external_found":   bool(external.get("found")),
        "external_snippet": external.get("snippet"),
    }
    prompt = _ON_POINT_PROMPT + "\nINPUT:\n" + json.dumps(payload, indent=2) + "\n\nOUTPUT:\n"
    try:
        raw = _mlx_generate(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250,
            temperature=0.0,
        )
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("LLM response contained no JSON object")
        parsed = json.loads(m.group(0))
        if parsed.get("on_point") not in (True, False, None):
            parsed["on_point"] = None
        if parsed.get("confidence") not in ("high", "medium", "low"):
            parsed["confidence"] = "low"
        parsed.setdefault("reasoning", "")
        parsed.setdefault("suggestion", "")
        return parsed
    except Exception as exc:
        log.warning(f"On-point LLM check failed: {exc}")
        return {"on_point": None, "confidence": "low",
                "reasoning": f"On-point check failed ({type(exc).__name__}).",
                "suggestion": "Verify manually."}


# ── Public verify() ────────────────────────────────────────────────────────

def verify(text: str) -> dict:
    """Extract every citation in `text` and run the three verification
    stages. Returns:
       {citations: [{...cite info..., found_in_corpus, found_external,
                     verified, on_point: {...}}], elapsed_ms}"""
    t0 = time.perf_counter()
    found = extract(text)
    out = []
    for c in found:
        in_corpus = _check_in_corpus(c["cite"])
        external = _check_external(c)
        verified_flag = bool(in_corpus.get("found") or external.get("found"))
        on_point = _check_on_point(c, in_corpus, external)
        out.append({
            **c,
            "found_in_corpus": in_corpus,
            "found_external":  external,
            "verified":        verified_flag,
            "on_point":        on_point,
        })
    return {
        "citations":  out,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "count":      len(out),
    }

"""documents_compare.py — structural diff + LLM commentary.

Comparing two legal documents (typically successive drafts of an
NDA / contract / pleading) is a workflow lawyers do constantly and
do badly with raw text-diff tools. The raw diff shows what changed;
it doesn't show why a lawyer should care.

This module:
1. Splits both inputs into sections (markdown-style headings + blank
   lines as fallback)
2. Runs `difflib.SequenceMatcher` over the section titles to align
   sections across the two docs
3. For each aligned pair where the bodies differ → ranks the change
   by significance (added / removed clause = high; whitespace-only =
   ignored)
4. Asks the LLM, in one call per significant change, "what changed
   and why a lawyer would care" with a tight structured-output prompt

The LLM commentary is **opinion**, not legal advice — the SOP makes
this explicit and the worker-ui badges it as such.

Concurrency: per-section LLM calls run sequentially in v1. The MLX
backend serializes through `inference_gate` anyway, so there's no
parallelism win to be had. If the firm switches to a backend with
real parallel inference, this is the obvious place to hoist
asyncio.gather + a semaphore.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
from typing import Optional

log = logging.getLogger("documents_compare")

# Section split: prefer markdown-ish headings (lines that look like
# a clause heading — short, end with no period, possibly numbered).
# Fall back to blank-line separation. Everything is bounded to keep
# the diff tractable for very flat docs (no headings → fall back to
# paragraph-level alignment).

_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s+.+|"                            # markdown headings
    r"\d+(?:\.\d+){0,3}\.?\s+[A-Z][^\n]{0,200}|"   # numbered: 1. / 1.1 / 4.2.3.
    r"[A-Z][A-Z\s\-]{2,80}|"                       # ALL-CAPS section heads
    r"(?:Clause|Article|Section|Schedule)\s+\d+[A-Za-z]?[^\n]{0,160})\s*$",
    re.MULTILINE,
)

_MAX_SECTIONS = 200             # bound: refuse to compare docs with insane structure
_MAX_LLM_SECTIONS = 30          # bound: cap the number of LLM commentary calls per compare


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Return [(heading, body), ...]. If we can't find headings, fall
    back to paragraph splits with synthetic numeric headings."""
    text = text.strip()
    if not text:
        return []
    matches = list(_HEADING_RE.finditer(text))
    if matches:
        sections = []
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            heading = m.group(0).strip()
            body = text[m.end():end].strip()
            sections.append((heading, body))
        # Capture any preamble before the first heading
        if matches[0].start() > 0:
            preamble = text[:matches[0].start()].strip()
            if preamble:
                sections.insert(0, ("(preamble)", preamble))
        return sections[:_MAX_SECTIONS]

    # Fallback: paragraphs
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [(f"§{i+1}", p) for i, p in enumerate(paras)][:_MAX_SECTIONS]


def _norm_for_diff(s: str) -> str:
    """Whitespace-collapse so trivial reformatting doesn't show as a
    change. Case is preserved — capitalisation often matters (defined
    terms)."""
    return re.sub(r"\s+", " ", s).strip()


def _classify_change(a: str, b: str) -> str:
    """One of: added | removed | rewritten | whitespace-only | unchanged."""
    if not a and not b:
        return "unchanged"
    if not a:
        return "added"
    if not b:
        return "removed"
    if _norm_for_diff(a) == _norm_for_diff(b):
        return "whitespace-only"
    return "rewritten"


# ── Heading alignment ──────────────────────────────────────────────────────

def _align(headings_a: list[str], headings_b: list[str]) -> list[tuple[Optional[int], Optional[int]]]:
    """Use SequenceMatcher over heading strings (normalised) to produce
    aligned index pairs. (None, j) → b-only; (i, None) → a-only."""
    norm_a = [_norm_for_diff(h).lower() for h in headings_a]
    norm_b = [_norm_for_diff(h).lower() for h in headings_b]
    sm = difflib.SequenceMatcher(a=norm_a, b=norm_b, autojunk=False)
    pairs: list[tuple[Optional[int], Optional[int]]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                pairs.append((i1 + k, j1 + k))
        elif tag == "replace":
            common = min(i2 - i1, j2 - j1)
            for k in range(common):
                pairs.append((i1 + k, j1 + k))
            for k in range(common, i2 - i1):
                pairs.append((i1 + k, None))
            for k in range(common, j2 - j1):
                pairs.append((None, j1 + k))
        elif tag == "delete":
            for k in range(i1, i2):
                pairs.append((k, None))
        elif tag == "insert":
            for k in range(j1, j2):
                pairs.append((None, k))
    return pairs


# ── LLM commentary ─────────────────────────────────────────────────────────

_SIGNIFICANCE_PROMPT = """You are a legal-drafting assistant. Two versions of a contract clause are below.
Explain in 1-3 sentences what changed and why it matters for a lawyer reviewing the drafts.
Focus on legal effect, not surface text.

Output STRICT JSON (no markdown fences, no commentary outside the JSON):
{
  "summary": "<one sentence: what changed>",
  "why_matters": "<one or two sentences: the legal effect>",
  "significance": "high" | "medium" | "low",
  "watch_for": ["<short bullet>", ...]   // optional, can be empty
}

significance:
  - "high"   = changes the parties' obligations, allocates risk, or alters remedies
  - "medium" = clarifies ambiguity, narrows/widens existing scope without re-allocating risk
  - "low"    = drafting-quality changes, rewording without legal effect
"""


def _llm_for_section(label_a: str, label_b: str, heading: str, body_a: str, body_b: str) -> dict:
    """Single LLM call asking for the significance of one section's change.
    Falls back to a structured 'unavailable' verdict if MLX is down."""
    try:
        from mlx_inference import generate as _mlx_generate
    except Exception:
        return {
            "summary": "LLM commentary unavailable in this deployment.",
            "why_matters": "Manual review required.",
            "significance": "medium",
            "watch_for": [],
        }
    payload = {
        "section_heading":  heading[:200],
        "version_a_label":  label_a,
        "version_a_text":   body_a[:3000],
        "version_b_label":  label_b,
        "version_b_text":   body_b[:3000],
    }
    prompt = _SIGNIFICANCE_PROMPT + "\nINPUT:\n" + json.dumps(payload, indent=2) + "\n\nOUTPUT:\n"
    try:
        raw = _mlx_generate(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.0,
        )
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("LLM response contained no JSON object")
        parsed = json.loads(m.group(0))
        sig = parsed.get("significance")
        if sig not in ("high", "medium", "low"):
            parsed["significance"] = "medium"
        parsed.setdefault("summary", "")
        parsed.setdefault("why_matters", "")
        parsed.setdefault("watch_for", [])
        return parsed
    except Exception as exc:
        log.warning(f"LLM section commentary failed: {exc}")
        return {
            "summary": "LLM commentary failed; manual review required.",
            "why_matters": str(exc)[:200],
            "significance": "medium",
            "watch_for": [],
        }


# ── Public API ─────────────────────────────────────────────────────────────

def compare(text_a: str, text_b: str, label_a: str = "Document A", label_b: str = "Document B") -> dict:
    """Compare two text bodies. Returns the structured comparison report
    that the API endpoint passes straight back to the caller."""
    sections_a = _split_sections(text_a)
    sections_b = _split_sections(text_b)
    headings_a = [h for h, _ in sections_a]
    headings_b = [h for h, _ in sections_b]
    pairs = _align(headings_a, headings_b)

    out_sections: list[dict] = []
    significant_count = 0
    llm_calls = 0

    for ia, ib in pairs:
        a_heading = headings_a[ia] if ia is not None else ""
        b_heading = headings_b[ib] if ib is not None else ""
        a_body    = sections_a[ia][1] if ia is not None else ""
        b_body    = sections_b[ib][1] if ib is not None else ""
        change = _classify_change(a_body, b_body)
        if change == "unchanged":
            continue
        section: dict = {
            "heading_a":   a_heading,
            "heading_b":   b_heading,
            "change_type": change,
            "diff":        _unified_diff(a_body, b_body, label_a, label_b, a_heading or b_heading),
        }
        # Only call the LLM for non-trivial changes; cap total calls
        if change != "whitespace-only" and llm_calls < _MAX_LLM_SECTIONS:
            section["commentary"] = _llm_for_section(label_a, label_b, b_heading or a_heading, a_body, b_body)
            llm_calls += 1
            if section["commentary"].get("significance") == "high":
                significant_count += 1
        elif change != "whitespace-only":
            section["commentary"] = {
                "summary":      "Change exceeded LLM commentary cap; review manually.",
                "why_matters":  "",
                "significance": "medium",
                "watch_for":    [],
            }
        out_sections.append(section)

    # Top-level summary so the UI can display a one-line verdict
    if not out_sections:
        summary = f"{label_a} and {label_b} are functionally identical."
        verdict = "identical"
    elif significant_count > 0:
        summary = f"{significant_count} significant change(s) and {len(out_sections) - significant_count} smaller change(s) between {label_a} and {label_b}."
        verdict = "material-changes"
    else:
        summary = f"{len(out_sections)} change(s) between {label_a} and {label_b}; none flagged as high significance."
        verdict = "minor-changes"

    return {
        "label_a":            label_a,
        "label_b":            label_b,
        "verdict":            verdict,
        "summary":            summary,
        "sections":           out_sections,
        "section_count_a":    len(sections_a),
        "section_count_b":    len(sections_b),
        "llm_calls":          llm_calls,
    }


def _unified_diff(a: str, b: str, label_a: str, label_b: str, header: str) -> str:
    """Compact unified diff. Bounded to 4000 chars so a giant
    rewrite doesn't bloat the response."""
    a_lines = a.splitlines(keepends=False)
    b_lines = b.splitlines(keepends=False)
    diff = difflib.unified_diff(
        a_lines, b_lines,
        fromfile=f"{label_a} :: {header[:60]}",
        tofile=f"{label_b} :: {header[:60]}",
        n=2, lineterm="",
    )
    out = "\n".join(diff)
    if len(out) > 4000:
        out = out[:4000] + "\n... (diff truncated)"
    return out

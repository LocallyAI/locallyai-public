"""conflicts.py — conflict-of-interest check for new matter intake.

Every law firm above ~5 lawyers runs conflict checks on every new
matter. The traditional process is a manual lookup in the firm's
matter database; this module automates the FIRST PASS using:

  1. Hybrid corpus search (Qdrant + BM25) for the proposed parties
  2. LLM pass over the strong hits + the new matter description,
     asking the model to classify the relationship as
     conflict / same-side / unrelated

The output is **advisory** — it surfaces hits the partner needs to
review. The firm's own conflict-of-interest policy (per SRA Code of
Conduct or KSA bar rules) remains the authoritative process. The
LLM output is recorded in the conflict log alongside the partner's
final decision.

Privacy / compliance:
- Party names are stored in the conflict log as SHA-256 hashes
  (salted with LOCALLYAI_AUDIT_SALT — same salt as audit log
  pseudonymisation). Original names live only in memory during the
  check + in the response to the requester; never persisted.
- Each check is audit-logged via _write_audit (event-category
  "conflict_check") so the firm has a record that checks ARE being
  run, even if the parties themselves are pseudonymised.
- The conflict log is HMAC-chained the same way audit + billing
  logs are — tamper-evident.

Replicated via Syncthing per HA architecture (lives at SHARED_DIR
alongside users.json, erasure.log, fleet.json, doc_acls.json).
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac as _hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import SHARED_DIR, _AUDIT_SALT  # type: ignore[attr-defined]

log = logging.getLogger("conflicts")

_LOG_FILE = SHARED_DIR / "conflicts.log"
_LOCK_FILE = SHARED_DIR / ".conflicts.lock"
_CHAIN_FILE = SHARED_DIR / ".conflicts_chain"


# ── Party-name normalisation ───────────────────────────────────────────────
# Lowercase, collapse whitespace, strip common corporate suffixes so
# "Acme Ltd", "ACME Limited", "acme  ltd." all hash to the same value.

_SUFFIX_RE = re.compile(
    r"\b(ltd|limited|llc|llp|inc|incorporated|corp|corporation|plc|gmbh|sa|s\.?a\.?|s\.?l\.?|pty|kk|co|company|holdings?|group)\b\.?",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s\-]")
_SPACE_RE = re.compile(r"\s+")


def normalise_party(name: str) -> str:
    """Return the canonical form of a party name for matching + hashing.
    Strips punctuation, collapses whitespace, removes common corporate
    suffixes."""
    if not name:
        return ""
    s = name.strip().lower()
    s = _SUFFIX_RE.sub("", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _SPACE_RE.sub(" ", s).strip()
    return s


def hash_party(name: str) -> str:
    """One-way pseudonymisation for the conflict log. Same salt as the
    audit-log user pseudonymisation so a single LOCALLYAI_AUDIT_SALT
    rotation invalidates every log uniformly."""
    salt = _AUDIT_SALT or ""
    return hashlib.sha256(f"{salt}:party:{normalise_party(name)}".encode()).hexdigest()[:16]


# ── HMAC chaining (same shape as audit + billing chains) ───────────────────

_AUDIT_HMAC_KEY = os.environ.get("LOCALLYAI_AUDIT_HMAC_KEY", "").encode("utf-8")


def _chain_hmac(entry_json: str, prev: str) -> str:
    if not _AUDIT_HMAC_KEY:
        return ""
    return _hmac.new(_AUDIT_HMAC_KEY, f"{prev}{entry_json}".encode(), "sha256").hexdigest()


def _prev_hash() -> str:
    if _CHAIN_FILE.exists():
        return _CHAIN_FILE.read_text(encoding="utf-8").strip()
    return "0" * 64


def _atomic_write_chain(chain: str) -> None:
    tmp = _CHAIN_FILE.with_suffix(".tmp")
    tmp.write_text(chain, encoding="utf-8")
    tmp.replace(_CHAIN_FILE)


@contextlib.contextmanager
def _lock():
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    _LOCK_FILE.touch(exist_ok=True)
    fd = open(_LOCK_FILE, "rb+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fd.close()


# ── Hit-search (corpus + matter_code metadata) ─────────────────────────────

def _corpus_hits(parties: list[str], top_k: int = 20) -> list[dict]:
    """Run the hybrid retriever with the parties as the query.

    We don't use the LLM-aware retrieve() entry point because we want
    raw hits (sources + matter_code + score) without query rewriting,
    sanitisation, or session context — this is a structured backend
    lookup, not a chat turn."""
    try:
        from retrieval import _get_retriever, _embed_query
    except Exception as exc:
        log.warning(f"retrieval unavailable for conflict check: {exc}")
        return []
    retriever = _get_retriever()
    if retriever is None:
        return []
    # Concatenate party names as the query — Qdrant + BM25 both handle
    # this naturally. Order doesn't matter; we don't want exact phrase.
    q = " ".join(p for p in parties if p)
    if not q.strip():
        return []
    vec = _embed_query(q)
    if vec is None:
        return []
    chunks = retriever.retrieve(q, vec, user="admin")  # admin → no ACL drop; conflict checks see everything
    out = []
    for c in chunks[:top_k]:
        meta = c.metadata or {}
        out.append({
            "source": c.source,
            "matter_code": meta.get("matter_code", "") or "",
            "score": float(c.score) if c.score is not None else 0.0,
            "snippet": (c.text or "")[:400],
        })
    return out


def _classify_hits(hits: list[dict]) -> tuple[list[dict], list[dict]]:
    """Bucket hits into strong (≥0.6) / weak (0.4-0.6); drop <0.4."""
    strong = [h for h in hits if h["score"] >= 0.6]
    weak = [h for h in hits if 0.4 <= h["score"] < 0.6]
    return strong, weak


# ── LLM assessment ─────────────────────────────────────────────────────────

_ASSESSMENT_PROMPT = """You are a conflict-of-interest classifier for a UK / KSA law firm.
You will be given:
  - Proposed new matter parties
  - A short description of the proposed engagement
  - 0-N hits from the firm's existing matter corpus (each carrying source + matter_code + a snippet)

Classify the relationship between the proposed engagement and the existing matter as ONE of:
  - "conflict"       — direct conflict (firm acts for one side, would now act against)
  - "same-side"      — firm previously acted on the same side as proposed engagement (usually fine but flag)
  - "related"        — touches the same matter but role unclear; partner needs to assess
  - "unrelated"      — no meaningful connection

Output STRICT JSON (no markdown fences, no commentary):
  {
    "status": "conflict" | "review" | "clear",
    "summary": "<one sentence explaining the call>",
    "key_concerns": ["<short bullet>", ...],
    "recommended_action": "<one sentence what the partner should do>"
  }

status mapping:
  - "conflict"  → status="conflict"
  - "same-side" → status="review"
  - "related"   → status="review"
  - "unrelated" or zero hits → status="clear"

Be concise. Err on the side of "review" when uncertain — a false-positive
costs the partner a 5-minute look; a false-negative breaches the SRA Code
of Conduct or KSA bar rules.
"""


def _llm_assess(parties: list[str], description: str, opposing_counsel: list[str], hits: list[dict]) -> dict:
    """Run the LLM over the strong hits + new-matter context, ask for
    a structured classification."""
    if not hits:
        return {
            "status": "clear",
            "summary": "No related-matter hits found in the firm corpus.",
            "key_concerns": [],
            "recommended_action": "Proceed; document the check in the conflict log.",
        }
    try:
        from mlx_inference import generate as _mlx_generate
    except Exception:
        # MLX backend unavailable; return a degraded "review" with raw hits
        return {
            "status": "review",
            "summary": "LLM assessment unavailable; hits below need partner review.",
            "key_concerns": [f"Hit in {h['source']} (matter {h['matter_code'] or 'unknown'})" for h in hits[:5]],
            "recommended_action": "Partner reviews the listed hits manually.",
        }
    user_payload = {
        "proposed_parties": parties,
        "proposed_description": description[:1000],
        "opposing_counsel": opposing_counsel,
        "existing_matter_hits": [
            {"source": h["source"], "matter_code": h["matter_code"],
             "score": round(h["score"], 3), "snippet": h["snippet"][:300]}
            for h in hits[:10]  # bound LLM context
        ],
    }
    prompt = _ASSESSMENT_PROMPT + "\n\nINPUT:\n" + json.dumps(user_payload, indent=2) + "\n\nOUTPUT:\n"
    try:
        raw = _mlx_generate(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.0,
        )
        # Extract first JSON object from the response
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("LLM response contained no JSON object")
        parsed = json.loads(m.group(0))
        # Validate shape
        if parsed.get("status") not in ("conflict", "review", "clear"):
            parsed["status"] = "review"
        parsed.setdefault("summary", "")
        parsed.setdefault("key_concerns", [])
        parsed.setdefault("recommended_action", "Partner review.")
        return parsed
    except Exception as exc:
        log.warning(f"LLM conflict assessment failed: {exc}")
        return {
            "status": "review",
            "summary": f"LLM assessment failed ({type(exc).__name__}); fall back to manual review.",
            "key_concerns": [f"Hit in {h['source']} (score {h['score']:.2f})" for h in hits[:5]],
            "recommended_action": "Partner reviews the listed hits manually.",
        }


# ── Public API ─────────────────────────────────────────────────────────────

def check(
    parties: list[dict],          # [{role: "client"|"opposing"|"interested", name: "..."}, ...]
    description: str,
    opposing_counsel: list[str] | None = None,
    matter_id: str | None = None,
    requester: str | None = None,
) -> dict:
    """Run a conflict check + return the structured result.

    Side effects:
      - Writes an entry to SHARED_DIR/conflicts.log (HMAC-chained).
      - Caller is responsible for the audit_log entry (the API endpoint
        wraps this with a _write_audit call).

    The returned dict is safe to send to the requesting UI — party
    names are NOT pseudonymised in the response (the operator needs
    to see them); pseudonymisation only applies to what's persisted.
    """
    party_names = [p.get("name", "").strip() for p in parties if p.get("name")]
    party_roles = {p.get("name", "").strip(): p.get("role", "interested") for p in parties}
    opposing_counsel = opposing_counsel or []

    if not party_names:
        return {"status": "clear", "summary": "No parties supplied; nothing to check.",
                "hits": [], "llm_assessment": None, "checked_at": datetime.now(timezone.utc).isoformat()}

    t0 = time.perf_counter()
    raw_hits = _corpus_hits(party_names + opposing_counsel)
    strong, weak = _classify_hits(raw_hits)
    llm = _llm_assess(party_names, description, opposing_counsel, strong)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    result = {
        "status": llm["status"],
        "summary": llm["summary"],
        "key_concerns": llm.get("key_concerns", []),
        "recommended_action": llm.get("recommended_action", ""),
        "hits": [
            {**h, "bucket": "strong"} for h in strong
        ] + [
            {**h, "bucket": "weak"} for h in weak
        ],
        "llm_assessment": llm,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": elapsed_ms,
        "matter_id": matter_id,
    }
    _persist(result, party_names, party_roles, opposing_counsel, requester)
    return result


def _persist(result: dict, party_names: list[str], party_roles: dict,
             opposing_counsel: list[str], requester: str | None) -> None:
    """Append a record to the chained conflict log."""
    entry = {
        "timestamp":              result["checked_at"],
        "matter_id":              result.get("matter_id"),
        "requester":              requester or "unknown",
        # Pseudonymise party names for at-rest storage
        "parties_hashed":         [
            {"role": party_roles.get(n, "interested"), "hash": hash_party(n)}
            for n in party_names
        ],
        "opposing_counsel_hashed": [hash_party(n) for n in opposing_counsel],
        "status":                 result["status"],
        "summary":                result["summary"][:300],
        "hit_count_strong":       sum(1 for h in result["hits"] if h.get("bucket") == "strong"),
        "hit_count_weak":         sum(1 for h in result["hits"] if h.get("bucket") == "weak"),
        # decision + decided_by are filled in by a separate
        # /v1/conflicts/{id}/decide endpoint when the partner records
        # their final call. Initial value: "pending".
        "decision":               "pending",
        "decided_by":             None,
        "decided_at":             None,
    }
    with _lock():
        prev = _prev_hash()
        entry_json = json.dumps(entry, sort_keys=True)
        chain = _chain_hmac(entry_json, prev)
        if chain:
            entry["_chain_hmac"] = chain
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        if chain:
            _atomic_write_chain(chain)
    try:
        os.chmod(_LOG_FILE, 0o640)
    except OSError:
        pass


def list_recent(limit: int = 50) -> list[dict]:
    """Tail the conflict log. Used by the UI to show the recent-checks
    panel. Returns most-recent first."""
    if not _LOG_FILE.exists():
        return []
    try:
        from audit_reader import tail
        lines = tail(_LOG_FILE, limit)
    except Exception:
        with open(_LOG_FILE, encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    out.reverse()
    return out


def summary_for_compliance() -> dict:
    """Compact summary used by the monthly compliance snapshot."""
    if not _LOG_FILE.exists():
        return {"total": 0, "last_30d": 0, "status_counts": {}}
    from audit_reader import iter_filtered
    from datetime import timedelta as _td
    cutoff = (datetime.now(timezone.utc) - _td(days=30)).isoformat()
    total = 0
    last_30 = 0
    status_counts: dict = {}
    for e in iter_filtered(_LOG_FILE, lambda _e: True):
        total += 1
        if e.get("timestamp", "") >= cutoff:
            last_30 += 1
            s = e.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1
    return {"total": total, "last_30d": last_30, "status_counts": status_counts}

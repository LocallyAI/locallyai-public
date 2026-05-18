# Document comparison

Compare two versions of a contract / pleading / policy and get a
structured diff plus AI commentary on the legal significance of each
change. Designed for the redlining workflow lawyers run on every
exchange of drafts.

## What the engine does

Given two documents (or two pasted text bodies), the engine:

1. **Splits both into sections** — markdown-style headings (`# Term`),
   numbered clauses (`1.1`, `4.2.3`), `Clause N` / `Article N` /
   `Schedule N` patterns, or ALL-CAPS section headers
2. **Aligns sections across the two docs** — `difflib.SequenceMatcher`
   over normalised heading text. Sections present in only one side
   show as `added` or `removed`
3. **Classifies each pair** — `added`, `removed`, `rewritten`,
   `whitespace-only`, `unchanged`. Whitespace-only changes are
   bucketed but skip the LLM pass
4. **LLM commentary per significant section** — one call per change
   asking the model to summarise *what changed* and *why it matters*,
   classified `high` / `medium` / `low` significance. Capped at 30
   LLM calls per compare to bound latency
5. **Top-level verdict** — `identical`, `minor-changes`,
   `material-changes`

The verdict + per-section commentary are **opinion**, not legal
advice. The lawyer reviewing the redline is authoritative.

## What the engine does NOT do

- It does **not** track-changes-merge — it doesn't produce a
  re-baseline document. Use Word's Compare Documents for that. The
  engine is for *understanding* the redline, not *applying* it
- It does **not** detect changes that aren't textual — page
  numbering, signature blocks, formatting that has no semantic
  effect are largely ignored (which is usually what you want)
- It does **not** read scanned PDFs without OCR — the existing
  ingest pipeline's text extractors apply, so a scanned-image PDF
  with no text layer returns empty text and the API returns 415

## Bounds and limits

- **200 KB per side** of extracted text — about 30,000 words. Larger
  documents return HTTP 413. For docs over the cap, compare
  section-by-section using the text-paste form
- **200 sections** max per side — if the doc has more, only the
  first 200 are aligned
- **30 LLM commentary calls** max per compare — after that, sections
  show change-type only with the structural diff. The UI lists these
  as *"review manually"*

## Operator workflow

### Worker UI

1. Open the **Documents** tab in the worker app
2. Select two documents (checkbox), then click **Compare**
3. A modal opens with:
   - **Top-level verdict** badge
   - **Section list** — each row shows the heading, change type,
     significance pill, and a one-line summary
   - **Drill into a section** to see the unified diff + full LLM
     commentary including `watch_for` bullets
4. To compare against pasted text (e.g. a counterparty's emailed
   draft you haven't ingested yet), click **Paste-compare** — pastes
   two text bodies, runs the same engine

### API

```bash
# Compare two ingested documents
curl -X POST http://office.local:8000/v1/documents/compare \
  -H "Authorization: Bearer $USER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "doc_a": "nda-2026-03-01.pdf",
    "doc_b": "nda-2026-03-12.pdf"
  }'

# Or compare two pasted text bodies
curl -X POST http://office.local:8000/v1/documents/compare \
  -H "Authorization: Bearer $USER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text_a": "Term: 2 years from execution.",
    "text_b": "Term: 3 years from execution, automatically renewed for further 1-year terms.",
    "label_a": "Our markup",
    "label_b": "Counterparty markup"
  }'
```

## Access control

Per-document ACLs apply to both sides. The caller's user identity
must be allowed to read **both** documents (the request is rejected
with 403 if either is restricted from them). This matters for
ethical-wall installations — a lawyer on the acquirer-side wall
can't accidentally compare their draft against the target-side
draft.

The `text_a` / `text_b` paste form is not ACL-gated — the caller is
providing the text directly. The intended use is comparing your own
draft against an externally-supplied draft you haven't ingested.

## What "significance" means

The LLM is asked to classify each section change as:

| Level | What the LLM is looking for |
|---|---|
| **high** | Changes the parties' obligations, allocates risk, alters remedies, modifies termination triggers, redefines defined terms in a way that propagates |
| **medium** | Clarifies ambiguity, narrows or widens existing scope without re-allocating risk, adds detail to existing obligations |
| **low** | Drafting-quality changes — reordering, rewording with no legal effect, typo fixes, formatting |

The pill colour in the UI maps directly to this scale (red / amber /
green). Trust the lawyer reading the redline more than the pill —
the LLM is a triage tool, not a substitute for review.

## Compliance + audit

Every compare call writes an audit-log entry (event recorded as a
chat with 2 sources). The entry does not capture the documents
themselves — those live in the ingest store with the firm's normal
retention policy. The `query_hash` field is empty because there is
no semantic query, just two doc IDs.

## Failure modes

| Failure | What you'll see | Fix |
|---|---|---|
| LLM backend unavailable | Per-section commentary shows `"LLM commentary unavailable in this deployment"` and falls back to structural diff only | Resolve the LLM backend issue (see [`incidents-service.md`](incidents-service.md)) |
| One doc is image-only PDF | API returns 415 with `"unsupported or empty document"` | Run OCR (out of scope for v1) or paste the text manually using `text_a` / `text_b` |
| One doc is over 200 KB extracted text | API returns 413 | Compare section-by-section using the paste form |
| Headings don't match | Many sections show as `added` + `removed` rather than `rewritten` | Normal when sections have been renumbered between drafts. The structural diff still surfaces; the LLM still comments. If it's noisy, compare smaller fragments via paste |

## Related

- [`docs/sop/document-acl.md`](document-acl.md) — per-doc ACL system
  (both sides of a compare are gated)
- [`docs/sop/conflict-checks.md`](conflict-checks.md) — sister
  feature for new-matter intake; comparison is for matter-in-progress
- [`docs/sop/incidents-service.md`](incidents-service.md) — what to
  do when the LLM or retrieval backend is down

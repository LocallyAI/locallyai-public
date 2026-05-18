# Citation checker

Verify that case-law and statute citations in any text are real,
findable, and being used correctly. Designed to catch
hallucinated citations in AI-generated drafts and to spot
mis-citations in human-drafted work.

## What the engine does

Given any text, the engine:

1. **Extracts citations** via a regex catalogue covering:
   - **UK** — neutral citations (`[YYYY] UKSC N`, `[YYYY] EWCA Civ N`,
     `[YYYY] EWHC N (Ch)`, `[YYYY] UKHL N`, `[YYYY] UKPC N`),
     reporter citations (`[YYYY] AC N`, `[YYYY] N WLR M`, `[YYYY] All ER`),
     and statute references (`<Act Name> Act YYYY[, s N]`)
   - **US** — federal reporters (`N U.S. M`, `N F.3d M`, `N S.Ct. M`)
   - **KSA** — royal decrees (`Royal Decree No. X dated DATE` /
     `مرسوم ملكي رقم X`)
2. **Per citation, runs three checks**:
   - **In-corpus** — BM25 search over the firm's own documents.
     Surfaces if the firm has previously discussed or cited this
     authority
   - **External** — for UK only in v1, queries BAILII (free, public
     case database). Positive matches cache for 30 days in
     `storage/citations_cache.json`. KSA + US external lookup is not
     yet implemented; the corresponding badge says "external check
     unavailable"
   - **On-point (LLM)** — given the citation + ~400 chars of
     surrounding context, the model judges whether the authority is
     being used *correctly* for the proposition. Returns
     `true` / `false` / `null` (insufficient evidence)
3. **Returns** a structured list of citations with verification
   metadata; the worker-ui decorates each with a status badge

## What the badges mean

| Badge | Triggers when | Meaning |
|---|---|---|
| **Verified** (green) | External match + LLM says on-point | High confidence the cite is real and being used correctly |
| **Found externally** (green) | External match, LLM uncertain | Real cite; on-point judgement deferred |
| **Found in corpus only** (amber) | BM25 hit in firm corpus, no external match | Cite exists in the firm's documents — may be a real cite the firm has used before. Worth manual check |
| **Real citation, possibly inapposite** (red) | External match + LLM says NOT on-point | Real citation, but the model thinks it doesn't support the proposition. Manual review essential |
| **Not found** (red) | No external match, no corpus hit | Likely hallucinated citation, or a cite from a database the engine doesn't cover. Manual verification required before relying on it |

## What the engine does NOT do

- It does **not** verify obscure or local-court citations outside
  the regex catalogue. If the parser doesn't extract a citation
  from your text, the engine can't verify it
- It does **not** verify scholarly articles, treatises, or restatements
- It does **not** access subscription databases (Westlaw, LexisNexis,
  Justis, Tabakhi) — only BAILII (free) for UK in v1
- It does **not** assess the legal merit of an on-point citation
  (the LLM is checking textual fit; *whether the cite supports the
  argument* is the lawyer's call)
- It is **not** authoritative — even a green-badged cite must be
  confirmed by the lawyer responsible for the work product

## Operator workflow

### Worker UI

1. After receiving an AI-generated response, click **Verify
   citations** in the message footer
2. The engine runs (3–10 s for typical text) and shows a list of
   citations under the response
3. For each citation, the badge shows the verification status. Click
   **Open in BAILII** to read the source case
4. The on-point reasoning + suggestion are the LLM's opinion —
   surface as a starting point for the lawyer's review

### API

```bash
curl -X POST http://office.local:8000/v1/citations/verify \
  -H "Authorization: Bearer $USER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "In Donoghue v Stevenson [1932] AC 562, the House of Lords established the modern law of negligence. The Companies Act 2006, s 172 codifies directors duties."
  }'
```

Returns `{citations: [...], elapsed_ms, count}`. Each citation
carries `cite, jurisdiction, kind, year, parsed, span, context,
found_in_corpus, found_external, verified, on_point`.

## Caching + rate limits

- BAILII results cache for **30 days** in `storage/citations_cache.json`.
  Cache hits are flagged in the response with `from_cache: true`
- Negative results are NOT cached — transient network blips
  shouldn't poison the cache
- BAILII may rate-limit aggressive querying. The engine retries
  once on transient failure; persistent failure surfaces as `reason:
  "BAILII unreachable"` and the citation falls back to corpus-only
  verification

## Configuration

| Env var | Effect |
|---|---|
| `LOCALLYAI_CITATIONS_NO_EXTERNAL=1` | Disable BAILII / external lookups entirely. Useful for offline deployments, KSA-only firms, or when BAILII is unreliable |

## Compliance + audit

Every verify call writes an audit-log entry. The `sources` field
records the number of citations verified; the text being verified
is not stored beyond the audit's normal pseudonymisation.

The citation cache is **firm-local data** — citations the firm has
verified are part of the firm's deployment state. Cache rotation
follows the same retention rules as `storage/`. The DPO controls
whether the cache is included in backups.

## Failure modes

| Failure | What you'll see | Fix |
|---|---|---|
| BAILII down or rate-limited | Citations badge as "Found in corpus only" or "Not found" with `reason: BAILII unreachable` | Wait + retry; check BAILII status. The engine never blocks waiting for BAILII — degraded results are returned within the timeout |
| LLM backend down | On-point check returns `null` with `reasoning: "LLM backend unavailable"` | Resolve LLM; manual verification of every citation |
| Retrieval backend down | In-corpus check returns `found: false` with `reason: "retrieval unavailable"` | Resolve the retriever; BAILII + LLM still work |
| Citation format outside catalogue | Citation simply isn't extracted | Add the format to the regex catalogue in `citations.py` and re-deploy. Coverage gaps are tracked in `docs/sop/roadmap.md` |
| KSA shariah-court cite | Extracted as `KSA / decree`; external check returns `"no public KSA case database"` | Manual verification — the engine flags this rather than returning a false negative |

## Related

- [`docs/sop/document-comparison.md`](document-comparison.md) — sister
  feature for redline understanding
- [`docs/sop/conflict-checks.md`](conflict-checks.md) — sister feature
  for new-matter intake
- [`docs/sop/incidents-service.md`](incidents-service.md) — what to
  do when LLM or retrieval is down

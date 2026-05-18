# Conflict-of-interest checks

LocallyAI ships a first-pass conflict-check engine that runs against
the firm's existing matter corpus + audit log. It is **advisory** —
it surfaces hits the partner needs to review. The firm's own
conflict-of-interest policy (per **SRA Code of Conduct Principle 7
and IB(3.1)–IB(3.7)** in England & Wales, or **KSA Bar Bylaws Art.
22** for the Saudi rules) remains the authoritative process.

## Why this exists

Every law firm above ~5 lawyers runs conflict checks on every new
matter. The traditional process is a manual lookup in the firm's
matter database, often duplicated across several systems. Three
problems with the manual process:

1. **Patchy** — the partner who opens the matter is the only person
   who knows what to look for; nothing prompts them to look across
   matters they personally weren't involved in
2. **Slow** — for firms above ~30 lawyers the conflict check often
   delays new-matter intake by 24–72 hours
3. **Inconsistent** — what counts as a "conflict" depends on the
   reviewer's experience; junior staff miss subtle same-side patterns

LocallyAI's automated first pass solves (1) and (2). The partner
still owns (3), but they're working off a complete + consistent hit
list.

## What the engine does

Given **proposed parties + a short description**, the engine:

1. **Normalises** party names — lower-case, whitespace-collapse,
   strips common corporate suffixes (`Ltd`, `LLC`, `Inc`, …) so
   `Acme Ltd` and `ACME Limited` match
2. **Searches the firm corpus** — Qdrant dense + BM25 sparse with
   the parties as the query. Each hit carries source + matter_code +
   relevance score
3. **Buckets the hits**:
   - **Strong** (score ≥ 0.6) — feeds the LLM pass
   - **Weak** (0.4 – 0.6) — surfaces in the UI but excluded from
     the LLM judgement to keep its context focused
   - **Dropped** (< 0.4) — discarded
4. **LLM pass** — feeds the strong hits + the new matter description
   to the model and asks: *"is this a conflict, a same-side
   involvement, or unrelated?"* Returns structured JSON with
   `status`, `summary`, `key_concerns`, `recommended_action`
5. **Persists the check** — writes a pseudonymised entry to
   `SHARED_DIR/conflicts.log` (HMAC-chained, replicated across HA
   peers via Syncthing)

## What the status badges mean

| Badge | When it fires | Operator response |
|---|---|---|
| **Conflict** (red) | LLM identified a direct conflict — firm acted for one side, would now act against | Decline the matter, OR pursue informed consent + ethical wall (UK SRA-permitted in narrow circumstances; **never** assume permitted in KSA) |
| **Review** (amber) | Hits exist but the relationship is ambiguous — same-side, related party, or partial-name match | Senior partner reviews the hit list before opening the matter |
| **Clear** (green) | Either no hits ≥ 0.4, or LLM judged unrelated | Proceed and open the matter; the conflict-log entry is the audit trail that the check ran |

## What the engine does NOT do

- It does **not** replace the firm's manual conflict-of-interest
  policy. SRA / KSA bar rules require a documented decision by a
  qualified person; the LLM is not qualified
- It does **not** check beneficial ownership chains, related parties
  not named in the input, or affiliated entities the firm hasn't
  ingested into the corpus
- It does **not** substitute for client-identity verification
  (KYC / AML)
- It does **not** flag a conflict for parties the firm has never
  acted for or against — the corpus is the only source of truth

## Operator workflow

### Manager UI (recommended)

1. Manager UI → **Conflicts**
2. Fill in the form:
   - **Matter ID** (optional) — your internal reference, e.g. `2026-046`
   - **Parties** — at least the proposed client and any opposing
     party. Use the **Add party** button for additional names.
     Roles: `client`, `opposing`, `interested`
   - **Matter description** — 1–2 sentences explaining what the
     engagement is. Helps the LLM disambiguate same-name parties
   - **Opposing counsel** (optional, comma-separated) — adds them
     to the corpus search
3. Click **Run conflict check**. Typical latency: 2–6 seconds for
   a typical hit list size on the M3 Ultra
4. The result panel shows the status, summary, key concerns,
   recommended action, and ranked hit list with snippets
5. The new check appears in **Recent checks** at the bottom

### API (for matter-intake-system integration)

```bash
curl -X POST http://office.local:8000/v1/conflicts/check \
  -H "Authorization: Bearer $LOCALLYAI_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "matter_id": "2026-046",
    "parties": [
      {"role": "client",   "name": "Acme Holdings Ltd"},
      {"role": "opposing", "name": "Widget Corp"}
    ],
    "description": "Acquisition of Widget Corp by Acme — buy-side advice",
    "opposing_counsel": ["Smith & Co LLP"]
  }'
```

Response shape: `{status, summary, key_concerns, recommended_action,
hits, llm_assessment, checked_at, elapsed_ms, matter_id}`.

## Conflict log

`SHARED_DIR/conflicts.log` is a JSON-lines file, one entry per check.
Each entry:

- `timestamp`, `matter_id`, `requester`
- `parties_hashed` — `[{role, hash}]` where `hash` is
  SHA-256(salt + "party:" + normalised name) truncated to 16 hex
  chars. Same salt as the audit-log user pseudonyms — a single
  `LOCALLYAI_AUDIT_SALT` rotation invalidates every log uniformly
- `opposing_counsel_hashed` — same hash function
- `status`, `summary`, `hit_count_strong`, `hit_count_weak`
- `decision`, `decided_by`, `decided_at` — initially `pending` /
  `null` / `null`. A future `/v1/conflicts/{id}/decide` endpoint
  will let the partner record the final decision; for v1 this is
  unset

The log is HMAC-chained the same way the audit + billing logs are
(SHA-256 over `prev || entry` keyed with `LOCALLYAI_AUDIT_HMAC_KEY`).
Tampering produces a chain break that the verifier will catch.

## Compliance integration

The monthly DPO snapshot (`/admin/compliance/snapshot`) includes a
**Conflict checks** section showing total + last-30-day counts +
status mix. Auditors look for evidence the firm runs checks
*systematically* — the section gives them that evidence without
disclosing party identities (party names are pseudonymised).

## Edge cases

- **Same name, unrelated parties** (e.g. two unrelated `Smith Ltd`
  in different industries) — the LLM uses the matter description to
  disambiguate. If the description is empty, the LLM will likely
  return `review` to be safe; partners see the hits and decide
- **Acquirer-side after target-side** (the classic M&A flip) — the
  LLM is trained to spot this pattern in the corpus. Status:
  `conflict` with `key_concerns` calling out the acquisition pattern
- **Foreign-language party names** — the underlying retriever +
  the BAAI/bge-reranker-v2-m3 cross-encoder are multilingual.
  English transliterations (e.g. شركة الواحة → "Al Waha Company")
  may need to be checked under both spellings
- **Opposing counsel as a separate firm** — included in the corpus
  search via the `opposing_counsel` field. A hit on opposing
  counsel surfaces in the hit list but does not by itself force
  `review` — the LLM uses it as context

## Failure modes

| Failure | What you'll see | Fix |
|---|---|---|
| LLM backend unavailable | Result returns with `status=review` and a generic key-concerns list pointing at the raw hits | Resolve the LLM backend issue (see [`incidents-service.md`](incidents-service.md)) — meanwhile the partner reviews hits manually |
| Retrieval backend unavailable | Empty hit list + `status=clear` with summary "No related-matter hits found" | **DO NOT TRUST** — this looks the same as a genuinely clear check. Cross-check with `/healthz` before opening any matter when you suspect retrieval is degraded |
| `LOCALLYAI_AUDIT_HMAC_KEY` not set | Conflict log entries write but `_chain_hmac` is empty | Set the key (see [`compliance.md`](compliance.md)) — without it, the chain is unforgeable but unverifiable |
| Disk full on `SHARED_DIR` | API returns 500; check fails to persist | Free disk space; the check itself ran but is not in the log |

## Related

- [`docs/runbooks/conflict-check.md`](../runbooks/conflict-check.md) —
  10-minute operator runbook for the UI
- [`docs/sop/document-acl.md`](document-acl.md) — `matter_code`
  metadata used by both ACLs and conflict checks
- [`docs/sop/compliance.md`](compliance.md) — DPO monthly snapshot,
  HMAC chains, audit log

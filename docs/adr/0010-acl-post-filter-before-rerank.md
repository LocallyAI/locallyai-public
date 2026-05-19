# 0010 — Per-document ACL as a post-filter applied BEFORE cross-encoder rerank

- **Status:** accepted
- **Date:** 2026-05-12
- **Deciders:** single-author
- **Tags:** retrieval, security, performance

## Context

The default LocallyAI behaviour is "every authenticated user can retrieve from every chunk in the firm's corpus". This works for small firms with shared knowledge bases, but breaks for:

- **Partner-only documents** (board materials, internal financials, employment records).
- **Matter-restricted documents** (M&A deal rooms where only the deal team should see the target's data room).
- **Ethical-wall separation** (the same firm acting for both sides of a transaction needs strict information barriers — UK SRA Outcome 4 / KSA Bar Bylaws Art. 22).

Per-document ACLs are how this is enforced. Each document has an entry in `SHARED_DIR/doc_acls.json`: `{source: "filename.pdf", allowed_users: ["alice", "bob"], matter_code: "2026-046", ethical_wall: ["acquirer-side"]}`. The ACL store is fcntl-locked, atomic-write, replicated across HA peers via Syncthing ([ADR-0005](0005-mac-ha-syncthing-rsync.md)).

The retrieval pipeline ([ADR-0001](0001-hybrid-retrieval-rrf-rerank.md)) is: **dense + BM25 → RRF fusion → cross-encoder rerank → top-K**. The ACL filter has to slot in somewhere. Three plausible positions:

1. **Pre-retrieval Qdrant filter only** — push `allowed_users CONTAINS <user>` into the dense-retrieval query.
2. **Post-fusion, pre-rerank** — let the Qdrant + BM25 retrievers return whatever they return, then drop disallowed chunks from the fused candidate pool before the cross-encoder runs.
3. **Post-rerank** — rerank everything, then drop disallowed chunks from the top-K right before the LLM sees them.

Each has different cost, correctness, and legacy-compatibility properties.

## Decision

**Belt-and-braces approach:**

1. **Qdrant payload also carries `allowed_users` + `matter_code`** as an optimisation — when a chunk is ingested, its ACL is denormalised into the Qdrant payload so dense retrieval can push-down the filter and avoid pulling forbidden chunks from disk in the first place.
2. **The canonical authority is the post-filter** at `doc_acls.is_allowed(source, user)`, applied **after RRF fusion and before cross-encoder rerank**. This is the only place that decides "can this user see this chunk?"

The post-filter precedes the rerank for **performance** (the cross-encoder is the most expensive stage; not running it on chunks the user can't see saves real cost — typically 30-80% of rerank cost in firms with sparse ACLs).

The post-filter is **canonical** (not just additional defence) because legacy chunks ingested before the ACL feature shipped have no `allowed_users` payload field — a Qdrant-only filter would silently drop them or silently include them depending on filter semantics; the post-filter resolves them against `doc_acls.json` with the documented `LOCALLYAI_DOC_ACL_DEFAULT=open|restricted` default policy.

The rerank step itself is ACL-naive — it scores whatever it's handed. The ACL filter is **before** the rerank; nothing slips through *after* rerank that the cross-encoder hasn't already pruned to the allowed set.

See `doc_acls.py:is_allowed`, `retrieval.py:HybridRetriever.retrieve`.

## Alternatives considered

- **Qdrant filter push-down only.** Cleanest in theory: the dense retriever returns only chunks the user can see; no post-filter needed. Rejected because (a) BM25 (the other retriever) doesn't share the Qdrant index, so BM25 results need a separate filter anyway, and (b) legacy chunks without payload ACL silently fall outside the filter — a quietly wrong outcome.
- **Post-rerank ACL filter.** Simpler to reason about — the rerank operates on the full candidate pool and you trim at the end. Rejected on performance: at 50 candidates and ~150 ms p50 rerank cost, running the cross-encoder on chunks the user can't see is pure waste. For firms with strict ACLs (only 5/50 candidates allowed), this is 10× wasted compute per query.
- **No ACL filter** — rely on the LLM to redact answers from forbidden documents. Rejected as fundamentally broken — the model has no way to know what the user is or isn't permitted to see; "redacting via prompt" is taught-pattern-shaped, not access-control-shaped. Also fails SRA / PDPL audit.
- **ACL at the document level rather than chunk level.** Considered. The current design IS document-level (the ACL is keyed by source filename) but applied per-chunk because the post-filter sees the chunk's `source` payload. Chunk-level keying would let a single document have per-section ACLs (e.g. "everyone can see this contract, but only the deal team can see the schedule"), which is a future feature; the current per-source design is the right primitive.
- **ACL filtering moved into Qdrant's filter DSL with a custom payload schema.** Considered. Rejected because it splits the truth: the JSON file (Syncthing-replicated, fcntl-locked) is the canonical source; the Qdrant payload is denormalised optimisation. Making Qdrant authoritative would break (a) HA replication semantics (Qdrant is per-node, JSON is shared), and (b) the legacy-chunk recovery story.

## Consequences

### Positive

- **Performance win.** Cross-encoder rerank runs over the ACL-allowed subset, saving 30-80% of rerank cost on ACL-strict firms. Measured directly via the `acl_ms` + `rerank_ms` timings in `/monitor/health/detailed`.
- **Correctness under legacy.** Pre-ACL-feature chunks still get evaluated against the documented default policy (`LOCALLYAI_DOC_ACL_DEFAULT=open|restricted`) — no silent drops, no silent inclusions.
- **Single source of truth.** Operators audit `SHARED_DIR/doc_acls.json` (small JSON file) rather than reasoning about Qdrant payload state. Edits via the manager UI are atomic; HA replication is fcntl-protected.
- **Ethical-wall + matter-code metadata** ride along with the ACL entry — the same lookup that decides "can this user see this chunk?" also surfaces the matter context the audit log records.

### Negative

- **Two ACL evaluations per query** (Qdrant push-down + post-filter). For firms without strict ACLs (`allowed_users=["*"]`), the post-filter is essentially a no-op but still runs. Cost: ~1 ms over the candidate pool — negligible.
- **Re-ingest required after schema additions.** When the ACL payload schema gains a new field (e.g. the future `ethical_wall` field), existing chunks need re-ingestion to populate it. Documented in `docs/sop/maintenance.md`.
- **The default policy is operator-set, not feature-set.** A firm that installs with `LOCALLYAI_DOC_ACL_DEFAULT=open` (the default) won't get restricted behaviour until they flip the env var. This is intentional — restricted-by-default would break upgrades for firms not using ACLs — but it does mean an operator who skims the docs can end up with a less-strict-than-they-thought posture.

### Neutral

- **The post-filter is the canonical authority.** Documented in `docs/sop/document-acl.md` so operators know that flipping a payload entry without updating `doc_acls.json` won't change behaviour (the post-filter overrides).
- **ACL evaluation happens with the user identity** (the authenticated user from the API key); admin / DPO bypasses for compliance use cases are explicit (audit-log lookups, snapshot generation).

## References

- `doc_acls.py:is_allowed` — canonical post-filter check
- `retrieval.py:HybridRetriever.retrieve` — pipeline placement (ACL before rerank)
- `ingest.py` — `allowed_users` + `matter_code` payload denormalisation at ingest time
- `docs/sop/document-acl.md` — operator guide
- `monitoring/monitor.py` — exposes `acl_ms` + `acl_dropped` + `rerank_ms` per request
- ADR-0001 (retrieval pipeline this slots into)
- ADR-0005 (HA replication of `doc_acls.json`)
- SRA Code of Conduct Outcome 4 (own-client confidentiality / ethical walls)
- KSA Bar Bylaws Art. 22 (conflict / information barrier)

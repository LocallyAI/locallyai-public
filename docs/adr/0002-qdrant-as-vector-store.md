# 0002 — Qdrant as the vector store (embedded → external for HA)

- **Status:** accepted
- **Date:** 2026-05-03
- **Deciders:** single-author
- **Tags:** infra, retrieval, storage

## Context

The platform needs a vector store for dense retrieval (see [ADR-0001](0001-hybrid-retrieval-rrf-rerank.md)). The constraints — informed by the deployment topology — are unusual for a vector-store choice:

1. **Single-Mac default deployment.** The smallest viable LocallyAI install is one Apple Silicon Mac running the API, the LLM, the embedder, and the vector store. Nothing managed; no DBA; no extra service to monitor. An external dependency is friction the firm's IT person eats forever.
2. **Two-node HA topology** is the production target ([ADR-0005](0005-mac-ha-syncthing-rsync.md)). At that scale, the vector store may need to be shared across nodes or per-node-replicated.
3. **Per-firm corpora are 10K–500K chunks**, growing slowly. No firm is going to push 10M chunks; the upper bound on scale is well-defined.
4. **Payload filtering matters.** Per-document ACLs and matter-code filters are pushed down to Qdrant so dense retrieval is already scoped server-side (see [ADR-0010](0010-acl-post-filter-before-rerank.md)).
5. **License posture matters.** A vector store that flips to a commercial-only licence later (Elastic, Redis pre-2024) would be a strategic timebomb.

The question: which vector store handles both the single-Mac embedded mode and the two-node external mode under one API?

## Decision

**Qdrant** — embedded mode (in-process, file-backed at `storage/collection/`) for single-Mac deployments, switched to external mode (Docker on one of the Mac fleet) via `QDRANT_URL` env var for HA pairs. Same `qdrant-client` Python library against either; same payload schema; same filter syntax.

See `config.py:make_qdrant_client`, `ingest.py`, `retrieval.py`.

## Alternatives considered

- **LanceDB.** Embedded-first, parquet-backed, very small footprint, no separate server. The strongest contender. Rejected because (a) payload filtering was less expressive at the time — needed JSON-path-style queries that map cleanly to ACL + matter-code constraints, and Qdrant's filter DSL handles this natively, and (b) the HA path (multi-process readers/writers against a shared parquet) wasn't well-trodden compared to Qdrant's HTTP API.
- **pgvector** (Postgres with vector extension). Reuses an existing Postgres deployment if the firm already has one. Rejected because **most firms do not have a Postgres deployment**, and asking a small law firm to install + maintain Postgres just for vector search is a non-starter. Also: pgvector's filtering is great (it's SQL) but its ANN index (`ivfflat` / `hnsw`) requires manual `REINDEX` tuning that's exactly the kind of DBA work LocallyAI tries to remove.
- **Weaviate.** Generally well-regarded, similar feature surface. Rejected primarily because (a) it requires a separate server even for the smallest install — no embedded mode — and (b) the resident memory footprint of an idle Weaviate instance was ~400 MB at the time, which is wasteful on a Mac that's already hosting a 7B LLM.
- **Milvus.** Production-grade scale (billion-vector clusters). Massive operational overhead — etcd, MinIO, multiple processes. Rejected as fatally over-provisioned for the per-firm corpus size; we're not building a Spotify-scale recommender.
- **Chroma.** Embedded-first, easy DX. Rejected because filter expressiveness was thin at the time (no nested or compound filters that the ACL story needs) and the on-disk format had churned across versions.
- **FAISS** directly. The lowest-level option — no payload, no server, no filters. Rejected because doing payload filtering manually in Python after retrieval (rather than push-down at the index) hurts at 50K+ documents and re-implements the filtering Qdrant already has well.

## Consequences

### Positive

- **Single library, two topologies.** `qdrant-client` works against the in-process embedded mode and the HTTP server mode with no code changes. The HA upgrade path (single-Mac → 2-node fleet) doesn't require a vector-store rewrite.
- **Filter push-down for ACLs + matter codes.** ACL constraints live in the Qdrant payload (`allowed_users`, `matter_code`); filtered retrieval is server-side, not Python-side. See ADR-0010 for why this is *also* applied as a post-filter (defence in depth for legacy chunks without payload).
- **Embedded mode = zero ops** on single-Mac deployments. The firm's IT person never knows Qdrant exists; it's just `storage/collection/`.
- **License is Apache-2.0** — stable open-source posture, no commercial-tier ratchet to worry about.

### Negative

- Qdrant's embedded mode is **single-writer** — concurrent ingest from multiple processes would corrupt the file-backed collection. Mitigated by running ingest in a single in-process worker (`ingest_queue.py`) and locking via `shared_lock.py` for cross-process operations.
- Schema migrations between Qdrant versions are not automated for the embedded path. A `qdrant-client` major-version bump requires manually rebuilding the collection from the BM25-index re-ingest path. Documented in `docs/sop/maintenance.md`.
- Idle resident memory of Qdrant embedded is ~150 MB even with an empty collection. For very small single-Mac deployments this is mild waste.

### Neutral

- The on-disk format is Qdrant-specific. A future store-swap means re-ingesting the corpus (which is cheap — re-ingest is the same code path as new ingest, just scaled up).
- `QDRANT_URL` is documented in `.env.example` as the single switch between embedded and external mode. The HA SOP explains when to flip it.

## References

- `config.py:make_qdrant_client` — factory that returns embedded or HTTP client based on `QDRANT_URL`
- `ingest.py` — chunk → embed → Qdrant insertion
- `retrieval.py:HybridRetriever` — Qdrant search + payload filtering
- `docs/sop/ha-architecture.md` — when to switch to external Qdrant
- `docs/sop/maintenance.md` — Qdrant version-upgrade procedure
- ADR-0010 (ACL filtering pipeline placement)
- ADR-0005 (HA topology)

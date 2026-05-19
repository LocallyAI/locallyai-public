# 0001 — Hybrid retrieval (Qdrant + BM25 → RRF fusion → cross-encoder rerank)

- **Status:** accepted
- **Date:** 2026-05-03
- **Deciders:** single-author
- **Tags:** retrieval, performance

## Context

LocallyAI does retrieval-augmented generation over a firm's own document corpus — typically a few thousand to fifty-thousand documents (PDFs, DOCXs, MDs) of contracts, advice memos, regulatory filings, and matter files. Three constraints apply:

1. **The corpus is multilingual.** UK firms are English-only; KSA firms mix English and Arabic in the same document set. Pure English-trained models silently mis-rank Arabic content.
2. **Precision matters more than recall at the LLM stage.** A lawyer reading a wrong-but-plausible citation in the LLM's output is a worse failure than the LLM declining to answer. Cheap-and-recall-y retrieval upstream → confident-and-wrong generation downstream.
3. **The whole pipeline runs on one Apple Silicon Mac** (M2/M3/M4). No external GPU cluster, no managed search service. CPU + MPS is the budget.

Initial implementation was Qdrant dense retrieval only. At ~5K documents this was fine; at ~50K documents the "Sources" panel started showing 3–5 medium-relevance chunks alongside the actual answer, and the LLM's responses degraded — it pattern-matched on the noise and produced confident but wrong synthesis.

The question: how to lift retrieval precision at 50K+ documents without a GPU.

## Decision

Three-stage hybrid pipeline:

1. **Dense retrieval (Qdrant + `intfloat/multilingual-e5-base`)** + **sparse retrieval (BM25, in-process Python implementation)** over the same firm corpus. Each returns its own ranked list of `CANDIDATE_POOL` ≈ 50 candidates.
2. **Reciprocal Rank Fusion** combines the two lists into one ranked list. RRF score = Σ 1 / (k + rank_i) with k=60. Order-preserving, no score calibration needed, robust to one retriever returning garbage.
3. **Cross-encoder rerank** of the fused top-50 using `BAAI/bge-reranker-v2-m3` (568M params, multilingual, MPS-accelerated). The cross-encoder scores (query, candidate) pairs jointly — not a vector similarity — producing a much sharper precision-at-5.

Per-document ACLs are applied **between** stage 2 and stage 3 (see ADR-0010) so we don't waste cross-encoder cost on chunks the user can't see.

See `retrieval.py:HybridRetriever.retrieve` and `reranker.py`.

## Alternatives considered

- **Dense-only retrieval (Qdrant alone).** What we started with. Fast (~50 ms p50 on 50K docs) and recall is fine, but precision-at-5 was ~70% on the internal eval set. Rejected because the downstream LLM amplifies noisy sources into confident-but-wrong outputs — exactly the failure mode law firms cannot tolerate.
- **BM25-only retrieval.** Tested as a baseline. Best on exact-citation lookups ("find me Section 8 of the Companies Act 2006") but blind to paraphrase and synonyms. Rejected; BM25 alone leaves the 50% of queries that don't share keywords with the answer un-served.
- **ColBERT / late-interaction models** instead of cross-encoder rerank. Higher precision than a single bi-encoder, lower latency than a cross-encoder. Rejected because (a) the model footprint at the time (≥3 GB) was tight on machines that also need to host the LLM + embedder, and (b) Qdrant didn't have first-class ColBERT-style multi-vector support; using it would have meant a second vector store.
- **Single bigger embedder model** (e.g. `intfloat/multilingual-e5-large`) with no rerank. Marginal improvement (~5 pp precision-at-5) for ~3× the resident memory. Rejected — the marginal precision didn't justify the memory hit, and cross-encoder rerank gets more lift cheaper.
- **LLM-as-reranker** (have the inference model itself score the top-50). Highest theoretical quality. Rejected because (a) it serialises with chat generation through the same MLX backend (inference gate), creating queue contention, and (b) the latency is ~10× the cross-encoder for marginal quality gain on this corpus type.

## Consequences

### Positive

- Precision-at-5 lifted from ~70% to ~85% on the internal eval set at 50K documents (qualitative — the hard metric is "do partners trust the Sources panel"; they now do).
- Multilingual quality is uniform — `multilingual-e5-base` + `bge-reranker-v2-m3` both handle Arabic and English well, no separate KSA pipeline needed.
- RRF is forgiving: if BM25 returns rubbish for a particular query, the dense list still dominates the top-K. No score-calibration pain.
- Reranker is hot-swappable — the model name lives in env (`LOCALLYAI_RERANKER_MODEL`), pin-enforced via `.reranker_lock` mirroring `mlx_inference._read_pin`.

### Negative

- Adds ~150 ms p50 to retrieval latency on the MPS path (the cross-encoder pass over 50 candidates). On CPU-only deployments it's worse — ~3 s — so MPS auto-detection (Apple Silicon) is mandatory.
- Reranker is ~1 GB resident memory in addition to the embedder and the LLM. Sizing tool's RAM heuristic was bumped to account for it.
- Cold-load on first chat after a process restart is now ~3.5 s (model download + load). Mitigated with a warm-up probe at startup but the first user-visible call still pays the cost.
- Cross-encoder rerank can be hard-disabled (`LOCALLYAI_RERANKER=off`) for debugging, but operators almost never do — adds an "is the reranker actually loaded?" diagnostic concern surfaced via `/monitor/health/detailed`.

### Neutral

- The candidate pool size (`CANDIDATE_POOL=50`) is a tunable, not a constant. Smaller pools save rerank cost; larger pools lift recall at the cost of latency. 50 is the empirical sweet spot for this corpus type.
- Pipeline order is **dense + BM25 → RRF → ACL → rerank → top-K**. The ACL step before rerank is load-bearing — see ADR-0010.

## References

- `retrieval.py:HybridRetriever.retrieve` — main entry
- `reranker.py` — cross-encoder loader + scorer
- `bm25.py` — sparse retriever
- `config.py` — `CANDIDATE_POOL`, `TOP_K`, `RERANKER_MODEL`
- `monitoring/monitor.py` — `/monitor/health/detailed` exposes per-phase timings (dense_ms, bm25_ms, rrf_ms, acl_ms, rerank_ms)
- ADR-0010 (ACL placement in the pipeline)
- Cormack et al. (2009), *Reciprocal Rank Fusion outperforms Condorcet and individual rank learning methods*

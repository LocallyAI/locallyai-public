# 0008 — Multi-backend inference (MLX + Ollama + LM Studio) behind one interface

- **Status:** accepted
- **Date:** 2026-05-03
- **Deciders:** single-author
- **Tags:** inference, performance, infra

## Context

LocallyAI runs an LLM on hardware the firm owns. Three realistic backends fit the constraint:

| Backend | Strength | Weakness |
|---|---|---|
| **MLX** (Apple Silicon) | Native Apple Silicon optimisation; lowest tokens-per-second-per-watt; in-process Python | Apple-only; smaller model selection than HuggingFace generally |
| **Ollama** | Cross-platform; ergonomic model management; ubiquitous in the local-LLM ecosystem | HTTP-based (extra hop); has had Metal-runner stability issues that needed self-heal handling |
| **LM Studio** | GUI-first model management; appeals to non-technical operators evaluating models | HTTP-based; less battle-tested in production; less debuggable than Ollama |

The platform's customers split:

- **Small UK firms** running on a single Mac Studio — MLX is the right answer (best perf per watt on the hardware they bought).
- **Larger firms with mixed Mac + Windows** fleet — Ollama is the lowest-common-denominator (it runs on both, same model file format works on both).
- **Firms still evaluating** which model to deploy — LM Studio's GUI helps the partner click through models before committing.

Picking one and only supporting it forces customer compromises. Supporting all three with three independent code paths multiplies maintenance.

The question: how to support all three with a single API surface and zero per-customer codepath fork?

## Decision

**Strategy pattern behind a single `generate(messages, model, max_tokens, temperature, stream) -> str | Iterator[str]` function.** The implementation is selected at startup by `LOCALLYAI_BACKEND=mlx|ollama|lmstudio` env var. The OpenAI-compatible API ([ADR-0006](0006-openai-compatible-api-surface.md)) is the shared surface; the backend module is the strategy.

- **MLX path** (`mlx_inference.py`): in-process `mlx_lm.generate` with a global single-thread queue (`inference_gate.py`) because MLX cannot safely interleave generations.
- **Ollama path** (HTTP to `localhost:11434/api/chat`): standard Ollama API client with stream-to-SSE adapter.
- **LM Studio path** (HTTP to `localhost:1234/v1/chat/completions`): LM Studio already speaks OpenAI's API natively, so the adapter is thin.

Model identity is pin-enforced for MLX (`.model_lock` file with HuggingFace commit SHA; load refuses on drift unless `LOCALLYAI_MODEL_DRIFT_ACK=1`). Pin enforcement was extended to the cross-encoder reranker (`.reranker_lock`) for the same reasons.

Health probing is backend-aware (`monitoring/monitor.py:_backend_check`) so an MLX deployment doesn't get a permanent CRITICAL "Ollama unreachable" alert.

## Alternatives considered

- **MLX-only.** Best per-watt; lock-in to Apple Silicon would foreclose the Windows DGX-Spark deployments LocallyAI also targets. Rejected on portfolio breadth.
- **Ollama-only.** Cross-platform, ergonomic, the "boring" choice. Rejected because (a) Ollama runs ~30% slower than MLX on the same Apple Silicon hardware for the same model, and (b) Ollama has had Metal-runner crashes that needed dedicated self-heal logic in the watchdog — running natively on MLX avoids that whole failure class.
- **vLLM.** Best throughput for batch serving. Rejected because (a) it's Linux + NVIDIA primarily, (b) the single-user inference pattern LocallyAI hosts (one chat, one response) doesn't exercise vLLM's batching wins, and (c) deploying it on the firm's Mac is operationally impractical.
- **llama.cpp directly** (no Ollama wrapper). Considered. Rejected because Ollama IS llama.cpp underneath with model-management UX added; cutting out Ollama means re-implementing that UX poorly. Worth reconsidering if Ollama ever bundles non-removable telemetry.
- **Multiple backends but a single chosen one per release.** Considered. Rejected because the operator's choice depends on their hardware + comfort level; the platform shouldn't pick for them.

## Consequences

### Positive

- **Operators pick the right backend for their hardware** without code changes — one env var.
- **The OpenAI-compatible surface is identical** regardless of backend choice. Consumer code (curl, SPAs, DMS connectors) doesn't know or care.
- **MLX path is fastest on the recommended Apple Silicon hardware** — ~30% improvement over Ollama on the same M3 Ultra for the same Qwen2.5-7B model. Measured in `tests/ha_chaos.py`-adjacent perf probes.
- **Backend-aware health checks** mean a deployment with `LOCALLYAI_BACKEND=mlx` doesn't get spammed with "Ollama unreachable" alerts. Each backend has its own health probe.
- **Self-heal logic is backend-specific.** The watchdog's Ollama-llama-runner-restart routine is conditional on `LOCALLYAI_BACKEND=ollama`; MLX deployments don't pay the false-positive cost.

### Negative

- **Three code paths to test.** Each backend can develop quirks independently. Mitigated by a small backend-agnostic test in `tests/smoke_e2e.py` that runs against whichever backend is configured.
- **MLX's in-process serialisation (one generation at a time)** is enforced by `inference_gate.py` because MLX state is process-global. This caps concurrency at 1 chat per node — fine for a small firm, would be a bottleneck at scale. Multi-node HA ([ADR-0005](0005-mac-ha-syncthing-rsync.md)) parallelises across nodes for partial relief.
- **Pin enforcement is per-backend.** MLX has `.model_lock`; Ollama has its own tag-based pinning via `OLLAMA_MODEL=qwen2.5-coder:7b@sha256:...`; LM Studio has weaker pinning. Operators using LM Studio in production are warned via the manager UI.
- **Operator support burden** when a user's backend choice doesn't match the SOP examples. Mitigated by the SOP being backend-agnostic by default and flagging backend-specific sections explicitly.

### Neutral

- **`LOCALLYAI_BACKEND=mlx` is the recommended default for Apple Silicon** in the install script. `=ollama` is the default for Windows. `=lmstudio` is opt-in for operator-driven evaluation.
- **Model size is bounded by the smallest backend.** Production runs Qwen2.5-7B (8-bit MLX, 4-bit Ollama) — fits on all three backends comfortably. 70B+ models work on MLX with enough RAM but aren't standard-issue.

## References

- `mlx_inference.py:generate` — MLX backend
- `inference_gate.py` — single-thread serialisation for MLX
- `config.py` — backend selection + Ollama vs LM Studio URL config
- `monitoring/monitor.py:_backend_check` — backend-aware health probe
- `watchdog/sentinel.py` — backend-conditional self-heal
- `.model_lock`, `.reranker_lock` — pin files for model integrity
- ADR-0006 (OpenAI API surface — what the backends serve)

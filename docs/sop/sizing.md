# Sizing — per-firm hardware + model recommendation

The Sizing tool lives in the **vendor monitor dashboard** at
`https://locallyai-monitor.your-cf-account.workers.dev/` → **Sizing** tab.
This is the vendor's cross-firm surface; firm-side UIs (the per-firm
Manager UI installed on each office Mac) intentionally do **not**
include it — firms don't size their own deployments.

The tool generates a hardware + model + checklist recommendation for a
new firm based on a small input profile. Use it during sales/onboarding
before ordering hardware, and again at contract renewal if usage
patterns have shifted.

A Python module at `sizing.py` runs the same logic for CLI / scripting
consumers (`scripts/onboard_firm.sh`). The dashboard's embedded
JavaScript is canonical at render time; the Python module mirrors it
for back-end use.

## Vendor policy: Q8 and FP only, never Q4

The model catalog intentionally **excludes Q4 (4-bit) quantised
models**. Q4 saves RAM but degrades enough that some legal-output
tasks become unreliable — wrong citations, dropped clauses, weaker
multi-hop reasoning on long documents. We standardise on:

- **Q8 (8-bit quantised)** for the small/medium RAM budget — ~99% of
  full-precision quality at half the RAM of FP16/BF16.
- **FP16 / BF16 (half-precision)** for the highest RAM tier (256 GB+)
  where fidelity is the priority.

Operators who want Q4 can override `LOCALLYAI_MODEL` in `.env`, but
the sizing tool never recommends it and our DPA-grade output claims
assume Q8 or higher.

## Inputs

| Field | Typical value | Notes |
|---|---|---|
| Total users (lawyers) | 5-100 | Headcount that may use the system |
| Concurrent users at peak | 20-30% of total | Mid-morning + post-lunch peaks |
| Document corpus (GB) | 1-500 | ~1 GB ≈ 5,000 pages of text-only PDFs |
| Region | UK or KSA | KSA forces multilingual embedder + Arabic-strong LLM |
| Primary use case | chat / research / mixed | Research-heavy = longer context, prefer fidelity |
| Quality preference | balanced / fidelity / throughput | "balanced" picks Q8 medium-tier; "fidelity" picks BF16 |
| Latency target (ms, p95) | 3000-10000 | Below 2000 needs the M3 Ultra and a small model |
| HA required | bool | Two identical Macs + Syncthing |

## Outputs

The tool returns four things:

1. **Hardware SKU + RAM + indicative price.** Cheapest Mac Studio
   configuration in the catalog that fits the computed RAM budget.
   M3 Ultra preferred at the same RAM tier (2× the memory
   bandwidth of M4 Max).
2. **Primary model + two alternatives.** All Q8 or BF16. Rationale
   per model. For KSA region only multilingual-strong models
   (Qwen2.5 family, Llama 3.1+) are eligible.
3. **Embedder.** `nomic-embed-text` for UK; `intfloat/multilingual-e5-base`
   for KSA (mandatory per LocallyAI policy).
4. **Dynamic checklist** of onboarding steps tailored to the profile.
   HA steps appear only if HA is recommended; KSA steps appear only
   for KSA region; bulk-ingest steps appear only for corpus ≥ 5 GB.

The performance envelope (tokens/second single-user, tokens/second
per-user at peak, cold-load time, P50/P95 latency) is an estimate
based on M3 Ultra baselines, scaled down ~40% for M4 Max.

## Sizing heuristics (under the hood)

```
ram_needed_gb =  model_size_gb
              +  concurrent_users × max(1, model_size_gb × 0.05)   # KV cache
              +  18 GB                                              # system + embedder + qdrant + headroom
              rounded up to nearest 4 GB
```

The model selector ranks the eligible catalog by a score function
that weighs:

- Concurrency pressure (higher concurrency → smaller model)
- Firm-size guidance (small firms → cap at 14B params)
- Quality preference (fidelity → biggest BF16; throughput → smallest)
- KSA multilingual preference (best > good > fair)
- MoE-model penalty for small firms (Mixtral's RAM weight not
  justified below ~30 users)

The cheapest catalog SKU that satisfies `ram_needed_gb` wins; ties
broken in favour of M3 Ultra over M4 Max.

## When to re-run

- **At sale time** — before ordering hardware. Run with the firm's
  best estimate of users + corpus. Cross-check the result against the
  firm's budget; downgrade quality preference to "throughput" or
  smaller model tier if needed.
- **At 6-month review** — actual concurrent-peak usage from the
  Manager UI's audit log frequently differs from the sale-time
  estimate. Re-run with updated numbers to confirm the firm hasn't
  outgrown its hardware.
- **At renewal** — re-run with current observed numbers; surface any
  recommendation to upgrade hardware or model.

## File references

- **Primary UI** — `docs/monitor/cloudflare-worker/src/dashboard/index.html` (Sizing tab)
- **Python module (CLI / scripts)** — `sizing.py` (catalog + heuristics)
- **No per-firm API endpoint** — the calculation is pure (no state, no
  deployment context required) and runs in-browser. The vendor monitor
  dashboard doesn't depend on any particular firm's deployment.

## Refreshing the catalog

Apple updates the Mac Studio lineup roughly annually. Update
`HARDWARE_CATALOG` in BOTH places — `sizing.py` AND the embedded
`HARDWARE_CATALOG` in `docs/monitor/cloudflare-worker/src/dashboard/index.html`.
Same for the model catalog and the embedder list. After updating, run
`npx wrangler deploy` from `docs/monitor/cloudflare-worker/` so the
dashboard ships the new catalog. The Python module is purely additive
— no deploy needed for it, just commit.

When they drift, the dashboard is canonical (it's the surface the
sales team uses); the Python module is a back-end mirror.

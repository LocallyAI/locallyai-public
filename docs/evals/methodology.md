# Eval methodology

This document explains what the LocallyAI project measures, what it
doesn't, and how to read the numbers that appear in
[`LocallyAI/locallyai-audit-agent/eval/runs/`](https://github.com/LocallyAI/locallyai-audit-agent/tree/master/eval/runs).

## Two products, two eval surfaces

LocallyAI has two evaluable surfaces, and people frequently conflate
them. They measure different things on different models against
different rubrics.

| Surface | What it is | Eval status today |
|---|---|---|
| **Main legal-RAG chat product** | The `/v1/chat/completions` endpoint backed by Qwen 2.5 7B + Qdrant + BM25 + RRF + cross-encoder rerank + ACL post-filter. The thing a lawyer types into. | **No eval yet.** This is the most important unanswered question in the project; see *future work* below. |
| **Audit-agent (forensics tool)** | A separate consumer at [`LocallyAI/locallyai-audit-agent`](https://github.com/LocallyAI/locallyai-audit-agent) that calls 4 tools (`log_search`, `hmac_verify`, `time_range_query`, `summary_stats`) to answer forensic questions about the HMAC-chained audit log. | 30-question eval suite, two-axis scoring, runnable end-to-end via `./run.sh eval`. **The 53.3% number below refers to THIS eval.** |

If a number labelled "answer correctness" appears anywhere, assume it
refers to the audit-agent eval unless explicitly stated otherwise.

## Audit-agent eval — what's measured

30 questions across 5 categories:

| Category | N | Probes |
|---|---|---|
| `log_search` | 8 | keyword / substring routing |
| `time_range` | 6 | ISO-8601 window + filter parsing |
| `aggregation` | 6 | `summary_stats` group_by enum routing |
| `integrity` | 5 | `hmac_verify` interpretation of the chain-intact dict |
| `multi_tool` | 5 | chained tool calls (e.g. summary_stats → hmac_verify) |

Two clean / three tampered fixtures for integrity questions; the
tampered fixture has a hand-edited entry at `seq=5` with field
`matter_code` mutated to break the HMAC chain.

Each question is judged on **two independent axes**:

- **`tool_pass`** — did the agent call the expected tool(s)?
  Boolean. Soft: extra tool calls are OK; the expected tool just
  needs to appear in the call list.
- **`answer_pass`** — does the final answer satisfy the rubric?
  Boolean. Strict: rubric is `expected_answer_contains` (substrings
  that must appear) + `expected_answer_excludes` (substrings that
  must NOT appear) + ground-truth-facts the judge cross-references.

A question only counts as "both pass" when BOTH axes pass.
"answer correctness" in headline tables = `answer_pass` rate.

The judge is `claude-haiku-4-5-20251001` over the Anthropic API
(cloud, dev-only — production never calls the judge). Total cost
per 30-question run: ~$0.03.

## Why the rubric is harsh

The `answer_pass` rubric is deliberately strict because the
production failure mode for a legal-tech tool is *confidently wrong*
answers, not unhelpful ones. Specifically:

- **`expected_answer_contains`** typically includes exact entity
  matches (e.g. matter codes, user-hash prefixes, ISO timestamps).
  Missing one trips the test even when the prose is otherwise
  correct. Reflects what a lawyer needs: when they ask "find entries
  for matter M-2026-0042", the answer better contain "M-2026-0042".
- **`expected_answer_excludes`** catches hallucinated entity names.
  e.g. for "find entries referencing Qwen", "Llama" is a forbidden
  substring; if the model bleeds through from a previous question
  and mentions Llama, the test fails. Mirrors the real-world risk
  of citing the wrong case.
- **Ground-truth-facts** are exact counts + dates + user-hashes from
  the fixture, NOT from the audit log itself. The judge knows the
  fixture's "10 UK entries at hour 16 on 2026-05-15"; if the agent
  reports 8 it fails answer_pass even though the agent saw what it
  saw.

A "soft" eval (BLEU, ROUGE, semantic similarity) would score 30–40
points higher on the same data. We deliberately use the strict
rubric so the headline number reflects reality, not vibes.

## The canonical number to cite

```
LocallyAI audit-agent eval (2026-05-21 baseline, re-graded 2026-05-25):
  Tool selection:      29/30  (96.7%)
  Answer correctness:  16/30  (53.3%)
  Both axes pass:      16/30  (53.3%)

  Model: Qwen 2.5 Coder 7B over mlx_lm.server (standalone)
  Judge: claude-haiku-4-5-20251001
  Dataset: eval/dataset.yaml (30 questions, 5 categories)
  Cost:  $0.05 to grade
```

This is the only valid, reproducible measurement currently. Three
sitting-5 + sitting-6 attempts in May 2026 produced higher or lower
numbers but were measurement artifacts — see *known measurement
bugs* below.

## How to read the numbers in `eval/runs/`

Each run produces `<ISO-timestamp>.jsonl` (one row per question) +
`<ISO-timestamp>_summary.md` (per-category breakdown). The summary
is the human-readable view; the jsonl is what the judge consumed.

The summaries vary in validity. The git history of `eval/runs/`
includes invalid runs marked as such in their commit messages
(e.g. the 2026-05-26 sitting-6 trilogy). When reading older
summaries, check the corresponding commit message for the validity
verdict before quoting numbers.

## Known measurement bugs

### Bug 1: LocallyAI-backend eval reads the live log, not the fixture

If `BASE_URL` points at a LocallyAI deployment (rather than a raw
LLM server like Ollama or `mlx_lm.server`), the audit-agent's tool
calls dispatch server-side via `mcp_servers/audit/server.py`. The
LocallyAI process is long-running under launchd and does not see
the `LOCALLYAI_AUDIT_LOG` env var that `eval/run.py` sets per
question — so the model reads from the live audit log instead of
the fixture.

This produces numbers that LOOK valid (30/30 graded, plausible
percentages) but compare model-output-on-live-log against rubric-
written-for-fixture. The 2026-05-25T233942Z sitting-5 result
(60%) and the 2026-05-26T130749Z sitting-6 result (53.3%) both
suffer from this bug.

Architectural fix (commit `82bdb9c`): `mcp_servers/audit/server.py`
now honours `LOCALLYAI_AUDIT_LOG` per-call, falling back to
`api._shared.AUDIT_LOG`. This makes the path-resolution correct
for any future scaffold that sets env vars on the LocallyAI
process. It does NOT solve the cross-process env-var propagation
issue — that requires either restarting LocallyAI between
questions OR a separate eval-mode HTTP header that the chat handler
reads + propagates.

For now: only run the eval against a NON-LocallyAI backend (Ollama,
LM Studio, `mlx_lm.server` standalone). The `BACKEND=ollama
./run.sh eval` path in `LocallyAI/locallyai-audit-agent/run.sh`
does this correctly.

### Bug 2: Ollama 0.22.1 Metal-shader crash on M-series GPUs

`./run.sh eval` against Ollama on a 2024+ Apple Silicon Mac with
Ollama 0.22.1 (the brew default at time of writing) returns
`{"error": {"message": "llama runner process has terminated"}}`
for every question. Reproducible on the maintainer's M3 Max and
documented as bug 7 in [`/tmp/postmortem-locallyai.md`](../../tmp/postmortem-locallyai.md).

Workaround: `brew install --cask ollama-app --force` reinstalls
with the macOS app shipping the fixed Metal shaders.

The 2026-05-26T131702Z (qwen2.5:14b) and 2026-05-26T132719Z
(qwen2.5:7b) sitting-6 attempts both fell to this. Numbers are 0/30
because every agent call errored, not because the model produced
wrong answers.

## Where to take this next

In rough order of value:

1. **Build the main-product eval.** The 53.3% audit-agent number
   gets quoted as if it were a product number — it isn't. The main
   legal-RAG product (retrieval + grounding + answer-generation)
   has zero measured points today. Highest-leverage research
   project against existing scaffolding: extend `eval/dataset.yaml`
   with a 30-question legal-RAG suite, seed with the demo corpus +
   3 forked plugins, re-use the judge.
2. **Fix Bug 1 properly.** Either (a) have eval/run.py restart
   LocallyAI's launchd job between questions (slow, robust), OR
   (b) add an `X-LocallyAI-Eval-Fixture` header that the chat
   handler reads + threads down to `mcp_servers/audit/server.py`.
   Option (b) is ~80 LOC; gates only enable-during-eval; lets the
   audit-agent eval run against the LocallyAI integration cleanly.
3. **Move the judge inside the firm.** The current judge is Claude
   Haiku over the API. For the eval to run in a customer's CI
   without an Anthropic key, the judge needs to be a local model
   (Qwen 2.5 32B is plausible). Different cost profile, different
   reliability — needs a calibration study against Haiku.
4. **Re-baseline against Qwen 3 7B / 14B.** Sitting-4 used Qwen 2.5
   Coder 7B. The default model has since changed to Qwen 2.5 7B
   Instruct (4-bit). Qwen 3 14B is now pullable. Each model
   re-baseline costs $0.05 + 10 min of wall time; pure judgement
   call when to do it.
5. **Publish the eval to a public dashboard.** Right now the
   numbers live in commit messages. A simple GitHub Pages page
   that renders `eval/runs/*_summary.md` over time would let any
   visitor see the model + rubric + score history. A weekend
   project.

## How to ask "is this number any good?"

When someone shows you a LocallyAI eval number, the three honest
questions to ask:

1. **Which surface?** Audit-agent eval or main-product eval. (If
   they say main-product, ask to see the dataset — it doesn't exist
   yet at time of writing.)
2. **Which model + backend?** Sitting-4's 53.3% is on Qwen 2.5
   Coder 7B over mlx_lm.server with fixtures correctly applied.
   Different model, different backend, different fixture state →
   different number, not necessarily comparable.
3. **Which rubric?** Strict substring rubric or soft semantic
   rubric. The same answer can score 30-40 points apart depending
   on which you use. We use strict.

The 53.3% answer is honest because (a) the rubric is hard and (b)
we publish the failures and the methodology, not just the
percentage.

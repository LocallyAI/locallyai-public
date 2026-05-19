# Architecture Decision Records (ADRs)

This directory captures *why* the architecture is what it is —
specifically, the load-bearing choices where a future contributor
(or future-author) would otherwise repeat already-rejected
alternatives.

Background reading on the format: Michael Nygard's
["Documenting Architecture Decisions"](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
(2011) and the [adr.github.io](https://adr.github.io/) community
reference. The template in [`0000-template.md`](0000-template.md)
follows the lightweight Nygard shape: **Context · Decision ·
Alternatives Considered · Consequences**.

## What belongs in an ADR (and what doesn't)

An ADR is for a decision that:

- **Constrains future work** — e.g. picking Qdrant locks the schema +
  ops model for retrieval; picking AGPL-3.0 constrains who can take
  the code and how
- **Has a credible alternative that was rejected for a non-obvious
  reason** — the alternative is the whole point of the record
- **Costs something to revisit** — if the reader could re-derive the
  reasoning from the code in 30 minutes, it doesn't need an ADR

If it's just a *how-to* or *what-it-does* explanation, it belongs in
the SOP ([`docs/sop/`](../sop/)) or as code comments. ADRs are
short, conceptual, and frozen-in-time (once accepted, they don't
get edited — they get *superseded* by a new ADR that links back).

## Numbering + status conventions

- **Filename format:** `NNNN-short-slug.md` where `NNNN` is the
  next zero-padded integer. Numbers are append-only; gaps are fine.
- **`0000-template.md`** is the template itself, never an actual ADR.
- **Status lifecycle:** `proposed` → `accepted` → (eventually)
  `superseded by ADR-NNNN` or `deprecated`. Always update status —
  never delete the file.
- When superseded, the *new* ADR explains why and links back to the
  one it replaces; the *old* ADR stays in place with its status
  flipped.

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-hybrid-retrieval-rrf-rerank.md) | Hybrid retrieval (Qdrant + BM25 → RRF fusion → cross-encoder rerank) | accepted |
| [0002](0002-qdrant-as-vector-store.md) | Qdrant as the vector store (embedded → external for HA) | accepted |
| [0003](0003-hmac-chained-audit-log.md) | HMAC-chained audit log for tamper evidence | accepted |
| [0004](0004-desktop-clients-swift-and-tauri.md) | Native Swift wrappers (Mac) + Tauri (Windows) for desktop clients | accepted |
| [0005](0005-mac-ha-syncthing-rsync.md) | Two-node Mac HA via Syncthing (governance) + rsync (corpus) | accepted |
| [0006](0006-openai-compatible-api-surface.md) | OpenAI-compatible API as the first-class interface | accepted |
| [0007](0007-bilingual-uk-ksa-regional-defaults.md) | Single multi-region build with regional defaults (UK + KSA, EN/AR) | accepted |
| [0008](0008-multi-backend-inference.md) | Multi-backend inference (MLX + Ollama + LM Studio) behind one interface | accepted |
| [0009](0009-cloudflare-workers-tiebreaker.md) | Cloudflare Workers as external tiebreaker, kill switch, onboarding gateway | accepted |
| [0010](0010-acl-post-filter-before-rerank.md) | Per-document ACL as a post-filter applied BEFORE cross-encoder rerank | accepted |
| [0011](0011-agpl-3-public-mirror.md) | AGPL-3.0 as the licence for the public mirror | accepted |

These are the load-bearing calls in the project — the ones future-me
(or any prospective collaborator) would benefit from seeing argued
out on paper rather than re-derived from the code.

## Writing a new ADR

1. Copy `0000-template.md` to the next number + a kebab-case slug
   describing the decision: `cp 0000-template.md 0001-rrf-rerank.md`.
2. Fill out the template top-down. The **Alternatives Considered**
   section is the most valuable; spend most of your time there.
3. Start status as `proposed`; flip to `accepted` once the decision
   is in the codebase.
4. Link the ADR from the relevant code or SOP chapter so a reader
   stumbles into it from context, not by browsing this directory.

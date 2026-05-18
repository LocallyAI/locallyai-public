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

## Planned ADRs

The following decisions in the current codebase are worth ADR-ing.
They haven't been written yet — placeholder list for the author to
expand as time allows:

| Proposed # | Topic |
|---|---|
| 0001 | RRF fusion + cross-encoder rerank vs single-stage retrieval (why not just BM25, why not just dense, why a 2-stage pipeline at 50K+ docs) |
| 0002 | Qdrant vs alternatives (LanceDB, pgvector, Weaviate, Milvus) for embedded single-node + future external HA |
| 0003 | HMAC chain for audit-log integrity vs blockchain / Merkle-tree / signed-line approaches |
| 0004 | Tauri + native Swift wrappers vs Electron vs pure browser for staff-laptop clients |
| 0005 | 2-node Mac HA with Syncthing + rsync vs Postgres replication vs DRBD vs S3-backed shared-state |
| 0006 | OpenAI-compatible API surface as the first-class interface (vs a bespoke API) |
| 0007 | Multi-region UK + KSA bilingual deployment — regional defaults vs single-tenant per-region forks |
| 0008 | MLX vs Ollama vs LM Studio as the inference backend — when each wins, why the abstraction is worth maintaining |
| 0009 | Cloudflare Workers as the external HA tiebreaker + kill-switch + onboarding gateway (vs running our own VPS or skipping the tiebreaker entirely) |
| 0010 | Per-document ACLs as a post-filter on the retrieved candidate pool vs Qdrant-side filtering (why ACL precedes rerank in the pipeline) |
| 0011 | AGPL-3.0 as the licence for the public mirror — copyleft vs permissive trade-off given the commercial parent |

These are the load-bearing calls in the project that future-me (and
any prospective collaborator) would benefit from seeing argued out
on paper.

## Writing a new ADR

1. Copy `0000-template.md` to the next number + a kebab-case slug
   describing the decision: `cp 0000-template.md 0001-rrf-rerank.md`.
2. Fill out the template top-down. The **Alternatives Considered**
   section is the most valuable; spend most of your time there.
3. Start status as `proposed`; flip to `accepted` once the decision
   is in the codebase.
4. Link the ADR from the relevant code or SOP chapter so a reader
   stumbles into it from context, not by browsing this directory.

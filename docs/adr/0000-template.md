# NNNN — <decision title in present tense>

- **Status:** proposed | accepted | superseded by [ADR-XXXX](XXXX-title.md) | deprecated
- **Date:** YYYY-MM-DD
- **Deciders:** <names / roles, or "single-author" for solo projects>
- **Tags:** *(optional — e.g. retrieval, security, infra, ui)*

## Context

What problem are we solving? What constraints / forces are in play?
Cover the relevant technical, regulatory, and operational background
that future-you (or a new joiner) needs to understand *why* this
decision was even worth making. Don't restate the codebase — link to
the relevant files instead.

A reader who knows nothing about the project should be able to
finish this section and understand the question on the table.

## Decision

The choice that was made. Stated as a single declarative sentence at
the top, then a paragraph or two on the shape of the implementation.

If the decision is "do nothing for now," say so explicitly — silent
no-decisions rot fastest.

## Alternatives considered

The other options that were on the table and **why they were rejected**.
This is the most valuable section of an ADR — it captures the dead
ends so future-you doesn't waste a week re-evaluating them.

- **Alternative A.** <one-line description.> Rejected because <reason>.
- **Alternative B.** <one-line description.> Rejected because <reason>.
- **Alternative C: do nothing.** Rejected because <reason>.

## Consequences

The trade-offs incurred — both the good and the bad.

### Positive

- <what this decision unlocks>

### Negative

- <what this decision costs — operational complexity, performance, optionality lost, etc.>
- <known follow-ups required to mitigate>

### Neutral

- <observations that aren't clearly good or bad but are worth recording>

## References

- <relevant code paths, e.g. `retrieval.py:HybridRetriever.retrieve`>
- <relevant SOP chapters, e.g. `docs/sop/ha-architecture.md`>
- <external links — RFCs, papers, vendor docs>

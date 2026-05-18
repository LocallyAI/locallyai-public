# Runbook: run a conflict check

**When to use:** before opening a new matter. Required by SRA Code
of Conduct (E&W) Principle 7 + Indicative Behaviours 3.1–3.7, and
KSA Bar Bylaws Art. 22. This runbook is the LocallyAI-specific
operator workflow; it does NOT replace the firm's own
conflict-of-interest policy.

**Time:** 5–10 minutes per check, single sitting.

**Who runs it:** intake clerk or partner. Partner sign-off is
required for `review` and `conflict` statuses.

---

## Steps

1. Open Manager UI → **Conflicts** in the sidebar
2. Click into the **Run a check** form
3. Fill in:
   - **Matter ID** — your firm's internal reference (optional but
     strongly recommended — links the check to your matter file)
   - **Parties** — at least the proposed client and any opposing
     party. Add more parties via the **Add party** button.
     Roles: `client`, `opposing`, `interested`
   - **Matter description** — 1–2 sentences. The LLM uses this to
     disambiguate same-name parties; skipping it produces more
     `review` results
   - **Opposing counsel** — comma-separated list of opposing law
     firms (optional)
4. Click **Run conflict check**
5. Read the **Result** panel:
   - **Status badge** — `clear`, `review`, or `conflict`
   - **Summary** — one-sentence reason
   - **Key concerns** — bullet list (if any)
   - **Recommended action** — one sentence
   - **Hits** — ranked list with score + matter_code + snippet.
     **Strong** hits (amber pill) carry the most weight
6. Decide:

| Status | Action |
|---|---|
| `clear` | Open the matter; the conflict-log entry is your audit trail that you ran the check |
| `review` | Senior partner reviews the hit list. They may decide to proceed (note rationale on the matter file) or decline |
| `conflict` | Decline OR pursue informed consent + ethical wall (UK SRA-permitted in narrow circumstances; never assume permitted in KSA) |

7. The check appears in **Recent checks** — confirm it's there
   before closing the page

---

## When the engine fails

- **No result, error banner** — copy the error message and check
  [`incidents-service.md`](../sop/incidents-service.md) for the
  matching backend issue (LLM backend, retrieval backend, disk full)
- **`clear` but you suspect there should be a hit** — the corpus may
  not contain the relevant matter file. Check **Documents** to
  confirm the relevant matters are ingested. If they are, try the
  check again with additional related-party names
- **`conflict` for a name you've never acted for** — the LLM may
  have misidentified a same-name unrelated party. Add a more
  specific matter description and re-run

---

## What to NEVER do

- **Don't skip the check because "I know there's no conflict"** —
  the conflict log is the firm's compliance evidence; missing
  entries look like missed checks to auditors
- **Don't override a `conflict` status without partner sign-off** —
  even if you're sure it's wrong. The partner records the override
  rationale on the matter file
- **Don't share the run output beyond the partners involved** —
  party names are visible in the operator UI session, even though
  they're pseudonymised at rest in `conflicts.log`
- **Don't run conflict checks for parties outside the firm's
  engagement** — the conflict log is part of the firm's regulated
  records; gratuitous checks pollute the audit trail

---

## Related

- [`docs/sop/conflict-checks.md`](../sop/conflict-checks.md) — full
  reference for the conflict-check engine
- [`docs/sop/compliance.md`](../sop/compliance.md) — how conflict
  checks feed the monthly DPO snapshot
- [`docs/runbooks/dpo-monthly-snapshot.md`](dpo-monthly-snapshot.md)
  — the snapshot exposes total + last-30-day check counts

# 0007 — Single multi-region build with regional defaults (UK + KSA, EN/AR)

- **Status:** accepted
- **Date:** 2026-05-05
- **Deciders:** single-author
- **Tags:** ui, compliance, internationalisation

## Context

LocallyAI ships into two regions today: the UK (English, GDPR + SRA + ICO) and KSA / Saudi Arabia (Arabic + English, PDPL + SDAIA + Saudi Bar bylaws). They differ in many specifics:

- **Language and direction.** Arabic is RTL; English is LTR. UI mirroring matters (margins, icons, sidebars).
- **Date format.** UK uses Gregorian; KSA legal practice quotes Hijri alongside Gregorian.
- **Regulator references.** UK SOPs cite SRA Code of Conduct, GDPR articles, ICO guidance. KSA SOPs cite PDPL Royal Decree M/19, SDAIA notification rules, Saudi Bar bylaws.
- **DPA template.** UK DPA references GDPR / UK Data Protection Act 2018 / SCCs. KSA DPA references PDPL with cross-border-transfer restrictions and Arabic clause translations.
- **Demo corpus.** UK demo is English legal forms (NDA, engagement letter, GDPR policy, conflict procedure). KSA demo is Arabic + English (DIFC-style NDA, PDPL policy, M&A confidentiality letter, corporate restructuring memo, Arabic welcome).
- **Operational SOP differences.** Breach notification windows differ (UK: 72h to ICO; KSA: SDAIA-defined with different triggers). Operator runbooks need to surface the right window for the deployed region.

Two paths existed: ship one codebase that branches on region, or fork a per-region build that ships only what that region needs.

## Decision

**Single codebase. Region is set per deployment at install time via `LOCALLYAI_DATA_REGION=UK|KSA`. Everything else branches off that value.**

- **i18n strings** live in `apps/{worker,manager}-ui/src/lib/i18n.ts` with English + Arabic translations co-located per key. The active language follows `LOCALLYAI_DATA_REGION`'s default but is overridable per-user.
- **RTL handling** is CSS-logical-properties throughout (`margin-inline-start` not `margin-left`, etc.) so the same component renders correctly in either direction. `dir="rtl"` is set on `<html>` when the active language is Arabic.
- **Date helpers** in `apps/*/src/lib/format.ts` switch to Hijri-pair format (`2026-05-19 / 1448-11-02`) when region is KSA.
- **DPA templates** live as `DPA_DRAFT.md` (UK) and `DPA_DRAFT_SA.md` (KSA) — both shipped, the install picks the one for the region.
- **Demo corpus** is `demo/data/` (UK) and `demo/data_sa/` (KSA); the installer copies the right set into `data/` based on region.
- **SOP region-specifics** live in `docs/sop/compliance.md` (UK + master) and `docs/sop/compliance-saudi.md` (KSA-specific overrides). Same for setup: `docs/sop/setup-mac-single.md` is region-agnostic; `docs/sop/setup-saudi.md` adds the region overlay.
- **Regulator-specific SOP sections** are conditional in prose ("If your region is KSA, see compliance-saudi.md §SDAIA").

## Alternatives considered

- **Fork per region** (`locallyai-uk`, `locallyai-ksa`). Rejected because every bug fix, security patch, and feature would need to land twice. Engineering velocity collapses; regression-risk compounds. The maintenance burden alone disqualifies it.
- **Per-region build flag at compile time** (e.g. Vite mode + tree-shake). Considered. Rejected because the surface that differs between regions is small enough that runtime branching is cheaper than build-time elimination, and shipping a binary that *contains* both regions makes "this is a UK deployment" auditable rather than baked-in.
- **Server-side rendering with region pre-injected** so the client never sees the other region's strings. Rejected because the SPAs are client-rendered (TanStack Start in client mode); SSR would be a separate architecture for a marginal compliance benefit (the strings aren't sensitive — they're regulator names).
- **External i18n service** (Transifex, Lokalise). Rejected because (a) it adds a sub-processor + a network dependency to translations and (b) the volume of strings (~200 per UI) doesn't justify the tooling.
- **One DPA template parameterised by region.** Considered. Rejected because the DPAs differ in structure, not just wording — KSA's cross-border-transfer clauses and Arabic-text annex have no UK equivalent. Maintaining two clean templates is less error-prone than one parameterised template with conditional Arabic.

## Consequences

### Positive

- **One codebase to ship, patch, and audit.** Security fixes land everywhere at once.
- **The "is this a KSA deployment?" check is one env var.** Auditors trying to confirm the right DPA was used can verify by reading `.env` + the compliance snapshot.
- **Demo data is region-aware out of the box.** A KSA firm seeing the demo sees Arabic + English samples, not English-only ones — important for the sales conversation.
- **Operators can override per-user.** A bilingual lawyer in a KSA firm who prefers English UI can switch without changing the region setting.

### Negative

- **The codebase ships both languages and both demo corpora always** — adds ~50 KB to the SPA bundle (i18n strings) + ~50 KB to the repo (KSA demo data). Trivial in absolute terms; mentioned for completeness.
- **RTL bugs hide.** A developer working primarily in English will not see RTL-mirror issues until they switch language. Mitigated by the manager-UI having a language toggle in the sidebar; the developer is expected to test both.
- **DPA template drift is a real risk.** Two templates can diverge if a clause is added to one and forgotten in the other. Mitigated by a checklist in `docs/sop/maintenance.md` for DPA-template updates: any change to `DPA_DRAFT.md` requires a corresponding diff against `DPA_DRAFT_SA.md`.
- **Hijri-date formatting** requires the `arabic-calendar` JS library in the UI bundle (~30 KB). Region-conditional import would save it on UK deployments; not implemented because the saving is small.

### Neutral

- New regions (DIFC, ADGM, UAE PDPL) can be added by setting a new `LOCALLYAI_DATA_REGION` value and adding the corresponding overlay files. The architecture supports it; no current customer is asking for it.
- Both `LOCALLYAI_DATA_REGION=UK` and `=KSA` are documented in `.env.example`.

## References

- `apps/worker-ui/src/lib/i18n.ts`, `apps/manager-ui/src/lib/i18n.ts` — string catalogues
- `apps/*/src/lib/format.ts` — date formatting with Hijri pair
- `DPA_DRAFT.md`, `DPA_DRAFT_SA.md` — region-specific DPA templates
- `demo/data/`, `demo/data_sa/` — region-specific demo corpora
- `docs/sop/setup-saudi.md` — KSA-specific setup overlay
- `docs/sop/compliance-saudi.md` — PDPL / SDAIA procedures
- `config.py:DATA_REGION` — single source of truth for the region setting

# Sales pipeline

> Prospect → demo → contract → handoff to onboarding. This chapter is
> the vendor process; the firm-facing onboarding pipeline starts at
> [docs/sop/onboarding.md phase 0](../sop/onboarding.md#phase-0--pre-engagement-vendor-side).

---

## Pipeline stages

Each prospect moves through these stages; track in a private Notion
page or simple spreadsheet (the goal is **one canonical place per
prospect**, not the tool).

| Stage | Definition | Exit criteria |
|---|---|---|
| Inbound | Lead has surfaced (referral, conference, content) | First-call scheduled OR rejected |
| Discovery | First call held — qualifying questions answered | Decision to proceed to demo OR no-fit |
| Demo | 30-min product demo + security overview delivered | Decision to request DPA OR pass |
| Legal | DPA template sent, in firm's legal review | DPA counter-signed OR negotiation stalls >30 days |
| Install scheduled | Onboarding intake form sent | Form returned + install date set |
| Live | Phase 7 acceptance criteria met (see [onboarding.md](../sop/onboarding.md)) | (steady state — no further sales work) |

---

## Qualifying questions (discovery call)

Use these to decide whether the prospect is a fit. ~30 min call.

**About the firm**:

1. Roughly how many fee-earners (lawyers / partners)?
2. Single office or multi-office?
3. Primary jurisdiction(s) you practise in?
4. Primary regulator(s)? (SRA / FCA / ICO / SDAIA / DIFC / ADGM)
5. What's your current tech stack — DMS, time recording, email?
6. Do you have a DPO or equivalent?

**About the use case**:

7. What problem are you trying to solve? (Surface-level: "AI" — keep
   probing for the actual workflow.)
8. What does "good" look like in 6 months?
9. What's stopping you from using cloud AI tools today?
   (Almost always: data sovereignty / regulator / partner concerns.)
   [House rule: never name specific competitor brands in writing or
    in client-facing materials. In a verbal discovery call you can
    discuss the *category* — "the major cloud AI offerings" — but
    don't name them. The vendor's pitch deck and comparison tables
    use "Cloud AI vendors" not specific brand names. Comparing on
    capabilities + posture, not by name, removes a meaningful legal
    risk and keeps the conversation about the firm's needs rather
    than vendor cage-matches.]
10. Do you have an existing on-prem footprint (NAS, server room) or
    are you cloud-first?

**About the procurement**:

11. Who decides? (Managing partner, COO, IT director, DPO?)
12. What's your typical procurement cycle for a software contract?
13. Have you done an on-prem AI evaluation before? What happened?
14. Do you have a budget range in mind? (If they push back, share
    pricing — see below.)

**Disqualifying signals** (politely close the call):

- "We just want a chatbot for marketing copy" — wrong product.
- "We need EU-region cloud" — wrong product (we are office Mac, not cloud).
- "We use Windows-only and won't consider Mac" — possible (we have
  Windows support) but flag the lower test coverage.
- "We can't sign a DPA" — not a fit; the DPA is the foundation.

---

## Demo (30 min)

Structure:

| Time | Topic |
|---|---|
| 0–5 min | Recap of the firm's stated problem (proves you listened on the discovery call) |
| 5–15 min | Live demo: ingest one of their sample documents, ask a question, show the sources panel, show the audit log |
| 15–20 min | Security overview: per-firm Mac, no cloud calls, kill switch, audit chain, pseudonymisation |
| 20–25 min | Their questions |
| 25–30 min | Next steps: send DPA + onboarding intake URL? |

**Use a fake firm name in the demo box** — `Demo & Co Solicitors LLP`,
not the prospect's real name (the firm-name pill in the worker UI
shows it; using a real name signals casualness about isolation).

**Sample documents**: pre-load 5–10 deliberately-generic legal
documents (NDAs, simple commercial agreements) into the demo box. Do
**not** ask the prospect to send their real documents to your demo
box for the demo — that would breach our own no-vendor-data-access
posture before we've even started.

---

## Pricing (current as of 2026-05)

> **Pricing is the most-revised section of any SOP. Verify with the
> founder before quoting; this section may be out of date.**

Current model:

- **Per-firm flat fee**: £[X] / year — includes office Mac install,
  unlimited users, unlimited documents, unlimited queries
- **Hardware**: firm provides their own Mac. Recommended: Mac Studio
  M2/M3 Ultra with 64–128 GB RAM for firms over ~25 users
- **No setup fee** for firms onboarded under our Q1-Q2 2026 cohort
- **Annual support**: included in the annual fee — covers monitoring,
  releases, incident response under the 4h SLA
- **Optional**: on-site install visit (£[Y] one-time, includes one
  return visit within 90 days for any operational issue)

What's NOT included:

- Custom integrations (DMS-specific connectors, etc.)
- Bespoke fine-tuning on firm data (we don't fine-tune; we use
  retrieval)
- Bilingual UI work beyond UK/KSA (other regions priced bespoke)
- Hardware replacement / Mac refresh

What we **don't charge for**:

- Telemetry token rotation
- DPA renewal at the 12-month mark
- Audit-export tooling (built in)

---

## DPA process

Each region has its own DPA template:

- UK / EU: `DPA_DRAFT.md` (England & Wales, UK GDPR)
- KSA: `DPA_DRAFT_SA.md` (Royal Decree M/19, governing law Riyadh)

Workflow:

1. Convert the .md template to PDF (use `scripts/build_dpa_pdf.sh`
   if/when that exists — for now, manual via Pages/Word).
2. Sign vendor-side first (founder signs).
3. Send to firm via 1Password share or email (PDF is not a credential,
   so email is acceptable).
4. Track redlines in your sales pipeline tracker.
5. Common firm asks:
   - "Can we add an audit-rights clause?" → already in template
   - "Can we add an incident-notification timeline?" → 24h in template
   - "Can we name [other firm] as a sub-processor?" → no, we don't
     share infrastructure between firms
6. When counter-signed, file in `vendor-records/dpas/<slug>-<YYYY-MM-DD>.pdf`.
7. Update the firm-profile.md to record the signature date.

DPA negotiation **stalls** are the #1 reason prospects drop out.
Indicators: no response for 14 days = chase; >30 days = ask whether
to pause the engagement.

---

## Handoff to onboarding

Once DPA is counter-signed, hand off to the onboarding pipeline:

1. Send the intake URL email per
   [onboarding.md phase 1](../sop/onboarding.md#phase-1--intake-firm-it-10-min)
   template.
2. Update sales tracker stage to "Install scheduled".
3. Block install date in your calendar.
4. The sales role is now done; the on-call engineer role takes over.

---

## Sales metrics worth tracking

Lightweight tracking at this scale (single-person team):

- **Prospects per quarter** (top of funnel)
- **Demo conversion rate** (demos held → DPAs sent)
- **DPA close rate** (DPAs sent → DPAs counter-signed)
- **Time to live** (DPA counter-sign → phase 7 acceptance)
- **Loss reason category** for any prospect that drops out

A simple monthly tally in `vendor-records/sales-summary-<YYYY-MM>.md`
is enough at this scale. Avoid over-instrumenting (CRMs are noise
when you're below ~20 firms).

---

## Sales hygiene

- **Discretion is paramount**: we sell to law firms. They care that we
  don't announce them as a customer without permission. Default is
  silence; reference customers are negotiated case-by-case in writing.
- **Never name-drop one prospect to another** even if both are
  unrelated. Word travels in legal circles.
- **No public case studies** without firm sign-off in writing AND a
  72h delay between approval and publication (gives them a window to
  reconsider).
- **Conferences**: vendor-team attendance at legal-tech conferences is
  fine; speaking on panels requires founder sign-off.
- **Public website / marketing**: keep abstract — talk about the
  capability and posture, not specific firms or specific document
  examples.
- **Never name competitor brands in any written material** (pitch
  deck, capability deck, security overview, website copy, social
  posts, emails to prospects, blog posts). The pitch deck's
  comparison table reads "Cloud AI vendors" not specific brand names;
  the security overview talks about "cloud-hosted AI offerings" not
  named services. Verbal discussion of the *category* is fine on
  discovery calls (the prospect will name names themselves; you
  acknowledge the category in response). Naming competitors in
  writing creates real legal-risk exposure (defamation,
  trade-disparagement, comparative-advertising claims) for marginal
  sales benefit; positioning on capabilities + posture lands harder
  anyway. Same rule applies to LLM model providers — discuss
  "open-weight 4-bit quantised models" not "Llama / Qwen / Mistral
  by name" in any prospect-facing material. (Internal SOP and
  vendor-records can name models freely — they're not competitors,
  they're tools we use.)

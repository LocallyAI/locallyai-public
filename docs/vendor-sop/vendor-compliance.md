# Vendor compliance

> Vendor's own posture as a data processor. The firm-facing compliance
> documentation is in [docs/sop/compliance.md](../sop/compliance.md)
> and [docs/sop/compliance-saudi.md](../sop/compliance-saudi.md).
> This chapter is the **vendor's** obligations: what we owe firms,
> what regulators expect of us, what audit-readiness looks like.

---

## Our role under the DPA

For every firm, we are the **processor**; the firm is the
**controller**. This means:

- The firm decides what data is processed (their corpus, their users)
- We process it on their instructions (queries → answers; ingestion →
  retrieval)
- We act under documented technical and organisational measures
  (Schedule 2 of the DPA — covered by `data-isolation.md` evidence)
- We notify the firm of any breach within 24 hours (UK DPA template)
  / 24 hours (KSA DPA template)
- We assist the firm with subject-access requests, erasure, and
  audit/inspection rights

We are **not** a sub-processor for the firm's own clients (the firm's
clients see the firm, not us).

---

## Vendor-side data we hold

Despite the architectural posture (firm data lives on firm Mac), the
vendor does hold some firm-attributable data on the vendor side. This
is the inventory:

| Data | Purpose | Where | Retention |
|---|---|---|---|
| Firm legal name | Contract, sub-processor identification | `vendor-records/firms/<slug>.md`, signed DPA, invoices | 7 years post-engagement (statutory record retention) |
| Firm IT primary + secondary contact name + email + phone | Operational support, SLA escalation | `vendor-records/firms/<slug>.md`, calendar, password manager | Duration of engagement + 1 year |
| Firm DPO contact | Breach notification | `vendor-records/firms/<slug>.md` | Duration of engagement + 1 year |
| Office Mac model + RAM + macOS version | Operational support, incident triage | `vendor-records/firms/<slug>.md` | Duration of engagement + 1 year |
| Office subnet (CIDR) | CORS / egress rule generation | `vendor-records/firms/<slug>.md` | Duration of engagement + 1 year |
| Anonymised firm_id (SHA-256(firm_name)[:16]) | Telemetry routing | `~/.locallyai/vendor/firms-registry.json`, monitor Worker FIRM_TOKENS, `firms-issued.log` | Duration of engagement |
| Telemetry token (32-byte hex) | Heartbeat auth | `~/.locallyai/vendor/firms-registry.json`, monitor Worker FIRM_TOKENS | Duration of engagement |
| Heartbeat history (per firm) | Operational visibility | Monitor Worker KV (`FIRM_STATE`) | 7 days (KV expiry) |
| Alert history (per firm) | Incident retro / SLA tracking | Monitor Worker KV (`ALERTS`) | 30 days (KV expiry) |

What we **do not** hold:

- Document content
- User names (firm staff names — DPO contact yes, individual lawyers no)
- Query text or response text
- Audit log content
- Conversation history

These never leave the firm's Mac.

---

## Vendor's own RoPA (Record of Processing Activities)

Per UK GDPR Art. 30, processors of any size must maintain a RoPA. For
clarity here it is:

```
Controller:    LocallyAI Ltd (vendor)
Role:          Processor for each customer firm (controller)
Categories:    Contact details (B2B)
                + Aggregated technical telemetry (anonymised)
Purposes:      Support obligations under DPA
                + SLA monitoring
                + Sub-processor disclosure to firms
Recipients:    Cloudflare (anonymised heartbeats)
                + Resend (alert subject lines, no firm names)
                + Apple/Microsoft (when code-signing in scope)
Retention:     Per the inventory above
Cross-border:  None (firm data never crosses; vendor data is held by
                providers per their published regions — CF EU/US, etc.)
TOM:           Per docs/sop/data-isolation.md (firm-side)
                + this chapter's vendor-side measures
```

Update this RoPA whenever the data-inventory table above changes.

---

## ISO 27001 / SOC 2 readiness

We are not certified today. The path to readiness:

### ISO 27001 (Annex A controls already in our SOPs)

We already implement these in operations (see firm-side
data-isolation.md for evidence). Listing here so an auditor knows
where to look:

- A.5.34 (privacy & PII protection) — pseudonymisation + audit chain
- A.8.10 (information deletion) — `manage_users.py erase` tombstones
- A.8.20 (network security) — egress allowlist + LuLu / pf rules
- A.8.22 (segregation of networks) — per-firm Mac, no shared infra

Vendor-side controls we'd add in a certification effort:

- A.5.7 (threat intelligence) — formal subscription to a TI feed
- A.5.23 (cloud services) — formalise sub-processor governance (this
  chapter is the foundation)
- A.6.3 (information security awareness, education and training) —
  formal annual training for vendor team
- A.7.1 (physical security) — formal physical-asset register
  (founder Mac + safe + envelope locations; partly in
  [vendor-infrastructure.md](vendor-infrastructure.md))
- A.8.6 (capacity management) — CF tier monitoring, model-storage
  capacity per firm

Realistic timeline to certification: 6–12 months of focused
preparation + an external audit. Cost: £15–25k for a small-org audit.
Trigger event: when 3+ firms ask for it during procurement.

### SOC 2 (Type 1 then Type 2)

US-firm-friendly equivalent. Less common request from UK / KSA firms.
Park until a US firm asks.

---

## Breach notification obligations

What we owe firms when we suffer a vendor-side breach (NOT firm-side
— firm-side breaches are handled per the firm SOP).

### Within 1 hour

- Confirm the breach is real (not a false alarm).
- Activate the appropriate
  [vendor-incidents-own-infra.md](vendor-incidents-own-infra.md) playbook.
- Identify which firms, if any, are affected.

### Within 24 hours

- Notify each affected firm by phone (DPO + IT primary).
- Send a written follow-up the same day with what we know so far.
- Use the template:

> Subject: LocallyAI — vendor-side incident notification (action may be required)
>
> Dear [firm name] DPO and IT primary,
>
> We are writing to notify you of a vendor-side security incident
> affecting LocallyAI. Per our DPA, you are receiving this within
> 24 hours of confirmed discovery.
>
> **What happened**: [factual description, no speculation]
>
> **What data, if any, was affected**: [scope — including the
> categories from your firm's record. If "none affected", say so
> explicitly.]
>
> **What we have done so far**: [actions taken — kill switch invoked,
> credentials rotated, etc.]
>
> **What we ask of you**:
> - [Specific actions, e.g., "rotate the telemetry token in your .env
>   per the attached 1Password share"]
> - [Or: "no action required — informational notification"]
>
> **Next update**: we will provide a fuller written report within
> 7 days. The post-incident review will be filed in our private
> records and a redacted version will be sent to you.
>
> If you have questions, please call [phone] — do not reply by email
> for time-sensitive matters.

### Within 7 days

- Send the post-incident review (redacted to omit other firms' names
  and any details that would identify them).
- Where the firm needs to notify their own regulator, support them
  with whatever documentation they require.

### Records

Every breach notification (whether or not the firm needed to act) is
filed in `vendor-records/breach-notifications/<YYYY-MM-DD>-<firm-slug>.md`
for the same 7-year retention as the contract.

---

## Audit / inspection rights

Each DPA grants the firm the right to inspect our processing
activities. Standard terms:

- Notice: 30 days
- Frequency: once per year unless triggered by a specific incident
- Scope: this Vendor SOP + the relevant chapters of the firm SOP +
  the vendor-records folder for the inspecting firm only (not other
  firms)
- Confidentiality: NDA in advance

When a firm requests an inspection:

1. Acknowledge within 5 business days.
2. Negotiate scope + dates within 14 business days of request.
3. Sign NDA at least 7 days before the inspection.
4. Prepare a redacted version of:
   - This Vendor SOP (PDF, redacting personal names, account IDs,
     credential locations — keep procedures intact)
   - The firm's own `firm-profile.md` from vendor-records
   - The firm's own `firms-issued.log` rows
   - Sub-processor DPAs from `vendor-records/sub-processor-dpas/`
5. Hold the inspection meeting (typically video call; on-site only on
   reasonable request and at firm's expense).
6. File the inspection record in
   `vendor-records/inspections/<YYYY-MM-DD>-<firm-slug>.md` with what
   was reviewed and any findings.

---

## Privacy by design (architectural commitments)

These are the design commitments that justify our pseudonymisation /
egress / no-cloud posture. They are commitments we make to firms in
the DPA Schedule 2; do not change them without DPA renegotiation:

1. **No firm operational data leaves the firm's office Mac.** Documents,
   queries, responses, user names, audit content — all stay on
   firm-controlled hardware.
2. **Firm names appear vendor-side only as anonymised firm_id hashes
   in operational systems** (monitor Worker, telemetry registry).
   Vendor-records and DPAs use the legal name (necessary for contract
   and SLA escalation).
3. **No cross-firm processing.** No model trained on multiple firms'
   data, no shared embedding store, no aggregated analytics across
   firms. Each firm = one Mac = one isolated deployment.
4. **No retention of firm metadata beyond contract + 1 year**, except
   statutory records (contracts, invoices) at 7 years.
5. **Vendor cannot read firm data even with malicious intent.** No
   admin path that reaches firm-side documents or queries; no remote
   shell into the office Mac (the firm's IT is the only path).

If a future product change would violate any of these, treat it as a
DPA renegotiation event — pause the change until DPA addenda are
signed by every affected firm.

---

## Vendor's own sub-processor obligations

Where we use upstream sub-processors (per
[vendor-sub-processors.md](vendor-sub-processors.md)), we owe firms:

- Disclosure on contract signature (initial sub-processor list in DPA
  Schedule 3)
- 30-day notice of new sub-processors (most DPA templates)
- Right to object to a new sub-processor (DPA-template-dependent;
  practical effect: we'd need to find an alternative or that firm
  could terminate)
- Annual review of the sub-processor list

The annual review is in
[vendor-daily-ops.md §annual](vendor-daily-ops.md#annual-tasks);
the inventory is [vendor-sub-processors.md](vendor-sub-processors.md).

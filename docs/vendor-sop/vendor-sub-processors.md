# Sub-processor management

> Inventory of every upstream service the vendor depends on. For each:
> what they have access to, what breaks if they go down, what we owe
> firms when we add or change one. Annual review required.

The DPA template requires us to disclose sub-processors. This chapter
is the source of truth for that disclosure.

---

## What counts as a sub-processor

A **sub-processor** is any third party that processes firm-attributable
data on our behalf, even briefly. By definition almost no third party
qualifies for LocallyAI because firms' operational data never leaves
their own Mac. But several upstream services hold:

- Anonymised firm metadata (firm_id hash, IP-address-as-egress, etc.)
- Vendor-managed code that runs on the firm's Mac (= we are the
  processor; they are an upstream supplier of code)
- Vendor-side records that include the firm's name as a customer

These are all in scope.

---

## Active sub-processors

### Hugging Face

| Field | Value |
|---|---|
| Service | Public model + embedder hosting |
| Data they see | Anonymous downloads of public models from the firm's office Mac IP |
| Vendor account? | None — anonymous downloads only |
| Contract status | None (public service, no account) |
| What breaks if down | Initial model download during `install.sh` fails. Existing installs unaffected (model is local). |
| Fallback | Vendor-side mirror (not built today; would be a Tier-C release). For air-gapped firms, vendor delivers model files on USB. |
| DPA disclosure | Yes — listed as "model hosting (public)" |
| Annual review owner | On-call engineer |

### GitHub

| Field | Value |
|---|---|
| Service | Source code hosting + signed-tag distribution + releases |
| Data they see | Source code (but firm's office Mac data isn't in source); code we push includes our own emails in commit metadata |
| Vendor account? | LocallyAI org (private); TheApolloTheory (legacy) |
| Contract status | GitHub Free / Pro per-repo (verify current plan in CF account) |
| What breaks if down | Office Macs can't pull updates during the outage. Existing installs unaffected. Kill switch is on Cloudflare (not GitHub) so emergency stop still works. |
| Fallback | Mirror to a separate Git host (GitLab self-hosted, Codeberg). Not built today. |
| DPA disclosure | Yes — listed as "code distribution" |
| Annual review owner | Founder |

### Cloudflare

| Field | Value |
|---|---|
| Service | Worker hosting (kill switch + monitor), KV namespaces, DNS |
| Data they see | Anonymised heartbeat metadata (firm_id hash, healthz status, disk %, RAM %, alert codes — NEVER document content / users / queries) |
| Vendor account? | Yes — `your-cf-account.workers.dev` (or your account name) |
| Contract status | Free tier; upgrade to Workers Paid ($5/mo) when fleet > ~30 firms |
| What breaks if down | Monitor dashboard down (vendor-side blindness). Kill switch unreachable. Office Macs continue running on their installed version. |
| Fallback | Move Workers to a different CF account (held by founder + sealed envelope per [vendor-team.md](vendor-team.md)). Not a different cloud — Workers' KV + free-tier economics are too useful. |
| DPA disclosure | Yes — listed as "vendor monitoring infrastructure" with full disclosure of metadata fields |
| Annual review owner | Founder |

### Resend

| Field | Value |
|---|---|
| Service | Outbound email for vendor alerts (not firm-facing email) |
| Data they see | Subject + body of each vendor alert email (sender = vendor, recipient = vendor on-call) |
| Vendor account? | Yes — see [vendor-infrastructure.md §resend](vendor-infrastructure.md#resend-email) |
| Contract status | Free tier (3000/mo) |
| What breaks if down | Vendor doesn't get email alerts. Slack webhook (if configured) provides redundancy. Dashboard still shows alerts visually. |
| Fallback | AWS SES, Postmark, Mailgun, or self-hosted relay. ~30 min to swap (one secret rotation, one Worker redeploy). |
| DPA disclosure | Optional (depends on whether the firm's name appears in alert subjects — current implementation: alert subjects use firm_id hash, not firm name, so Resend never sees firm-attributable identifiers) |
| Annual review owner | On-call engineer |

### Slack (optional)

| Field | Value |
|---|---|
| Service | Vendor-side alert echo (incoming webhook) |
| Data they see | Same as Resend — alert content with firm_id hash |
| Vendor account? | Optional — only if vendor team chooses to use Slack |
| Contract status | Free tier |
| What breaks if down | Loss of one alert channel (Resend redundancy preserved). Dashboard unaffected. |
| Fallback | Drop the integration (Resend + dashboard suffice). |
| DPA disclosure | Same calculus as Resend — firm_id hash only, no firm names. |
| Annual review owner | On-call engineer |

### Anthropic (development only — NOT production)

| Field | Value |
|---|---|
| Service | Claude API for vendor-side development (e.g., this Claude Code session) |
| Data they see | Source code, public docs, design discussions — **never** firm operational data |
| Vendor account? | Yes — vendor's Anthropic console account |
| Contract status | Pay-as-you-go |
| What breaks if down | Vendor-side dev velocity slows; production unaffected (no runtime call to Claude) |
| Fallback | Other LLM coding assistants. Vendor work continues by hand. |
| DPA disclosure | **No** — Anthropic does not process firm data. Disclose only if asked by a particular firm during DPA negotiation. |
| Annual review owner | Founder |

### Apple (when client-app code-signing happens)

| Field | Value |
|---|---|
| Service | Apple Developer Program (code-signing certificates for Tauri Worker / Manager .app bundles) |
| Data they see | App bundle contents (which they may scan as part of notarisation) |
| Vendor account? | Not yet enrolled |
| Contract status | $99/year individual or $299/year org |
| What breaks if down | Future client-app builds can't be signed (until then: end users use unsigned builds with right-click → Open friction) |
| Fallback | None — Apple is the only signing authority for macOS. |
| DPA disclosure | Yes when enrolled — Apple sees app bundle contents (which contain no firm data) |
| Annual review owner | Founder |

### Microsoft Authenticode (when Windows code-signing happens)

| Field | Value |
|---|---|
| Service | Code-signing certificate for Windows .msi |
| Data they see | MSI contents |
| Vendor account? | Not yet acquired |
| Contract status | TBD (£300–£1000/year via reseller) |
| What breaks if down | Windows .msi shows SmartScreen warning |
| Fallback | None |
| DPA disclosure | Yes when enrolled |
| Annual review owner | Founder |

---

## What we deliberately do NOT use

For each, the rationale matters more than the absence — firms ask
about these in DPA review.

| Not using | Why not |
|---|---|
| OpenAI / Azure OpenAI | We don't call any LLM service at runtime. All inference is on the firm's Mac. |
| Pinecone / Weaviate / Chroma cloud | Vector store is embedded Qdrant on the firm's Mac. |
| Datadog / Sentry / New Relic | Vendor monitoring uses our own minimal-disclosure heartbeat. Third-party APMs would see firm-attributable telemetry. |
| AWS / GCP / Azure | We have no cloud-hosted infrastructure that touches firm data. CF Workers + KV is the only vendor-managed cloud, and only sees anonymised heartbeats. |
| Stripe / billing-as-a-service | Direct invoicing for now. (When we add billing automation: the billing service becomes a sub-processor and gets its own row.) |
| Intercom / Zendesk / HubSpot | Email is the support channel. CRMs see firm names — keep things simpler. |
| Google Workspace for vendor email | Vendor uses `<vendor-email>@example.com` (existing account). Migration to a vendor-domain mailbox is a future task — when it happens, the chosen provider becomes a sub-processor. |

---

## Adding a new sub-processor

When considering adding a new third-party service to vendor or firm
operations:

1. **Document the candidate** — fill in the row template above with
   provisional values.
2. **Assess data scope** — what firm-attributable data, if any, will
   they see? If the answer is "any operational firm data", the bar is
   very high — consider building it ourselves first.
3. **Notify firms 30 days before adoption** per most DPA templates.
   Comms should include:
   - What service
   - What data they will see
   - Why we're adding them
   - Any opt-out (rarely possible, but flag if it is)
4. **File the DPA addendum** — most DPAs have a sub-processor
   notification clause.
5. **Add the row to this chapter** in the same commit as the code
   change that introduces the dependency.
6. **Update the firm-facing data-isolation.md** if the new
   sub-processor changes the network-call inventory.

---

## Annual review

Every January (per [vendor-daily-ops.md §annual](vendor-daily-ops.md#annual-tasks)):

1. For each row above, verify:
   - The "Vendor account" status (still active, billing current)
   - The "Contract status" (any pending plan changes from the provider)
   - The "Data they see" description (provider may have added scope
     creep)
2. For each provider, check their published DPA / privacy
   documentation for material changes.
3. For each firm, confirm the disclosure list in their signed DPA
   matches what's in this chapter. If we've added a sub-processor that
   pre-dates the DPA-mandated notification, we may owe the firm a
   retroactive disclosure.
4. Update this chapter; commit; tag the commit `vendor-subproc-review-<year>`.

---

## DPA passthrough requirements

Each sub-processor's own DPA / terms govern what they can do with the
metadata they see. For each tier-A provider (CF, GitHub, HF), we
maintain a copy of their current DPA in `vendor-records/sub-processor-dpas/`
so an inspecting firm can see the full chain without each going to the
provider's site directly.

These are not legally binding on us (they're contracts between us and
the upstream), but they're useful evidence in DPA review.

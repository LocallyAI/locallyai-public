# DPO compliance portal

This chapter is the **reference manual** for the LocallyAI compliance
portal: what it is, what each section means, which regulation each
piece satisfies, and when to use it. It is **not** the action
manual — the action manual is the runbook at
[`docs/runbooks/dpo-monthly-snapshot.md`](../runbooks/dpo-monthly-snapshot.md),
which a DPO uses under time pressure. This chapter is read once to
understand the system; the runbook is opened every month.

Read [`compliance.md`](compliance.md) first for the per-Article
playbooks (Articles 15, 17, 30, 32, 33). This chapter explains how
the portal makes those Articles inspectable in one view.

> **Compliance frame:** GDPR (EU/UK), ISO 27001:2022, UAE PDPL
> (Federal Decree-Law 45/2021), KSA PDPL (Royal Decree M/19, 2023),
> DIFC DP Law 5/2020, ADGM DP Regs 2021. Where a portal section
> satisfies a specific Article, the citation appears inline below.

---

## What the portal is

A single signed document, refreshed on demand, that aggregates every
compliance-relevant signal LocallyAI produces. The DPO opens it once
a month (or ad-hoc when a regulator asks), downloads a printable HTML
report, signs the resulting PDF, files it in their internal audit
folder. The HMAC signature on the document lets anyone — DPO,
internal auditor, regulator — verify offline that the contents
weren't altered after generation.

The portal does NOT:

- Replace `compliance.md` Article-15 / Article-17 / Article-33
  procedures. Those individual data-subject and breach workflows
  still flow through the procedures in that chapter.
- Replace the audit log itself. The portal reports the chain's
  integrity; the chain itself remains the per-event evidence.
- Replace the manage_users.py erasure procedure. The portal surfaces
  that erasures happened; the actual erasure is initiated via the
  CLI per [`compliance.md` Article 17](compliance.md#article-17--erasure-right-to-be-forgotten).

## Where to find it

| Surface | Access |
|---|---|
| Manager UI | `https://<office-mac>:8000` → sidebar → **Compliance** |
| JSON API | `GET /admin/compliance/snapshot` (admin key bearer) |
| Printable HTML | `GET /admin/compliance/snapshot?format=html` |
| Offline verifier | `scripts/verify_compliance_snapshot.py <file>` |

Same admin key as the rest of the Manager UI. There is no separate
DPO credential tier in this release.

## Sections explained — what each piece means

The bundle has 14 top-level sections (snapshot version 1.1+). They
appear in the same order in both the JSON response and the printable
HTML. Sections 1-9 are the original v1.0 set; 10-14 were added to make
the snapshot meaningfully closer to "sufficient" for ISO 27001
certification audits, ICO/SDAIA inspections following a breach, and
SRA Outcome 7 evidence.

### 1. Deployment

Identity of the deployment: `deployment_id`, `firm_id` (the SHA-256
hash; the firm name is never on the wire), `node_id`, `region` (UK
or KSA), `version` (release version from `release_manifest.json`).

Use to confirm the snapshot is from the **right firm**. The `firm_id`
matches what the vendor monitor dashboard shows.

### 2. RoPA — Records of Processing Activities

Direct embed of `GET /admin/processing-record`. Includes controller,
purposes, lawful basis per category of data, recipients (always
`None` for LocallyAI by architecture), international transfers
(always `None` — data stays on the deployment host), retention,
security measures, data-subject rights, regulations acknowledged.

| Satisfies |
|---|
| GDPR Art. 30 — controller's record of processing |
| ISO 27001 A.5.34 — privacy / PII protection register |
| UAE PDPL Art. 21 — record-of-processing obligation |
| KSA PDPL Art. 31 — record-of-processing obligation |
| DIFC DP Law Art. 14 |
| ADGM DP Regs s.20 |

If the controller's lawful basis changes for any category (e.g. the
firm moves from "contract" to "legitimate interests" for billing
metadata), update the per-region branch in `processing_record()` in
`api.py` and re-issue this snapshot.

### 3. Audit chain integrity

Direct embed of `GET /admin/audit-verify`. Status is `ok`, `skipped`
(no HMAC key configured), or `TAMPERED`. Includes `entries`
verified (over all archives + live log), the `node_id`, and if
TAMPERED, the `broken_at_line` or `reason`.

| Satisfies |
|---|
| ISO 27001 A.8.15 — logging integrity |
| GDPR Art. 32 — security of processing (tamper-evident logs) |
| UAE PDPL Art. 19 / KSA PDPL Art. 19 — security measures |

A `TAMPERED` status is **never** ignored. The runbook
[`audit-chain-broken.md`](../runbooks/audit-chain-broken.md) classifies
the cause (salt rotation, partial write, clock skew, manual edit,
archive corruption) and routes recovery actions. Most causes are
benign; one (archive tampered after rotation) is potentially a
notifiable security incident.

### 4. Key-material posture

List of findings from `config.verify_key_material()`. Each finding
has `code`, `level` (`ok` / `warn` / `fail`), `message`. Checks the
length and entropy of `LOCALLYAI_AUDIT_HMAC_KEY` and
`LOCALLYAI_AUDIT_SALT`, plus the rotation history and the existence
of historical salt eras.

| Satisfies |
|---|
| GDPR Art. 32 — appropriate technical measures, key management |
| ISO 27001 A.8.24 — use of cryptography |
| UAE PDPL Art. 8(2) / KSA PDPL Art. 19 — security of processing |

`warn`-level findings (e.g. short salt) are acceptable in dev/test
but should be `0` on production deployments. `fail`-level findings
prevent the API from starting at all (Round-2 startup-gate addition).

### 5. Sub-processors (DPA Schedule §6.2)

Authoritative list of every upstream service the deployment uses.
For each: what they observe, whether they have any Client Data
exposure. Mirrors [`DPA_DRAFT.md` §6.2](../../DPA_DRAFT.md) so the
DPO can re-confirm at filing time that the live posture matches the
contract.

| Satisfies |
|---|
| GDPR Art. 28 — processor obligations, sub-processor disclosure |
| GDPR Art. 13 — transparency obligations |
| UAE PDPL Art. 21 / KSA PDPL Art. 25 |

Any change to this table (new sub-processor, expanded data exposure
of an existing one) requires a 30-day prior-notice to the firm per
DPA §6.3. The portal is your check that the list hasn't drifted.

### 6. Telemetry disclosure

Current heartbeat field set + version + the firm's active allowlist
(if `LOCALLYAI_TELEMETRY_FIELDS` is set). Two sub-tables: "always
carries" and "never carries". The disclosure-then-deploy commitment
from [`data-isolation.md`](data-isolation.md#optional-vendor-health-telemetry-opt-in-anonymised)
is auditable here.

| Satisfies |
|---|
| GDPR Art. 13 — transparency on what is collected |
| GDPR Art. 25 — data minimisation by default |
| UAE PDPL Art. 13(1) / KSA PDPL Art. 7 |

If `version` doesn't match what the firm consented to (per the
`Field-set change log` in `data-isolation.md`), telemetry has been
expanded without re-disclosure — that's a compliance defect. Use the
template at `docs/vendor-sop/templates/telemetry-field-expansion-notice.md`
to catch up.

### 7. Retention status

Per-stream cutoffs for `audit`, `billing`, `security` logs:
configured retention days, oldest entry timestamp, current size.

| Satisfies |
|---|
| GDPR Art. 5(1)(e) — storage limitation |
| ISO 27001 A.5.34 — privacy / retention controls |
| UAE PDPL Art. 6 / KSA PDPL Art. 22 |
| UK HMRC / KSA ZATCA — 6-year accounting record retention (billing) |

`audit` and `security` default to 365 days; `billing` defaults to
2555 days (7 years — exceeds the 6-year tax-law floor by one year).
Configured via `LOCALLYAI_AUDIT_RETENTION_DAYS`,
`LOCALLYAI_BILLING_RETENTION_DAYS`, `LOCALLYAI_SECURITY_RETENTION_DAYS`
(see [`maintenance.md`](maintenance.md#log-retention-rotation-automatic)).

### 8. Erasure log

Lifetime count + last 5 erasure events from `erasure.log`. Each entry
shows timestamp, pseudonym (16-hex), salt era. **Real names never
appear in the portal** — by design, the erasure ledger only ever
holds pseudonyms.

| Satisfies |
|---|
| GDPR Art. 17 — right to erasure ("right to be forgotten") |
| UAE PDPL Art. 14 / KSA PDPL Art. 18 |

If the DPO's external records show an Article-17 request that
isn't in this list, **escalate** — the erasure may not have run,
or it ran on a different deployment.

### 9. Breach events (last 30 days)

Bucketed count of `security.log` events by severity:code. Empty for
most firms.

| Satisfies |
|---|
| GDPR Art. 33 — breach awareness & 72-hour clock |
| GDPR Art. 32(1)(d) — regular testing of measures |
| UAE PDPL Art. 9 / KSA PDPL Art. 31 |

Any `critical:*` row deserves a same-day look. The sentinel breach
detector (watchdog/sentinel.py) writes structured events here; not
every `info` event is a problem (some are routine self-heals).

### 10. DPIA — Data Protection Impact Assessment

Auto-generated from RoPA per GDPR Art. 35 / KSA PDPL Art. 33 / UAE
PDPL Art. 22. Sections:

- **Necessity & proportionality** — auto-filled where deterministic
  (lawful basis, purpose limitation, data minimisation, storage
  limitation). The "accuracy" section is firm-completed (describe
  AI-output review processes).
- **Risks to rights & freedoms** — three baseline risks are
  pre-populated (unauthorised access to privileged content;
  re-identification of audit subjects; AI-output reliance without
  review) with vendor-side mitigations listed. The firm's DPO adds
  any firm-specific risks + mitigations.
- **Controller sign-off** — firm-completed: DPO name, signature
  date, consultation with data subjects, supervisory-authority
  consultation flag.

| Satisfies |
|---|
| GDPR Art. 35 — DPIA for high-risk processing |
| UK ICO guidance on DPIAs for AI |
| KSA PDPL Art. 33 / UAE PDPL Art. 22 |
| ISO 27001 4.4 / 6.1 (risk assessment) |

A DPIA is **required** for AI processing of legal data under most
regulators' interpretations of "high-risk processing." The
auto-generated portion is the vendor's input; the firm's DPO must
review, augment, and sign before this is filed evidence.

### 11. Audit-log sample (last 30 entries)

The actual content of the last 30 audit entries — pseudonymised user
hash, model used, sources retrieved, latency, query hash (not
content), matter code. Auditors want to see the **shape** of what's
logged, not just an "entries: N" count.

| Satisfies |
|---|
| GDPR Art. 5(2) — accountability (demonstrate compliance) |
| ISO 27001 A.8.15 — logging (operational evidence) |
| SRA Code Outcome 7 — supervisory oversight evidence |

Query content is not stored anywhere (only its SHA-256 hash) — by
design — so this section is safe to embed verbatim. There is no
content-leak risk.

### 12. Incident register (last 90 days)

Full `security.log` entries from the last 90 days, ordered most-recent
first. The bucketed counts in section 9 complement this; they don't
replace it. Auditors following an incident inspection want the
records, not summaries.

| Satisfies |
|---|
| GDPR Art. 33 — breach awareness, 72-hour clock |
| ISO 27001 A.5.24-A.5.27 — incident management lifecycle |
| KSA PDPL Art. 31 |

Capped at 100 entries to keep the snapshot bounded; if the firm has
more than 100 incidents in 90 days they have a separate problem the
snapshot is not the right surface for.

### 13. Training records (ISO 27001 A.6.3)

Summary of user training events recorded via
`/admin/training-records`. Each record carries: user, topic,
completion timestamp, optional notes. Snapshot reports total records,
unique users trained, per-topic counts, last recorded timestamp.

| Satisfies |
|---|
| ISO 27001 A.6.3 — information-security awareness, education, training |
| GDPR Art. 32(4) — staff acting under authority of controller |
| SRA Code Outcome 7 — competence |

Operator records training via the Manager UI's Compliance page (quick-
add panel under the Training Records card) or via the API directly.
Topics that the firm typically tracks: GDPR fundamentals, AI-output
review process, incident reporting, password hygiene, PDPL
fundamentals (KSA), client-confidentiality refresher.

### 14. Backup test attestations (ISO 27001 A.8.13/14)

Operator records each successful restore-from-backup test (ad-hoc or
scheduled). Snapshot reports the most recent 5 + cadence. Auditors
want evidence that backups are **tested**, not just configured.

| Satisfies |
|---|
| ISO 27001 A.8.13 — information backup |
| ISO 27001 A.8.14 — redundancy of information processing facilities |
| GDPR Art. 32(1)(c) — restoration ability |

Test types: `full restore` (full data + config), `partial`,
`smoke` (sanity-check only). Cadence target: at least one full restore
test per quarter.

### Snapshot HMAC

Last on the page. The 64-hex HMAC-SHA-256 over the entire bundle
(less the HMAC field itself), keyed with `LOCALLYAI_AUDIT_HMAC_KEY`
— the same key that protects the audit chain. Anyone with that key
can verify offline:

```bash
set -a && source .env && set +a
python scripts/verify_compliance_snapshot.py /path/to/snapshot.html
```

| What VERIFIED proves | What it does NOT prove |
|---|---|
| The bundle was generated by a node holding the audit-chain key | That the data IN the bundle is correct (e.g. the audit chain inside could itself be TAMPERED — the snapshot would still verify as a faithful capture of that fact) |
| The file you have hasn't been altered since download | That the snapshot was generated FROM the right deployment (check `deployment.firm_id`) |
| The HMAC algorithm hasn't been downgraded | Anything about who saw the snapshot in transit |

If you're a regulator and you receive an "unverified" snapshot
(`snapshot_hmac` empty/missing), treat it as informational only —
production deployments always sign. An unsigned bundle indicates the
deployment didn't have `LOCALLYAI_AUDIT_HMAC_KEY` set, which itself
is a compliance defect.

---

## When to use the portal

| Scenario | Cadence | Procedure |
|---|---|---|
| Monthly internal-audit cycle | First business day of each month | [`runbooks/dpo-monthly-snapshot.md`](../runbooks/dpo-monthly-snapshot.md) |
| Regulator asks for current compliance posture | Ad-hoc | Same runbook; file under the ticket reference |
| Insurance / professional-indemnity audit | Ad-hoc | Same |
| Post-incident evidence pack | Within 24h of incident close | Same; pair with the incident's own evidence pack from [`compliance.md` Art-33](compliance.md#article-33--personal-data-breach-notification) |
| New sub-processor disclosed | Within 30 days of disclosure | Pre-disclosure snapshot + post-disclosure snapshot in the firm record |
| New telemetry field set version | Pre-bump + post-bump | Both filed; the firm's record of disclosure depends on both |

For day-to-day operations, the portal is **not** opened — it's a
monthly cadence. The runbooks handle day-to-day.

---

## Integration with the DPO's own audit cycle

A small firm DPO typically has a quarterly internal-audit cycle and
keeps a folder per-supplier where they file:

1. Most recent signed DPA
2. Monthly compliance evidence
3. Incident records (when applicable)
4. Sub-processor change notices

The portal output replaces "most recent screenshot of the admin
dashboard" with a signed, machine-verifiable bundle. The DPO files
the PDF; if they're ever asked to produce evidence later, they can
prove the PDF wasn't edited after they filed it.

For firms with a larger compliance team (mid-size law firm with a
GDPR / KSA-PDPL compliance officer), the monthly snapshot also
feeds their own quarterly report up the management chain. The
machine-verifiable signature means the head of compliance doesn't
have to re-verify with each handover.

---

## What the portal does NOT do

Worth being explicit:

- **It does not test the system.** A `VERIFIED` snapshot showing
  `audit_chain: ok` proves the chain was self-consistent at the
  moment of generation. It does not prove there were no entries
  that should have been written but weren't. For active testing of
  the chain mechanism, run the chaos suite at `tests/ha_chaos.py`.
- **It does not replace the SRA's own audit obligations.** UK firms
  have separate annual filing obligations with the SRA. The portal
  contributes evidence to those filings; it doesn't satisfy them on
  its own.
- **It does not show what data the firm holds.** The portal reports
  on processing posture, not on contents. For the user-facing
  "what data is held on me" answer, see
  [`compliance.md` Article 15](compliance.md#article-15--subject-access-show-me-what-you-hold-on-me).
- **It does not survive a key compromise.** If
  `LOCALLYAI_AUDIT_HMAC_KEY` was leaked, an attacker could mint a
  snapshot that VERIFIES. Treat key rotation per
  [`maintenance.md` HMAC chain key rotation](maintenance.md#hmac-chain-key-rotation)
  as the recovery path; old snapshots remain valid under the old
  key (verifier accepts the key in the env regardless of
  rotation history).
- **It does not flag DPA-clause violations.** It surfaces signals
  (sub-processors, retention, telemetry, breaches). A human still
  has to read the DPA and decide whether the signals match what was
  promised.

---

## Failure modes & escalation

| Symptom | What it means | Next |
|---|---|---|
| `GET /admin/compliance/snapshot` returns 401 | Wrong/rotated admin key | Re-confirm key in vendor-records; [`runbooks/dashboard-locked-out.md`](../runbooks/dashboard-locked-out.md) has the recovery |
| 500 with traceback referencing `audit_verify` | Audit chain is failing to verify; the snapshot endpoint inherits the failure | [`runbooks/audit-chain-broken.md`](../runbooks/audit-chain-broken.md) |
| `snapshot_hmac` is empty in the response | `LOCALLYAI_AUDIT_HMAC_KEY` is unset on the deployment | Production-grade defect — escalate to the founder; the startup gate (Round-2 B6) should have prevented this |
| `verify_compliance_snapshot.py` reports MISMATCH on a fresh download | Either the file was modified in transit, or the key in your local `.env` doesn't match the deployment's | Re-download in a clean session; if still MISMATCH, **escalate** (possible key drift across nodes in HA) |
| Two sequential snapshots disagree on `total_erasures` count | Erasure happened between them, or replication lag in HA fleet | Normal if expected; suspicious if no Art-17 request was filed |
| Sub-processor list shows a name not in the firm's DPA | DPA is stale relative to deployed code | Either ship a DPA amendment with 30-day notice (DPA §6.3) OR pin the deployment to a release where the list matches |

For "escalate" in this chapter: founder by phone within 1 business hour. The compliance portal is part of the firm's regulatory defence; degraded operation of the portal is itself a defect.

---

## Where the portal lives in code (for future maintainers)

| File | Role |
|---|---|
| `api.py` | `compliance_snapshot()` handler + `_render_compliance_snapshot_html()` |
| `api.py` `_COMPLIANCE_SUB_PROCESSORS` | Source of truth for the §6.2 table — keep in sync with `DPA_DRAFT.md` |
| `api.py` `_COMPLIANCE_TELEMETRY_FIELD_SET` | Version + field set — bump version when expanding |
| `apps/manager-ui/src/routes/compliance.tsx` | UI |
| `apps/manager-ui/src/lib/api.ts` | Typed client + `getComplianceSnapshot()` / `downloadComplianceSnapshotHtml()` |
| `scripts/verify_compliance_snapshot.py` | Offline verifier |
| [`runbooks/dpo-monthly-snapshot.md`](../runbooks/dpo-monthly-snapshot.md) | Action manual |
| This file | Reference manual (you're reading it) |

When the bundle structure changes:

1. Bump `bundle["version"]` in `compliance_snapshot()` (currently `"1.0"`).
2. Update the section list in this chapter.
3. Re-issue this chapter (the firm DPO references it).
4. Verify `verify_compliance_snapshot.py` still works (it's version-agnostic — HMAC over JSON less the signature field — so structural changes are fine).

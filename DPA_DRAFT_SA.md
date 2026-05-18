# Data Processing Agreement — LocallyAI (KSA / Saudi Arabia)

> **Template — DRAFT — for adaptation by the Controller's Saudi-qualified
> counsel before signature.** This is not a translation of the UK DPA
> (`DPA_DRAFT.md`); KSA PDPL has structurally different processor
> obligations. Bilingual (Arabic + English) execution is recommended;
> the Arabic version, prepared by Saudi counsel, governs in case of
> conflict before SDAIA or KSA courts.

---

**Between:**

**(1) Controller** (the deploying organisation; the law firm or
professional services entity ingesting documents into LocallyAI),
having its registered office at __________________________ (the
"Controller"); and

**(2) Processor** — the LocallyAI software and supporting services
provided by ___________________________ (the "Processor"), having
its registered office at __________________________.

(Each a "Party"; together the "Parties".)

**Effective Date:** _______________________

**Governing Law:** the laws of the Kingdom of Saudi Arabia.
**Jurisdiction:** the competent courts of Riyadh, KSA.

---

## 1. Subject matter

This DPA governs the Processor's processing of Personal Data on behalf
of the Controller, as a processor under the **Personal Data Protection
Law of the Kingdom of Saudi Arabia (Royal Decree M/19, 2023)** (the
"PDPL") and its implementing regulations issued by the Saudi Data &
Artificial Intelligence Authority ("SDAIA"). It sets out the rights and
obligations of each Party in relation to the Processing operations
described below.

For any term defined in the PDPL that is used in this DPA, the meaning
under the PDPL prevails.

## 2. Nature, purpose and duration of processing

| | |
|---|---|
| **Nature** | Local on-premises Retrieval-Augmented Generation (RAG): the Processor's software runs on Controller-owned hardware in Controller-controlled premises and answers user queries against documents the Controller has chosen to ingest. |
| **Purpose** | (i) Operating an internal AI assistant scoped to the Controller's documents; (ii) per-user usage measurement for internal billing; (iii) auditing of model usage for compliance and accountability obligations. |
| **Categories of Personal Data** | (a) **User identifiers**: names of authorised users (the Controller's personnel) in `users.json`. (b) **Audit metadata**: pseudonymised user hashes, model identifiers, source-chunk counts, latencies, query hashes (no query content). (c) **Billing metadata**: real user names, model used, latency, matter codes (admin-access only). (d) **Document corpus**: documents the Controller has placed in `data/`, ingested into the local Qdrant index. |
| **Categories of Data Subjects** | (i) The Controller's personnel (authorised users of LocallyAI). (ii) Any natural persons referenced in the document corpus the Controller has chosen to ingest. |
| **Duration** | The term of the underlying services agreement between the Parties, plus any post-termination retention period for compliance records (see §10). |

## 3. Lawful basis (PDPL Art. 5–6)

The Controller represents and warrants that it has lawful basis under
PDPL Articles 5 and 6 for each category of processing it instructs the
Processor to perform. Indicative bases mapped to each category:

- **User identifiers** — Art. 5(1)(b) (necessary for performance of an
  employment / contractual relationship between Controller and the
  authorised user).
- **Audit metadata** — Art. 5(1)(c) (legal obligation of Controller in
  respect of records of processing and security of processing).
- **Billing metadata** — Art. 5(1)(b) (necessary for invoicing under the
  Controller's services contract with its end-clients).
- **Document corpus** — Art. 5(1)(b) and/or (f) (Controller's processing
  of its own and its clients' documents pursuant to the engagement
  contracts under which it holds them).

## 4. Controller's obligations

The Controller shall:

- Determine the purposes and means of Processing.
- Ensure all data subjects whose Personal Data appears in the document
  corpus have been informed of the processing in accordance with PDPL
  Art. 11–13 disclosure obligations.
- Maintain its own Records of Processing Activities (PDPL Art. 30
  equivalent). The Processor's `/admin/processing-record` endpoint
  outputs a machine-readable record (RoPA v1.3+) the Controller may use
  as input.
- Keep `users.json` accurate; off-board users promptly when their
  authorisation ends; honour data subject erasure requests under
  §6 below.
- Maintain the technical and organisational measures specified in §5
  on an ongoing basis (including disk encryption — BitLocker on
  Windows, FileVault on macOS).
- **Not apply, install, or permit the application or installation of
  any operating system update, firmware update, or material
  configuration change to the Hardware without the prior written
  approval of the Processor.** "Material configuration change"
  includes, without limitation, macOS major-version upgrades,
  replacement of the Python toolchain, or modification of the
  launchd service definitions installed by the Processor. Breach of
  this obligation voids the Service Level Agreement for the period
  during which the Hardware is operating outside the Processor's
  supported configuration. Apple Rapid Security Response patches
  (or equivalent successor mechanism) are excluded from this
  requirement.

## 3b. Model substitution

3b.1 The Processor may substitute the underlying open-source
language model and/or embedding model used by the Services with an
equivalent open-source model on not less than thirty (30) days'
written notice to the Controller. "Equivalent" means a model of
materially comparable parameter count, quantisation tier (Q8 or
higher half-precision), and licence (open-weights with no telemetry).
The Controller may object within fourteen (14) days; an unresolved
objection allows the Controller to terminate the Services agreement
without penalty.

## 4a. Nature of AI-generated outputs and required human review

**4a.1 The Services produce outputs (including but not limited to
summaries, drafted text, retrieved passages, suggested authorities,
and answers to questions) that are generated by automated language
models. Such outputs are, in every case, AI-generated suggestions
intended to assist a qualified human professional and are not, and
must not be relied upon as, legal, financial, regulatory, or other
professional advice.**

**4a.2 The Controller is solely responsible for ensuring that every
output produced by the Services is reviewed and verified by an
appropriately qualified human professional before being acted upon,
communicated to a client, filed with a court or regulator, or used
as the basis for any decision affecting a third party. This
obligation applies notwithstanding any other provision of this
Agreement.**

**4a.3 The Processor accepts no liability whatsoever for any loss,
damage, claim, sanction, or adverse outcome arising from the use of
an AI-generated output that has not been independently reviewed and
verified by a qualified human professional. This exclusion is a
fundamental term reflecting the nature of the technology supplied
and applies regardless of any cap on liability set out elsewhere in
this Agreement or in the related services agreement.**

4a.4 The Processor will display, in the user interface of the
Services, a visible, persistent disclaimer alongside every
AI-generated response indicating that the response is AI-generated
and must be verified before use. This disclaimer is provided for the
benefit of end users and does not transfer the Controller's
verification obligation to the Processor.

## 5. Processor's obligations (PDPL Art. 18–19, 21, 23)

The Processor undertakes to:

### 5.1 Process only on Controller's instructions (Art. 21)
Process Personal Data solely on the documented instructions of the
Controller. The Controller's instructions consist of: (a) this DPA;
(b) the Controller's deployment configuration in `.env`; (c) the
documents the Controller chooses to place in `data/`; (d) the API
calls authorised users make against the Controller's deployment.

The Processor shall not Process Personal Data for any other purpose
(including no analytics, no model training, no telemetry).

### 5.2 Confidentiality (Art. 23)
Ensure that any persons authorised to access Personal Data on the
Processor's behalf are bound by a written confidentiality obligation
with terms equivalent to this DPA.

### 5.3 Security of Processing (Art. 19)
Implement appropriate technical and organisational measures, including:

- TLS 1.2+ for transport-level confidentiality (cert generated at
  install, country-of-issuance: SA).
- Pseudonymisation of user identifiers in audit records using
  PDPL-Art-19 compliant key material (a salted SHA-256 hash whose
  salt is held under separate access controls; rotation procedure
  documented in `docs/sop/maintenance.md`).
- HMAC-SHA-256 chained audit log for tamper-evidence.
- Per-user API keys with TTL-based expiry.
- IP-based lockout and credential-stuffing detection (alert sent
  to the Controller per §7 below).
- Disk encryption requirement enforced at install time
  (BitLocker / FileVault).

### 5.4 Sub-processors
The Processor confirms that **no sub-processor has access to Personal
Data processed under this DPA** (defined as documents, queries,
responses, audit content, user identifiers, or any other personal
data resident on the Hardware). The deployment runs on
Controller-controlled hardware; the Processor's role is to maintain
the software, not to host data.

The Processor uses the following upstream services for software
distribution and operational monitoring of the deployment; none has
access to Personal Data:

| Upstream | Role | Data observed | Personal Data exposure |
|---|---|---|---|
| Cloudflare | Worker hosting (Processor's monitoring + kill-switch infrastructure) | Anonymised heartbeats: `firm_id` (SHA-256 hash of firm name, one-way), `node_id`, boolean/numeric health gauges, structured alert codes, platform version strings | **None.** No document content, user names, queries, responses, audit entries, or billing entries are transmitted. |
| GitHub | Source code repository + signed release tag distribution | Source code (Processor's, not Controller's data) | **None.** |
| Hugging Face | Anonymous public model downloads at install time | Source IP of the install transaction | **None.** No account is held; no firm-identifying tag is transmitted. |
| Resend | Outbound email for Processor's vendor-side alerts | Subject + body of alert emails to Processor's on-call inbox; subjects use `firm_id` hash | **None.** |
| Slack (optional) | Processor's vendor-side alert echo channel | Same content as Resend when configured | **None.** |
| Apple (future) | Code-signing certificate for client applications (not yet enrolled) | App bundle contents | **None.** |

If, in the future, the Processor proposes to engage a sub-processor
that would have access to Personal Data, it shall give the Controller
at least thirty (30) days' written notice before any such sub-processor
is granted access, identifying the proposed sub-processor and the
activities to be performed. The Controller may object during that
period; if it objects on reasonable grounds, the Parties shall in good
faith seek an alternative, failing which the Controller may terminate
without penalty.

The Processor maintains the current sub-processor list at
`docs/vendor-sop/vendor-sub-processors.md` in the codebase repository
and provides a current copy on request.

> **Translation note for Saudi counsel**: the term "sub-processor"
> ("معالج فرعي") should be replaced with the precise PDPL-recognised
> Arabic legal term once confirmed.

### 5.5 Cross-border transfers (Art. 29)
The Processor confirms that **no Personal Data is transferred outside
the Kingdom of Saudi Arabia** as part of operating LocallyAI:

- The deployment runs on hardware located at the Controller's
  premises within KSA.
- After installation, no outbound network calls are made by the
  LocallyAI software during normal operation. (Verifiable at the
  Controller's network layer.)
- The Processor will not initiate any export of Personal Data outside
  KSA without the Controller's express prior written instruction
  and only in accordance with PDPL Art. 29 / Art. 30 export
  conditions including SDAIA approval where required.

### 5.6 Data subject rights (Art. 9–17)
The Processor shall, taking into account the nature of the processing,
assist the Controller by appropriate technical and organisational
measures, in fulfilling the Controller's obligation to respond to
requests from Data Subjects exercising their rights under PDPL
Articles 9–17, namely:

- **Access** (Art. 9): the Processor's `/admin/processing-record` and
  `/admin/audit-verify` endpoints support extraction of records about
  a specific data subject by the Controller.
- **Correction** (Art. 10): supported via `manage_users.py rotate`.
- **Erasure** (Art. 18): supported via `manage_users.py erase`, which
  removes the user from `users.json`, redacts billing log entries,
  appends an erasure tombstone to `erasure.log`, and refuses future
  audit writes for the affected pseudonym (across all salt eras).
- **Restriction / Objection** (Art. 11): supported via account
  suspension at the Controller's discretion.

The Controller is responsible for evaluating each request and deciding
whether to act; the Processor's tools execute the Controller's
decisions.

### 5.7 Records of Processing (Art. 30 equivalent)
The Processor maintains the live RoPA at the `/admin/processing-record`
endpoint of the deployment. Version 1.3 of the RoPA stamps the
deployment's `data_region` ("KSA") and the applicable regulatory
framework (PDPL + ISO 27001).

## 6. Personal data breach notification (PDPL Art. 31)

The Processor shall notify the Controller without undue delay, and in
any event within **twenty-four (24) hours** of becoming aware, of any
suspected or confirmed Personal Data breach affecting data Processed
under this DPA. Notification shall include, to the extent known:

- The nature of the breach.
- The categories and approximate number of Data Subjects and
  records affected.
- The likely consequences.
- The measures taken or proposed to address the breach and mitigate
  its possible adverse effects.

The Controller is responsible for any notification to SDAIA or to
affected Data Subjects required under PDPL Art. 31 (breach
notification) and Art. 22 (notification to Data Subjects of breaches
likely to cause significant harm). The Processor will provide
reasonable assistance.

The Processor shall maintain an internal incident register
documenting each breach, the response, and the post-incident
remediation. The register is available to the Controller and to
SDAIA on request.

## 7. Audit and inspection (Art. 19)

The Controller (or an independent auditor mandated by the Controller
and bound by appropriate confidentiality obligations) may, on
reasonable prior notice and at the Controller's expense, audit the
Processor's compliance with this DPA. Audits shall:

- Take place at reasonable times and not unreasonably interfere with
  the Processor's operations.
- Be limited to information necessary for verifying compliance with
  this DPA and applicable PDPL obligations.
- Not extend to other Controllers' data or to Processor trade secrets
  unrelated to the agreed processing.

The Processor shall make available to the Controller all information
necessary to demonstrate compliance, including:

- The output of `/admin/processing-record` (live RoPA).
- The output of `/admin/audit-verify` (chain integrity verification).
- The outputs of `scripts/audit_install.sh` (control verification).
- The ISO 27001 control map (`docs/iso27001-controls.md`).
- The SOP and CHANGELOG (`docs/SOP.md`, `docs/sop/CHANGELOG.md`).

## 8. Personal data retention

| Category | Retention period | Reference |
|---|---|---|
| User identifiers (`users.json`) | Until the user is removed by the Controller. | §4 |
| Audit metadata | `LOCALLYAI_AUDIT_RETENTION_DAYS` env var (default 365 days; Controller configures per its own retention schedule and PDPL guidance from Saudi counsel). | sentinel rotation |
| Billing metadata | Same env var as above. | sentinel rotation |
| Document corpus | Until the Controller deletes documents from `data/`. | Controller-controlled |
| Pseudonymisation salt eras | At least the longest retention period above (otherwise re-identification on subject access fails for old records). Configurable via `manage_users.py rotate-audit-salt --keep-eras N`. | maintenance.md |

The Controller may shorten retention by lowering
`LOCALLYAI_AUDIT_RETENTION_DAYS`; the Processor will not lengthen
beyond Controller's instruction.

## 9. Termination

On termination of this DPA or the underlying services agreement, the
Processor shall, at the Controller's choice:

- **Return** all Personal Data to the Controller in a structured,
  commonly-used, machine-readable format. (The Controller already
  holds the data — the deployment runs on its hardware — so this
  typically reduces to producing a final evidence pack per
  `docs/sop/decommission.md`.)
- **Delete** all Personal Data and certify the deletion in writing.
  The decommission procedure includes cryptographic erasure of the
  encryption key (effectively irreversible erasure of every encrypted
  artefact on the disk).

The Processor will retain copies only to the extent required by
applicable law and only for the period mandated by such law.

## 10. Term, modification, severability

This DPA takes effect on the Effective Date and continues for the
duration of the underlying services agreement plus any survival
periods for compliance records.

Modifications must be in writing, in English and Arabic
(Arabic-controlling), and signed by both Parties.

If any provision is held invalid, the remainder remains in force.

---

## Schedule A — applicable laws and regulations

| | |
|---|---|
| **Primary** | Personal Data Protection Law of the Kingdom of Saudi Arabia (Royal Decree M/19, 2023). |
| **Implementing regulations** | SDAIA-issued regulations and decisions in force from time to time. |
| **Information security** | ISO/IEC 27001:2022 (controls map at `docs/iso27001-controls.md`). |
| **Sector-specific** | Any rules of the Saudi Bar Association, the Capital Market Authority, the Saudi Central Bank (SAMA), or other regulator with jurisdiction over the Controller's activities. |

> Acknowledged but not primary: the deployment-resident may also touch
> jurisdictions where the Controller's clients are situated (UAE
> PDPL, GDPR for European clients, etc.). The Controller is
> responsible for any cross-jurisdictional implications of its own
> client portfolio; the Processor's posture is determined by the
> deployment site (KSA).

---

## Schedule B — technical and organisational measures (summary)

The full evidence is in the live RoPA, the SOP, and the ISO 27001
control map. This is a summary for signature pages.

- **Pseudonymisation** of user identifiers in audit records (PDPL
  Art. 19; salted SHA-256, salt era rotation tooling).
- **Tamper-evidence** of the audit log (HMAC-SHA-256 chain per node;
  `/admin/audit-verify`).
- **Encryption at rest** (FileVault / BitLocker; mandatory at
  install).
- **Encryption in transit** (TLS 1.2+; self-signed root in OS
  keychain; cert subject `C=SA`).
- **Access control** (per-user API keys with TTL; admin key
  separation; IP-based lockout; per-key rate limit).
- **Breach detection** (sentinel watches `security.log` for
  credential-stuffing; PDPL Art. 31 alert).
- **Backup and recovery** (per `docs/sop/recovery.md`).
- **Incident response** (per `docs/sop/incidents-*.md`).
- **Logging and monitoring** (per ISO 27001 A.8.15-16; live audit
  + monitor endpoints).
- **Personnel and confidentiality** (Processor's personnel
  contractually bound).

---

## Signature page

**Controller:** _______________________

  Name: ___________________________
  Title: __________________________
  Date: ___________________________
  Signature: ______________________

**Processor (LocallyAI):** _______________________

  Name: ___________________________
  Title: __________________________
  Date: ___________________________
  Signature: ______________________

---

> **For Saudi counsel:** Please review §5.4 (sub-processor language —
> the Arabic legal term needs confirmation against PDPL implementing
> regulations); §5.5 (cross-border transfer assertion — verify wording
> matches Art. 29 expected language); §6 (breach notification window —
> 24h to Controller is conservative versus Art. 31's "without undue
> delay"; confirm acceptable). Translate the entire DPA into Arabic
> for execution; both versions to be initialled on every page.

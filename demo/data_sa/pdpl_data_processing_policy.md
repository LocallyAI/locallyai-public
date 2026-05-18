# Personal Data Processing Policy (PDPL-aligned)

> **Synthetic demo document** — corporate-internal data processing
> policy aligned with the **Saudi Personal Data Protection Law
> (Royal Decree M/19, 2023)** and SDAIA implementing regulations.
> Not legal advice; demo seed for LocallyAI Saudi deployments.

---

## 1. Purpose

This policy establishes the firm's approach to processing Personal
Data in accordance with the Personal Data Protection Law of the
Kingdom of Saudi Arabia ("PDPL") and its implementing regulations
issued by the Saudi Data & Artificial Intelligence Authority
("SDAIA").

## 2. Scope

This policy applies to:

- All Personal Data processed by the firm in the course of its
  operations within the Kingdom of Saudi Arabia.
- All firm personnel (employees, contractors, partners, paralegals,
  IT staff, summer associates, secondees) who process Personal Data
  on the firm's behalf.
- All systems and applications used to process Personal Data,
  including the firm's on-premises AI systems and document
  management.

## 3. Definitions

- **Personal Data** — any information that identifies or makes
  identifiable a natural person.
- **Sensitive Personal Data** — categories specified in PDPL Art. 1
  including health data, biometric data, racial / ethnic origin,
  religious beliefs, criminal records, financial information, and
  data relating to children.
- **Processing** — any operation performed on Personal Data:
  collection, recording, organisation, structuring, storage,
  adaptation, retrieval, consultation, use, disclosure, dissemination,
  alignment, restriction, erasure, or destruction.
- **Controller / Processor** — as defined in PDPL Art. 1.
- **Data Subject** — the identified or identifiable natural person to
  whom the Personal Data relates.

## 4. Lawful basis for processing (PDPL Art. 5)

The firm processes Personal Data on one or more of the following
bases as set out in PDPL Art. 5:

- **(a) Consent** of the Data Subject — used only where another
  basis does not apply and where consent is freely given, specific,
  informed, and unambiguous.
- **(b) Contract** — necessary for performance of a contract with the
  Data Subject (employment, professional services engagements).
- **(c) Legal obligation** — necessary to comply with a legal
  obligation (anti-money-laundering, tax records, professional
  conduct rules of the Saudi Bar Association, employment law).
- **(d) Vital interests** — to protect life or physical integrity.
- **(e) Public interest** — narrow application; documented case by
  case.
- **(f) Legitimate interests** — only where the firm's legitimate
  interests are not overridden by the Data Subject's rights.

Each processing activity is recorded in the firm's Records of
Processing Activities (RoPA, see §10) with its lawful basis.

## 5. Sensitive Personal Data

Processing of Sensitive Personal Data is subject to additional
safeguards:

- Explicit consent or specific legal basis required (PDPL Art. 6).
- Access restricted to a named, minimised set of personnel.
- Encrypted at rest and in transit; logged accesses; reviewed
  quarterly.
- Cross-border transfer prohibited absent specific PDPL Art. 29
  conditions and SDAIA approval where applicable.

## 6. Data subject rights (PDPL Art. 9–17)

Data Subjects have the following rights, exercisable by written
request to the firm's Privacy Office (privacy@firm.example):

- **Right to be informed** — clear privacy notices at point of
  collection.
- **Right of access** — copy of Personal Data held; response within
  the statutory window from receipt of a verifiable request.
- **Right to correction** — inaccurate data corrected promptly.
- **Right to erasure** — Personal Data erased where the firm has no
  ongoing lawful basis to retain it (subject to limited retention
  obligations under professional rules and PDPL Art. 18 carve-outs).
- **Right to restriction** — restriction during dispute resolution.
- **Right to object** — to processing based on legitimate interests
  or for marketing purposes.
- **Right to data portability** — structured, commonly-used format.

The Privacy Office maintains a register of every request received,
the response, and the timeline.

## 7. Cross-border transfer (PDPL Art. 29)

The firm does not transfer Personal Data outside the Kingdom of Saudi
Arabia except where one of the conditions in PDPL Art. 29 is
satisfied:

- adequate level of protection determined by SDAIA;
- explicit informed consent of the Data Subject;
- contract performance with the Data Subject; or
- legal obligation.

Where SDAIA approval is required for a specific transfer, it is
obtained before the transfer occurs.

The firm's on-premises AI systems (see §11) do not effect cross-
border transfers; they run on hardware physically located in KSA and
make no outbound API calls during operation.

## 8. Retention

Personal Data is retained only for as long as necessary for the
lawful basis on which it is processed, plus any extension required by
professional rules (e.g. retention of client files for the period
mandated by the Saudi Bar Association).

A retention schedule, broken down by category, is maintained by the
Privacy Office and reviewed annually.

## 9. Security (PDPL Art. 19)

The firm implements appropriate technical and organisational
measures including, without limitation:

- access control (least privilege; per-user credentials with TTL);
- encryption at rest (BitLocker / FileVault on all firm devices);
- encryption in transit (TLS 1.2+);
- pseudonymisation of identifiers in audit and logging
  (PDPL Art. 19);
- tamper-evident logging (HMAC-chained logs in security-critical
  systems);
- network segregation (privileged systems on isolated VLAN);
- multi-factor authentication for administrative access;
- security incident response procedure (see §12);
- annual penetration testing of internet-facing systems;
- staff training on data protection (annual mandatory completion).

## 10. Records of Processing Activities (RoPA)

The firm maintains a Record of Processing Activities listing each
processing operation:

- name and purpose;
- categories of Data Subjects and Personal Data;
- recipients;
- cross-border transfers (none, or specifics under PDPL Art. 29);
- retention period;
- technical and organisational measures;
- lawful basis.

The firm's on-premises AI systems automatically expose a live RoPA
fragment for that processing activity (see §11).

## 11. On-premises AI processing

The firm operates on-premises Retrieval-Augmented Generation systems
(LocallyAI deployments) for internal AI assistance:

- runs entirely on firm-owned hardware located in KSA;
- ingests only documents the firm has chosen to make available;
- audit log is HMAC-chained, pseudonymises user identifiers per
  PDPL Art. 19, and is retained per the firm's retention schedule;
- no outbound network traffic during normal operation
  (verifiable at the network firewall);
- the live RoPA at the deployment's `/admin/processing-record`
  endpoint stamps `data_region: "KSA"` and identifies the applicable
  regulatory framework as PDPL + ISO 27001;
- separate Data Processing Agreement (DPA) governs the relationship
  with the LocallyAI vendor; this policy supplements that DPA.

## 12. Personal data breach response (PDPL Art. 31)

On suspected or confirmed breach:

1. Notify the Privacy Office immediately (within the same business
   day).
2. The Privacy Office determines whether the breach is reportable
   under PDPL Art. 31. The deciding factors include the categories
   of Personal Data affected, the number of Data Subjects, and the
   likelihood of harm.
3. Reportable breaches are notified to SDAIA without undue delay.
4. Where the breach is likely to cause significant harm, affected
   Data Subjects are notified in plain language with mitigation
   steps.
5. An incident register is maintained; each breach has a
   post-incident review and lessons-learned entry.

## 13. Training and accountability

- All firm personnel complete data protection training on
  joining and annually thereafter.
- The Privacy Office reports quarterly to the Managing Partner.
- Compliance with this policy is part of personnel performance
  reviews.

## 14. Policy review

This policy is reviewed annually and on any material change to PDPL
or SDAIA implementing regulations.

---

**Approved by:** Managing Partner

**Effective:** _________________

**Next review:** _________________

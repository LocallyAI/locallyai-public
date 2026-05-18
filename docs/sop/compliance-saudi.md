# Compliance ops — KSA / PDPL

PDPL-specific procedures for Saudi deployments. Read this alongside
the master [compliance.md](compliance.md) — the structure is the
same; this chapter covers the Saudi-specific differences. The
DPO + Saudi counsel run point.

---

## When to use this chapter vs the master

- Master `compliance.md` covers GDPR / UK GDPR / ISO 27001 / DIFC /
  ADGM with KSA PDPL mentioned in passing.
- This chapter is the canonical KSA procedure when
  `LOCALLYAI_DATA_REGION=KSA`. Subject-access, breach notification,
  cross-border, RoPA — all Saudi-correct.

If your deployment is UK, ignore this chapter.

---

## PDPL Art. 9 — subject access (right of access)

The technical procedure mirrors the master GDPR Art. 15 procedure
but cites PDPL.

### Procedure

1. **Verify the requester is the subject** (firm policy outside this
   doc — typically national-ID-based).
2. **Identify the user's name as it appears in `users.json`**:
   ```bash
   python manage_users.py list | grep -i "First Last"
   ```
3. **Compute the user's pseudonyms across every salt era**:
   ```bash
   .venv/bin/python -c "
   from dotenv import load_dotenv; load_dotenv('.env')
   from config import pseudonymise_user, known_salt_eras
   name = 'First Last'
   for era in known_salt_eras():
       p = pseudonymise_user(name, era=era)
       print(f'  era {era}: {p}')
   "
   ```
4. **Extract every audit + billing entry**. Same script as in
   master `compliance.md § Article 15`, with one additional filter:
   the `data_region` field — typically all entries are `"KSA"`, but
   if the firm migrated from a UK deployment some old entries may be
   `"UK"`. Include both in the response and explain to the subject
   that the pre-migration entries fall under the prior data-region's
   framework.

### Reply (Arabic version recommended)

The firm's Saudi-counsel-approved Arabic reply template should be
used. The `compliance.md` Appendix has an English template; an
Arabic translation is firm-specific (the firm's lawyer drafts it).

### Time limit

PDPL doesn't specify a fixed window like GDPR's 1 month; "without
undue delay" is the standard. Firm policy: respond within 30 days
unless legitimately complex.

---

## PDPL Art. 18 — erasure (right to be forgotten)

### Procedure

```bash
python manage_users.py erase "First Last"
```

The output is identical to the UK procedure; the only difference is
the `regulation` field in `erasure.log` shows
`"GDPR art.17 / UAE PDPL art.14 / KSA PDPL art.18"`.

### Saudi-specific carve-outs

Like GDPR Art. 17(3), PDPL Art. 18 has carve-outs where the firm may
refuse erasure:

- compliance with another legal obligation (e.g. Saudi Bar professional
  record-keeping rules; AML obligations);
- defence of legal claims;
- public interest in the field of public health.

The DPO + Saudi counsel evaluate; the IT-ops procedure executes the
DPO's decision.

---

## PDPL Art. 30 — Records of Processing Activities

```bash
ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' .env | cut -d= -f2)
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/admin/processing-record \
  > ropa_<date>.json
```

Output is RoPA v1.3 with:
- `data_region: "KSA"`
- `applicable_regulations: ["KSA PDPL ...", "ISO/IEC 27001:2022"]`
- `breach_notification: "PDPL Art. 31 (notification to SDAIA + data subjects)"`
- `data_subject_rights.erasure: "manage_users.py erase <name> (PDPL art. 18 / UAE PDPL art. 14)"`

Hand to the firm's DPO. The Saudi RoPA register maintained at the
firm level should reference this output for the LocallyAI processing
activity.

---

## PDPL Art. 31 — breach notification

**Trigger:** an actual or suspected breach. Examples per the master
`compliance.md`; the Saudi-specific bits are below.

### 72-hour clock to SDAIA

Like GDPR Art. 33, PDPL Art. 31 requires notification to the
supervisory authority — for Saudi, the **Saudi Data & Artificial
Intelligence Authority (SDAIA)** — within 72 hours of becoming aware
of a breach that's likely to cause harm to data subjects.

The DPO drafts the notification. Likely contents per Art. 31:

- Nature of the breach.
- Categories and approximate number of data subjects and records
  affected.
- Likely consequences.
- Measures taken / proposed.
- Contact point at the firm (the DPO).

### Notification to data subjects (Art. 22)

Where the breach is likely to cause significant harm to a data
subject, notify them in clear language. Saudi-specific: notification
should be in Arabic for Arabic-speaking subjects.

### IT-ops contribution

Same as the master `compliance.md § Article 33` procedure — preserve
evidence, contain, eradicate, document. Then hand the evidence pack
to the DPO so they can complete the SDAIA filing.

The evidence pack's RoPA snapshot (RoPA v1.3 with `data_region: KSA`)
shows SDAIA the firm self-identifies as a Saudi processor and is
reporting under PDPL Art. 31, which is the right framing.

---

## PDPL Art. 22 — Cross-border transfer

Saudi PDPL is stricter than GDPR on cross-border transfer. PDPL
Art. 29 / Art. 30 requires that personal data leaving KSA satisfies
one of:

- adequate level of protection determined by SDAIA;
- explicit, informed, freely-given consent of the data subject;
- contract performance with the data subject;
- legal obligation of the controller; or
- specific exception including SDAIA approval.

LocallyAI's design avoids the question: the deployment runs entirely
on hardware located in KSA and makes no outbound API calls during
operation. The RoPA's `international_transfers` field documents this.

**If the firm needs to export data** (e.g. to a foreign auditor, to
a parallel deployment in another jurisdiction):

1. The DPO + Saudi counsel evaluate against PDPL Art. 29/30.
2. If approved, document the basis in the RoPA before the export.
3. Verify SDAIA approval has been obtained where the basis requires
   it.
4. The export itself is a manual operation by the operator (e.g.
   `tar` of `data/` or selected `audit-*.log.gz` archives) — there
   is no LocallyAI feature that initiates cross-border transfer.

---

## PDPL Art. 19 — security of processing (key material posture)

The deployment carries the same security measures as the UK posture
plus PDPL-specific framing:

- **Pseudonymisation** of user identifiers (PDPL Art. 19 explicitly
  recognises this as a relevant technical measure).
- **Tamper-evident logging** (HMAC chain).
- **Encryption at rest** (FileVault / BitLocker; mandatory at install).
- **Access control** (per-user keys with TTL).
- **Salt rotation** (`manage_users.py rotate-audit-salt`) is the
  Art. 19 cryptographic-key rotation control — annual at minimum,
  immediate on suspected leak.

Verify at the deployment:

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/admin/processing-record \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d['pseudonymity'], indent=2))"
```

Expected: `current_salt_era` set, `key_material_state` shows ok / warn
findings, no fails.

---

## SDAIA inspection

A regulator inspection plays out the same way as the UK GDPR / ICO
inspection in master `compliance.md § Regulator subpoena / on-site
inspection`. SDAIA-specific notes:

- The DPO + senior partner coordinate; IT-ops is on standby.
- Build the evidence pack in advance (the RoPA v1.3, all per-control
  verifications from `docs/iso27001-controls.md`, the SOP, the
  CHANGELOG).
- Prepare an Arabic version of the RoPA summary (one page) for the
  inspector.
- Demo the verification commands live; they should produce the
  expected output.

The Saudi-specific evidence the inspector will likely ask for:

- Proof the deployment is physically in KSA (server location,
  procurement records).
- Proof of `data_region: "KSA"` on every audit entry (run
  `audit-verify` and tail `audit.log`).
- Proof of PDPL Art. 19 controls (the RoPA's `security_measures`).
- Proof there is no cross-border transfer (the
  `international_transfers` field; firewall logs at the deployment
  site).

---

## Annual self-check (Saudi posture)

Once a year, with DPO + Saudi counsel:

- [ ] DPA_DRAFT_SA.md still aligns with current PDPL implementing
      regulations (SDAIA may have published updates).
- [ ] Salt rotation has happened in the past 12 months
      (per `maintenance.md § Salt rotation`).
- [ ] Subject-access requests in the year — handled within the
      "without undue delay" expectation; Arabic responses where
      applicable.
- [ ] Breach incidents in the year — SDAIA notifications filed
      where required; data-subject notifications sent in Arabic
      where applicable.
- [ ] No unintended cross-border transfers occurred (firewall
      logs at the deployment confirm).
- [ ] DPO has the latest RoPA snapshot (v1.3+).
- [ ] Saudi counsel has reviewed any changes to the LocallyAI codebase
      that affect compliance posture (CHANGELOG.md for the year).

File the year's findings.

---

## Differences from UK posture (quick reference)

| Topic | UK GDPR | KSA PDPL |
|---|---|---|
| Supervisory authority | ICO | SDAIA |
| Breach window | 72h to ICO | 72h to SDAIA |
| Erasure article | Art. 17 | Art. 18 |
| Subject access article | Art. 15 | Art. 9 |
| Pseudonymisation article | Art. 25 | Art. 19 |
| Cross-border default | Adequacy + SCCs | Stricter; SDAIA may approve specific transfers |
| Sub-processor language | Standard | Arabic translation needed (Saudi counsel confirms term) |
| RoPA citing | "GDPR Art. 30" | "PDPL Art. 30 (record of processing)" |
| Currency rendering (worker / manager UI) | GBP | SAR |
| Calendar rendering (manager UI) | Gregorian | Hijri (Umm al-Qura) — see `setup-saudi.md § 6` |
| Time zone (manager UI) | Browser-local (typically Europe/London) | Asia/Riyadh enforced when ar |
| TLS cert subject | C=GB | C=SA |
| Demo doc set | UK NDA + GDPR policy + FRI lease | DIFC NDA + PDPL policy + M&A letter + restructuring memo + bilingual welcome |
| Default embed model | nomic-embed-text:latest (English-centric) | intfloat/multilingual-e5-base (Arabic-capable) |

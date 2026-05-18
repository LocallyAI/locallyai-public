# Compliance operations

DPO-grade procedures for GDPR, ISO 27001, UAE PDPL, KSA PDPL, DIFC,
ADGM. Each procedure produces an evidence artefact (log line, screenshot,
exported JSON) you file against the request.

This chapter assumes you've read the master
[../iso27001-controls.md](../iso27001-controls.md) at least once — that
file is the canonical control map; this one is the operational
playbook.

> **For the monthly compliance evidence pack and the in-product DPO
> surface**, see [`dpo-compliance-portal.md`](dpo-compliance-portal.md)
> — that chapter explains the Compliance tab in the Manager UI, what
> each section means, and which regulation Article each piece
> satisfies. The runbook
> [`../runbooks/dpo-monthly-snapshot.md`](../runbooks/dpo-monthly-snapshot.md)
> is the click-by-click action manual for the monthly snapshot.
>
> This chapter remains the canonical playbook for **individual
> data-subject requests** (Articles 15, 17, 30, 32, 33). The portal
> aggregates posture; this chapter handles per-request workflows.

---

## Article 15 — Subject access ("show me what you hold on me")

**Trigger:** a data subject asks what you process about them.

### What to return

Per GDPR Art. 15(1) and equivalents: confirmation of processing, the
purposes, the categories of data, the recipients, the retention
period, and a copy of the data.

For LocallyAI specifically, the data on the subject is:

- Their real name in `users.json` and `billing.log`.
- Their pseudonym (`SHA-256(salt:name)[:16]`) in every `audit.log`
  entry they've ever generated, plus the historical pseudonyms under
  every retired salt era.
- Their query content is **not** stored — only a SHA-256 hash of each
  query lands in the audit log.

### Procedure

1. **Verify the requester is the subject** (firm-policy outside this
   doc).
2. **Identify the user's name as it appears in `users.json`**:
   ```bash
   python manage_users.py list | grep -i "first last"
   ```
3. **Compute their pseudonym across every salt era**:
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
4. **Extract every audit entry under any of those pseudonyms**:
   ```bash
   .venv/bin/python <<'PY'
   import json, gzip, glob, os
   from dotenv import load_dotenv; load_dotenv('.env')
   from config import pseudonymise_user, known_salt_eras
   name = 'First Last'
   pseudonyms = {pseudonymise_user(name, era=e) for e in known_salt_eras() if pseudonymise_user(name, era=e)}
   def scan(reader):
       for line in reader:
           try:
               e = json.loads(line)
           except Exception:
               continue
           if e.get('user_hash') in pseudonyms:
               print(json.dumps(e))
   scan(open('logs/audit.log'))
   for arc in sorted(glob.glob('logs/audit-*.log.gz')):
       scan(gzip.open(arc, 'rt'))
   PY
   ```
   Redirect into a JSON file you'll send the subject:
   ```bash
   ... > subject_access_<name>_<date>.json
   ```
5. **Extract the billing rows** (real-name, admin-only):
   ```bash
   .venv/bin/python -c "
   import json
   for line in open('logs/billing.log'):
       e = json.loads(line)
       if e.get('user') == 'First Last':
           print(json.dumps(e))
   " > subject_access_billing_<name>_<date>.json
   ```
6. **Send the subject** (1) the JSON files, (2) a plain-language
   covering letter explaining: pseudonymised audit log, real-name
   billing log, query content not stored. Template letter is
   firm-specific; example phrasing in the appendix at the bottom of
   this file.
7. **File the evidence** — keep the two JSON outputs, the timestamped
   request, and your reply for the firm's data-subject-request
   register.

### Time limit

GDPR Art. 12(3): one month, extensible by two more if "complex." Don't
push the limit.

---

## Article 17 — Erasure ("right to be forgotten")

**Trigger:** the subject formally requests erasure AND no overriding
legal obligation requires you to keep their data (regulatory
record-keeping, ongoing litigation, etc.).

### Decision: do you erase?

The firm's DPO decides — not the IT-ops person. Common reasons to
**refuse** are documented in Art. 17(3): legal obligation, public
interest, legal claims defence. If you have one of these, document it
and refuse. Otherwise, proceed.

### Procedure

```bash
python manage_users.py erase "First Last"
```

Output:

```
Erasure complete for 'First Last'.
  user: First Last
  pseudonym: <16-hex>
  billing_redacted_lines: 47
  users_json: removed
  erasure_log_entry: 2026-05-06T16:30:11Z
  peers_notified: {'mac-b': 'ok'}                 # HA only
```

What just happened:

1. The user is removed from `users.json`. Their API key is dead.
2. Every line in `billing.log` mentioning their real name is rewritten
   to `"user": "(erased)", "erased": true`.
3. A **tombstone per salt era** is appended to
   `$LOCALLYAI_SHARED_DIR/erasure.log` (in HA: visible to all peers
   within ~10s via Syncthing, and immediately via fan-out refresh).
4. Going forward, `validate_key` rejects any key that resolves to one
   of the erased pseudonyms. `_write_audit` refuses entries.

### What is NOT erased (by design — defensible to the regulator)

- **Past audit entries** stay. They contain only the pseudonym, not
  the name, and the HMAC chain forbids in-place editing. Erasing them
  would destroy the tamper-evidence of the chain — which is itself a
  legal-obligation control under ISO 27001 A.5.33 and is required to
  defend against compromise allegations. Document this trade-off in
  your reply.
- **Past billing entries** keep their redacted form (`(erased)` user)
  but retain timestamps + matter codes for regulatory accounting
  (audit-trail under SRA / FCA / equivalent).

### Verification

The user's API key no longer authenticates:

```bash
curl -sk -H "Authorization: Bearer <their old key>" https://localhost:8000/healthz
# → ok (healthz is unauthed)
curl -sk -H "Authorization: Bearer <their old key>" https://localhost:8000/v1/me
# → 401 Invalid API key
```

Their pseudonym is in the erasure ledger:

```bash
grep "<their-pseudonym>" $LOCALLYAI_SHARED_DIR/erasure.log
# → one line per salt era
```

If you try to use their old key from elsewhere, `security.log` will
record the auth_failure with the salted key fingerprint — not the
real name (Art. 25 data minimisation).

### Evidence to file

The CLI output above + the matching lines from `erasure.log` + the
DPO's signed decision. File against the request.

### HA

The fleet automatically broadcasts a refresh; verify on the peer:

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://<peer-ip>:8000/v1/me \
  -H "Authorization: Bearer <their old key>"
# → 401
```

---

## Article 30 — Records of Processing Activities (RoPA)

**Trigger:** the regulator asks for the RoPA, OR you want to update
the firm's RoPA register.

### Procedure

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/admin/processing-record \
  > ropa_<date>.json
```

The output is the live, machine-generated RoPA at version 1.2.
Includes: controller info, purposes, data categories, recipients,
international transfers (None — on-prem), retention, security
measures, data-subject rights, regulations acknowledged, the
**high_availability** block (active nodes, Qdrant topology, sync
layer, failover model), and the **pseudonymity** block (current era,
known eras, key-material findings).

Hand the JSON to the firm's DPO. They can either include it verbatim
or use it as raw input for the firm's combined RoPA across all
processing activities.

---

## Article 32 — Security of processing (evidence pack)

**Trigger:** an auditor asks for evidence of the technical and
organisational measures.

### Evidence files

Build the evidence pack:

```bash
mkdir -p ~/locallyai-evidence-<date>
cd ~/locallyai-evidence-<date>

ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' ~/locallyai/.env | cut -d= -f2)
BASE=https://localhost:8000

curl -sk -H "Authorization: Bearer $ADMIN_KEY" $BASE/admin/processing-record       > 01_ropa.json
curl -sk -H "Authorization: Bearer $ADMIN_KEY" $BASE/admin/audit-verify             > 02_audit_chain_status.json
curl -sk -H "Authorization: Bearer $ADMIN_KEY" $BASE/admin/fleet/audit-verify       > 03_fleet_audit_chain.json
curl -sk -H "Authorization: Bearer $ADMIN_KEY" $BASE/admin/fleet/qdrant-health      > 04_qdrant_health.json
curl -sk -H "Authorization: Bearer $ADMIN_KEY" $BASE/admin/fleet/gate               > 05_gate_state.json
curl -sk -H "Authorization: Bearer $ADMIN_KEY" $BASE/monitor/health/detailed        > 06_monitor_detailed.json

cp ~/locallyai/logs/install_audit_*.log .                # weekly install audit
cp ~/locallyai/docs/iso27001-controls.md .               # control map
cp ~/locallyai/docs/SOP.md .                             # SOP
cp -r ~/locallyai/docs/sop .                             # full SOP set

echo "Evidence pack created at $(pwd)"
```

Then `tar czf evidence_<date>.tar.gz ~/locallyai-evidence-<date>` and
file it.

### What an auditor will look for

For each row in [../iso27001-controls.md](../iso27001-controls.md),
the verification command should produce the expected output. Run them
in front of the auditor if asked — that's why every row has a runnable
verification.

---

## Article 33 — Personal data breach notification

**Trigger:** an actual or suspected breach. Non-exhaustive examples:

- `audit-verify` returns `TAMPERED` and you can't account for it.
- `security.log` shows a **credential-stuffing** alert from the breach
  detector.
- A user reports their key was stolen / phished / found in a leaked
  password dump.
- A box was unattended in an untrusted location ("forgot my Mac at the
  café").
- Salt or HMAC key may have leaked.

### 72-hour clock

GDPR Art. 33(1): notify the supervisory authority within **72 hours**
of becoming aware. Start the clock now.

### Procedure

1. **Contain** — see the relevant chapter:
   - [incidents-security.md § "Salt leak"](incidents-security.md#salt-leak) /
     [§ "Admin key leak"](incidents-security.md#admin-key-leak) /
     [§ "User key leak"](incidents-security.md#user-key-leak)
   - [incidents-software.md § "Audit chain TAMPERED"](incidents-software.md#audit-chain-tampered)
2. **Preserve evidence** — DO NOT clear logs.
   ```bash
   cp -p logs/audit.log     ~/incident-<date>/
   cp -p logs/security.log  ~/incident-<date>/
   cp -p logs/billing.log   ~/incident-<date>/
   cp -p .env               ~/incident-<date>/      # for the salt era id, NOT to share
   cp -p users.json         ~/incident-<date>/      # may help identify scope
   ```
3. **Establish scope**:
   - Which users' pseudonyms appear?
   - Date range (first audit timestamp → last)?
   - Was query content exposed? (No — only hashes.)
   - Was real name exposed? (Only via `billing.log` if the breach
     covered that file.)
4. **Notify the DPO immediately** — they own the regulator
   notification.
5. **Notify users if "high risk"** (Art. 34) — typically required if
   the breach exposed real names + activity history that could
   embarrass or expose the user.
6. **Remediate** — rotate keys / salt / HMAC key per
   [incidents-security.md](incidents-security.md). Do not skip.
7. **Document** the incident, response, and lessons in your firm's
   incident register.

### What you can honestly tell the regulator

- Audit logs are pseudonymised, salted, and HMAC-chained per node.
  Tampering is detectable; you'll know whether the breach involved
  modification or only read.
- Real names are in a separate, admin-only billing log under tighter
  ACLs.
- Query content was never logged — only SHA-256 hashes.
- Salt and key material live on disk encrypted at rest (FileVault /
  BitLocker). If the box wasn't physically taken AND the disk was
  encrypted at rest, the read-only blast radius is bounded by what the
  attacker had network access to.

### Evidence to file

The contents of `~/incident-<date>/`, the DPO's regulator notification,
the user notifications (if any), the incident-register entry, and the
remediation evidence (salt rotation output, key rotation output,
forensic conclusion).

---

## Article 25 — Data minimisation evidence

**Trigger:** the auditor wants proof you don't collect more than you
need.

Show them:

1. A live audit-log entry: `tail -1 logs/audit.log | jq`. Walk through
   the fields — there is no email, no IP, no query text, no answer text,
   only the pseudonymised user, model, source-chunk count, latency,
   query hash, matter code.
2. The `pseudonymise_user` function in `config.py:299-336`: the actual
   one-way hash with salt, era-aware.
3. The RoPA's `data_categories` list: every category listed has a
   declared purpose and lawful basis.

---

## ISO 27001 control evidence-gathering

The full per-control evidence list lives in
[../iso27001-controls.md](../iso27001-controls.md). For each control,
that file specifies the verification command. Run them; capture
output; file.

A 1-page evidence checklist:

- [ ] A.5.30 — `curl /admin/fleet/nodes` shows ≥1 alive after a node
      stop.
- [ ] A.5.33 — `curl /admin/fleet/audit-verify` → `fleet_status: ok`.
- [ ] A.5.34 — `tail -1 logs/audit.log` shows pseudonym + salt_era,
      no real name.
- [ ] A.8.3  — `python manage_users.py list` shows TTL on every key.
- [ ] A.8.5  — Trigger 10 bad auths from one IP, verify lockout via
      `grep auth_locked_attempt logs/security.log`.
- [ ] A.8.10 — Show `manage_users.py rotate-audit-salt --keep-eras 0`
      (in a sandbox) drops eras.
- [ ] A.8.13 — Show `ls logs/audit-*.log.gz` (rotated archives) and
      most-recent Qdrant snapshot.
- [ ] A.8.14 — Stop one node, `/admin/fleet/qdrant-health` shows the
      partition; bring it back, both peers active.
- [ ] A.8.15 — `audit-verify` ok across the boundary.
- [ ] A.8.16 — `monitor/alerts` returns JSON; trigger a test alert.
- [ ] A.8.24 — `openssl s_client -connect localhost:8000` shows TLS
      handshake; show salt era + pseudonymity.key_material_state in
      processing-record.
- [ ] A.8.25 — `git log --oneline` shows reviewed commits with phase
      tags (v0.ha-phase*, v0.ha-phase8, etc.).
- [ ] A.8.26 — Send a malformed body, get 422; show the
      injection-resistant system prompt in `api.py`.
- [ ] A.8.28 — `grep -rn "shell=True" *.py` returns no matches.
- [ ] A.8.29 — `tests/ha_chaos.py` returns exit 0.
- [ ] A.8.30 — `cat .model_lock` shows the pinned commit (or note its
      absence and the warning logged on each model load).

---

## UAE / KSA PDPL specifics

UAE Federal Decree-Law 45/2021 and KSA Royal Decree M/19/2023 align
with GDPR conceptually but with some specifics:

- **Data residency:** the UAE PDPL (Art. 22) and KSA PDPL (Art. 29)
  both restrict cross-border transfers. LocallyAI runs entirely
  on-prem; there are no transfers. The RoPA's
  `international_transfers: "None"` is the truthful answer; document
  the deployment IPs to confirm "the box never made an outbound API
  call" (verifiable via firewall logs at the deployment site).
- **Notification window:** UAE PDPL requires "without undue delay"
  (Art. 9); KSA PDPL specifies 72 hours like GDPR.
- **Consent records:** if the firm relies on consent rather than
  contract-of-employment for processing user data, the firm (not
  LocallyAI) must keep the consent record. LocallyAI's audit log is
  not the consent log.

---

## DIFC / ADGM specifics

DIFC DP Law 5/2020 and ADGM DP Regulations 2021 are GDPR-equivalent.
The same procedures above produce evidence acceptable to both
regulators. No additional steps.

---

## Annual self-check

Once a year, the DPO + IT-ops together:

- [ ] Walk through every Article above. Is the procedure still
      runnable? Do the example commands still work?
- [ ] Attempt a subject-access request against a synthetic user — time
      it end-to-end.
- [ ] Attempt an erasure against a synthetic user — verify the audit
      log refuses to record further entries for them.
- [ ] Pull a fresh RoPA — diff against last year's. Does the change
      narrative make sense?
- [ ] Run `audit_install.sh` — pass=14 warn≤1 fail=0.
- [ ] Run `tests/ha_chaos.py` — pass=13 fail=0 (current).
- [ ] Review the credential register. Rotate.
- [ ] Salt rotation if 12+ months since last (see
      [maintenance.md § "Salt rotation"](maintenance.md#salt-rotation-gdpr-art-32--iso-27001-a824)).

File the year's evidence in a single timestamped folder.

---

## Appendix: subject-access reply template

```
Dear <name>,

Thank you for your request dated <date> regarding personal data we
process about you in our LocallyAI deployment.

Confirmation: we do process personal data relating to you.

Purposes:
  - Operating an internal AI assistant scoped to firm documents
    (contractual / legitimate-interest basis).
  - Auditing model usage (legal obligation, ISO 27001 A.8.15).
  - Per-user usage measurement for internal billing (contract).

Categories of data we hold:
  - Your name and an API key, in our user registry.
  - A pseudonymised hash of your name in our audit log
    (one entry per chat). The hash is one-way; we cannot reverse it
    without the salt that lives only on our deployment.
  - Your name + model used + duration of each chat in our billing
    log.

Categories we do NOT hold:
  - The text of your queries. We log only a hash.
  - The model's responses to you.

Recipients: none. The deployment runs on hardware in our office; no
data is transmitted externally.

International transfers: none.

Retention: <your retention period>. After that, audit and billing
entries about you are deleted.

A copy of the data is attached as JSON files (subject_access_*.json).

Your further rights:
  - Rectification (your name in our user registry): contact <DPO>.
  - Erasure (Art. 17 / equivalent): contact <DPO>.
  - Lodge a complaint with <supervisory authority>.

Yours,
<DPO name>
```

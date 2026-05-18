# Incident playbooks — legal & regulatory

When the firm receives a court order, regulator inspection, litigation
hold, or mass subject-access request. These are situations where
operationally-correct action can be legally wrong, and vice versa.
**The DPO and external counsel run point. IT-ops produces evidence.**

---

## Court order / police request for data

**Trigger:** a court order, search warrant, police request, or
similar legal compulsion arrives demanding data from the LocallyAI
deployment.

### Do not act on the request directly

**Hand it to the firm's senior partner / general counsel
immediately.** They:

- Verify the order is valid (issuing authority, signed, applicable
  jurisdiction).
- Decide which data is in scope vs out of scope (legal-privilege
  carve-outs, third-party data).
- Coordinate with the firm's external IT lawyer on the response.

### What IT-ops does in parallel

1. **Litigation-hold mode** (see [Litigation hold](#litigation-hold)
   below): immediately stop all rotation, retention deletion, and
   erasure that could affect data in scope.
2. **Preserve evidence** but do not yet hand it over:
   ```bash
   mkdir -p ~/legal-hold-<case-id>
   cp -rp logs/audit.log     ~/legal-hold-<case-id>/
   cp -rp logs/audit-*.log.gz ~/legal-hold-<case-id>/
   cp -rp logs/billing.log   ~/legal-hold-<case-id>/
   cp -rp logs/security.log  ~/legal-hold-<case-id>/
   cp -rp logs/erasure.log   ~/legal-hold-<case-id>/
   chmod -R 0400 ~/legal-hold-<case-id>/  # write-protect
   ```
3. **Document the chain of custody.** A simple text file:
   ```
   ~/legal-hold-<case-id>/CHAIN_OF_CUSTODY.txt
   2026-05-06T14:23Z  Order received from <issuing court / authority>, ref <X>
   2026-05-06T14:25Z  Senior partner <name> notified
   2026-05-06T14:30Z  Logs preserved by <IT-ops name> per LocallyAI SOP
                       sop/incidents-legal.md § Court order
   2026-05-06T14:32Z  SHA-256 of preserved bundle:
                       <output of: shasum -a 256 ~/legal-hold-<case-id>/*>
   ```

### The hand-over

Counsel decides: which files, in what format, to whom, by when. Likely
shapes:

- **Targeted to one data subject:** the same evidence pack as a
  subject-access request (per
  [compliance.md § "Article 15"](compliance.md#article-15--subject-access-show-me-what-you-hold-on-me)).
- **All audit logs in a date range:** the audit-* archives covering
  that range, plus the chain of custody.
- **A specific user's chat history:** LocallyAI doesn't store query
  text; you can produce metadata (timestamps, sources retrieved,
  query hashes) but not the actual queries or answers. Counsel
  explains this to the issuing authority — it's a feature, not
  obstruction.

### What IT-ops should NOT hand over

- The salt or HMAC key (these stay; they make the audit log
  re-identifiable, but the audit log itself is what's compelled).
- TLS private key.
- Other clients' data (legal-privilege barrier; counsel scopes).

### After-action

- Maintain the litigation hold (see below) until counsel formally
  releases it.
- Document the closure: when the order was satisfied, by whom,
  what was handed over.

---

## Regulator subpoena / on-site inspection

**Trigger:** a supervisory authority (ICO, CNIL, DIFC Authority,
SDAIA, etc.) notifies the firm of an inspection, OR they show up
in person.

### Pre-arrival (notice given)

If you got notice (typical):

1. The DPO + senior partner coordinate. IT-ops is on standby.
2. Build the **evidence pack** in advance per
   [compliance.md § "Article 32 — Security of processing (evidence pack)"](compliance.md#article-32--security-of-processing-evidence-pack).
3. **Do a dry run** of every verification command in
   [../iso27001-controls.md](../iso27001-controls.md). Each row's
   verification command should produce its expected output. Note
   anything that doesn't.
4. **Print the SOP master** (`docs/SOP.md` and the relevant
   chapters). Auditors prefer paper they can flag.
5. Walk through with the DPO so they can answer questions
   confidently.

### On the day

Auditors typically ask three classes of question:

1. **"Show me the document that says <X>."** Hand them the SOP
   chapter or the ISO control map.
2. **"Run this verification."** You demo the command live (the
   evidence pack you built has the canned outputs; live demo proves
   they're current).
3. **"What happens if <X>?"** Walk them through the relevant
   incident playbook chapter.

### What you do NOT do

- Speculate. "I think we'd…" is a regulator's worst answer. Either
  the SOP says or the DPO says.
- Improvise on questions about data subjects or scope. Defer to DPO.
- Volunteer information beyond what's asked. Polite, complete, brief.

### After

- Auditor's findings come in writing later. The DPO leads the
  response. IT-ops produces evidence for any follow-ups.
- Update [CHANGELOG.md](CHANGELOG.md): "<date>: <regulator> inspection;
  findings <ref>; remediation closed <date>."
- Update SOP for any control gap the auditor identified. Not later;
  while the conversation is fresh.

### On-the-day surprise inspection

If they show up unannounced:

- Buy time politely: "I need to alert the DPO; they'll be here in
  <minutes>." Most authorities accept a 30–60 minute wait.
- DO NOT modify anything during the wait. No fixing-on-the-fly.
- DO NOT clear or rotate logs.
- DO NOT shut down services trying to "tidy up."
- The state of the system at the moment they arrived is what's
  audited.

---

## Litigation hold

**Trigger:** a litigation-hold notice from internal or external
counsel — preserve all data potentially relevant to a claim. Could
be from:

- The firm being sued.
- The firm suing someone (you preserve evidence for your own case).
- A regulator preceding formal proceedings.
- A criminal matter.

### What changes in operations

For the duration of the hold, **stop**:

- **Rotation that deletes:** disable retention deletion. Either set
  `LOCALLYAI_AUDIT_RETENTION_DAYS=99999` in `.env`, restart, OR
  manually move retention archives to a write-protected location
  before they age out.
- **Salt rotation:** do not run `manage_users.py rotate-audit-salt`
  with `--keep-eras 0`. Every era in scope must remain re-identifiable.
- **Erasure:** do not run `manage_users.py erase` for any user
  in scope (counsel specifies which). For a user requesting Art.
  17 erasure during a litigation hold: counsel responds explaining
  the legal-obligation override under GDPR Art. 17(3)(e).
- **Decommission:** never. The deployment must continue to exist
  for the duration of the hold, even if the firm wants to retire it.

### What changes in storage

The preserved bundle is **immutable** until counsel releases it:

```bash
mkdir -p ~/legal-hold-<case-id>/snapshots-<date>
# Daily rsync; chmod 0400 after each run.
rsync -av logs/ ~/legal-hold-<case-id>/snapshots-<date>/logs/
chmod -R a-w ~/legal-hold-<case-id>/snapshots-<date>/
```

**Better:** push to immutable off-site (S3 Object Lock, write-once
NAS share). The on-deployment snapshot is for fast access; the
off-site is the legally-defensible copy.

### What you tell users

If users ask why the system is acting "different" (slower retention
purges, etc.): direct them to the firm's litigation-hold notice.
Don't speculate.

### Exiting the hold

When counsel releases:

- Resume normal retention.
- The snapshots stay archived per the firm's overall retention
  policy.
- Document the exit: "<date>: hold lifted by <counsel>; rotation
  resumed; archives <ref> retained."

---

## Mass subject-access request

**Trigger:** 10+ data subjects file Art. 15 requests in the same
window. Could be:

- A firm-wide user organising a class action.
- A regulator-coordinated fishing expedition.
- The firm's own staff exercising rights after a redundancy round.
- A disgruntled departed employee passing instructions to others.

### Operationally

Doing 10 individual extracts of the kind in
[compliance.md § "Article 15"](compliance.md#article-15--subject-access-show-me-what-you-hold-on-me)
is fine but tedious. For 100+ requests it's untenable.

### Bulk extraction

```bash
.venv/bin/python <<'PYEOF'
import json, gzip, glob, sys
from dotenv import load_dotenv; load_dotenv('.env')
from config import pseudonymise_user, known_salt_eras

# List of names from the firm's bulk-request register:
NAMES = [
    "First Last 1",
    "First Last 2",
    # ...
]

# Compute every era's pseudonym for every name:
mapping = {}
for name in NAMES:
    for era in known_salt_eras() or [""]:
        p = pseudonymise_user(name, era=era)
        if p:
            mapping[p] = name

# Open per-user output files:
files = {name: open(f"sar_{name.replace(' ', '_')}.json", "w") for name in NAMES}

def scan(reader):
    for line in reader:
        try:
            e = json.loads(line)
        except Exception:
            continue
        owner = mapping.get(e.get("user_hash"))
        if owner:
            files[owner].write(json.dumps(e) + "\n")

scan(open("logs/audit.log"))
for arc in sorted(glob.glob("logs/audit-*.log.gz")):
    scan(gzip.open(arc, "rt"))

for f in files.values():
    f.close()

print("Per-user files written:", list(files.keys()))
PYEOF
```

This is faster than 10 sequential runs and produces 1 file per
subject in 1 pass.

### Deadlines

GDPR Art. 12(3): 1 month per request, **per request** — not
collectively. So 10 requests received the same day each have their
own clock. The DPO drafts replies in parallel.

### When to push back to the regulator

If the requests appear to be:

- Manifestly unfounded or excessive.
- A coordinated DoS-style abuse of process.

The DPO can refuse with reason or charge a reasonable fee (Art. 12(5)).
This is a DPO + counsel call, not IT-ops's. IT-ops produces the
evidence; the DPO writes the legal reply.

---

## Cross-border data request

**Trigger:** a UAE authority asks the firm about data on a UK user;
or vice versa. The firm has one LocallyAI deployment but operates
across jurisdictions.

### Default position

LocallyAI runs entirely on-prem at the firm's office. No data leaves
the box without an outbound network call. The RoPA's
`international_transfers: "None"` is the truthful answer; the firm
is the data controller; whichever authority has jurisdiction over the
firm at the location where the data is processed (= where the box
lives) is the relevant one.

### When the request is from a different jurisdiction's regulator

Counsel handles. IT-ops doesn't comply directly. The procedure is
the same as [Court order](#court-order--police-request-for-data)
above — preserve, document, defer to counsel.

### When the data subject is in a different jurisdiction

The user's rights apply under the regime where they reside, but the
processing happens at the firm's office. There's typically a
straightforward answer (the firm complies under the strictest of the
applicable regimes). Counsel decides.

---

## Cyber-insurance forensic request

**Trigger:** a security incident has triggered a cyber-insurance
claim; the insurer's forensic firm requests evidence.

### Scope

Insurers want:

- Logs from the incident window (audit, security, billing,
  sentinel).
- The incident-response chain of custody.
- Evidence of controls in place at the time of the incident
  (the iso27001-controls.md verifications run from a date BEFORE
  the incident, ideally; if not, run now and note the date).
- The post-incident remediation evidence.

### What to provide

A copy of the
[incidents-security.md § "After every security incident"](incidents-security.md#after-every-security-incident)
post-mortem + the relevant section's evidence + the underlying logs.

### What NOT to provide

- Salt, HMAC key, admin key. Insurers don't need them; sharing
  weakens the security posture.
- Other clients' data.
- The contents of the firm's litigation-hold preservation (counsel
  decides when it's safe to share with insurers).

### Privilege

Talk to counsel before sending anything to the insurer. Some
materials are privileged; sending them to a third party (even an
insurer) may waive privilege.

---

## Data breach lawsuit (the firm is sued)

**Trigger:** the firm is sued for a data breach involving the
LocallyAI deployment.

### IT-ops role

You are now an evidence custodian, not an operator. Procedure:

1. Litigation hold (above).
2. Preserve everything the firm's counsel asks you to preserve.
3. Be ready to testify (depositions, interrogatories) about what
   you did and didn't do, with the SOP and CHANGELOG as your
   reference.
4. Do not destroy anything — even if it's old, even if it's
   embarrassing, even if your firm's procedures call for it. The
   litigation hold overrides routine.

### What the SOP gives you that's defensive

- A written, version-controlled, dated procedure for every action
  you took.
- Audit-chain evidence that nothing was modified out of band.
- Per-control verification commands that prove (with timestamps) the
  state of the system on any given date you have logs for.

### What weakens your defence

- Skipped procedures ("we didn't run the audit that month").
- Unrecorded customisations ("we changed retention to 30 days but
  didn't document it").
- Missing CHANGELOG entries.

This is why the SOP is paranoid about CHANGELOG entries.

---

## Subpoena for the SOP itself

**Trigger:** opposing counsel or a regulator demands the SOP as
evidence of process.

### Provide it

The SOP is a non-confidential operational document. Hand the entire
`docs/SOP.md` + `docs/sop/*` set over. The version under litigation
is the version current at the time of the incident — confirmed by
git history (`git log docs/SOP.md`).

### What this implies for your CHANGELOG

If the CHANGELOG lies (says you did things you didn't), or omits
material changes, or backdates entries — you've handed the opposing
party your defence-buster on a plate. **Keep it honest.** Mistakes
in the operations are usually defensible; mistakes in the records
of operations rarely are.

---

## Annual self-check (legal posture)

Once a year, with counsel:

- [ ] Is the firm's RoPA up to date with the LocallyAI deployment's
      v1.2 record?
- [ ] Are the credential register and access-list current?
- [ ] Have any data subjects filed Art. 15 / 17 in the past year?
      Were they handled within the deadline?
- [ ] Has the firm experienced any breach event? Was it logged in
      this SOP's incident-folder convention?
- [ ] Are the litigation-hold conventions tested (i.e. has counsel
      ever issued a hold-and-release cycle to verify the IT-ops
      response works)?
- [ ] Are the firm's data-subject reply templates up to date?

File the year's findings.

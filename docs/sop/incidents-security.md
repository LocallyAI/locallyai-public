# Incident playbooks — security

When credentials leak, malware lands, or you suspect unauthorised
access. Each section: **trigger → assess → contain → eradicate →
recover → after-action**, mapped to the GDPR Art. 33 / NIS / SRA
incident-response phases.

> **VENDOR ON-CALL: malicious release detected, GitHub or signing key
> compromised, or any cause to halt the update pipeline?**
> The kill switch is your emergency-stop. See the
> [kill-switch runbook](updates.md#kill-switch-runbook--invoking-it-when-something-goes-wrong)
> for the one-command incident playbook (TOTP-gated, takes effect
> at every firm in ≤60 s).

---

## Salt leak

**Trigger:** the audit salt may have left the firm's control.
Indicators:

- Someone took a screenshot of `.env` and shared it.
- A backup containing `.env` plaintext was stored somewhere unintended
  (Dropbox, personal email, lost USB).
- A box containing `.env` was stolen and the disk wasn't encrypted.
- A no-longer-trusted admin had `.env` on their personal device.

### Assess

The salt's only job is to keep pseudonyms in `audit.log` from being
correlatable to a name list. With the salt + the `users.json` name
list, an attacker reverses every audit entry to a real name + activity
pattern.

If the attacker has the salt but **not** `users.json` (the firm's user
list), they can't enumerate. But they *can* check whether a *guessed*
name was a user — so any name they guess that matches confirms that
person was a user.

If the attacker has `users.json` too: full re-identification. Treat as
[Theft of a Mac](incidents-physical.md#theft-of-a-mac)-class breach.

### Contain

```bash
# Rotate the salt immediately. This drops correlation between old
# audit entries and any future ones produced under the new salt.
python manage_users.py rotate-audit-salt --keep-eras 0
```

`--keep-eras 0` is the leak-response setting: it drops every retired
era including the leaked one, so that salt era can never be used to
re-identify anything from the live `.env` again. This makes historical
audit entries unrecoverable to anyone — including future legitimate
subject-access requests on those entries. **The DPO must approve this
trade.**

If the DPO needs subject-access to keep working on old entries:

```bash
python manage_users.py rotate-audit-salt --keep-eras 4
```

…and the leaked salt becomes ERA_1. New entries get a new era; old
entries are still re-identifiable via the leaked salt — **so users
who need erasure for old entries should ALSO be `manage_users.py
erase`d immediately**, since the leaked salt could otherwise let an
attacker confirm their activity history.

Restart the service per the rotation procedure
([maintenance.md § "Salt rotation"](maintenance.md#salt-rotation-gdpr-art-32--iso-27001-a824)).
HA: rotate ON one node, copy new env vars to the other, restart both.

### Eradicate

- Find and destroy every copy of the leaked salt that's no longer
  needed:
  - Slack/Teams messages where it was shared.
  - Backups containing `.env` plaintext (re-back-up after rotation,
    delete the old).
  - Personal devices where `.env` ended up.
- Update access controls so this can't recur:
  - Move `.env` access to admin-vault-only (no flat-file `.env` on
    untrusted machines).
  - Tighten ACLs (`chmod 600` re-checked).

### Recover

The deployment is operational throughout — only the audit-chain era
changed. New writes work. Re-verification:

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/processing-record \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d['pseudonymity'], indent=2))"
```

`current_salt_era` should be the new one.

### After-action — Art. 33 evidence

- Whether to notify the regulator depends on the data exposed — i.e.
  whether `users.json` and `audit.log`/billing log were exposed
  alongside the salt. The DPO decides.
- Build the evidence pack per
  [compliance.md § "Article 33"](compliance.md#article-33--personal-data-breach-notification).
- Document the rotation: dropped era ids, new era id, timestamp of the
  `salt_era_boundary` audit entry.

---

## HMAC chain key leak

**Trigger:** `LOCALLYAI_AUDIT_HMAC_KEY` may have leaked.

### Assess

Without the HMAC key, the attacker can't verify the chain or forge
plausible-looking entries. With the key, they could forge entries
that pass `audit-verify`. This is a **forgery risk**, not a privacy
risk (the chain key doesn't reveal pseudonyms).

### Contain — destructive procedure

There is no in-place rotation. See
[maintenance.md § "HMAC chain key rotation"](maintenance.md#hmac-chain-key-rotation).
Procedure:

1. Force-rotate the audit log (move current archives off-host for
   forensic preservation).
2. Generate a new HMAC key, write to `.env`.
3. Delete `logs/.audit_chain` so the new chain starts at `0000…`.
4. Restart the service.
5. The OLD chain is preserved (in your forensic archive) and remains
   verifiable under the OLD key (which you also archived). The LIVE
   chain is fresh.

### After-action

- File as Art. 33-eligible. The risk to data subjects is forgery of
  records about them — not privacy disclosure — but it's still a
  control failure that may need disclosure.
- The forensic archive is essential: keep the OLD audit log + OLD
  HMAC key together in a secure cold-storage location, accessible only
  for regulator-instructed re-verification.

---

## Admin key leak

**Trigger:** `LOCALLYAI_ADMIN_KEY` may have leaked.

### Assess

The admin key authorises **every** `/admin/*` endpoint: audit-verify,
fleet management, user management (via the API, not the CLI), billing
read, RoPA export, processing-record, gate state.

An attacker with the admin key can:
- Enumerate users (`/admin/users`).
- See raw billing data (`/billing/<user>`).
- Read pseudonymised audit data via verify/snapshots.
- Cannot forge audit entries (HMAC chain). Cannot read query content
  (it isn't logged).

### Contain

Rotate the admin key immediately. Single-node:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
# Copy the new key.
# Edit .env: LOCALLYAI_ADMIN_KEY=<new>
# Save with chmod 600.
launchctl kickstart -k gui/$(id -u)/com.locallyai.server     # Mac
Restart-Service LocallyAIServer                              # Win
```

HA: edit `.env` on **both** nodes (admin key is per-node in `.env`),
restart in rolling fashion.

Update the firm password vault with the new key. Notify whoever uses
it (typically: you and the DPO).

### After-action

- Search `security.log` for any admin endpoint hits between the leak
  time and the rotation time. Note IPs.
- File the incident.

---

## User key leak

**Trigger:** a user reports their key was sent in a phishing reply,
posted in screenshot, found in a leaked password dump, etc.

### Contain

```bash
python manage_users.py rotate <user>
```

Old key is dead immediately. Print the new key to the user (in
person, password vault entry, end-to-end-encrypted DM — never email).

### Assess scope

Identify any chats made under the leaked key:

```bash
.venv/bin/python <<PY
import json
from dotenv import load_dotenv; load_dotenv('.env')
from config import pseudonymise_user, known_salt_eras
name = '<user>'
pseudonyms = {pseudonymise_user(name, era=e) for e in known_salt_eras() if pseudonymise_user(name, era=e)}
print('Recent activity for:', name)
print('pseudonyms:', pseudonyms)
for line in open('logs/audit.log'):
    e = json.loads(line)
    if e.get('user_hash') in pseudonyms:
        print(json.dumps(e))
PY
```

### After-action

- File. Treat as a low-severity incident — query content was never
  logged, only metadata.
- Consider whether the user needs `manage_users.py erase` — if their
  key was widely leaked AND their activity history is sensitive, the
  full erasure is appropriate.

---

## Ransomware on a node

**Trigger:** files have been encrypted; ransom note on desktop;
`audit.log` unreadable; or an EDR alert.

### Immediate action — do NOT pay

1. **Isolate the box from the network.** Pull the cable / disable
   Wi-Fi. Don't shut down — that loses memory forensics.
2. **Tell the DPO immediately.** GDPR Art. 33 clock starts now.
3. **HA: assume the peer is also infected** until you can verify
   it isn't. Inspect with EDR on the peer; if clean, isolate the
   infected one but keep the peer running.
4. **Single-node: the firm is fully down.** Tell users.

### Eradicate

This is a malware-response problem outside the LocallyAI codebase.
Your firm's incident-response provider handles forensics. The
LocallyAI-specific bits:

- The Mac that's encrypted is a write-off until the IR team is done.
- Treat all credentials as compromised: salt, HMAC key, admin key,
  every user key. Rotate per the relevant section above.
- The `data/` corpus may contain client-confidential material —
  factor that into the IR/breach assessment.

### Recover

Restore from cold backup ([recovery.md § "Restore from cold
backup"](recovery.md#restore-from-cold-backup)) onto fresh hardware.
**Never** restore onto the infected hardware until it's been wiped to
firmware level.

### After-action

- Full Art. 33 / Art. 34 — likely required.
- Post-mortem with the IR team. What was the entry vector? Phishing
  on the local user account? RDP exposed? An out-of-date macOS?
- Patch the entry vector.

---

## Malware on a node (non-ransomware)

**Trigger:** EDR alert; AV finds a file; suspicious process running.

### Action

Same first 3 steps as ransomware (isolate, DPO, HA-peer assessment).
Then your EDR / IR provider takes over. LocallyAI-specific:

- Are credentials at risk of read by the malware? Assume yes.
- Was the malware able to write `audit.log`? If yes, the chain is
  effectively tampered. Verify:
  ```bash
  curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://<node>:8000/admin/audit-verify
  ```

### Recovery

Same template as ransomware — fresh hardware, restore from cold
backup, rotate everything.

---

## Suspected unauthorised access

**Trigger:** uncertain. Maybe a strange auth pattern, maybe a user
reports "I logged in but didn't do that," maybe a partner spotted
something off.

### Assess (don't act yet)

Pull the relevant logs:

```bash
# All auth events for the past 24h:
awk -v since="$(date -u -v-24H +%Y-%m-%dT%H:%M:%SZ)" \
    'NR==1 || ($1 >= since)' logs/security.log

# All audit entries for a specific user (real or pseudonym):
.venv/bin/python <<PY
import json
from dotenv import load_dotenv; load_dotenv('.env')
from config import pseudonymise_user
target = pseudonymise_user('<user-name-or-paste-pseudonym>')
for line in open('logs/audit.log'):
    e = json.loads(line)
    if e.get('user_hash') == target:
        print(json.dumps(e))
PY
```

What's "suspicious":

- Auth_failure count for one IP > the breach detector threshold.
- Activity from an IP that's never used the system before.
- Activity outside business hours by a user who's only ever used it
  in business hours.
- An admin key hit from an IP that isn't the IT-ops desk.
- Audit entries for a user who's been on holiday.

### Contain (only if confirmed)

If confirmed: `manage_users.py rotate <user>` for the affected
user(s); rotate admin key if any admin endpoint was hit; consider
salt rotation if pseudonym data was exfiltrated.

### After-action

If the suspicion was unfounded: document the false positive so future
admins know not to chase the same shadow.

If confirmed: file as Art. 33; full evidence pack.

---

## Audit chain TAMPERED with no known cause

**Trigger:** `audit-verify` says TAMPERED but you can't account for it
(you didn't truncate, no rotation race, archives intact).

### This is a possible compromise

The HMAC chain doesn't break by itself. If it's broken without an
operator action, treat as an integrity failure — i.e. someone or
something modified the audit log.

### Action

1. Read [incidents-software.md § "Audit chain TAMPERED"](incidents-software.md#audit-chain-tampered)
   for the **operational** recovery (preserve evidence, start new
   chain era).
2. Run a malware scan on the box. EDR / `clamav` on Mac, Defender on
   Win.
3. Inspect for unexpected writes:
   ```bash
   ls -la logs/audit.log     # mtime changed unexpectedly?
   stat logs/audit.log       # access patterns
   sudo fs_usage | grep audit.log    # who's writing?  (Mac, real-time)
   ```
4. Rotate ALL key material on the suspicion of compromise.

### After-action

Full Art. 33. Forensics provider engaged.

---

## Supply-chain — model SHA drift

**Trigger:** `logs/launchd_error.log` shows
`MODEL INTEGRITY DRIFT: <model> pinned to <X> but loaded <Y>`.

Either Hugging Face mid-flight overwrote the model file (rare; their
versioning normally prevents this) or someone tampered with the
download. Either way the model you loaded is not what you pinned.

### Action

1. **Stop the service.** You don't want to serve answers from an
   unverified model.
2. Inspect the `.model_lock` pin vs the loaded commit:
   ```bash
   cat .model_lock
   # Look up what's currently on Hugging Face for that repo+revision.
   ```
3. Decide: was the upstream legitimately updated (vendor changed the
   model intentionally)? If yes, accept the new SHA — overwrite
   `.model_lock`. If no, treat as a supply-chain incident.
4. Re-pull from a known-good source if you have one.
5. Verify the SHA matches expectations before restarting.

### After-action

- File. Add the SHA to your "approved versions" register.
- Consider air-gapping model downloads — pull on a clean box,
  hash-verify, copy to the deployment via trusted media.

---

## Credential stuffing detected

**Trigger:** `monitor/alerts` shows `auth_breach: Possible
credential-stuffing: <ip>=<n> failed auths in 300s window`. The
sentinel has noticed ≥10 failed auths from one IP within 5 minutes.

### Action

1. Identify the IP.
2. Block at the network level — firewall rule, switch ACL, or
   tarpit. Out of scope for LocallyAI; talk to network admin.
3. Check if any auth from that IP succeeded just before/during/after
   the failures:
   ```bash
   grep "<ip>" logs/security.log | grep -E "auth_success|auth_failure"
   ```
   If you see auth_success: the attacker may have a valid key. Treat
   that user's key as leaked — rotate immediately.
4. The lockout-counter should already be 429-ing them, but the
   network block is the firmer remedy.

### After-action

- Was the IP a legitimate user with a typo? If yes, document — and
  suggest password-vault adoption to the user.
- Was it external? File as a security incident.

---

## After every security incident

A post-incident review:

- What was the entry vector?
- How long until detection?
- How long until containment?
- How long until full recovery?
- What did we learn that would have shortened any of those?
- What change to this SOP / to the codebase / to the firm's policy
  prevents recurrence?

Update [CHANGELOG.md](CHANGELOG.md) and the relevant playbook chapter
with what you learned. The SOP getting better is the goal.

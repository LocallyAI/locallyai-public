# Vendor-side incident playbook

> When **our** infrastructure is compromised, not the firm's. Firm-side
> incidents are in the firm SOP under `docs/sop/incidents-*.md`. This
> chapter is for the moments where the vendor is the attack surface.
>
> **Every scenario in this chapter ends with the same closing step**:
> file a post-incident review in `vendor-records/incidents/<YYYY-MM-DD>-<slug>.md`
> within 7 days. The format is in the appendix at the bottom.

---

## Triage matrix

When you suspect compromise, find the row that fits and jump:

| Symptom | Scenario | Section |
|---|---|---|
| Laptop stolen / lost | Founder's daily driver gone | [Laptop loss](#laptop-loss-or-theft) |
| Strange GitHub commits on `main` you didn't make | Org takeover | [GitHub LocallyAI org compromise](#github-locallyai-org-compromise) |
| Strange CF Worker deployments / KV writes | CF account takeover | [Cloudflare account compromise](#cloudflare-account-compromise) |
| Released artefact that won't verify against `docs/release-signing-key.gpg` | Bad release escape | [Bad-release escape](#bad-release-escape) |
| GPG private key file or passphrase found outside Keychain | Key leak | [GPG release-signing key leak](#gpg-release-signing-key-leak) |
| TOTP authenticator gives wrong codes / extra entries appear | TOTP secret leak | [TOTP secret leak](#totp-secret-leak) |
| 1Password says "new device sign-in" you don't recognise | Vault compromise | [1Password vault compromise](#1password-vault-compromise) |
| Resend dashboard shows emails you didn't send | API key leak | [Resend API key leak](#resend-api-key-leak) |
| Heartbeats from an unknown firm_id | Stolen telemetry token | [Telemetry token leak](#telemetry-token-leak) |
| Domain DNS records changed | Registrar takeover | [Domain registrar takeover](#domain-registrar-takeover) |

If you're not sure which row fits — start from the top and work down.
Most scenarios cascade (laptop loss often → GH compromise → CF
compromise → kill switch).

---

## Laptop loss or theft

The founder's daily-driver Mac is the highest-blast-radius device in
the vendor estate. It holds GPG private key (in Keychain), gh CLI
keyring, 1Password local cache, `~/.locallyai/vendor/firms-registry.json`,
and active wrangler sessions.

**Assumptions on threat model**: device-encrypted (FileVault), strong
login password, auto-lock after 5 min idle. With those, an opportunistic
thief gets nothing useful unless they shoulder-surfed the password. A
targeted attacker with the password has full vendor compromise.

### Step 1 (within 1 hour) — assume worst-case

Treat as **full vendor compromise** until proven otherwise. Do not
spend time on probability arguments before doing step 2.

### Step 2 (within 1 hour) — invoke kill switch

From any other device with internet:

```sh
bash scripts/kill_switch_emergency.sh stop  "Vendor laptop loss — investigating"
```

Or if scripts not accessible: open the kill-switch Worker URL directly,
sign in with TOTP from your phone, set status JSON to `{"status":"stop","reason":"..."}`.

This stops all firms from auto-applying any further updates within ≤60s
(kill_switch.py poll cache). Released artefacts already on disk are
unaffected — firms keep running.

### Step 3 (within 4 hours) — credential rotation cascade

In this order (each item assumes the previous ones are done):

1. **GitHub LocallyAI org password + 2FA** — log in from another device
   (using sealed-envelope backup codes for 2FA), reset password,
   regenerate 2FA from a new authenticator entry.
2. **Cloudflare account password + 2FA** — same flow.
3. **Domain registrar password + 2FA** — same flow.
4. **1Password account password** — sign out other devices, change
   master password.
5. **Resend API key** — log into Resend, revoke old key, create new key,
   `wrangler secret put RESEND_API_KEY` on the monitor Worker.
6. **CF API tokens** — revoke all `wrangler` deploy tokens via CF
   dashboard; reissue from a new authenticated device.
7. **Per-firm telemetry tokens** — `~/.locallyai/vendor/firms-registry.json`
   on the lost laptop is suspect; rotate all firms by re-running
   `scripts/onboard_firm.sh` against each `firm-profile.md` in
   vendor-records. Coordinate with firm IT to swap in new tokens within
   24h.
8. **GPG release-signing key** — see
   [GPG release-signing key leak](#gpg-release-signing-key-leak) below
   for the revoke + reissue flow.

### Step 4 (within 24 hours) — kill-switch decision

If you've completed step 3 and are confident the cascade caught
everything: lift the kill switch.

```sh
bash scripts/kill_switch_emergency.sh resume "Vendor laptop loss — credentials rotated"
```

Tell every firm via 1Password share message: "We rotated your telemetry
token after a vendor-side device incident. Token is in this
1Password share."

If you are **not** confident — leave the kill switch on until you are.
The cost of a few days of "no auto-updates" is much lower than the
cost of a malicious release going out under the old GPG key.

### Step 5 (within 7 days) — post-incident review

Per [appendix](#appendix-post-incident-review-template).

---

## GitHub LocallyAI org compromise

Symptom: commits on `main` you didn't make, or new collaborators you
didn't add, or release tags you didn't sign.

### Step 1 — kill switch (immediate)

```sh
bash scripts/kill_switch_emergency.sh stop "GitHub org compromise — investigating"
```

This stops firms from auto-applying any new releases (signed or
otherwise — the kill switch is independent of GitHub's signing chain).

### Step 2 — secure the org

1. From a known-clean device, sign in to GitHub with the LocallyAI org
   owner account.
2. Settings → Security → Sessions → revoke all sessions except your
   current one.
3. Settings → Personal Access Tokens → revoke every token.
4. Settings → SSH keys → audit every key, remove anything you don't
   recognise.
5. Settings → Authorised GitHub Apps + OAuth Apps → audit, remove
   anything unfamiliar.
6. Org → People → audit member list, remove unauthorised members.
7. Org → Audit log → screenshot the last 30 days for the post-incident review.

### Step 3 — assess release integrity

Look at every signed release tag. For each, locally verify:

```sh
git fetch --tags
git verify-tag <tag>     # must show "Good signature from..."
```

Any tag that signs against a key that is **not** the documented
release-signing key fingerprint = unauthorised release. Treat as a
[bad-release escape](#bad-release-escape).

### Step 4 — consider GPG key safety

If the attacker had any path to your laptop (e.g., via a compromised
SSH-deploy-key), assume the GPG private key is also compromised. Go to
[GPG release-signing key leak](#gpg-release-signing-key-leak).

### Step 5 — kill-switch decision + comms + post-incident

Same as the laptop-loss steps 4 + 5. Comms wording: "We have completed
investigation of a vendor-side GitHub-account access event. No firm
data was accessed (vendor never holds firm data). Telemetry tokens
have been rotated as a precaution. Updates will resume on the next
release."

---

## Cloudflare account compromise

Symptom: Worker deployments / KV writes you didn't make, billing
charges you don't recognise, new API tokens in the dashboard.

### Step 1 — assess kill-switch state

The first concern: is the kill-switch Worker still under your control,
or has the attacker overwritten it to `{"status":"go"}` so they can
push a malicious release? Sign in to CF (from a known-clean device,
new password if needed) and verify the kill-switch KV value matches
what you set last.

If the kill-switch value has been tampered with → assume the attacker
intends to push a release. **Do not rotate GPG yet** (rotating
mid-attack tells the attacker you noticed). Instead:

- Set the kill-switch to `{"status":"stop", "reason":"vendor incident"}`
  manually via the CF KV editor.
- Then proceed with the rest of the rotation.

### Step 2 — secure the CF account

1. Settings → Members → audit + revoke unrecognised users.
2. My Profile → API Tokens → revoke every token.
3. My Profile → Sessions → terminate all.
4. Account → Audit log → screenshot the last 30 days.
5. Reset account password from a known-clean device, regenerate 2FA.

### Step 3 — re-deploy known-good Workers

```sh
cd docs/kill-switch/cloudflare-worker && npx wrangler deploy
cd ../../monitor/cloudflare-worker     && npx wrangler deploy
```

Wrangler will use your new API token (you re-authed in step 2).

### Step 4 — verify Worker source matches git

The deployed Worker should match `src/worker.ts` at the current `main`
HEAD. Compare via `npx wrangler tail` and a test heartbeat. Any
divergence = the attacker may have left behind a backdoor in the
deployed bundle.

### Step 5 — kill-switch decision + comms + post-incident

As above.

---

## Bad-release escape

Symptom: a release tag exists that you didn't sign, OR a release tag
that signs against the right key but contains malicious changes.

### Step 1 — kill switch (immediate)

```sh
bash scripts/kill_switch_emergency.sh require-version <KNOWN_GOOD_VERSION> \
  "Bad release detected — pinning to known-good"
```

This forces every firm to roll back to the known-good version on next
update poll (≤60s). The deploy.py atomic-deploy + healthz auto-rollback
chain handles this without manual firm-side intervention.

### Step 2 — find the radius

```sh
git log --tags --simplify-by-decoration --pretty="%h %d %s" | head -20
git tag -v <suspect-tag>
git diff <known-good-tag>..<suspect-tag>
```

Identify what changed and whether any firm pulled the bad version
(check the monitor dashboard for firms reporting the suspect version
in their heartbeat metadata).

### Step 3 — communicate to affected firms

If any firm pulled the bad version:

- Phone call (not email) within 1 hour of discovering the escape.
- Walk them through a manual rollback if their auto-rollback didn't
  kick in (uncommon — deploy.py is robust).

### Step 4 — root cause

A bad release escape means **either** a GPG key compromise (someone
else signed the tag) **or** an insider error (the right person signed
the wrong contents). Investigate which, and proceed to
[GPG key leak](#gpg-release-signing-key-leak) if it was the former.

---

## GPG release-signing key leak

Symptom: GPG private key file (`pubring.kbx` + private key data) found
outside macOS Keychain (e.g., copy on Desktop, in Downloads, in a
backup tarball that wasn't supposed to include it), OR the passphrase
in a text file or chat message, OR a release tag signed by the right
key with contents you didn't author.

The GPG release-signing key is the single most sensitive vendor
secret. Its compromise means the attacker can sign a release that
appears legitimate to every firm.

### Step 1 — kill switch + lock down

```sh
bash scripts/kill_switch_emergency.sh stop \
  "GPG release-signing key compromise — pausing all updates"
```

### Step 2 — publish the revocation certificate

The revocation certificate was generated when the key was first
created (per `docs/sop/repo-access.md`). It lives in the sealed
envelope. Retrieve it and:

```sh
# Import the revocation certificate
gpg --import /path/to/release-signing-key.revoke

# Push the now-revoked key to the keyserver (best-effort)
gpg --send-keys <KEY_ID>
```

Update `docs/release-signing-key.gpg` in the repo with a top-line
comment indicating the key is revoked + the date + the new key
fingerprint (once generated in step 4).

### Step 3 — purge the leaked copy

If you found the leaked private key in a file:

```sh
# Use srm or shred where available; else `rm` is acceptable on APFS
# (file content is not recoverable on encrypted volumes)
rm -P /path/to/leaked.key      # -P overwrites three times on macOS
```

Audit the surrounding location for other leaks (Desktop, Downloads,
~/Documents, recent backups). If the leak was in a backup, the backup
is now also a leak source — re-encrypt or delete the backup.

### Step 4 — reissue a new release-signing key

```sh
gpg --full-generate-key
# Select RSA + RSA, 4096 bits, no expiry (or 5y expiry — see SOP)
# Real name: LocallyAI Release Signing
# Email: releases@locallyai.app
# Comment: 2026 (year of issue)
```

Export the new public key:

```sh
gpg --armor --export <NEW_KEY_ID> > docs/release-signing-key.gpg
```

Generate the new revocation certificate immediately and store in the
sealed envelope (replacing the old envelope contents):

```sh
gpg --output release-signing-key-2026.revoke --gen-revoke <NEW_KEY_ID>
```

Print the revocation cert + the new key fingerprint, seal in envelope.

### Step 5 — re-sign all known-good release tags with the new key

```sh
for tag in v0.1.0 v0.2.0 ...; do
  git checkout $tag
  git tag -d $tag                     # delete local
  git push --delete origin $tag       # delete remote
  git tag -s $tag -m "<original message>"
  git push origin $tag
done
git checkout main
```

This is destructive; do it only for tags you have personally verified
the contents of (against your own copy or off-site backup).

### Step 6 — comms

Email every firm:

> Subject: LocallyAI — release-signing key rotation (action required)
>
> We have rotated the GPG key used to sign LocallyAI releases. Please
> import the new public key into your trustdb:
>
>   curl -O https://raw.githubusercontent.com/LocallyAI/locallyai/main/docs/release-signing-key.gpg
>   gpg --import docs/release-signing-key.gpg
>
> The new key fingerprint is:
>   <NEW_KEY_FINGERPRINT_HERE>
>
> Updates will resume after you confirm the new key is trusted. There
> is no urgency — your office Mac will continue running on the version
> currently installed.

### Step 7 — kill-switch decision + post-incident

Lift kill switch only after confirming the majority of firms have
imported the new key (track via vendor outreach).

---

## TOTP secret leak

Symptom: extra entries in your authenticator app you didn't add (rare —
usually means phone compromise), OR codes you generate stop working,
OR you find the base32 secret in a file or chat message.

### Step 1 — generate a new secret

```sh
bash scripts/totp_secret_helper.sh <label>      # e.g. "kill-switch" or "monitor"
```

This produces a fresh 160-bit base32 + new 10 recovery codes.

### Step 2 — push to the relevant Worker

```sh
cd docs/kill-switch/cloudflare-worker            # or monitor/...
echo "<NEW_SECRET>" | npx wrangler secret put TOTP_SECRET_BASE32
echo '<NEW_RECOVERY_HASHES_JSON>' | npx wrangler secret put TOTP_RECOVERY_HASHED
```

### Step 3 — refresh authenticator + sealed envelope

- Delete old entry from authenticator. Add new entry from the QR.
- Print new secret + recovery codes; reseal in the envelope.
- Update 1Password entry.

### Step 4 — verify

Use the new code to sign in to the affected dashboard once.

---

## 1Password vault compromise

Symptom: 1Password notifies of a sign-in from an unfamiliar device,
OR you find the master password written somewhere unexpected.

### Step 1 — secure 1Password itself

1. From a known-clean device, sign in to 1Password.
2. Settings → Devices → revoke all unrecognised devices.
3. Change master password.
4. Generate a new Secret Key (1Password supports this — keep both old
   and new working for 30 days, then revoke old).
5. If your Emergency Kit was in physical form (and could be in the
   leaked area), regenerate it.

### Step 2 — assume cascade

Treat as full secret leak: every Tier B + Tier C secret in the vault
needs rotation per the matrices in
[vendor-infrastructure.md](vendor-infrastructure.md). Tier A secrets
(GPG key, kill-switch TOTP) live in the Founder vault — if **that**
specific vault was accessed, also do the GPG-leak + TOTP-leak flows.

### Step 3 — comms + post-incident

If firm-side credentials were in the vault (per-firm telemetry tokens
in cleartext shouldn't be — they're in `~/.locallyai/`), rotate those
too. Comms only if firm tokens were in scope.

---

## Resend API key leak

Symptom: Resend dashboard shows emails you didn't send.

Quick fix: revoke the old key, generate a new one, push to the monitor
Worker:

```sh
cd docs/monitor/cloudflare-worker
echo "<NEW_KEY>" | npx wrangler secret put RESEND_API_KEY
```

Update 1Password. No firm-side comms required (Resend doesn't see firm
data — it only sees vendor's own alert emails).

Post-incident review still required.

---

## Telemetry token leak

Symptom: heartbeats arriving for a firm_id from a network that doesn't
match the firm's office IP, OR a firm IT contact reports their token
was exposed (e.g., committed to a public repo by accident).

### Single firm

```sh
# Re-run the onboard script with the existing profile — it will offer to rotate
bash scripts/onboard_firm.sh ~/locallyai-vendor-records/firms/<slug>.md
# Type 'y' at the rotation prompt.
```

The script generates a new token, updates the local registry, pushes
the merged FIRM_TOKENS to the Worker (old token immediately invalid),
appends a row to firms-issued.log marked "rotated".

Send the new token to firm IT via 1Password share.

### Multiple firms (suspected `firms-registry.json` leak)

Iterate:

```sh
for profile in ~/locallyai-vendor-records/firms/*.md; do
  bash scripts/onboard_firm.sh "$profile"   # answer 'y' for each
done
```

This rotates every firm. Coordinate the cutover with firm IT (each
firm has 60s of token-mismatch downtime as the new token reaches the
Worker; benign for telemetry which retries).

---

## Domain registrar takeover

Symptom: DNS records changed, SSL/TLS errors on the monitor URL, MX
records point elsewhere.

### Step 1 — kill switch (assume the worst — attacker may redirect download URLs)

```sh
bash scripts/kill_switch_emergency.sh stop "Domain takeover — investigating"
```

### Step 2 — recover the registrar account

Contact the registrar's security team via their published incident
channel. Provide proof of identity (D-U-N-S, original email, original
payment instrument). Most registrars have a fast-track for owner
recovery.

### Step 3 — re-establish DNS

Once the account is recovered: set DNS records back to Cloudflare's
nameservers; verify `dig locallyai.app` returns the right answers.

### Step 4 — comms + post-incident

Email firms via a non-domain channel (your personal email if needed)
with the all-clear once DNS is restored.

---

## Appendix: post-incident review template

Save as `vendor-records/incidents/<YYYY-MM-DD>-<slug>.md`:

```markdown
# Incident: <short title>

**Date discovered:** YYYY-MM-DD HH:MM TZ
**Date resolved:**   YYYY-MM-DD HH:MM TZ
**Severity:**        Tier A / B / C
**Affected firms:**  <count>, [list of slugs] OR "none — vendor-only"
**Discovered by:**   <person> via <signal>

## Summary
One paragraph — what happened, what we did, what's left.

## Timeline
- HH:MM — <event>
- HH:MM — <event>

## Root cause
What enabled this incident? Be honest — no "human error" without
explaining the system that allowed the human error.

## What worked
What part of our defence stopped this from being worse?

## What didn't
Where were the gaps? What runbook step was missing or wrong?

## Action items
- [ ] <person> — <action> by <date>
- [ ] update SOP chapter <X> to reflect <Y>

## Lessons
Two or three sentences future-us will read in 12 months and benefit from.
```

File one within 7 days of every incident in this chapter, even minor
ones. The cumulative review log is more valuable than any single
review.

# Vendor team & succession

> Who does what, who's the backup, who has which keys, and what
> happens when someone is unavailable.

This is the **first chapter to update** when team composition changes.
Stale entries here cascade into broken incident response (we page
someone who left two months ago) and broken succession (no one knows
who has the GPG key when the founder is on a flight).

---

## Roles

LocallyAI is a small team. Roles are not job titles; they are
responsibilities. One person can hold multiple roles; every role must
have at least one **primary** and one **named succession candidate**.

| Role | Primary | Succession | Notes |
|---|---|---|---|
| Founder / engineering lead | <vendor-founder> | *(unfilled — see [Open succession gaps](#open-succession-gaps))* | Holds GPG key, CF account, GitHub admin |
| On-call engineer | <vendor-founder> | *(unfilled)* | 4h SLA front line |
| Customer success | <vendor-founder> | *(unfilled)* | Onboarding + check-in cadence |
| Legal / DPO liaison | <vendor-founder> (interim) | *(external counsel TBD)* | DPA negotiation, breach response |
| Sales | <vendor-founder> | *(unfilled)* | Prospect → DPA |
| Bookkeeping | <vendor-founder> | *(external accountant TBD)* | Invoicing firms, paying upstream |

> **Single-person operation as of 2026-05.** Open succession gaps are
> the #1 operational risk. See [Open succession gaps](#open-succession-gaps)
> for the mitigation plan.

### Contact

| Channel | Where | When |
|---|---|---|
| Email (primary) | <vendor-email>@example.com | Business hours, ≤4h response |
| Email (incident) | *(set up dedicated incident@locallyai.app)* | Auto-forwarded + SMS-bridge |
| Phone (incident) | *(personal mobile — don't list here)* | 24/7 for SLA escalation |
| Slack | *(none yet — single-person)* | n/a |

> Phone numbers do **not** belong in this document. Keep them in the
> 1Password vault under "Vendor team — contact card". This document is
> committed to git; phone numbers should not be.

---

## On-call rota

Single-person team: Emanuel is on-call 24/7 with a soft "best-effort
overnight" understanding for non-critical alerts (the monitor
dashboard's SLA escalation cron only re-pages on alerts that have been
unacked for >3.5h, which gives an overnight cushion).

When the team grows past 1: this section becomes a weekly rota table
with primary + secondary on-call, with handover at Monday 09:00 local.
Use a shared calendar (Apple Calendar shared with vendor team) — never
verbal handover.

---

## Decision-making rights

Who can authorise what without a second sign-off:

| Action | Authority |
|---|---|
| Push to a feature branch | Anyone with repo write |
| Merge a PR to `main` | Anyone with repo write (PR review ≥1 once team > 1) |
| Tag a `dev` release | Anyone with GPG key access |
| Promote `dev → stable` | Founder only (single-person era); requires 2-of-N once team > 1 |
| Invoke kill switch (stop) | Anyone with TOTP + Worker URL |
| Invoke kill switch (require-version) | Founder only — affects all firms |
| Add a new firm to FIRM_TOKENS | Anyone running `scripts/onboard_firm.sh` |
| Decommission a firm | Founder only — irreversible |
| Approve a new sub-processor | Founder only — DPA implication |
| Sign a DPA | Founder (or duly-authorised director once incorporated) |

Scaling note: when team > 1, "Founder only" rows become "Founder + 1
other named director" with a clear rule for which director (alphabetical
by surname is one workable convention).

---

## Key custody matrix

Which person can access which credential. **Critical for succession** —
a key with no documented custody is a key that disappears when its
holder is unavailable.

| Credential | Where | Primary holder | Succession holder | Backup location |
|---|---|---|---|---|
| GPG release-signing private key | macOS Keychain (pinentry-mac) | Emanuel | *(unfilled)* | Encrypted USB in fireproof safe (off-site) |
| GPG passphrase | Memorised + 1Password | Emanuel | *(unfilled)* | Sealed envelope (off-site safe) |
| Kill-switch TOTP secret | Phone authenticator (1Password) | Emanuel | *(unfilled)* | Sealed envelope + base32 in 1Password |
| Kill-switch recovery codes | 1Password ("Kill switch — recovery codes") | Emanuel | *(unfilled)* | Sealed envelope (off-site safe) |
| Monitor TOTP secret | Phone authenticator (different entry) | Emanuel | *(unfilled)* | Same arrangement as kill switch |
| Monitor recovery codes | 1Password ("Monitor — recovery codes") | Emanuel | *(unfilled)* | Sealed envelope (off-site safe) |
| Cloudflare account password | 1Password | Emanuel | *(unfilled)* | n/a |
| Cloudflare 2FA TOTP | Phone authenticator | Emanuel | *(unfilled)* | Sealed envelope |
| Cloudflare API tokens | 1Password ("CF — Worker deploy") | Emanuel | *(unfilled)* | n/a (rotatable) |
| GitHub LocallyAI org owner | gh CLI keyring + 1Password | Emanuel | *(unfilled)* | n/a (recoverable via email + 2FA) |
| GitHub LocallyAI 2FA TOTP | Phone authenticator | Emanuel | *(unfilled)* | Sealed envelope |
| Resend API key | 1Password ("Resend — alerts") | Emanuel | *(unfilled)* | n/a (rotatable) |
| Per-firm telemetry tokens | `~/.locallyai/vendor/firms-registry.json` (mode 0600) | Emanuel | *(unfilled)* | Time Machine to encrypted disk; **also** off-site backup quarterly |
| Vendor-records repo write | gh CLI keyring | Emanuel | *(unfilled)* | n/a |
| Domain registrar (locallyai.app) | 1Password ("Domain — locallyai.app") | Emanuel | *(unfilled)* | n/a |

**Annual audit**: open this table on the 1st of January and verify each
row is still accurate. Note the date of last audit at the bottom of
this section.

> Last full key-custody audit: **(none yet — schedule for 2027-01-01)**.

---

## Open succession gaps

As of 2026-05, the vendor team is one person. Every succession column
above is unfilled. This is the #1 operational risk.

**Mitigation plan in priority order:**

1. **Sealed-envelope handover** (week 1 priority, no hire needed). Create
   a sealed envelope containing:
   - Printed copy of the GPG passphrase
   - Printed copy of the kill-switch TOTP secret (base32)
   - Printed copy of the monitor TOTP secret (base32)
   - Printed copies of all recovery codes (kill-switch + monitor)
   - The CF account email + 2FA backup codes
   - The GitHub LocallyAI org email + 2FA backup codes
   - Domain registrar 2FA backup codes
   - One paragraph: "If you're reading this, Emanuel is unavailable. Please
     contact [trusted-friend-1] and [trusted-friend-2] before doing
     anything with the contents of this envelope."

   Store in a fireproof safe at a location that is **not** the office and
   **not** Emanuel's home. Suggested: a parent / sibling / partner's
   safe-deposit box. Document the location in 1Password (without
   describing the contents).

2. **Trusted-friend brief** (week 2). Identify two trusted individuals
   (not necessarily technical) who:
   - Know the envelope exists and where to find it
   - Have phone numbers for: Emanuel's emergency contact, the largest
     onboarded firm, the trusted external counsel (when retained)
   - Are willing to be called by a firm IT contact who can't reach
     Emanuel after 24h
   They do **not** need access to the envelope contents — only the
   ability to convene the people who do.

3. **External counsel retainer** (month 1). Retain a UK solicitor for
   DPA review + breach-response advisory. They are the legal succession
   point for "vendor incapacitated, firm needs DPA enforcement help".

4. **External accountant** (month 2). For invoicing + tax. Reduces
   founder bus factor on financial continuity.

5. **First technical hire** (when first 5 firms are onboarded). Co-engineer
   with full key cascade — this is the moment "(unfilled)" succession
   slots get real names. Onboarding ceremony in
   [vendor-people.md](vendor-people.md).

---

## Communication channels

| Channel | Purpose | Retention |
|---|---|---|
| Email (`<vendor-email>@example.com`) | Default for everything | Indefinite — used as audit trail |
| 1Password share | Credential exchange (with firms or future team members) | Per-link expiry (24h default) |
| Signal (when team > 1) | Out-of-band channel for incident coordination | 4 weeks (auto-disappear) |
| GitHub issues (private repos) | Internal tracking + post-incident reviews | Indefinite |
| Calendar (shared) | On-call rota, customer renewal dates | Indefinite |

**Never use Slack/Discord/WhatsApp** for credential exchange or anything
that mentions a specific firm by name. These services have variable
retention, are search-indexed by employees of the providers, and don't
fit the no-third-party-data-disclosure posture in DPAs.

---

## Unanswered questions log

When you encounter a question this SOP doesn't answer, append it here
**before** you answer it (so the question is captured even if you forget
to come back). When you find / decide / discover the answer, edit the
entry inline.

| Date | Question | Answer / status |
|---|---|---|
| 2026-05-10 | What's the right firm-of-record to take over if LocallyAI dissolves? | TBD — see [vendor-disaster-recovery.md](vendor-disaster-recovery.md) |
| 2026-05-10 | When does GPG sub-key delegation become useful? | When team > 1 — sub-key per engineer, master key in safe |

---

## Onboarding a new vendor team member

See [vendor-people.md](vendor-people.md) for the full ceremony. High level:

1. Background check + signed NDA + signed equity agreement
2. 1Password vault grant (limited scope — not "Founder vault" yet)
3. Add to `LocallyAI/locallyai` org (Triage role, not Owner)
4. Add to `LocallyAI/vendor-records` (Read role only initially)
5. Authenticator entries: monitor dashboard (their own TOTP secret),
   kill switch (90-day probation before they get this)
6. GPG sub-key generated, signed by master, given to them
7. First on-call shift shadowed by founder

## Offboarding a vendor team member

See [vendor-people.md](vendor-people.md). High level:

1. Same-day: revoke 1Password access, remove from GitHub orgs, revoke
   Cloudflare access, revoke their GPG sub-key (publish revocation cert)
2. Same-day: rotate any **shared** TOTP secrets they could have
   exfiltrated (kill switch, monitor — generate new secrets, update
   Worker, refresh authenticators)
3. Within 24h: audit `firms-issued.log` for any token rotations they did
   in the prior 30 days; consider rotating those tokens defensively
4. Within 7 days: document the offboarding in this chapter's
   [Open succession gaps](#open-succession-gaps) section if their
   departure recreates a single-person dependency

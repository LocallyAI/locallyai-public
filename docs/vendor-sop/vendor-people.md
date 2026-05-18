# People — hiring, onboarding, offboarding vendor staff

> Adding or removing a person from the vendor team is a high-friction
> ceremony for good reason: every team member ends up with custody of
> credentials that, if leaked or rogue, could break every firm's
> deployment. This chapter is the ceremony.

---

## Hiring

### Decision criteria

Before any hire, the founder verifies:

- The hire fills a documented succession gap (per
  [vendor-team.md](vendor-team.md§open-succession-gaps)) OR a
  documented operational need
- The compensation arrangement is sustainable (cash + equity)
- The candidate is willing to operate under the
  [hard rules](../VENDOR_SOP.md#hard-rules-for-the-vendor-team) of the
  Vendor SOP
- The candidate has read the Vendor SOP cover-to-cover **before**
  signing
- The candidate has no overlap with employment by current customer
  firms (conflict of interest)

### Background check

- DBS basic disclosure (UK) or equivalent local check
- Reference checks: 2 professional references, contacted by founder
- Companies House lookup for any live director roles in firms that
  could conflict
- Public-record check (LinkedIn, professional history) to verify
  claimed experience

### Documents to sign before access is granted

1. **Employment contract / consultancy agreement** — including IP
   assignment to the LocallyAI legal entity, non-compete (where
   enforceable), garden-leave clause
2. **NDA** — explicit clauses about firm data, vendor credentials, and
   no-disclosure to current/future employers
3. **Equity vesting schedule** (4-year, 1-year cliff is the typical
   shape; document specifics in their offer letter)
4. **Acceptable Use Policy** — explicit acknowledgement of the Vendor
   SOP's hard rules
5. **Vendor SOP read receipt** — signed acknowledgement that they have
   read this entire SOP (date stamped)

File all of the above in `vendor-records/people/<staff-slug>/`.

---

## Onboarding ceremony

The new hire's first day. Allow 4 hours for the ceremony — do not
rush.

### Phase 1 — context (1 hour)

- Tour of the Vendor SOP (this document)
- Tour of the firm SOP (`docs/SOP.md`)
- Walk-through of one onboarded firm's `firm-profile.md` (if there is
  one) — anonymise mentally; the new hire is now a custodian
- Q&A — list every question they have, file in
  [vendor-team.md §unanswered-questions-log](vendor-team.md#unanswered-questions-log)

### Phase 2 — credentials (limited initial scope) (1 hour)

The new hire gets these on day 1:

- **1Password** — added to the `Vendor team vault` (NOT the
  `Founder vault`)
- **GitHub** — added to `LocallyAI/locallyai` with **Triage** role
  (read + manage issues, no write to main; promoted to **Write** after
  30-day probation)
- **GitHub** — added to `LocallyAI/vendor-records` with **Read** role
  only (no write at all initially; write granted only when they need
  to file a firm record themselves)
- **Cloudflare** — added as a member with **Member** role (read + can
  deploy specific Workers; not Owner)
- **Resend** — read access to the dashboard (for monitoring sends,
  not generating API keys)
- **Monitor dashboard** — their own TOTP entry (their phone, not
  shared with anyone) added to ADMIN_TOTP_SECRETS rotation
- **Shift notes folder** — write access to
  `vendor-records/shift-notes/`

The new hire **does NOT get** on day 1:

- Kill-switch TOTP secret (90-day probation)
- GPG release-signing key access (90-day probation + verified competence)
- Founder vault in 1Password (founder-only, ever)
- Wrangler tokens that touch the kill-switch Worker (90-day probation)
- Direct access to `~/.locallyai/vendor/firms-registry.json` (need to
  use `scripts/onboard_firm.sh`, which abstracts the file)

### Phase 3 — first task (1 hour)

A bounded, observable task. Suggested:

- Read one open firm-side incident playbook
- Run `bash scripts/audit_install.sh` on a test deployment
- Walk through one onboarding intake form fill (with founder watching)

The point is to confirm hands-on competence before granting any
production access.

### Phase 4 — record (1 hour)

- Add the new hire's name to [vendor-team.md §roles](vendor-team.md#roles)
  with their role + initial primary/succession assignments
- Add a row to the
  [key-custody matrix](vendor-team.md#key-custody-matrix) under
  "succession holder" for any role they're now backing up
- Update [vendor-infrastructure.md](vendor-infrastructure.md) for any
  shared credentials they now have access to
- Commit all of the above in one commit:
  `team: add <name> to vendor team — initial probation scope`

---

## 30-day probation review

End of the new hire's 30th day. Founder and new hire have a 1-hour
review:

- Operational competence: have they handled at least 5 dashboard
  events without escalation?
- SOP discipline: have they updated the SOP when they spotted gaps?
  (Even one update is a good sign.)
- Hard-rules compliance: any near-misses?
- Their own feedback on the onboarding process

If green: extend access:

- GitHub `LocallyAI/locallyai` → **Write** role
- Cloudflare → can deploy any Worker (still Member, not Owner)

If yellow: extend probation 30 days, identify the specific gaps.

If red: pull back to read-only for the protected period in their
contract; coordinate with HR / legal counsel for next steps.

---

## 90-day probation review

End of the new hire's 90th day. Same conversation, with these
additional questions:

- Are they ready for the kill-switch TOTP?
- Are they ready for the GPG signing key (they would only ever sign
  releases under founder co-presence initially)?

If green:

- **Kill switch** — generate them their own TOTP entry (don't share
  the founder's secret); update the kill-switch Worker
  `ADMIN_TOTP_SECRETS` to include the new entry
- **GPG signing** — generate a sub-key for them (delegated by the
  master key); they can sign dev releases independently, but stable
  promotions still require founder co-sign for the first 6 months

If yellow / red: hold the credentials, document the gap, schedule the
next review.

---

## Day-to-day ongoing

Throughout the engagement:

- New hire takes increasing on-call load: shadow shifts in month 1,
  weekend backup in month 2, primary on-call rotation in month 3
- Quarterly competence check: they pick one runbook chapter and
  rehearse the procedure live with founder
- Annual SOP read-through review (founder and team go through the
  whole Vendor SOP together, identify what changed and what's stale)

---

## Offboarding ceremony

When a vendor team member departs (resignation, end of contract,
mutual parting, or termination), the ceremony is the **same day** —
not "by end of week".

### Same day — within 1 hour of confirmed departure

1. **Revoke 1Password access** — remove from all vaults; confirm via
   1Password admin console
2. **Remove from GitHub orgs** — both `LocallyAI/locallyai` and
   `LocallyAI/vendor-records`
3. **Revoke Cloudflare access** — remove from CF Members panel
4. **Revoke Resend access**
5. **Revoke their GPG sub-key** — publish revocation cert to the
   keyserver; update `docs/release-signing-key.gpg` if their sub-key
   was published there
6. **Revoke their gh CLI keyring** (we can't actually revoke their
   local copy, but the GitHub side is gone — their `gh` will fail on
   next call)
7. **Revoke any wrangler API tokens** they had

### Same day — within 4 hours

8. **Rotate any shared TOTP secrets they could have accessed**:
   - Kill-switch TOTP secret (defensive; they had read access during
     probation expiry)
   - Monitor TOTP secret (defensive; same reasoning)
   - Update authenticator entries on remaining team members' phones
9. **Audit `firms-issued.log`** for any token rotations they did in
   the prior 30 days; consider rotating those tokens defensively
10. **Update [vendor-team.md](vendor-team.md)** roles + key-custody
    matrix to remove their name; if their departure recreates a
    single-person dependency, append to
    [open-succession-gaps](vendor-team.md#open-succession-gaps)
11. **Update [vendor-infrastructure.md](vendor-infrastructure.md)** to
    remove their name from any "Primary holder" / "Succession holder"
    cells

### Within 24 hours

12. **Comms** — single announcement to current firms (only if their
    departure changes the named on-call contact for the firm):
    > "[Name] has departed the LocallyAI vendor team as of [date].
    > Your day-to-day contact is now [name]. There are no operational
    > changes to your service."
13. **NDA reminder letter** — formal letter reminding them of NDA
    obligations re: firm data and vendor credentials post-employment

### Within 7 days

14. **Equity settlement** per their vesting schedule
15. **Final pay** including any accrued holiday
16. **Post-departure access verification** — try every credential
    rotation from step 1–7 from the departed person's hypothetical
    perspective; confirm no path remains open
17. **Lessons learned** — append to a "vendor people" log if anything
    in the offboarding revealed a gap (e.g., "discovered they had
    access to X we hadn't documented" → add X to
    vendor-infrastructure.md)

---

## Termination for cause

If the departure is termination for cause (e.g., violation of hard
rules, fraud, etc.), accelerate everything:

- Steps 1–7 within 30 minutes (block all access immediately, before
  any conversation with the person about the termination)
- Step 8 the same hour
- Steps 9–11 the same day
- Steps 12–13 the same day
- Get external counsel involved within 24 hours
- Treat as a [V5](vendor-incidents-own-infra.md) tier-A incident even
  if no actual breach occurred — assume worst case
- Post-incident review

---

## Garden leave

If contract specifies garden leave: the person retains employment
status (and pay) but not access. Treat their **access** as offboarded
per the ceremony above. Reinstate only if they return; otherwise the
formal end-of-contract is the second offboarding event for records
purposes.

---

## Annual training

Once per year, every vendor team member completes:

- Re-read of this entire Vendor SOP (4 hours, blocked in calendar)
- Re-read of the firm SOP master + at least 5 chapters they don't work
  with daily
- One incident-response tabletop: founder picks a scenario from
  [V5](vendor-incidents-own-infra.md) and walks through it with each
  team member (each engineer plays a different role on different years)
- DPA literacy refresher: re-read one of the signed firm DPAs to stay
  current with what we've agreed to

Track completion in `vendor-records/people/<staff-slug>/training-log.md`.

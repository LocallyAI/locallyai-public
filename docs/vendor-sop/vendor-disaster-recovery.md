# Vendor disaster recovery

> Recovery from scenarios where the vendor itself — not just one piece
> of infrastructure — is compromised or unavailable. This chapter
> assumes that whoever reads it is in a recovery context: the founder
> is unavailable for ≥24h, has been seriously injured, or has died.
> Or: the LocallyAI legal entity is being wound down.

This is the chapter you hope no one ever needs. It is also the most
important chapter in the Vendor SOP, because every firm has bet
operational continuity on the vendor remaining operational.

---

## Recovery time objectives (RTO)

| Outcome | Target RTO |
|---|---|
| Dashboard read access (someone can see what state firms are in) | 1 hour from new device |
| Kill-switch capability (someone can stop a bad release) | 1 hour from new device |
| Release-signing capability (someone can issue a new release) | 4 hours from new device |
| Full operational handover (new vendor engineer fully productive) | 1 business day |
| Legal handover (firm DPAs novated to a new processor) | 30 days |

These are objectives, not promises. They become realistic only with
the prerequisites in [Recovery prerequisites](#recovery-prerequisites)
in place.

---

## Recovery prerequisites

These must be in place **before** disaster strikes. Reviewing them is a
quarterly task per [vendor-daily-ops.md](vendor-daily-ops.md).

### Sealed envelope

A physical envelope, sealed (wax or tamper-evident sticker), in a
fireproof safe at a location physically separate from the founder's
home and the office.

Contents (this list is the actual checklist for what must be in the
envelope — verify quarterly):

- [ ] One-page cover letter: "If you're reading this, please contact:
      [Trusted Friend 1 phone], [Trusted Friend 2 phone]. Do nothing
      else with the contents until at least one of them confirms."
- [ ] Printed GPG release-signing key passphrase
- [ ] Printed kill-switch TOTP secret (base32)
- [ ] Printed kill-switch recovery codes (10 codes)
- [ ] Printed monitor TOTP secret (base32)
- [ ] Printed monitor recovery codes (10 codes)
- [ ] CF account email + 2FA backup codes
- [ ] GitHub LocallyAI org owner email + 2FA backup codes
- [ ] Domain registrar email + 2FA backup codes
- [ ] 1Password Emergency Kit (printed)
- [ ] One-page reference: locations of (a) the founder's daily-driver
      Mac, (b) the off-site Time Machine disk, (c) any other key
      physical assets
- [ ] One-page reference: list of largest 5 firms with their primary IT
      contact phone numbers (so the recovery person can call them)
- [ ] One-page reference: external counsel contact (when retained)

The envelope contents must NOT include:

- Any unsealed credentials (everything is in the sealed envelope or it
  isn't recoverable)
- The founder's personal documents or unrelated material
- Anything that could embarrass the founder if opened by a recovery
  person — keep this strictly operational

### Trusted-friend brief

Two trusted individuals who:

- Know the envelope exists and where to find it
- Know how to access it (key, combination, safe-deposit-box visit)
- Have phone numbers for: founder's spouse / family emergency contact,
  the largest 3 onboarded firms' IT contacts, the external counsel
- Have agreed to act as the convening authority — they are not expected
  to do technical work, only to convene the people who can

These individuals are documented by **role only** in this chapter (not
by name) — names live in 1Password and the sealed envelope.

### Designated successor firm

A pre-arranged commercial agreement with another technology firm to
take over LocallyAI operations under one of three scenarios:

1. Founder incapacity (>30 days)
2. Founder death
3. LocallyAI corporate dissolution

The agreement covers: source-code custody, asset transfer, novation of
firm DPAs to the successor, customer comms script, IP assignment.

> **Status as of 2026-05**: not yet arranged. This is the **#1 vendor
> dispatch risk**. See [Open succession gaps](vendor-team.md#open-succession-gaps).

### Source-code escrow (alternative to successor firm)

If a designated successor firm cannot be identified, an alternative is
source-code escrow: deposit the LocallyAI/locallyai repo (and a
recovery README) with a regulated escrow agent under a release
condition like "released to firms on certified founder death".

This is a worse outcome than a designated successor (firms have to
self-host or migrate to another vendor), but it is better than nothing.

---

## Scenario 1 — Founder unavailable for 24 hours (no contact)

**Symptom**: Founder doesn't respond to email, phone, or text for 24h
during a normal week. No prior notice of being away.

### Within 24h

A trusted-friend or firm IT contact who notices may not know whether to
escalate. The escalation rule:

- Routine 24h silence → wait another 24h before acting (people get sick)
- 24h silence + a critical-tier alert firing on the monitor dashboard →
  escalate immediately

### Trusted-friend action (when escalated)

1. Attempt direct contact (phone, in-person if local).
2. Contact the founder's emergency contact.
3. Contact the second trusted friend to coordinate.
4. **Do not yet open the sealed envelope.**

### When to open the envelope

Only when **all** of the following are true:

- 72h has elapsed since last contact
- Founder's emergency contact confirms incapacitation or worse
- A firm-side incident is in flight that requires vendor action

OR:

- 7 days has elapsed since last contact regardless of firm state
  (preserves operational continuity even without an active incident)

### What the recovery person does first

1. Use the kill-switch credentials to verify the kill-switch Worker is
   in `{"status":"go"}`. If it isn't (or if a firm reports an issue),
   set it to `{"status":"go"}` to ensure no auto-rollback freeze.
2. Use the monitor credentials to sign in to the dashboard and assess
   the fleet state. Note any firms in the red.
3. Use the largest-firm phone numbers to call IT contacts of those
   firms; explain the founder is temporarily unavailable, give an
   estimate, ask if anything urgent is in flight.
4. Email the rest of the fleet a coordinated notification (template
   below).
5. **Do not** issue any new releases. Do not promote dev → stable.
   Hold steady-state until the founder returns or a successor takes
   over.

### Steady-state operations under recovery

The recovery person can run the existing fleet for up to 30 days
without releasing new code. Routine ops:

- Monitor dashboard check daily
- Acknowledge alerts within SLA where the self-healers don't
- Forward firm reports to a designated technical advisor (e.g., the
  external counsel, or a contracted consultant)
- Collect any feedback / new prospect inquiries to a holding inbox

### Comms template

> Subject: LocallyAI — temporary operational handover
>
> Dear [firm name] team,
>
> Please be advised that LocallyAI is currently being managed by
> [recovery person name] following [generic descriptor — e.g., "a
> family emergency affecting the founder"]. Day-to-day operations
> (monitoring, incident response under the SLA) continue as normal.
>
> We are not issuing new releases during this period. Your office Mac
> will continue running on the version currently installed.
>
> If you have an urgent issue, please contact: [recovery person email
> or phone].
>
> We will provide a fuller update by [date — typically 14 days from
> initial outage].
>
> Thank you for your patience.

---

## Scenario 2 — Founder permanently unavailable

**Triggers**: confirmed founder death, or a 30-day incapacity that
medical advisors don't expect to recover from.

### Within 7 days

The recovery person + the trusted-friend convening authority + the
external counsel (if retained) coordinate a board meeting (or
equivalent for the legal entity) to decide:

- Activate the designated successor firm? (If pre-arranged.)
- Activate source-code escrow release? (If pre-arranged but no
  successor.)
- Wind up LocallyAI and offer firms a refund + 90-day notice to migrate?

### If a successor firm is activated

1. Successor firm's lead engineer collects the sealed envelope.
2. Successor firm signs an IP assignment for the LocallyAI codebase.
3. Successor firm signs a sub-processor agreement with each existing
   firm (DPA novation — legal counsel handles this).
4. Successor firm receives:
   - GitHub LocallyAI org owner credentials (from sealed envelope)
   - Cloudflare account credentials
   - Domain registrar credentials
   - GPG release-signing private key (re-imported from sealed
     envelope encrypted USB) + revocation certificate (envelope)
   - 1Password Founder vault contents (via Emergency Kit)
   - The local clone of `~/locallyai-vendor-records/`
   - The `~/.locallyai/vendor/firms-registry.json` file
5. Successor firm announces to fleet:
   > "LocallyAI is now operated by [successor firm]. Your DPA has been
   > novated. There are no operational changes to your service. The
   > new vendor contact is [email]."

### If source-code escrow is activated

1. Escrow agent releases the LocallyAI/locallyai source archive to
   each registered firm.
2. Comms to firms: "LocallyAI is winding down. The source code for
   your office Mac install is now released under [licence]. You may
   continue running it indefinitely; you will not receive further
   updates from us. We recommend [migration paths]."
3. Provide a 90-day support tail for migration questions where
   feasible (depends on residual cash + recovery person's bandwidth).

### If wind-up

1. Provide a 90-day notice to all firms (per most DPA templates).
2. Refund any pre-paid annual fees pro-rata.
3. Send each firm their `firm-profile.md` + their
   firm-issued telemetry token history (for their records).
4. Invoke kill switch with `{"status":"stop","reason":"vendor wind-up — please disable telemetry per shutdown notice"}`.
5. Decommission CF Workers (preserves data sovereignty — no leftover
   vendor infrastructure holding firm metadata).
6. Delete `~/.locallyai/vendor/firms-registry.json` after firms confirm
   migration complete.
7. Submit a wind-up notice to the ICO / SDAIA per vendor's own RoPA
   obligations.

---

## Scenario 3 — Vendor company dissolution (planned)

If LocallyAI is being wound down on the founder's own decision (not an
emergency), the timeline is more relaxed but the steps are the same as
the wind-up branch of scenario 2. Add a phase 0:

- 6 months out: announce intent to firms, give them maximal notice.
- 3 months out: stop selling. Continue supporting existing firms.
- 1 month out: invoke kill switch as a notification mechanism.
- 0: wind-up per scenario 2.

---

## Scenario 4 — Mass exfiltration of firms-registry.json

**Symptom**: confirmed leak of `~/.locallyai/vendor/firms-registry.json`
with all firms' telemetry tokens.

This is a dispatch-level vendor incident even if no individual firm
is yet harmed. The cascade:

1. Kill switch — pause everything.
2. Iterate `scripts/onboard_firm.sh` over every firm-profile.md to
   rotate every token. (See
   [V5 §telemetry-token-leak — multiple firms](vendor-incidents-own-infra.md#telemetry-token-leak).)
3. Coordinate with each firm to swap in new tokens within 24h.
4. Lift kill switch.
5. Post-incident review per [V5 appendix](vendor-incidents-own-infra.md#appendix-post-incident-review-template).
6. Consider whether `firms-registry.json` should be encrypted at rest
   (currently mode-0600 only) — this is the action item that should
   come out of the post-incident review.

---

## Recovery testing

Every 12 months, run a recovery drill:

1. Pick a random scenario from this chapter.
2. Walk through it on paper with the trusted-friend convening authority
   (or, when team > 1, with a co-engineer playing the recovery role).
3. Identify any step that the recovery person couldn't execute
   (missing credentials, ambiguous instructions, expired contact
   numbers).
4. Fix in this chapter and the corresponding inventory.

> Last recovery drill: **(none yet — schedule for first anniversary of
> first onboarded firm)**.

The drill is the only way to know whether the prerequisites in this
chapter are real or notional. Skipped drills are how recovery plans
become recovery fiction.

# Daily vendor ops

> What the on-call engineer does at the start, middle, and end of a
> shift. The point of a checklist is to make routine work boring;
> boring routine is the absence of forgotten obligations.

---

## Start-of-shift checklist (every weekday morning)

Estimated time: ~10 minutes. Do this before opening any other tab.

### 1. Monitor dashboard (2 min)

Open `https://locallyai-monitor.<your-cf-account>.workers.dev/` and sign
in with your TOTP. Verify:

- [ ] No red dots on the firm grid
- [ ] No unacked critical alerts in the alert table
- [ ] No SLA-at-risk highlighting (>3.5h unacked)
- [ ] No firms have gone "stale" (grey dot, >1h since last heartbeat)

If any of the above is not true → page yourself; switch to incident
response per [vendor-monitoring.md](../sop/vendor-monitoring.md) and
log a row in your shift notes.

### 2. Email + inboxes (3 min)

- [ ] `<vendor-email>@example.com` — scan for firm reports, prospect inquiries, vendor email (CF / GitHub / Resend service notifications)
- [ ] GitHub notifications — anything filed against `LocallyAI/locallyai` overnight?
- [ ] Failed-CI digest from `LocallyAI/locallyai/actions` — anything red on `main`?

### 3. Weekly tasks check (1 min)

What day is it?

- **Monday** — review prior week's alerts: any patterns? File a "weekly
  trend" note in `vendor-records/weekly-notes/<YYYY-WW>.md` if anything
  warrants it.
- **Wednesday** — pre-release sanity: are we planning a release this
  week? If yes, run pre-release checklist in
  [vendor-release-engineering.md](vendor-release-engineering.md§pre-release-checklist).
- **Friday** — pre-weekend dashboard sweep: tighten on every yellow
  warning so the weekend on-call isn't paged at 02:00 for stuff that
  could've been fixed Friday at 16:00.

### 4. Sales pipeline glance (1 min)

Open the sales tracker (Notion / spreadsheet / wherever — see
[vendor-sales.md](vendor-sales.md)) and verify:

- [ ] No prospect has been waiting >3 days for a response
- [ ] No DPA in legal review for >7 days without a chase

### 5. Calendar peek (1 min)

- [ ] Any onboarding milestones today (install visit, 30-day review)?
- [ ] Any annual-renewal reminders firing today?
- [ ] Any sub-processor renewal reminders (CF, registrar, Apple Dev)?

---

## During-shift expectations

### SLA window

The 4-hour SLA applies to **critical** alerts. Lesser alerts (warning,
info) do not page; they show up on the dashboard but the on-call deals
with them at the next start-of-shift.

Critical-alert response:

1. Acknowledge in the dashboard within 30 min (stops the SLA escalation
   cron from re-paging).
2. Diagnose using the alert code → diagnostic mapping in
   [vendor-monitoring.md](../sop/vendor-monitoring.md).
3. Self-healers (`healthz_kickstart`, `ollama_restart`,
   `disk_pressure_clean`) handle the most common cases. If a self-heal
   succeeded, confirm the next heartbeat is green and ack the alert.
4. If self-heal failed or didn't apply: contact firm IT primary by
   phone (not email — too slow for SLA).
5. Resolve within 4h of the original alert timestamp.
6. Post-incident: append a row to your shift notes (`incident at 11:32
   for firm X — root cause Y — resolved 12:14`).

### Don't deviate without writing it down

If you're about to do something that's **not** in the firm SOP, the
Vendor SOP, or a documented incident playbook — pause. Write what you're
about to do in your shift notes first. Then do it. Then update the
relevant SOP chapter in the same git commit as the incident-response
work. The point is to leave the trail richer than you found it, not
just to fix this one issue.

---

## End-of-shift handover (single-person team)

Even with a team of one, a written end-of-shift note is invaluable —
six months later, you will not remember what you were in the middle of.

Daily: append to `vendor-records/shift-notes/<YYYY-MM>.md`:

```
## 2026-05-12 (Mon)

Open at start: 0 red, 0 critical, 1 stale (firm-XYZ — known overnight maintenance).
Closed during shift:
  - 11:32 firm-XYZ stale → resolved at 12:14 (firm IT confirmed planned reboot)
Started during shift:
  - drafted v3.10 release notes
  - sent intake URL to prospect "Acme & Co"
Open at end: 0 red, 0 critical, 0 stale.
```

(When team grows: this becomes a Slack/Signal handover message to the
incoming on-call.)

---

## Weekly tasks

### Mondays

- [ ] Review prior-week alerts. Any patterns? File trend note.
- [ ] Verify Time Machine backup completed successfully every day last week.
- [ ] Check sub-processor status pages for incidents that affected us:
      cloudflarestatus.com, githubstatus.com, status.huggingface.co.
- [ ] Refresh the sales pipeline forecast.

### Fridays

- [ ] Pre-weekend dashboard sweep: ack every info/warning that doesn't need overnight watch.
- [ ] Mute non-critical Resend alerts for the weekend (per individual firm's pref).
- [ ] Review the kill-switch status JSON on the Worker — is it `{"status":"go"}`? (paranoia check; a misset value would silently kill firms)
- [ ] Off-laptop the day's work (push every WIP branch, even ugly ones).

---

## Monthly tasks

First weekday of each month:

- [ ] **Audit `firms-issued.log`** — any token rotations you don't remember authorising?
      Cross-check against your shift notes for the prior month.
- [ ] **Verify backups**: pick one random firm, restore their `firm-profile.md`
      from the off-site Time Machine and confirm bytes match the live file.
- [ ] **Sub-processor renewal scan**: any annual renewals coming up in
      the next 60 days? Domain, Apple Dev, CF paid plans, etc.
- [ ] **Inventory drift check**: open
      [vendor-infrastructure.md](vendor-infrastructure.md) and verify each
      row's "Where stored" column still describes reality. Fix any drift.
- [ ] **Open succession gaps review**: anything new to add to
      [vendor-team.md §open-succession-gaps](vendor-team.md#open-succession-gaps)?

---

## Quarterly tasks

- [ ] **Off-site backup rotation**: swap the encrypted Time Machine
      disks between the primary and off-site locations.
- [ ] **Sealed-envelope review**: open the envelope, verify contents
      still match
      [vendor-infrastructure.md §backup-locations](vendor-infrastructure.md#backup-locations).
      Reseal with new wax/sticker.
- [ ] **CF API token rotation**: rotate any `wrangler` deploy tokens older
      than 90 days. Update `~/.wrangler` config + 1Password vault.
- [ ] **Per-firm telemetry token rotation** (optional): pick the 25%
      oldest registrations and re-run `scripts/onboard_firm.sh` to
      rotate tokens. Coordinate the rotation with firm IT in advance.

---

## Annual tasks

January 1st (or first business day after):

- [ ] Full key-custody audit per
      [vendor-team.md §key-custody-matrix](vendor-team.md#key-custody-matrix).
      Update last-audit date.
- [ ] Account-password rotation for all Tier B accounts (CF, GH, registrar, 1P).
      Tier A accounts (GPG, kill-switch TOTP) stay unless incident
      indicates otherwise.
- [ ] Sub-processor annual review per
      [vendor-sub-processors.md](vendor-sub-processors.md).
- [ ] DPA template review: legal counsel re-reads against the latest UK
      / KSA / EU regulatory updates and flags any clauses that need
      revision.
- [ ] Renew calendar reminders for the next year of recurring tasks.
- [ ] Run `scripts/audit_install.sh` on the founder's daily-driver Mac
      to confirm any local LocallyAI install is still healthy (you do
      not need a local install to operate, but if you have one for dev,
      keep it green).

---

## "Quiet day" backlog

When start-of-shift is fully clean and there's no firm work, work down
this backlog instead of inventing tasks:

1. Improve one SOP chapter (firm or vendor) — pick the one that bit you
   most recently in an incident.
2. Add one test case to the chaos suite (`tests/ha_chaos.py`) for a
   failure mode that's not covered.
3. Triage one open prospect lead.
4. Read one chapter of someone else's incident write-up (e.g., AWS
   post-mortems, Cloudflare blog) to widen your incident vocabulary.
5. Pre-stage one DPA review (UK or KSA) so it's ready when the next
   prospect signs.

The goal of "quiet day" is to compound — a quiet day spent improving
the runbook saves a panicked day six months later.

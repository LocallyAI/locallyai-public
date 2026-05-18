# Incident playbooks — people

The SOP is technically perfect and operationally useless if the only
person who knows it is on holiday. This chapter is about humans:
absence, handover, escalation when reachability fails, and the
single-point-of-knowledge problem that bites every small ops team.

---

## Sole IT-ops person on holiday during an incident

**Trigger:** the firm's only IT-ops person is on annual leave; the AI
service has gone down or is misbehaving; users are noisy.

### Pre-conditions (do these BEFORE leave starts)

The off-leave person never reads this section in time. Do these in
the week before:

1. **Brief the back-up.** A named partner or office manager. Walk
   them through:
   - How to read this SOP (the master index is `docs/SOP.md`; the
     incident chapters are linked from there).
   - Where the credential register lives (the firm password vault).
   - The **two** numbers to call in priority order: (a) the LocallyAI
     vendor's support contact, (b) the back-up IT consultant.
2. **Set an admin escalation pager.** A free Slack/Teams webhook
   wired to `LOCALLYAI_ALERT_WEBHOOK_URL` in `.env` so when sentinel
   posts a critical alert, somebody at the firm sees it without the
   IT-ops person needing to be online.
3. **Pre-print the on-call cheat sheet.** A 2-page printed copy of:
   - This file.
   - [daily.md § "Quick health sweep"](daily.md#quick-health-sweep-do-this-every-morning).
   - [incidents-software.md § "API not responding"](incidents-software.md#api-not-responding).
   The back-up doesn't need the whole SOP; they need the 80% of
   problems that are "the service stopped responding."
4. **Brief the firm.** "Your AI assistant runs on hardware in this
   office. The IT person is on leave from <date> to <date>. If it
   stops working, contact <back-up>."

### During the incident (back-up is on it)

The back-up's decision tree:

1. **Open the fleet dashboard / hit `/healthz`.** If it responds: the
   service is up and the user complaint is something else (search,
   model quality, their own machine).
2. **If `/healthz` fails:** restart the service per
   [daily.md § "Start / stop / restart"](daily.md#start--stop--restart-the-service).
   Wait 5 min. If it comes back: open a ticket for the IT-ops person
   to look at when they return; tell users it's resolved.
3. **If restart doesn't fix it:** call the LocallyAI vendor support
   contact. Don't try anything destructive (no `rm`, no editing
   `.env`, no rotating salts). Wait for a real engineer.
4. **If the vendor is unreachable:** tell users "Service is offline
   until <IT person> returns on <date>." This is a defensible answer
   — it's better than a half-fix that breaks worse.

### After the IT person returns

- Read the incident log (`logs/launchd_error.log`, sentinel.log,
  security.log) over the leave window.
- Did the back-up do anything irreversible? (They shouldn't have, per
  the brief, but verify.) Run `audit_install.sh`.
- Add anything the back-up needed but didn't have to the cheat sheet.
- Update [CHANGELOG.md](CHANGELOG.md) with a line on what happened
  and what the back-up cheat sheet now covers.

---

## DPO out of office during a regulatory event

**Trigger:** a data subject has filed a Subject Access Request under
deadline; or a breach happens; the DPO is unreachable for >24h.

### What you (IT-ops) can do without the DPO

- **Acknowledge** the request from the firm's general
  privacy@firm.tld inbox: "We received your request on <date> and
  will respond within <statutory limit>." This protects the firm.
- **Preserve evidence** without taking any operational action:
  ```bash
  # Snapshot relevant logs into a holding folder
  cp -p logs/audit.log logs/billing.log logs/security.log logs/erasure.log \
     ~/dpo-pending-<date>/
  ```
- **Do not erase, do not rotate** anything. Both actions destroy
  evidence the DPO will need to assess the scope.

### What you must NOT do without the DPO

- Issue an Art. 15 reply to the data subject (only the DPO can; the
  IT-ops role is to produce evidence, not to interpret it).
- Issue an Art. 17 erasure (only the DPO authorises).
- Send a regulator notification (only the DPO drafts).
- Decide the scope of the breach (only the DPO concludes).

### Escalation order if the DPO can't be reached

1. The DPO's nominated deputy (firm-policy field — must exist, must
   be in the credential register).
2. The senior partner with privacy oversight.
3. External counsel on retainer.
4. The supervisory authority's helpdesk for an extension.

### After the DPO returns

Brief them on: what was preserved, what was acknowledged, what was
NOT done. They take over from there.

---

## Vendor (LocallyAI engineer) unreachable

**Trigger:** the SOP suggests calling the vendor; the vendor doesn't
respond.

### Tier-down decision tree

1. **Critical (firm fully down):** wait no more than 2 hours. If
   still no vendor response, your firm's IT consultant or any
   competent Python/devops engineer can read this SOP, the
   `incidents-software.md` chapter, and try the relevant procedure.
   Document what you (or they) tried.
2. **Non-critical (degraded but working):** wait up to a business
   day. Most "I can't figure this out" issues with LocallyAI resolve
   from a fresh pair of eyes reading the relevant SOP chapter
   carefully.
3. **Unknown severity:** treat as critical. Better to over-call than
   to discover Monday morning that yesterday's silent issue cost the
   firm.

### What to send the vendor when you do reach them

A 4-line summary, not a transcript:

```
WHAT'S BROKEN: <one sentence>
WHEN IT STARTED: <timestamp>
WHAT YOU'VE TRIED: <three bullets>
LOG TAIL: <50 lines from logs/launchd_error.log around the time>
```

Attach: the relevant log files. Do NOT attach `.env`, `users.json`,
or `tls/key.pem` — never, even to the vendor.

### If the vendor goes out of business

LocallyAI is open-source (or your firm has the source). Procedure:

1. Make sure the firm has a complete clone of the repository in a
   firm-controlled git remote (not just on the deployment Mac).
2. Identify a back-up engineer or consultancy who can read this SOP
   and operate the deployment. The whole codebase is meant to be
   readable by one careful Python engineer over a weekend.
3. Run the deployment on autopilot until you've engaged the back-up.

---

## Key person leaves the firm

**Trigger:** the IT-ops person who set up LocallyAI is leaving (notice
period or sudden departure).

### Pre-departure checklist (notice period)

1. **Knowledge transfer to the successor or back-up:**
   - Walk through every chapter under `docs/sop/` together. Don't
     just say "read it" — sit with them while they restart the
     service, run the audit, ingest a doc.
   - Have them perform a DR drill (per [recovery.md](recovery.md))
     under your supervision.
   - Have them perform a salt rotation under your supervision.
2. **Update the credential register:**
   - Successor's name on every entry.
   - Old IT person's access revoked from the password vault on their
     last day, NOT before.
3. **Change which credentials are in the leaving person's personal
   notes / 2FA / Apple ID Find My:**
   - The leaving person knew the FileVault recovery key — rotate it
     (System Settings → FileVault → Reset; firm vault holds the new
     one).
   - The leaving person had the admin key — rotate it on their last
     day per [maintenance.md § "HMAC chain key rotation"](maintenance.md#hmac-chain-key-rotation)-style flow but for admin key.
4. **Hand-over document:** a 1-page list of the firm's deployment
   specifics:
   - Mac/Win hostnames + IPs
   - Model in use + why this one
   - Outstanding tickets / known issues
   - Next scheduled maintenance items
   - Anything that's been customised away from defaults

### Sudden departure (involuntary, escorted out)

Same as above but compressed and with adversarial assumptions:

1. **Within 1 hour** of departure being declared:
   - Rotate every credential they had: admin key, audit salt + HMAC
     key, every user key (forces every user to update — annoying but
     mandatory), TLS cert (regenerate + redistribute), Qdrant API
     key (HA), Syncthing — re-pair from scratch.
   - Lock the leaving person out of the firm's vault.
   - Lock the leaving person's macOS / Windows accounts on the
     deployment box.
2. **Within 24 hours:**
   - Successor or back-up performs full audit: who accessed what
     when, anything anomalous in `security.log`?
   - File the rotation as an Art. 32 control event (not necessarily
     Art. 33-eligible — depends on whether you have evidence of
     misuse).

### After-action

- Update [CHANGELOG.md](CHANGELOG.md): "<date>: KP <X> off-boarded;
  all credentials rotated."
- Schedule a 90-day review with the successor — gaps in the SOP that
  the successor identified, which the original IT person didn't
  notice.

---

## Weekend on-call coverage

**Trigger:** the firm uses LocallyAI 7 days a week, but IT-ops only
works Mon-Fri.

### Two viable patterns

**Pattern A — best-effort:** users tolerate weekend outages. The
service is critical Mon-Fri 9-6; weekend issues wait. Document this
in the user-facing comms:

> The AI assistant is monitored Mon-Fri 9-6. Weekend issues are
> resolved on Monday morning.

This is the cheapest pattern. Honest with users.

**Pattern B — paid on-call:** an external IT consultancy holds a
24/7 contact number; the firm pays a monthly retainer. Provide them:

- Read access to this SOP (a published copy in the firm's intranet
  or a shared doc).
- A break-glass admin key (a separate one from yours, scoped via a
  secondary `LOCALLYAI_ADMIN_KEY` if your codebase supports it; or
  the same one shared on a need-to-know basis).
- Access to the firm's password vault (read-only on the LocallyAI
  folder).

### What the on-call should be authorised to do

- Restart services.
- Run the audit script.
- Triage from logs.

### What the on-call should NOT be authorised to do without firm sign-off

- Rotate credentials (impacts users' Monday).
- Modify `.env`.
- Erase users.
- Issue regulator notifications.

If something happens that needs those, on-call escalates to the firm
DPO + IT-ops.

---

## Single-point-of-knowledge

**Trigger:** something in the deployment was customised away from
defaults (custom `.env` value, custom launchd plist, custom firewall
rule) and only one person knows about it.

### Prevention

When you customise, document. **Right now, while you're doing it.**
Not later.

The right place: a section at the end of [maintenance.md](maintenance.md)
titled "Local customisations" listing:

```
- LOCALLYAI_AUDIT_RETENTION_DAYS=180   (firm policy: 6-month retention not 12)
- Custom firewall rule: allow 8000 only from 10.0.0.0/24
- launchd plist edited to set NICE -5 (priority over background tasks)
- ...
```

### Recovery (you're the new person and find a customisation you don't understand)

1. **Don't undo it.** It was put there for a reason; the reason may
   not be in the doc but the customisation almost certainly is
   load-bearing.
2. **Try to find the reason** from git history (`git log -p .env`),
   the firm's ticket system, or by asking the leaver.
3. **Add the reason** to maintenance.md `Local customisations` once
   you know.
4. **Only then** consider whether it's still needed.

---

## On-call paging

**Trigger:** you want sentinel alerts to wake somebody at 3am when
the service goes critical.

### Setup

In `.env`:

```
LOCALLYAI_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/<your-webhook>
```

(Slack webhook URL or Teams equivalent.) The sentinel POSTs critical
alerts to that webhook. Test:

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"text":"sop test alert"}' \
  $LOCALLYAI_ALERT_WEBHOOK_URL
```

### Pager fatigue

Two failure modes to avoid:

1. **Alerts fire constantly** → ops ignores them all → the real one
   slips through. Symptom: you've muted the channel.
2. **Alerts never fire** → ops assumes everything is fine forever.
   Symptom: there hasn't been a green-coloured "all clear" message
   in months — the channel is dead.

Fix: schedule a **monthly synthetic alert** to confirm the pipe
works. Sentinel doesn't currently do this; can be added as a
`scripts/test_alert.sh` cron.

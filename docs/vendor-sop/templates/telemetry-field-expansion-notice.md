# Telemetry field expansion — firm notice template

> Send to every firm that has `LOCALLYAI_TELEMETRY=on` in their `.env`
> BEFORE the release containing new heartbeat fields lands on their
> Mac. The vendor's commitment in the DPA + data-isolation SOP chapter
> is to disclose-then-deploy, never deploy-then-disclose.

---

**To:** firm IT primary + DPO
**From:** LocallyAI vendor (<your-ops-email>@example.com)
**Subject:** LocallyAI — vendor health telemetry: new fields starting [DATE]

Dear [firm name] team,

Per our DPA Schedule 2 / our `data-isolation.md` SOP chapter §
"Optional vendor health telemetry", we are writing to disclose an
upcoming expansion of the fields the heartbeat agent sends to our
monitoring infrastructure. You have `LOCALLYAI_TELEMETRY=on` so this
notice applies to your deployment.

**Effective with LocallyAI release [version]** (expected on your Mac
on [date]), the heartbeat will additionally include:

| Field | Example value | Why |
|---|---|---|
| `macos_version` | `14.4` | Vendor needs to know which macOS major+minor each firm runs to flag any firm operating on an un-tested OS version (per `maintenance.md §macos-version-policy`). Lets us pause your fleet's auto-updates before a known-bad combination affects you. |
| `macos_build` | `23E214` | Distinguishes Apple Rapid Security Response patches that ship under the same marketing version. |
| `python_version` | `3.12.13` | If Apple bumps the system Python underneath your venv, MLX inference can subtly break; this lets us see it early. |
| `backend_version` | `mlx-lm 0.31.3` | Same — a regressed dep version is a leading indicator of issues vendor can warn you about. |

**What this is NOT:**

- It is NOT firm-attributable beyond what `firm_id` already conveys.
- It is NOT document content, user identifiers, audit content, or
  query data. The list of fields the heartbeat **never** carries
  (see SOP) is unchanged.
- It is NOT a change to the opt-in default. Telemetry remains off by
  default for any new firm.

**Your options:**

1. **Accept** (no action required). On the release date the new fields
   start appearing in your heartbeats automatically.

2. **Disable telemetry entirely.** Run on the office Mac:
   ```sh
   sed -i.bak 's/^LOCALLYAI_TELEMETRY=.*/LOCALLYAI_TELEMETRY=off/' ~/locallyai/.env
   launchctl kickstart -k "gui/$(id -u)/app.locallyai.api"
   ```
   Your Mac will stop posting heartbeats; our dashboard will show
   "no recent heartbeat" within ~10 min. Our 4-hour SLA continues to
   apply but only when you contact us — we lose the proactive view.

3. **Reply to this email** if you want the change but with a specific
   field excluded, or if you want a written record of consent for
   your own audit file. We're happy to keep your fields restricted to
   the original set if that's your preference.

Either way, please **acknowledge receipt** within 14 days so we can
file a record of disclosure in your firm record.

Best,
LocallyAI vendor

---

**Vendor operator instructions (delete before sending):**

1. Substitute `[firm name]`, `[version]`, `[date]` placeholders.
2. Send from the vendor's verified `@locallyai.co.uk` sender.
3. Wait for acknowledgement; record date + responder in
   `vendor-records/firms/<slug>-cs-log.md` as `telemetry-field-expansion-ack`.
4. If no acknowledgement after 14 days, follow up by phone before
   the release date.
5. If firm declines or wants partial fields, file a follow-up
   ticket in vendor-records to honour that preference (the Worker
   side has no per-firm field gating today; this would be a code
   change in `telemetry.py`).

# Vendor onboarding playbook

> The vendor's deep playbook for taking a firm from first-prospect-call
> to "fully onboarded — engagement closed". This chapter is **what the
> vendor does**, step by step, with scripts, decisions, conversation
> templates, and checklists.
>
> The pipeline structure (phases, timing, acceptance criteria) is the
> same one in [docs/sop/onboarding.md](../sop/onboarding.md) — that
> chapter is dual-audience (it appears in both SOPs). This chapter is
> vendor-only and dives into each phase from the vendor's perspective.

---

## Mental model

Onboarding takes the vendor through six "modes":

| Mode | What you're being | Phases (per [docs/sop/onboarding.md](../sop/onboarding.md)) |
|---|---|---|
| **Salesperson** | Curious, qualifying, honest about fit | 0 — pre-engagement |
| **Process owner** | Patient, structured, respectful of legal cycles | 1–2 — intake + DPA |
| **Engineer** | Methodical, paranoid, audit-passing | 3 — on-site install |
| **Coach** | Patient, hands-off, building firm self-sufficiency | 4–5 — client distribution + user provisioning |
| **Librarian** | Slow, careful with corpus quality | 6 — initial corpus ingestion |
| **Trainer** | Pedagogical, willing to repeat, written follow-up | 7 — handover & training |

Most onboardings fail (or wobble) when the vendor is in the wrong
mode — e.g. **engineer** mode during a discovery call (over-explaining
the architecture instead of listening for the actual problem), or
**salesperson** mode during install (talking up features instead of
running the audit script). The mode column above is the antidote.

---

## Phase 0 — Pre-engagement (vendor as salesperson)

Detailed sales mechanics live in [vendor-sales.md](vendor-sales.md).
This section covers what the **onboarding-side** vendor needs to know
about phase 0 — the bits that bleed into operational reality once a
deal is signed.

### What to capture for handoff to phase 1

Even before the DPA is signed, capture in the prospect's record:

- [ ] Firm legal name (exact spelling — affects firm_id hash)
- [ ] Primary IT contact name + email + phone
- [ ] Decision-maker name + role (often different from IT contact)
- [ ] Stated data region (UK / KSA / other) — drives DPA template
- [ ] Stated regulator(s)
- [ ] Anticipated install date (for capacity planning)
- [ ] Anything sensitive said on the discovery call
  ("we're under regulatory scrutiny right now") — context for DPA
  negotiation tone
- [ ] What model they want (most pick "we'll defer to vendor advice"
  — that's fine, but record it)

File in the sales tracker.

### Decision: do we onboard this firm at all?

Some firms are technically possible to onboard but operationally
unwise. Decline (politely) when:

- They want to use LocallyAI for **mass document review** at industrial
  scale — we're better for partner-grade workflows; mass review
  needs different tooling
- They want **multi-tenant** ("can we let our clients query into
  our docs?") — explicitly violates the per-firm isolation promise
- They want to **pin to a specific outdated model** indefinitely — we
  can't realistically support model versions older than ~2 years
- They want **no telemetry at all** AND **no quarterly check-in** AND
  **no incident reporting** — that's not enough vendor visibility to
  honour the 4h SLA

When declining, do it personally (not by email) and explain the
specific mismatch. Don't burn the bridge — the firm's needs may
change.

---

## Phase 1 — Intake (vendor as process owner)

The intake URL email goes out the moment the DPA is sent for
counter-signature (don't wait for signature — let intake and DPA
proceed in parallel).

### Send the intake URL

Use the email template in [docs/sop/onboarding.md
§1.1](../sop/onboarding.md#11-send-the-intake-url). One vendor-side
addition:

**CC the founder on the outbound email** (until single-person
operation ends). Reason: the founder needs visibility on every
prospect-to-firm conversion.

### What to do while you're waiting (typically 1–3 days)

The form takes ~10 minutes to fill but firms often take 1–3 days to
get to it. Vendor activities during the wait:

- [ ] Pre-stage the DPA template (UK or KSA) for sending — convert
      to PDF if you haven't already
- [ ] Pre-block calendar time for the install visit (estimate 4 hours
      on-site plus travel)
- [ ] Pre-stage the welcome 1Password vault (you'll add the firm's
      shared items here once you have a slug)
- [ ] Pre-stage a draft `vendor-records/firms/<slug>-cs-log.md` so the
      first row is ready when handover happens

### Don't chase too aggressively

If 5 days pass with no return: one polite chase. If 10 days: a phone
call to the IT contact. If 14 days: escalate to the decision-maker.

Many firms have IT teams that operate slowly by design (security
posture). The pause is usually not disinterest — it's process.

### When the form returns (vendor's first 30 minutes)

Both files in inbox:

```
firm-profile-<slug>.md     ~10 KB
install-<slug>.env         ~500 bytes
```

First 30 minutes:

1. **Scan the .md** for any unexpected disclosures — DPO contact, a
   mention of an existing AI tool that's being replaced, a hardware
   constraint we didn't anticipate. Note in your shift log.
2. **Verify the firm_id hash** per phase 2 step 2.1.
3. **Confirm the install date** is still on. Email the IT contact:
   "Got the intake — install date still confirmed for [date]?"
4. **Move into phase 2.**

---

## Phase 2 — Vendor processing (vendor as process owner)

This is the most script-heavy phase. Each step is a discrete vendor
action.

### 2.1 Hash verification (paranoia-driven)

```sh
FN="<firm legal name from H1, exact>" python3 -c \
  "import os, hashlib; print(hashlib.sha256(f'locallyai-firm:{os.environ[\"FN\"]}'.encode()).hexdigest()[:16])"
```

The output **must** match the value in the .md's "Anonymised firm_id"
line. If it doesn't:

- **Most likely cause**: firm IT typed the legal name slightly
  differently in the form than what's now in the H1. The H1 is what
  appears in `vendor-records/firms/<slug>.md` as the canonical value;
  the firm_id was hashed from what they typed in the form.
- **Action**: phone the IT contact. Ask them to confirm the canonical
  legal name. Re-hash. If the new hash matches what's in the .md →
  proceed with the corrected name (edit the H1 in the .md). If it
  doesn't → ask them to re-fill the form with the correct name.

Do **not** silently proceed when hashes don't match. The firm_id is
the routing identifier for telemetry; a wrong value means the dashboard
shows the wrong firm.

### 2.2 File the profile to vendor-records

```sh
cd ~/locallyai-vendor-records
cp ~/Downloads/firm-profile-<slug>.md firms/<slug>.md
git add firms/<slug>.md
git commit -m "onboard <firm_name> (<firm_id>)"
git push
```

Naming convention: the slug is the firm name in lowercase with
hyphens. Match the slug in the filename to keep grep useful.

### 2.3 Run scripts/onboard_firm.sh

```sh
cd /path/to/locallyai
bash scripts/onboard_firm.sh ~/Downloads/firm-profile-<slug>.md
```

What you see (output banner):

```
  ════════════════════════════════════════════════════════════════════
   ✓ Registered: <Firm Legal Name>
     firm_id:  <16-hex>

   Telemetry token (share via 1Password / signed PGP — never plain mail):

     <64-hex>

   Firm IT pastes this into .env on the office Mac as:

     LOCALLYAI_TELEMETRY_TOKEN=<64-hex>

  ════════════════════════════════════════════════════════════════════
```

**Immediately**:

1. Copy the token to clipboard.
2. Open 1Password, create a new entry titled `<Firm slug> — telemetry token`.
3. Paste the token into the entry's password field.
4. Generate a 1Password share link with 24h expiry.
5. **Clear clipboard** (1Password does this automatically; verify).

The token will not be shown again — don't lose this 30-second window.

### 2.4 Send the DPA

If you haven't already (you should have, in phase 0):

```sh
cp DPA_DRAFT.md /tmp/dpa-<slug>.md     # or DPA_DRAFT_SA.md for KSA
# Edit /tmp/dpa-<slug>.md to fill in:
#   - Firm legal name + address (from the firm-profile.md)
#   - Vendor name + address
#   - DPA effective date
#   - Sub-processor list (per vendor-sub-processors.md)
# Convert to PDF — Pages, Word, or pandoc
pandoc /tmp/dpa-<slug>.md -o /tmp/dpa-<slug>.pdf
```

Sign vendor-side first using your preferred PDF-signing tool.

Send to firm via email with this template:

> **Subject:** LocallyAI — Data Processing Agreement for review &amp; signature
>
> Hi [name + DPO if separate],
>
> Please find attached the DPA for our LocallyAI engagement. We have
> signed the vendor side; please review, sign your side, and return.
>
> The DPA references [UK GDPR / KSA PDPL]. If your legal team has
> redlines, send them through and we'll work through them. Most firms
> sign without changes.
>
> Schedule 3 (sub-processors) is the most-asked-about section — happy
> to discuss any of those.
>
> Thanks.

### 2.5 Send the telemetry token

Email template:

> **Subject:** LocallyAI — telemetry token (handle as a credential)
>
> Hi [IT contact name],
>
> Here is the telemetry token for your office Mac. **Treat it like a
> password** — please don't paste it into chat or unencrypted email.
>
> 1Password share: [share link, 24h expiry]
>
> When the office Mac install happens, this token goes into `.env` as:
>
>     LOCALLYAI_TELEMETRY_TOKEN=<the token>
>
> If the share link expires before you've used the token, just reply
> and I'll re-share.
>
> Thanks.

### 2.6 Acceptance criterion for phase 2

Before moving on:

- [ ] Hash verified
- [ ] Profile filed in `vendor-records/firms/`
- [ ] `firms-issued.log` updated (script does this)
- [ ] Audit log committed: `cd ~/locallyai-vendor-records && git add firms-issued.log && git commit -m "log: issued token for <firm_name> (<firm_id>)" && git push`
- [ ] DPA sent for counter-signature
- [ ] Telemetry token shared via 1Password
- [ ] Calendar reminder set for the install visit

---

## Phase 3 — On-site install (vendor as engineer)

This is the only phase that requires physical travel. Treat it like a
production deployment, not a meeting.

### Vendor laptop kit (pack the night before)

- [ ] Founder's daily-driver Mac (charged + cable)
- [ ] USB-C hub (HDMI, USB-A, ethernet)
- [ ] Wired Ethernet cable (~3 m) — install Wi-Fi can be flaky
- [ ] USB-A flash drive with `install-<slug>.env` and a copy of the
      latest LocallyAI .dmg / .msi (in case the office Mac can't
      reach GitHub)
- [ ] Phone with TOTP authenticator + monitor + kill-switch entries
- [ ] Backup phone (or yubikey) in case primary phone fails
- [ ] Printout of the relevant install chapter (setup-mac-single.md or
      setup-mac-ha.md or setup-windows.md) — Wi-Fi may be down
- [ ] Printout of this chapter (vendor-onboarding.md) for reference
- [ ] Business cards
- [ ] Notebook + pen for shift notes (some firms ban screenshots in
      certain rooms)

### Pre-arrival call (morning of install)

Phone the IT contact:

- "I'm leaving in [X] minutes; my ETA is [time]"
- "Can you confirm the office Mac is powered on and on the network?"
- "Can you confirm the Mac has at least 200 GB free disk?"
- "Can you confirm the IT contact (you) will be on-site for the
  install?"

If any of those are no, decide whether to proceed or postpone. (Going
on-site to discover the Mac doesn't have disk space wastes a day.)

### On arrival

1. **Sign in at reception** — many law firms have visitor logs that
   become part of the firm's compliance evidence. Use your full name +
   "LocallyAI vendor".
2. **Be escorted to the Mac** — don't wander. Many firms have rooms
   with strict access (partner offices, file rooms) that vendor isn't
   cleared for.
3. **Verify physical context** — note the Mac's location for the
   firm-profile.md if it differs from what was declared in intake.
4. **Photograph the Mac (with IT contact's permission)** — model
   sticker, RAM badge, S/N. Useful later for hardware-replacement
   planning. Save to `vendor-records/firms/<slug>-photos/` (private repo).
5. **Connect via Ethernet** if possible — vendor's own Wi-Fi access
   is often a hassle in law firms.

### Pre-stage the .env

Two paths — pick one:

**Path A — bootstrap one-liner (recommended).** The form's *Generate
install command* button produces a curl invocation backed by a
**single-use, 7-day-expiry** install token. The intake blob is stored
server-side in the monitor Worker's `INTAKE_TOKENS` KV namespace; the
office Mac fetches it via the token, which the Worker atomically
marks consumed on first read. Replayed commands (intercepted email,
shared shell history, shoulder-surfed) get HTTP 410.

On-site IT pastes the form's command into Terminal; the bootstrap
prompts interactively for the deploy key + telemetry token (paste
from 1Password — never echoed, never via env).

For first installs (and any production firm), use the **verified**
form of the command which the form also displays under "Verify the
bootstrap before running":

```sh
curl -fsSL https://raw.githubusercontent.com/LocallyAI/locallyai/main/docs/release-signing-key.gpg | gpg --import 2>/dev/null
curl -fsSL https://locallyai-monitor.<vendor-cf>.workers.dev/bootstrap     -o /tmp/locallyai-bootstrap
curl -fsSL https://locallyai-monitor.<vendor-cf>.workers.dev/bootstrap.sig -o /tmp/locallyai-bootstrap.sig
gpg --verify /tmp/locallyai-bootstrap.sig /tmp/locallyai-bootstrap \
  && LOCALLYAI_INTAKE="$(curl -fsSL https://locallyai-monitor.<vendor-cf>.workers.dev/onboarding/intake?t=<TOKEN>)" \
       bash /tmp/locallyai-bootstrap
```

Both variants consume the same single-use token. If the install fails
partway through, the firm IT regenerates a fresh command from the form
(takes ~10 seconds). The previous token is dead even if not consumed,
so a stuck install can't accidentally complete from a stale link.

**Path B — manual git clone (fallback).** If the firm's network can't
reach the monitor Worker (rare), or you prefer the traditional flow:

```sh
git clone git@github.com:LocallyAI/locallyai.git ~/locallyai
cd ~/locallyai
cp /path/to/usb/install-<slug>.env .env
echo "LOCALLYAI_TELEMETRY_TOKEN=<paste from 1P>" >> .env
cat .env | grep -E "LOCALLYAI_(FIRM_NAME|DATA_REGION|TELEMETRY|UPDATE_CHANNEL|OFFICE_SUBNET)"
```

**When to prefer A vs B**:

- **A** for any firm where the office Mac has internet access at
  install time. Less time on-site, fewer manual paste errors, deploy
  key never written to disk during clone (only held by the temporary
  ssh-agent).
- **B** when the firm wants to inspect every step before running
  anything, or when the firm's office network whitelist doesn't yet
  permit the monitor Worker URL.

### Run install.sh

Per the topology chapter ([setup-mac-single.md](../sop/setup-mac-single.md)
/ [setup-mac-ha.md](../sop/setup-mac-ha.md) /
[setup-windows.md](../sop/setup-windows.md)).

The installer is well-documented; this section covers the **vendor's**
specific concerns:

- **Don't skip the audit step** — `bash scripts/audit_install.sh` after
  install. If it doesn't say `pass=14 warn≤1 fail=0`, debug before
  moving on.
- **Don't skip the egress audit** — `bash scripts/audit_egress.sh`.
  Should print PASS. If not, install LuLu (free firewall) per
  [data-isolation.md](../sop/data-isolation.md§egress-allowlist).
- **Don't run start_locallyai.sh as root** — even if the install
  prompted for sudo elsewhere, the daily start script must run as
  the firm's IT user.
- **First start**: launch via the one-click `LocallyAI Worker.app` and
  `LocallyAI Manager.app` from the firm's IT user (not your vendor
  laptop). The launchd LaunchAgents register against that user.

### Verify telemetry round-trip

Before leaving site:

```sh
cd /path/to/locallyai
.venv/bin/python -m telemetry post
# Expected: ok=True
```

Then on your phone or laptop, sign in to the monitor dashboard. The
firm's card should appear within ~5 min with a green dot and the
firm_id hash you registered. **Don't leave site until the firm appears
on the dashboard.** If it doesn't, debug:

- Is `LOCALLYAI_TELEMETRY=1` set in .env?
- Is `LOCALLYAI_TELEMETRY_TOKEN` set + correct?
- Does `.venv/bin/python -m telemetry post` print ok=True?
- If ok=False with HTTP 401 → token mismatch. Re-check the token
  pasted into .env exactly matches the 1Password share value.
- If ok=False with HTTP 5xx → Worker side issue; check
  `npx wrangler tail` from your laptop.

### Pre-departure checklist

Before leaving site:

- [ ] healthz green: `curl -k https://localhost:8000/healthz`
- [ ] audit_install.sh: pass
- [ ] audit_egress.sh: PASS
- [ ] First telemetry post: ok=True
- [ ] Firm visible on monitor dashboard with green dot
- [ ] First admin user created via `.venv/bin/python manage_users.py add --tier admin --name "..."`
- [ ] IT contact has logged into the manager UI successfully (firm
      pill shows correct firm name)
- [ ] One-click start (`LocallyAI Worker.app`) tested as the firm's IT user
- [ ] One-click stop (`Stop LocallyAI.app`) tested
- [ ] One full restart of the launchd daemons (verifies KeepAlive)
- [ ] All your removable media + printouts collected
- [ ] Visitor log signed out at reception

### Post-departure (back at base)

- [ ] Update `vendor-records/firms/<slug>.md` with:
  - Install date
  - Photos (committed to vendor-records, not anywhere else)
  - Anything that diverged from the intake (different room location,
    different macOS version than declared, etc.)
- [ ] Update `vendor-records/firms-issued.log` if you rotated tokens
      on-site
- [ ] First entry in `vendor-records/firms/<slug>-cs-log.md` (per
      [vendor-customer-success.md](vendor-customer-success.md))
- [ ] Email the IT contact: "Install complete. Dashboard shows healthy.
      Talk soon."

---

## Phase 4 — Client app distribution (vendor as coach)

The vendor's role here is mostly **availability**. Firm IT is doing the
work — pushing .app/.msi via their MDM. Vendor-side tasks:

### Pre-departure briefing (during phase 3)

Walk firm IT through:

- Where to download the .dmg / .msi from on the office Mac
  (`/manager` → Downloads page)
- The MDM-specific notes in [client-install.md](../sop/client-install.md)
  for whichever MDM they declared in intake (Jamf / Munki / Intune / GP)
- The "config-plant" script that pre-stages the office Mac URL so end
  users skip the first-launch prompt
- What end users will see: "macOS may show a security warning the first
  time — right-click → Open. After that, normal launch."

### Vendor-side standby

For the first 3 days after install: be reachable by phone. Most MDM
issues fire in this window.

Common issues + fixes:

| Symptom | Cause | Fix |
|---|---|---|
| End user sees a blank screen | mDNS doesn't resolve `office-mac.local` from the user's network | Pre-stage the office Mac IP via the config-plant script |
| End user can't sign in | They have the wrong admin key | Verify against `vendor-records/firms-issued.log` (admin keys) |
| App won't launch on Windows | SmartScreen | "More info → Run anyway" — document for future MDM push as auto-trust |

### Acceptance criterion for phase 4

- [ ] At least 3 staff laptops have the LocallyAI Worker app installed
- [ ] Each successfully signs in with a worker key
- [ ] Each shows the firm's isolation pill correctly

---

## Phase 5 — User provisioning (vendor as coach)

Walk firm IT through user creation **once**; they do the rest.

### The walkthrough

Sit with IT (in person or video call):

```sh
# Add an admin user
.venv/bin/python manage_users.py add --tier admin --name "Jane Smith (IT lead)"
# Output: prints an admin key — save to firm's password manager IMMEDIATELY

# Add a worker user
.venv/bin/python manage_users.py add --tier worker --name "John Smith (lawyer)"
# Output: prints a worker key — share via firm's chosen secure method
```

Show:

- Where the keys appear (stdout — copy immediately)
- That keys are **never** retrievable later (forget = revoke + reissue)
- The manager UI Users panel as an alternative to CLI

Then **leave them to it**. Don't create users for them — vendor
shouldn't see firm staff names. If IT insists vendor handles user
creation: politely decline, citing the no-vendor-data-access posture
in the DPA.

### Acceptance criterion for phase 5

- [ ] At least one admin key created and verified working
- [ ] At least 3 worker keys created and verified working
- [ ] Vendor has not seen any of the worker users' identities (only
      pseudonymised hashes in any audit data we observe in dashboard)

---

## Phase 6 — Initial corpus ingestion (vendor as librarian)

This is firm work — they choose what to ingest. Vendor's role:

### Pre-ingestion advice (briefly, during phase 7 handover)

- "Start small — ingest 100 documents, run a few queries, get a feel
  for retrieval quality. Then scale up."
- "Avoid one-shot dumping a whole DMS — incremental is easier to
  troubleshoot."
- "If you ingest documents in multiple languages (e.g., English +
  Arabic for KSA), test queries in each language before declaring
  the corpus complete."
- "Re-ingestion is cheap. Don't be precious about getting it right
  first time."

### Vendor-side standby during ingestion

Watch the dashboard. A large corpus ingestion often fires the
`disk_pressure_clean` self-healer if the Mac wasn't pre-sized
adequately. If you see that fire repeatedly, schedule a hardware
upgrade conversation.

### Acceptance criterion for phase 6

The firm's manager runs a query against the corpus and gets:

- [ ] `sources_retrieved > 0`
- [ ] A coherent answer that cites real firm documents
- [ ] No "no relevant sources" failure mode on common queries

If these don't hold after ingestion completes: investigate per
[incidents-service.md](../sop/incidents-service.md).

---

## Phase 7 — Handover &amp; training (vendor as trainer)

The most pedagogical phase. Block 2 hours; do it as a single video
call with all stakeholders present.

### Stakeholders to invite

- Firm IT primary (technical owner)
- Firm IT secondary (succession on firm side)
- Firm DPO (compliance owner)
- One representative lawyer (UX feedback)
- Optional: managing partner (signals importance to staff)

### Agenda (with vendor's slides — keep simple)

#### Slide 1 — Welcome (2 min)

- Acknowledge the install milestone
- Restate the SLA (4h critical incident response)
- Restate the comms channels (vendor email, vendor phone for incidents)

#### Slide 2 — Daily ops walkthrough (30 min, with IT)

Hands-on:

- One-click start: vendor demos starting via `LocallyAI Worker.app`
- One-click stop: vendor demos stopping via `Stop LocallyAI.app`
- Where logs live: `logs/api.log`, `logs/audit.log`
- The healthz check: `curl -k https://localhost:8000/healthz`
- What an alert looks like: vendor demonstrates by triggering a fake
  one (e.g., temporarily disabling the API to fire a healthz_kickstart
  alert) — only do this with IT's permission and only on a quiet
  weekday

After demo, **ask IT to do each step themselves** while vendor
watches. Catch any "I'm not sure where to click" moments.

#### Slide 3 — Compliance walkthrough (45 min, with DPO)

Hands-on:

- Subject-access request: walk through `manage_users.py export --user <name>`
- Erasure: walk through `manage_users.py erase --user <name>` (do it
  on a test user, not a real one)
- Audit export to PDF: manager UI `/audit` → Export
- RoPA fetch: `curl -kH "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/processing-record`
- Breach response: walk through the relevant chapter
  ([incidents-security.md](../sop/incidents-security.md) for UK,
  [compliance-saudi.md](../sop/compliance-saudi.md) for KSA)
- Where to find the DPA: hand over a printed signed copy in person
  if not already done

After demo, **ask DPO to run one audit export themselves** while
vendor watches.

#### Slide 4 — Lawyer walkthrough (15 min, with one user)

Hands-on:

- Sign in with their worker key
- Ask one factual question against their own documents
- Show the sources panel — emphasise that every answer cites real
  firm documents
- Show the conversation rename + delete features
- Ask them: "what's missing from this UX?" — capture for product
  backlog

#### Slide 5 — Q&A + close (10 min)

Open floor. Capture every question — even trivial ones — into the
firm's CS log.

### Leave-behind email (send same day)

Use the template in [docs/sop/onboarding.md
§7.4](../sop/onboarding.md#74-leave-behind). Customise:

- The first 30 days checklist (some items may be done already)
- Vendor's specific contact for routine questions vs incidents
- Any commitments made during the call (e.g., "we'll send a portability
  export research note within 2 weeks")

### Acceptance criterion for phase 7

This is the **engagement close-out**. All of the following must be
green:

- [ ] Firm profile filed in `vendor-records/firms/<slug>.md`
- [ ] DPA counter-signed and filed in `vendor-records/dpas/`
- [ ] Office Mac visible on monitor dashboard with green heartbeat
- [ ] At least one admin user + 3 worker users created
- [ ] Sample query returns sources + coherent answer
- [ ] IT has run `bash scripts/start_locallyai.sh` themselves once
- [ ] IT has run `bash scripts/audit_install.sh` themselves once
- [ ] DPO has run one audit export themselves
- [ ] Calendar reminder set for 12-month profile re-confirmation
- [ ] First entry in `vendor-records/firms/<slug>-cs-log.md` written

If any are not green, the engagement is not closed. Schedule a
follow-up to close them out. Do **not** declare the firm "live" with
open items.

---

## Vendor sign-off

When all acceptance criteria above are green, append to
`vendor-records/firms/<slug>.md`:

```markdown

---

## Onboarding sign-off

- **Phase 7 acceptance**: YYYY-MM-DD
- **Vendor signing off**: <vendor name>
- **Firm acknowledged by**: <IT primary name> + <DPO name>
- **First-30-days plan emailed**: YYYY-MM-DD
- **First annual review due**: YYYY-MM-DD

Engagement closed. Firm enters steady state per [vendor-customer-success.md](../../vendor-sop/vendor-customer-success.md).
```

This is the moment the firm transitions from "onboarding" to "live".
The on-call engineer takes over from the onboarding-lead.

---

## Common variations

### KSA firms

Insert phase 3a between phases 3 and 4:

- Read [setup-saudi.md](../sop/setup-saudi.md) on-site to apply Arabic
  UI strings, RTL layout, Hijri date helpers, and the KSA demo corpus
- The intake form's "Data residency region = KSA" should have set
  `LOCALLYAI_DATA_REGION=KSA` in install-<slug>.env automatically;
  verify this is set before install.sh
- DPA template differs (`DPA_DRAFT_SA.md`)
- Breach playbook differs ([compliance-saudi.md](../sop/compliance-saudi.md))
- Phase 7 compliance walkthrough uses Saudi-specific procedures

### HA fleets (2-node Mac)

Phase 3 uses [setup-mac-ha.md](../sop/setup-mac-ha.md) instead of
setup-mac-single.md.

The intake form already captures both Macs in the hardware section.
Vendor's additional concerns:

- Both Macs need to be on at the time of install
- Pairing step requires both to see each other on the LAN
- Verify failover during phase 3 (kill the primary; verify secondary
  takes over within 5s) — do this on-site, not as a homework task

### Pre-existing Mac (firm already owns the hardware)

Skip the hardware-procurement assumption in phase 0. Add an extra
audit step at the start of phase 3:

```sh
bash scripts/audit_install.sh --pre-flight
```

Checks the Mac is clean of conflicting Python / Ollama / MLX installs.
Common findings on pre-existing Macs:

- Homebrew Python conflicts with .venv (usually OK, but flag)
- Old Ollama installs with downloaded models that aren't ours (advise
  removal to free disk)
- Existing GPG keys in keychain (warn the user before pinentry-mac
  config changes)

### Air-gapped firms (no telemetry opt-in)

- Phase 2.3 (monitor Worker registration) is skipped
- Phase 2.5 (telemetry token send) is skipped
- Phase 3 verification step changes: instead of monitor dashboard,
  verify by tailing `logs/api.log` on the office Mac during install
- Phase 8 (steady state) changes per [vendor-customer-success.md
  §air-gapped](vendor-customer-success.md): vendor cannot detect issues
  remotely; firm must call in. Add a fortnightly check-in call
  instead of dashboard watching.

---

## Common onboarding failures (and how to recover)

### "We forgot to ingest the corpus"

You completed phase 7 and discovered firm hasn't actually loaded
documents. Phase 6 was skipped or done incompletely. Recovery:

- Schedule a separate ingestion-day call (1 hour)
- Walk firm manager through bulk-ingest.md UX
- Vendor watches dashboard while ingestion runs
- Re-acceptance phase 6 criterion before declaring sign-off

### "The DPA still isn't signed"

Engagement is technically not legal until DPA is signed. If you find
yourself in phase 5+ without a signed DPA:

- Pause user provisioning if not already done
- Phone the firm's decision-maker: "I need to flag we shouldn't be
  going further without the signed DPA"
- Most cases: legal review is just slow; firm signs within 1–2 weeks
- Edge case: firm wants substantive redlines now that they've used the
  product — these are negotiable but slow it down further

### "The IT contact left the firm"

You discover during phase 4 or 5 that the named IT primary is gone.
Recovery:

- Phone the secondary IT contact (you captured this in intake)
- Re-run the intake form **partially** — at minimum, get a fresh
  contact card
- Rotate the admin keys if the departed IT had any (assume worst case)
- Schedule a fresh handover call with the new primary

### "Telemetry isn't reaching the dashboard"

You're in phase 3 and the firm doesn't appear on the dashboard. Debug
chain:

1. Is `LOCALLYAI_TELEMETRY=1` set?
2. Is `LOCALLYAI_TELEMETRY_TOKEN` set + correct?
3. `.venv/bin/python -m telemetry post` — output?
4. If 401 → token mismatch; re-check
5. If 5xx → Worker issue; `npx wrangler tail` from your laptop
6. If everything looks right but dashboard is empty → check the
   monitor's KV directly via wrangler:
   ```sh
   cd docs/monitor/cloudflare-worker
   npx wrangler kv key list --binding FIRM_STATE
   ```

If after 30 min you can't find the issue: leave site with the firm in
"live but unmonitored" state, finish the install otherwise, debug from
base. Email IT confirming this is the state.

---

## Onboarding metrics worth tracking

Per onboarded firm, capture in `vendor-records/onboarding-metrics.md`:

- Days from LOI signature to phase-7 close
- Days in DPA legal review (subset of above)
- Phase-7 acceptance completion: any items not green at first attempt?
- Total vendor hours invested (sales + processing + on-site + handover)
- Vendor on-site travel cost
- Any "common failures" from above triggered

These metrics drive vendor capacity planning. If the median onboarding
takes 12 working days at 20 vendor hours, vendor capacity is the
bottleneck for growth — and that's worth knowing before signing the
6th firm.

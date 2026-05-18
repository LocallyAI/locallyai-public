# Vendor onboarding — end-to-end pipeline

> **Audience:** vendor team only. Firm-side roles (IT, DPO, partners)
> appear by name in each phase but never read this chapter.
>
> **Purpose:** the master runbook that orders every onboarding step from
> first prospect call to steady-state operation. Every phase links to
> the chapter that holds the click-by-click detail; this chapter is the
> sequence + acceptance criteria, not the per-step keystrokes.
>
> **For the deep vendor-side playbook** — scripts to run, conversation
> templates, on-site checklists, vendor laptop kit, recovery from
> common onboarding failures — see [VENDOR_SOP V8 — Onboarding
> playbook](../vendor-sop/vendor-onboarding.md). That chapter is the
> vendor's working document during an actual onboarding; this one is
> the high-level sequence shared with anyone who needs to understand
> the pipeline.

---

## Pipeline at a glance

| Phase | What | Owner | Wall-clock | Reading |
|---|---|---|---|---|
| 0 | Pre-engagement | Vendor sales/legal | ~2 weeks (firm-driven) | this chapter |
| 1 | Intake | Firm IT (vendor sends URL) | ~10 min | this chapter |
| 2 | Vendor processing | Vendor on-call | ~1 hour | this chapter |
| 3 | On-site install | Vendor engineer + firm IT | 2–4 hours | [setup-mac-single.md](setup-mac-single.md) / [setup-mac-ha.md](setup-mac-ha.md) |
| 4 | Client app distribution | Firm IT | ~1 day to push, async | [client-install.md](client-install.md) |
| 5 | User provisioning | Firm IT (with vendor) | ~30 min | [daily.md](daily.md) |
| 6 | Initial corpus ingestion | Firm manager | hours–days | [bulk-ingest.md](bulk-ingest.md) |
| 7 | Handover & training | Vendor + firm IT/DPO | ~2 hours | this chapter |
| 8 | Steady state + annual review | Vendor + firm | ongoing | [vendor-monitoring.md](vendor-monitoring.md), [maintenance.md](maintenance.md) |

**Total elapsed time** from "firm signs LOI" to "users productive": typically
**3–5 working days** if firm IT is responsive. Phase 3 is the only step
that requires an on-site visit; everything else is remote.

**Acceptance criterion for "fully onboarded"** (close-out at end of
phase 7): all of the following are green.

- [ ] Firm profile filed in `vendor-records/firms/<slug>.md`
- [ ] DPA counter-signed and filed in `vendor-records/dpas/`
- [ ] Office Mac visible on monitor dashboard with green heartbeat
- [ ] At least one admin user + one worker-tier user created and tested
- [ ] First retrieval query returns sources and a coherent answer
- [ ] Firm IT has run `bash scripts/start_locallyai.sh` themselves once
- [ ] Firm DPO has run an audit export themselves once
- [ ] Calendar reminder set for 12-month profile re-confirmation

---

## Phase 0 — Pre-engagement (vendor side)

Before the intake form goes out, the firm needs enough material to
green-light the engagement.

1. **Initial call** with the firm's managing partner, COO, or general
   counsel. 30 min. Pitch the on-premises posture: their data never
   leaves their Mac, no shared infrastructure, vendor cannot read their
   documents.

2. **Send pre-engagement pack** as a single email:
   - Capability deck (`docs/sales/capability-deck.pdf`)
   - Security overview (`docs/sales/security-overview.pdf` — distilled
     from `data-isolation.md`)
   - Sample DPA: `DPA_DRAFT.md` (UK) or `DPA_DRAFT_SA.md` (KSA)
   - The signed-SOP PDF as the deepest reference (`dist/locallyai-sop-*.pdf`)

3. **Decision call** (firm-driven; usually 1–2 weeks later). On a
   green-light:
   - Vendor confirms data region (UK / KSA / other)
   - Firm names a primary IT contact
   - Vendor moves to phase 1

**Acceptance criterion**: vendor has the firm IT primary contact's email
and a written go-ahead (email is fine — DPA gets signed later).

---

## Phase 1 — Intake (firm IT, ~10 min)

### 1.1 Send the intake URL

Email template — **copy-paste**, edit only the bracketed parts:

> **Subject:** LocallyAI onboarding — quick intake form (~10 min)
>
> Hi [IT contact name],
>
> Before we install the office Mac, please fill out our onboarding
> intake form so we have everything we need to support you under the
> 4-hour SLA:
>
> [https://locallyai-monitor.your-cf-account.workers.dev/onboarding.html](https://locallyai-monitor.your-cf-account.workers.dev/onboarding.html)
>
> When you click *Generate &amp; download* at the bottom, you'll get two
> files. Please email both back to this address:
>
> 1. `firm-profile-<your-firm-slug>.md` — vendor records
> 2. `install-<your-firm-slug>.env` — the office Mac install
>
> Nothing leaves your browser until you click that button — the form
> doesn't submit anywhere. Takes about 10 minutes.
>
> Let me know if any field is unclear.

### 1.2 What firm IT does

Firm IT opens the URL, fills:

- Identity (firm legal name, primary + secondary IT contacts, DPO,
  time zone, office hours)
- Hardware (one or more Macs — model, RAM, storage, macOS, role; UPS,
  internet, AC)
- Network (subnet, proxy, MDM, backup, mDNS test)
- Compliance (data region, regulators, DPA status, retention)
- Users (worker count, admin count, corpus size, languages)
- Telemetry &amp; updates (opt-in default ON, channel default `stable`)

Clicks **Generate &amp; download**, gets two files, emails them back.

**Acceptance criterion** (vendor inbox): both files received; firm name
in both filenames matches the H1 inside the .md.

---

## Phase 2 — Vendor processing (~1 hour)

### 2.1 Verify the firm_id hash

Open `firm-profile-<slug>.md`, find:

```
- **Anonymised firm_id**: `a1b2c3d4e5f60718`
```

Re-compute locally to confirm:

```sh
FN="Acme Solicitors LLP" python3 -c "import os, hashlib; print(hashlib.sha256(f'locallyai-firm:{os.environ[\"FN\"]}'.encode()).hexdigest()[:16])"
```

Both values must match. If not, the firm typed a different legal name in
the form than what's now in the H1 — confirm the canonical name out-of-band
before proceeding.

### 2.2 File the profile to vendor-records

```sh
cd ~/locallyai-vendor-records   # private repo, see vendor-monitoring.md§Account-separation
cp ~/Downloads/firm-profile-<slug>.md firms/<slug>.md
git add firms/<slug>.md
git commit -m "onboard <firm_name> (<firm_id>)"
git push
```

### 2.3 Register in monitor Worker

**As of 2026-05-11, telemetry tokens are auto-issued by the monitor
Worker at form-submit time.** The form's *Generate install command*
button mints the token, registers it server-side, and embeds it in
the install blob. The bootstrap on the office Mac writes it straight
into `.env`. **No vendor command is needed for new onboardings.**

The legacy `scripts/onboard_firm.sh` path remains for:

- **Token rotation** on existing firms
- **Air-gapped firms** that didn't use the bootstrap
- **Vendor-side audit log** in `vendor-records/firms-issued.log`

Run the script in those cases:

```sh
cd /path/to/locallyai
bash scripts/onboard_firm.sh ~/Downloads/firm-profile-<slug>.md
```

The script:

1. Re-parses + re-verifies the firm_id (catches profile tampering).
2. Generates a fresh 32-byte hex telemetry token.
3. Merges into `~/.locallyai/vendor/firms-registry.json` (mode 0600 —
   local source of truth, since wrangler can't read secrets back).
4. Pushes the merged `FIRM_TOKENS` JSON to the monitor Worker via
   `npx wrangler secret put`.
5. Appends a row to `vendor-records/firms-issued.log` (firm_id +
   firm_name + timestamp + operator — **never the token value**).
6. Prints the new token in a banner for you to share with firm IT.

> **Re-running on a registered firm rotates the token** with confirmation.
> Old token stops working as soon as the wrangler push lands.
>
> **Back up `~/.locallyai/vendor/firms-registry.json`** — Time Machine to
> encrypted disk is fine. If lost, every firm has to swap their token.

After the script finishes, commit the audit-log update:

```sh
cd ~/locallyai-vendor-records
git add firms-issued.log
git commit -m "log: issued token for <firm_name> (<firm_id>)"
git push
```

### 2.4 Send the DPA

Pick the template by the firm's declared region:

- **UK** → `DPA_DRAFT.md` (England &amp; Wales, UK GDPR)
- **KSA** → `DPA_DRAFT_SA.md` (Royal Decree M/19, governing law Riyadh)

Convert to PDF, sign vendor-side, send for counter-signature. When the
signed PDF returns:

```sh
cd ~/locallyai-vendor-records
cp ~/Downloads/dpa-signed-<slug>.pdf dpas/<slug>-$(date +%F).pdf
# also update firms/<slug>.md DPA section with the signature date
git add dpas/<slug>-*.pdf firms/<slug>.md
git commit -m "DPA signed for <firm_name>"
git push
```

### 2.5 Securely share the telemetry token

Use **one of**:

- **1Password share** (preferred — auto-expiring link)
- **Signed PGP email**
- **In-person paste at the install visit** (deferred to phase 3)

**Never** plain email or Slack DM. Send with this template:

> **Subject:** LocallyAI — your telemetry token (handle as a credential)
>
> Hi [IT contact name],
>
> Here is the telemetry token for your office Mac. Treat it like a
> password — don't paste it into chat or email. It will be added to the
> office Mac's `.env` during install.
>
> Token: [via 1Password share — link below]
>
> Paste it as: `LOCALLYAI_TELEMETRY_TOKEN=<the token>` in `.env`.
>
> Thanks.

**Acceptance criterion**: profile filed, monitor Worker registered,
DPA en route to firm signatory, token shared via secure channel.

---

## Phase 3 — On-site install (2–4 hours on-site)

The vendor engineer travels to the firm with:

- The firm's `install-<slug>.env` on a USB or cloud drive
- The vendor laptop (with `~/.locallyai/vendor/firms-registry.json` and
  vendor-records cloned)
- A pinentry-mac install + GPG signing key for any release verification
- Phone with TOTP authenticator (kill-switch + monitor entries)

### 3.1 Choose the install chapter

| Topology | Chapter |
|---|---|
| Single Mac | [setup-mac-single.md](setup-mac-single.md) |
| 2-node Mac HA | [setup-mac-ha.md](setup-mac-ha.md) |
| Single Windows | [setup-windows.md](setup-windows.md) |
| KSA-specific | [setup-saudi.md](setup-saudi.md) **after** the topology chapter |

### 3.2 Pre-stage the .env (saves ~5 min)

Drop `install-<slug>.env` into the install directory as `.env` **before**
running `install.sh`:

```sh
cd /path/to/locallyai
cp ~/Downloads/install-<slug>.env .env
```

Then add the telemetry token (kept off the form for security):

```sh
echo "LOCALLYAI_TELEMETRY_TOKEN=<token-from-phase-2.5>" >> .env
```

The installer reads `LOCALLYAI_FIRM_NAME`, `LOCALLYAI_DATA_REGION`,
`LOCALLYAI_TELEMETRY`, `LOCALLYAI_UPDATE_CHANNEL`, `LOCALLYAI_OFFICE_SUBNET`
from env and skips the matching prompts.

### 3.3 Run install.sh

Follow the topology chapter step-by-step. Expected wall-clock:

- Single Mac: ~30–45 min including model download
- 2-node HA: ~1.5–2 hours including pairing

### 3.4 Verify telemetry round-trip

Once the API is healthy:

```sh
cd /path/to/locallyai
.venv/bin/python -m telemetry post
# expected: ok=True
```

Then check the monitor dashboard — within ~5 min the firm card should
appear with a green dot and the firm_id hash you registered.

### 3.5 Run the install audit

```sh
bash scripts/audit_install.sh
# expected: pass=14 warn≤1 fail=0
bash scripts/audit_egress.sh
# expected: PASS
```

**Acceptance criterion**: API answers `https://localhost:8000/healthz`
locally; dashboard shows green for this firm; audit script passes;
LuLu egress allowlist installed (or `pf` rules in place for
single-purpose Macs).

---

## Phase 4 — Client app distribution (~1 day, async)

The vendor engineer can leave site after phase 3 — phase 4 is firm IT's
job, with vendor available by phone for questions.

Detailed procedure: [client-install.md](client-install.md).

### 4.1 IT downloads the .app / .msi

From the manager UI on the office Mac (`/downloads`), or directly from
the GitHub Releases page if the office mirror isn't set up yet.

### 4.2 IT pushes to staff laptops via MDM

Per-MDM steps in `client-install.md`:

- **Jamf** (most common at UK firms): policy + custom trigger
- **Munki**: pkginfo + manifest entry
- **Microsoft Intune** (KSA firms with Microsoft estate): line-of-business app
- **Group Policy** (Windows firms): MSI deployment

Pre-stage the office Mac URL via the `config-plant` script so end users
skip the first-launch prompt.

**Acceptance criterion**: at least 3 staff laptops show the LocallyAI
Worker app in their Applications folder; opening it shows the firm's
isolation pill in the login gate.

---

## Phase 5 — User provisioning (~30 min)

Detailed daily procedure: [daily.md](daily.md).

### 5.1 Create the first admin user

On the office Mac:

```sh
cd /path/to/locallyai
.venv/bin/python manage_users.py add --tier admin --name "Jane Smith (IT lead)"
# prints an admin key — save to firm's password manager immediately
```

### 5.2 IT logs into the manager UI

Using the printed key, IT signs into the manager UI at
`https://office-mac.local:8000/manager`. Confirms:

- Firm name pill in the header
- Vendor monitoring opt-in shown correctly
- Users panel renders without the "name.split is not a function" error
  (fixed in v3.5; if seen, vendor escalation)

### 5.3 IT creates worker-tier users

Either via the manager UI's Users panel, or in bulk via CLI:

```sh
.venv/bin/python manage_users.py add --tier worker --name "Lawyer Name"
```

Each user's key is shared via 1Password share to that lawyer.

**Acceptance criterion**: at least 3 worker keys issued; each lawyer
can sign into the worker app on their laptop.

---

## Phase 6 — Initial corpus ingestion (variable)

Detailed procedure: [bulk-ingest.md](bulk-ingest.md).

The firm's manager (one of the admin-tier users) drag-drops the firm's
document archive into the manager UI's `/documents` page. Indexing runs
asynchronously; the ticker shows progress.

Sizing reference:

- **Small** (<1k docs, mostly PDFs): minutes to ~1 hour
- **Medium** (1k–100k): hours, often overnight
- **Large** (>100k): a day or two — vendor checks in by phone

**Acceptance criterion**: a sample query against the ingested corpus
returns `sources_retrieved > 0` and a coherent answer citing real
firm documents.

---

## Phase 7 — Handover &amp; training (~2 hours)

Combined session — vendor on the call, firm IT + firm DPO + a
representative lawyer from the firm side.

### 7.1 Daily ops walkthrough (30 min, with IT)

- One-click start: `LocallyAI Worker.app` and `LocallyAI Manager.app`
- One-click stop: `Stop LocallyAI.app`
- Where the logs live: `logs/api.log`, `logs/audit.log`
- The healthz check: `curl -k https://localhost:8000/healthz`
- What an alert looks like (vendor demonstrates by triggering a fake
  one if there's appetite)

### 7.2 Compliance walkthrough (45 min, with DPO)

- Subject-access request: `manage_users.py export --user <name>`
- Erasure: `manage_users.py erase --user <name>`
- Audit export to PDF: manager UI `/audit` → Export
- RoPA fetch: `curl -kH "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/processing-record`
- Breach response: walk through the relevant chapter
  ([incidents-security.md](incidents-security.md) for UK,
  [compliance-saudi.md](compliance-saudi.md) for KSA)

### 7.3 Lawyer walkthrough (15 min, with one user)

- Sign in with their worker key
- Ask one factual question against their own documents
- Confirm sources panel shows real document names
- Show the conversation rename + sources panel polish

### 7.4 Leave-behind

Email after the call:

> **Subject:** LocallyAI — your first 30 days
>
> Hi all,
>
> Below is your 30-day checklist. Most items are vendor-driven (we'll
> contact you proactively); the few firm-side ones are starred.
>
> **Day 0–7**
> - Vendor watches dashboard for warning patterns ⏵ daily
> - ⭐ IT runs the office Mac through one full restart cycle to verify launchd KeepAlive
> - ⭐ DPO runs one practice audit export
>
> **Day 8–30**
> - Vendor confirms first stable update applied cleanly
> - ⭐ IT pushes the worker app to remaining staff laptops
> - ⭐ Manager finalises the document corpus (rolling re-ingest if needed)
>
> **Day 30**
> - Vendor 30-day review call (we'll book it)
>
> Anything urgent: [vendor-emergency-line]. Anything routine:
> [vendor-on-call email].

**Acceptance criterion**: all four checklist items in the
[overview](#pipeline-at-a-glance) checklist are green. Engagement
closed; firm enters steady state.

---

## Phase 8 — Steady state + annual review

### 8.1 Daily

- Vendor on-call watches the [monitor dashboard](vendor-monitoring.md)
- Self-healers handle most issues (healthz_kickstart, ollama_restart,
  disk_pressure_clean) without paging
- Critical alerts page on-call within 4 hours per SLA

### 8.2 Per-update (every 1–4 weeks)

Vendor releases via `scripts/release_server.sh dev → promote`. Tier A
auto-applies after 24h soak; tier B needs the firm's maintenance
window. Detailed flow: [updates.md](updates.md).

### 8.3 Quarterly (optional)

- Rotate telemetry token: `bash scripts/onboard_firm.sh <profile>`
  with the existing profile (script prompts to rotate)
- Verify GPG release-signing key still in firm's trustdb
- Rotate `LOCALLYAI_AUDIT_HMAC_KEY` per [maintenance.md](maintenance.md)

### 8.4 Annual

Calendar reminder fires 12 months from intake. Vendor:

1. Re-sends the intake URL with subject "annual confirmation".
2. Compares the new profile to the filed one; updates anything that
   changed (contacts, hardware, DPO, etc.).
3. Re-issues the DPA if approaching its term.
4. Re-files in vendor-records: `firms/<slug>.md` overwritten with a
   commit message like `annual review 2027 — <firm_name>`.

---

## Common variations

### KSA firms

Insert phase 3a between phases 3 and 4: read [setup-saudi.md](setup-saudi.md)
to apply Arabic UI strings, RTL layout, Hijri date helpers, and the
KSA demo corpus. The intake form's "Data residency region = KSA"
selection drives `LOCALLYAI_DATA_REGION=KSA` in the install env, which
triggers most of these automatically; setup-saudi.md covers the
manual checks.

DPA template differs (`DPA_DRAFT_SA.md`); breach playbook differs
([compliance-saudi.md](compliance-saudi.md)).

### HA fleets

Phase 3 uses [setup-mac-ha.md](setup-mac-ha.md) instead of
setup-mac-single.md. The intake form already captures both Macs in
the hardware section. Phase 5 onwards is identical — users see one
hostname; failover is invisible.

### Pre-existing Mac (firm already owns the hardware)

Skip the hardware-procurement assumption in phase 0. Add an extra
audit step at the start of phase 3:

```sh
bash scripts/audit_install.sh --pre-flight
```

This checks the Mac is clean of conflicting Python / Ollama / MLX
installs that could interfere.

### Air-gapped firms (no telemetry opt-in)

Phase 2.3 (monitor Worker registration) is skipped. Phase 8.1 changes:
vendor cannot detect issues remotely; firm must call in. Add to phase
7 leave-behind: the vendor on-call number gets a fortnightly check-in
call instead of dashboard watching.

---

## What's NOT in this pipeline

- **Admin keys** — issued during phase 5, not collected at intake.
- **TOTP secrets / GPG keys** — vendor-side credentials. Never
  collected from the firm.
- **Document content / user names / queries** — operational data.
  Lives only on the firm's own Mac, never in vendor records.

If a firm volunteers any of these (it happens), do not file them under
`vendor-records/`. Either delete the inbound email or ask them to
re-send via a credentials channel (1Password share). Audit-evidence
hygiene matters.

---

## Future: optional submission endpoint

The form is intentionally client-side-only today (no backend) so that
no submission can be misused to flood the vendor with fake intake
records. If we ever scale past ~30 firms and the email-back workflow
gets noisy, the obvious extension is:

- Add a `POST /onboarding/submit` endpoint to the monitor Worker, gated
  by a per-firm signed-URL token issued by the vendor when sending the
  intake link.
- On submit, write the JSON to a new KV namespace `INTAKE_SUBMISSIONS`
  and email the vendor on-call.
- Keep the email-back path as a fallback.

Until then, the form is print-and-mail-style: firm IT fills, downloads,
emails, vendor files. Simple, no abuse surface, no GDPR data subject
issues from inadvertent collection.

# LocallyAI — Vendor Standard Operating Procedure (Vendor SOP)

> **PRIVATE — vendor team only.** This document covers things firms
> never need to read: vendor team org & succession, infrastructure
> inventory, daily ops checklists, sales pipeline, vendor-side
> incident playbook (when *our* infra breaks, not the firm's),
> disaster recovery for the vendor itself, and people processes
> (hiring/onboarding/offboarding vendor staff).
>
> The firm-facing operations runbook is at [docs/SOP.md](SOP.md). That
> SOP is dual-audience (firm IT + vendor engineer). This SOP is
> single-audience (vendor team only) and assumes you've already read
> the firm SOP.
>
> If a firm exercises DPA inspection rights and asks to see this
> document, **do not share it directly.** Offer a redacted version
> with personal names, account IDs, and credential locations removed.

---

## How to read this document

Read top-to-bottom on first onboarding to the vendor team. Subsequent
visits: jump to the chapter that matches your current task.

| # | Chapter | When to read |
|---|---|---|
| V1 | [Vendor team & succession](vendor-sop/vendor-team.md) | Joining the vendor team. Reviewing on-call rota. Planning succession. |
| V2 | [Vendor infrastructure inventory](vendor-sop/vendor-infrastructure.md) | Auditing accounts/secrets. Rotating credentials. Onboarding a new engineer. |
| V3 | [Daily vendor ops](vendor-sop/vendor-daily-ops.md) | Every weekday morning. Start-of-shift checklist. |
| V4 | [Release engineering](vendor-sop/vendor-release-engineering.md) | Cutting a new release. Promoting dev → stable. Bad-release rollback. |
| V5 | [Vendor-side incidents](vendor-sop/vendor-incidents-own-infra.md) | When *our* infra is compromised: laptop theft, GitHub takeover, GPG-key leak, etc. |
| V6 | [Disaster recovery](vendor-sop/vendor-disaster-recovery.md) | Sole engineer incapacitated. Vendor company dissolution. Succession to a partner firm. |
| V7 | [Sales pipeline](vendor-sop/vendor-sales.md) | Prospect → demo → contract → handoff to onboarding. |
| V8 | [Onboarding playbook](vendor-sop/vendor-onboarding.md) | The vendor's deep playbook for taking a firm from prospect to live (scripts, conversation templates, on-site checklists, recovery from common failures). |
| V9 | [Customer success cadence](vendor-sop/vendor-customer-success.md) | Proactive engagement with firms (beyond incident response). |
| V10 | [Sub-processor management](vendor-sop/vendor-sub-processors.md) | Annual review of upstream vendors (HF, GitHub, Cloudflare, Resend). |
| V11 | [Vendor compliance](vendor-sop/vendor-compliance.md) | Vendor's own posture as a data processor. ISO 27001 / SOC 2 readiness. |
| V12 | [People — hire, onboard, offboard](vendor-sop/vendor-people.md) | Adding/removing vendor team members. Key handover ceremony. |
| V13 | [Internal dry-run / dogfood onboarding](vendor-sop/vendor-internal-dryrun.md) | Pre-flight check before each "first" onboarding (first paying firm, first KSA firm, first HA fleet, first major install change). Run the whole pipeline on a vendor-controlled Mac as if it were a real firm. |
| – | [CHANGELOG](vendor-sop/CHANGELOG.md) | What changed in this Vendor SOP and when. |

---

## Hard rules for the vendor team

These never change. Violation is grounds for emergency review by the
remaining team and (if persistent) removal of vendor credentials.

1. **Never push to `main` of `LocallyAI/locallyai` without verifying
   you intend it.** This branch is what every firm runs against. A
   broken commit lands as the next release if not caught. Use feature
   branches + PR review for anything beyond a one-line typo fix.

2. **Never paste a firm's admin key, telemetry token, or any of their
   audit data into any third-party SaaS** (cloud AI assistants, team
   chat tools, web-based pastebins, email body, etc.). This is an
   automatic incident even if "no harm done" — the data left vendor
   custody. Use 1Password share for credential exchange.

3. **Never edit `vendor-records/firms-issued.log` by hand.** It is
   append-only; the integrity of the audit trail depends on this.
   Rows are added by `scripts/onboard_firm.sh`.

4. **Never store the GPG release-signing key passphrase in a file
   readable by your daily-driver shell.** Pinentry-mac with Keychain
   storage is the only sanctioned location. If you discover a copy
   elsewhere on your machine, treat as a key-leak incident
   ([V5](vendor-sop/vendor-incidents-own-infra.md#gpg-release-signing-key-leak)).

5. **Never give a single person both** (a) admin access to the
   `LocallyAI/locallyai` GitHub org **and** (b) custody of the
   GPG release-signing key **and** (c) the kill-switch TOTP secret,
   without a documented succession contact who has the same coverage.
   The kill switch exists precisely because ownership of all three
   would let a malicious actor push a signed bad release before
   anyone could intervene.

6. **Never decline to run `audit_install.sh` or `ha_chaos.py` before
   tagging a release.** If they fail, fix or pull the release. If they
   don't fit the change ("it's a doc change"), say so explicitly in
   the release commit message — don't silently skip.

7. **Never accept access to a firm's documents, users, or audit
   contents.** Vendor's only privileged read on firm data is the
   anonymised heartbeat. If a firm asks the vendor to "have a look at
   what's there" during an incident, decline and walk them through
   doing it themselves (preserves the no-vendor-data-access posture
   in the DPA).

8. **Never approve a release on a Friday after 14:00 local.** Bad
   releases are caught faster on weekday mornings when firms are
   active and the on-call is fresh. Friday-afternoon releases routinely
   blow up over the weekend with no one to respond inside the SLA.

---

## Document hierarchy

```
docs/
├── SOP.md                          ← firm-facing master (dual-audience)
├── sop/                            ← firm-facing chapters
│   ├── setup-mac-single.md
│   ├── daily.md
│   ├── ...
│   ├── vendor-monitoring.md        ← cross-references this Vendor SOP
│   ├── onboarding.md               ← cross-references this Vendor SOP
│   └── CHANGELOG.md
│
├── VENDOR_SOP.md                   ← THIS document (vendor-only master)
└── vendor-sop/                     ← vendor-only chapters
    ├── vendor-team.md
    ├── vendor-infrastructure.md
    ├── ...
    └── CHANGELOG.md
```

The two SOPs cross-reference but never duplicate. When a chapter would
naturally fit in both (e.g., release engineering), the firm SOP gets
the firm-relevant slice (`docs/sop/updates.md` — what to expect when an
update lands) and the Vendor SOP gets the vendor-relevant slice
(`docs/vendor-sop/vendor-release-engineering.md` — how to cut and sign a
release).

---

## Build the PDF

```sh
.venv/bin/python scripts/build_sop_pdf.py --vendor
# → dist/locallyai-vendor-sop-<git>-<utc>.pdf
```

Without `--vendor`, the script renders the firm SOP. The two PDFs share
the same print stylesheet but produce different bookmarks/TOCs.

---

## When in doubt

- **Never seen this before, no one to ask, can't find the answer here?**
  Stop and document the question in [vendor-team.md §unanswered](vendor-sop/vendor-team.md#unanswered-questions-log).
  Future-you and future-team will thank you.

- **Discovered a gap or wrong info in this SOP?** Fix it in the same
  commit as the work that made you notice. The CHANGELOG entry says
  what you learned.

- **Discovered a security or compliance gap?** Treat as an incident:
  document in [vendor-incidents-own-infra.md](vendor-sop/vendor-incidents-own-infra.md)
  even if you fixed it the same hour. The point is the trail.

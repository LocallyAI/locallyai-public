# Support

LocallyAI is a small project. This document is honest about what we can
support today, what we will support post-revenue, and what the firm's IT
team should expect to handle themselves.

## Today (pre-revenue)

| Channel | Response target |
|---|---|
| GitHub issues on `LocallyAI/locallyai-public` | Best-effort, typically < 5 business days |
| GitHub Security Advisories (private) | Acknowledged within 72 hours (see `SECURITY.md`) |
| Direct email to maintainer | Not offered to non-paying users |

We do **not** offer:

- 24/7 on-call rota
- Phone support
- Guaranteed response time SLAs
- Emergency hotfix turnaround commitments
- Custom-development services billed by the hour

This is a deliberate limit, not a temporary state — the project is one
person plus AI assistants. See `SLA.md` for the formal contract.

## Design-partner tier (planned Q3 2026)

The first firms to deploy LocallyAI as a paid pilot get:

- Direct Slack/email to the maintainer
- 1-business-day response on routine questions
- 4-hour response on production-down incidents
- Quarterly check-in calls
- Influence on the roadmap (first dibs on prioritising features that
  unblock them)
- A named designated successor in their contract (per `ESCROW.md`)

Pricing is not yet published. If you want to be a design partner, open
a GitHub issue tagged `design-partner` or email `partner@locallyai.app`.

## Paid tier (planned post-co-founder hire, Q4 2026 or later)

A real commercial-support contract — to be drafted when the co-founder
joins (see `docs/hiring/co-founder-profile.md` once it's published).
Expected shape:

- 24/5 European-hours coverage (24/7 added at a premium)
- 1-hour acknowledgement on P1 incidents
- Same-business-day on P2
- Escrow agreement executed at contract signing (per `ESCROW.md`)
- SOC 2 Type II evidence package shared annually
- Annual third-party pen test report shared with the firm

Pricing TBD — the published `PRICING.md` will appear before the first
contract is signed.

## What firms should handle themselves

- Backup of `~/.locallyai/` and `~/locallyai/storage/` (cron + offsite)
- Periodic restore-drill (script: `scripts/restore_drill.sh`, planned
  Week 1; runbook: `docs/runbooks/restore-drill.md`)
- TLS cert renewal (current installer ships a 10-year self-signed cert;
  per-firm internal CA is planned Week 2 — see
  `scripts/issue_internal_ca.sh`)
- macOS OS updates (we keep the venv pinned but Apple's monthly security
  patches are the firm's call)
- Network firewall rules (allowlist for vendor kill-switch + update
  endpoints; the egress-allowlist doc lives at
  `docs/egress-allowlist/README.md`)

## Escalation matrix (today)

| Severity | What it means | What to do |
|---|---|---|
| **P1: API down** | Chat doesn't respond, audit log not writing | Open GitHub issue with `P1` label + post in your firm's IT chat. We'll see the GitHub email and respond when possible — but this is not a paid SLA |
| **P2: Functional degradation** | One feature broken (plugin, citation, search) | GitHub issue with reproduction steps |
| **P3: Question / docs** | "How do I X" | GitHub issue or check `docs/sop/` |
| **Security** | Disclosure-class issue | See `SECURITY.md`; do NOT open a public issue |

## What "production" means in our context

LocallyAI is designed for installations that the firm's own IT can
operate. The vendor's role is the software; the firm's role is the
hardware, network, OS, and operational hygiene. If your firm doesn't
have an IT function that can run a Mac with brew + launchd + Syncthing,
you should consider a hosted competitor before adopting LocallyAI.

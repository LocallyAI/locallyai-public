# Service Level Agreement

This document describes the formal commitment LocallyAI's maintainer
makes to running installs. It is **not** a contract — it is a statement
of intent, honest about what's offered today versus what becomes
available after commercial contracts begin.

## Tier 0 — Open-source (today, everybody)

The codebase under `LocallyAI/locallyai-public` is offered under
AGPL-3.0 with **no warranty whatsoever**. See the LICENSE file. This
is the default state for every installation today, including the
maintainer's own dogfood.

| Metric | Commitment |
|---|---|
| Uptime | None — your hardware, your problem |
| Response time on issues | Best-effort via GitHub |
| Bug-fix turnaround | When the maintainer has time |
| Security disclosure ack | 72 hours (see `SECURITY.md`) |
| Roadmap influence | None — community input considered but not binding |

## Tier 1 — Design partner (planned Q3 2026)

Available to the first firms who pilot LocallyAI under a written
agreement. Terms are non-final and will change before the first
contract is signed.

| Metric | Commitment (target) |
|---|---|
| API uptime (firm-side) | 99.5% measured over a calendar month, excluding firm-side outages and scheduled maintenance |
| P1 ack | 4 hours, European business hours (08:00-18:00 GMT) |
| P1 resolution target | Best-effort within 24 hours |
| P2 ack | 1 business day |
| Quarterly review | Yes |
| Roadmap influence | First in queue for features that unblock the design partner |
| Designated successor | Named in the contract (per `ESCROW.md`) |
| Pricing | Not yet published; contact `partner@locallyai.app` |

## Tier 2 — Commercial (planned post-co-founder hire, Q4 2026+)

Available once the team is at least two people. Terms below are
indicative.

| Metric | Commitment (indicative) |
|---|---|
| API uptime | 99.9% measured over a calendar month |
| P1 ack | 1 hour, 24/5 European-hours (24/7 at premium) |
| P1 resolution target | 8 business hours |
| P2 ack | 4 hours |
| Annual third-party pen test | Shared with the firm |
| SOC 2 Type II evidence package | Shared annually |
| Source-code escrow | Executed at contract signing (per `ESCROW.md`) |
| Service credits for SLA misses | Yes — formula TBD |
| Pricing | Per-firm flat fee + per-lawyer seat; published in `PRICING.md` before first contract |

## What's specifically excluded (all tiers)

- Damage caused by the firm's own modifications to the source
- Damage caused by third-party plugins not shipped from
  `LocallyAI/locallyai-plugins-uk-public`
- Damage caused by running LocallyAI on hardware below the documented
  minimum spec (Apple Silicon, 16 GB RAM, 30 GB free disk — see
  `install.sh`'s pre-flight)
- Damage caused by disabling the kill-switch (`LOCALLYAI_KILL_SWITCH_REQUIRED=0`)
  when a security advisory has been issued for that release
- Damage caused by air-gap mode (`LOCALLYAI_AIR_GAP=1`) preventing a
  security patch from auto-applying

## Force majeure

The vendor is excused from SLA breaches caused by events outside
reasonable control: Cloudflare outage affecting the kill-switch +
monitor Workers, GitHub outage affecting the release-tag fetch,
Apple deciding to revoke a developer certificate, an Internet
backbone failure isolating the firm from the update endpoints.

## Honest disclosure

The current maintainer is one person. Until the team grows (Q3 2026
target; see `docs/hiring/co-founder-profile.md` once published), the
"24/5 cover" promise above is aspirational. Firms considering a paid
contract before that date should expect the maintainer's holiday and
illness to extend response times. This will be documented in the
contract itself, not buried.

## Review cadence

This SLA document is re-issued on commit hash bumps to the repo's
`SLA.md`. The version in effect for any given firm is the one
referenced in their signed contract, not the latest on `main`.

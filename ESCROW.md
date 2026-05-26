# Source-code escrow + designated successor

This document addresses the "bus factor" risk that any single-developer
software project carries: if the maintainer becomes unavailable —
illness, exit, hit by a bus — what happens to firms that depend on the
software?

## What's already in place (today)

1. **The codebase is open source.** `LocallyAI/locallyai-public` is
   AGPL-3.0. Any firm can fork, modify, and continue running it without
   the original maintainer's involvement. This is the primary
   protection — there is no proprietary blob that disappears with the
   maintainer.

2. **The audit chain + HMAC keys are firm-owned.** The keys live in
   the firm's own `.env` on the firm's own Mac. Nothing the vendor
   does can break verification of the firm's existing audit log.

3. **The kill-switch is OPT-OUT.** Setting
   `LOCALLYAI_KILL_SWITCH_REQUIRED=0` in the firm's `.env`, or
   `LOCALLYAI_AIR_GAP=1` (planned Week 1), disables the
   vendor-controlled cut-off. A firm that wants zero vendor leverage
   can have it.

4. **All deployment artifacts are reproducible.** The installer,
   Swift WKWebView wrappers, MLX model identifiers, Qdrant version
   pins, and Cloudflare Worker code all live in the public mirror. A
   competent IT firm could fork + re-deploy without the original
   maintainer.

## What's NOT in place today (the gap)

- **No formal escrow agent.** There's no third party legally bound to
  release a "successor build" if the maintainer disappears.
- **No designated successor.** No named individual or organisation has
  committed to maintaining the project if the maintainer steps away.
- **No firm-side hardware-failover handoff documentation.** The
  installer assumes the maintainer is reachable to help with edge cases.

## What we commit to (timed)

| Deadline | Commitment |
|---|---|
| **Week 1 post-first-design-partner-LOI** | Name a designated successor in this document. Candidate profile: a UK-based senior engineer who has reviewed the codebase + signed an MOU agreeing to take over publishing GPG-signed releases for at least 12 months if the maintainer becomes unavailable. Compensation: equity grant or fixed retainer, documented at the time. |
| **Within 30 days of first paid contract** | Sign a formal escrow agreement with one of: NCC Group (UK), Iron Mountain (UK), or Codekeeper (NL). Quarterly deposit of the full source tree + build instructions + the latest GPG release-signing private key. Release triggers: maintainer's verified death, 90+ days of unresponsiveness, or bankruptcy. |
| **Within 60 days of first paid contract** | Publish a runbook (`docs/runbooks/successor-handoff.md`) covering: how to retrieve from escrow, how to rotate the release-signing key, how to take over the Cloudflare Workers, how to communicate with active installs. The runbook is rehearsed by the designated successor before being signed off. |

## Why this is a commitment, not a feature

Escrow agents charge real money (NCC's standard contract starts around
£3-5k/year). Designated successors expect either equity or a retainer.
Both are cost line-items that don't fit into a zero-revenue project.
They become contractually achievable the moment the project has paying
customers — which is why the deadlines above are anchored to
contract-signing, not to calendar dates.

If you're a firm evaluating LocallyAI and the escrow story is
load-bearing for your procurement, raise it during contract
negotiation. The vendor will commit to executing the escrow agreement
*before* your contract goes live, and will pass through the escrow
cost in the contract.

## What firms can do today to protect themselves

If you adopt LocallyAI before the formal escrow is in place:

1. Mirror the source tree internally on the firm's own infrastructure.
   `git clone --mirror https://github.com/LocallyAI/locallyai-public.git`
   into a backup repo your IT controls. Refresh weekly via a cron job.
2. Mirror the plugin pack similarly:
   `git clone --mirror https://github.com/LocallyAI/locallyai-plugins-uk-public.git`.
3. Keep an offline copy of the documented Python dependencies (the
   exact versions referenced by `requirements.txt`).
4. Document the firm's own Mac hardware spec, .env values
   (in a secure vault), and Qdrant collection name. With those three
   things, any competent Python developer can re-deploy onto a new Mac
   in a day.
5. If you're paranoid: set `LOCALLYAI_AIR_GAP=1` (Week 1) so vendor
   updates cannot reach you. Your install becomes immune to vendor
   compromise at the cost of immunity from vendor security patches —
   a trade-off the firm makes consciously.

## Maintainer commitment

The current maintainer commits that, in the event of any planned
multi-week absence, they will:

1. Post a `MAINTAINER-AWAY.md` to the public mirror with the absence
   window
2. Pause vendor-side releases for the duration (no new tags signed)
3. Leave the kill-switch in the green state so existing installs are
   not at risk of being frozen by an automated kill-switch flip
4. Notify any paying-customer firms directly via the support channel
   in their contract

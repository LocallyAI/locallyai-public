# 0011 — AGPL-3.0 as the licence for the public mirror

- **Status:** accepted
- **Date:** 2026-05-18
- **Deciders:** single-author
- **Tags:** licensing, governance

## Context

The canonical LocallyAI repository is private; it's an active commercial product. There's a parallel intent to publish a **sanitised public mirror** as an engineering-portfolio artifact and reference codebase ([per the sanitisation workflow that produced this repo](../../README.md#locallyai--public-mirror)).

Publishing source code irrevocably defines what others can do with it. The choice of licence has to thread three needs at once:

1. **Hiring signal first.** The public mirror exists so engineering managers and recruiters can evaluate the code. The licence should encourage them to read, reproduce, and discuss — not deter them with usage friction.
2. **Don't enable a SaaS knock-off.** The commercial value of LocallyAI lives in the *on-premises product + the operational SOP around it*. A licence that lets a competitor take the code, wrap it in a hosted service, and re-sell it without contributing back would erode the commercial parent.
3. **Patent posture matters for enterprise readers.** The audience includes prospective enterprise customers and acquirers. A licence that doesn't include a patent grant introduces ambiguity that enterprise legal teams flag.
4. **Single-author project.** The licence has to be one a solo founder can maintain — no CLA bureaucracy, no per-contributor signed waivers.

The shortlist:

- **MIT** — maximum permissive; minimum protection.
- **Apache-2.0** — permissive + explicit patent grant.
- **AGPL-3.0** — copyleft including network use; forces source-share for SaaS use.
- **BSL 1.1** (Business Source Licence) — commercial-use-restricted for N years, then auto-converts to a permissive licence.

## Decision

**AGPL-3.0.**

The canonical licence text is in `LICENSE` (verbatim from `https://www.gnu.org/licenses/agpl-3.0.txt`, SHA-256 `0d96a4ff…abcb0`). GitHub auto-detects it as SPDX `AGPL-3.0`.

## Alternatives considered

- **MIT.** Lowest friction for readers; maximum adoption. Rejected because a knock-off SaaS provider could take the codebase, deploy it as `legallyai.example`, and never give anything back — actively eroding the commercial parent the mirror is meant to support. The hiring signal isn't worth that exposure.
- **Apache-2.0.** Strong permissive choice; the explicit patent grant addresses concern (3) above. Rejected for the same SaaS-knock-off reason as MIT — it's permissive without copyleft. *If the project were a pure library with no SaaS angle, Apache-2.0 would be the better pick;* for a deployable product, copyleft makes the difference.
- **BSL 1.1** (e.g. with a 3-year conversion to Apache-2.0). Considered seriously — it's the licence Sentry, CockroachDB, and several other commercial-OSS projects use. Rejected because (a) BSL is not OSI-approved, which means GitHub doesn't display the "AGPL"-equivalent badge enterprise readers look for, (b) BSL's commercial-use restrictions are vague enough that legal teams have to read them carefully — which is friction the recruiter audience doesn't have appetite for, and (c) the time-bomb model (auto-converts to permissive after N years) doesn't actually solve the SaaS-knock-off problem unless the conversion date is "never" — which BSL doesn't support.
- **GPL-3.0** (non-Affero variant). Considered. Rejected because the "network use is not distribution" gap leaves the SaaS-knock-off hole open — the whole point of AGPL over GPL is closing that loophole.
- **Dual-licence** (AGPL for open-source use + a paid commercial licence for proprietary use). The "commercial open-source" pattern. Not rejected per se — the public mirror is AGPL-3.0; if a future customer wants a non-AGPL build of the platform, that's a commercial conversation handled separately. The mirror's licence doesn't preclude it.
- **No licence at all** (all rights reserved). Rejected because GitHub treats unlicensed code as "all rights reserved" by default, which would deter the very readers the mirror is targeting.

## Consequences

### Positive

- **Closes the SaaS-knock-off hole.** Anyone running LocallyAI as a network service (the obvious commercial vector for someone wanting to "borrow" the code) must publish their modified source under AGPL-3.0. This is the intent.
- **Patent grant included** (AGPL-3.0 §11). Enterprise readers don't have the ambiguity they'd have with MIT.
- **OSI-approved and FSF-recommended.** Reads as "real open source" to evaluators who care about that distinction (a meaningful subset of the hiring audience does).
- **No CLA needed.** Outbound = inbound (everyone contributing licenses their changes under the same AGPL-3.0). A solo maintainer doesn't have to track per-contributor agreements.
- **The commercial-mirror relationship is honest.** The CONTRIBUTING.md explicitly states the mirror is a sanitised snapshot of a private commercial project, and the AGPL-3.0 licence makes it clear what use is permitted.

### Negative

- **Some companies (Google's well-known internal policy, several enterprise IT shops) avoid AGPL-3.0 entirely.** A subset of the audience won't engage with the repo because their employer's licence policy forbids it. Accepted cost — the SaaS protection is worth more than the slice of readers we lose.
- **AGPL-3.0 is "scary" to some readers** who haven't read it carefully — the copyleft scope is sometimes overstated by hostile commentary. Mitigated by the README clearly explaining the project is a sanitised mirror and pointing licensing enquiries (commercial-licence requests) at issues.
- **Contributions that derive from the mirror flow back into AGPL-3.0 only** — the private commercial parent can ingest them but must respect the AGPL terms for any deployments that follow. In practice the parent already operates under AGPL principles for its public footprint; this constraint is acceptable.
- **AGPL-3.0 incompatibility with some other open-source licences** (notably Apache-2.0 in one direction). The mirror imports nothing exotic; current dependencies are all AGPL-compatible.

### Neutral

- **GitHub's auto-licence-detector** identifies the file correctly as AGPL-3.0 (verified via `gh api repos/.../license` returning `"spdx_id": "AGPL-3.0"`). No badge work needed.
- **The private commercial repo continues to exist** with whatever proprietary licensing the firm-side deployments operate under. The mirror's licence binds the mirror only.

## References

- `LICENSE` — full AGPL-3.0 text from gnu.org
- `README.md` — explains the mirror relationship and points licensing enquiries to issues
- `CONTRIBUTING.md` — affirms inbound = outbound AGPL-3.0
- `/tmp/sanitization_final.md` — the verification report for the publication
- AGPL-3.0 full text: https://www.gnu.org/licenses/agpl-3.0.html
- FSF rationale for the AGPL: https://www.gnu.org/licenses/why-affero-gpl.html

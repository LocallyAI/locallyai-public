# 0009 — Cloudflare Workers as external tiebreaker, kill switch, onboarding gateway

- **Status:** accepted
- **Date:** 2026-05-04
- **Deciders:** single-author
- **Tags:** infra, ha, onboarding, vendor-ops

## Context

LocallyAI is on-premises by thesis — the office Mac (or Mac fleet) owns the data and the inference. But three concerns *cannot* be satisfied from the office Mac alone:

1. **HA tiebreaker** (see [ADR-0005](0005-mac-ha-syncthing-rsync.md)). With two nodes in one office, no real consensus is possible — you need a third witness outside the office. The witness's job: answer "which node is the current primary?" when both nodes ask, on a cadence that supports sub-5-second failover.
2. **Vendor kill switch.** If a firm's deployment goes rogue (operator key leaks, version compromised, sub-processor breach forces takedown), the vendor needs an out-of-band way to stop the platform from serving new requests. Asking the firm's IT to SSH in and stop a service is too slow and assumes the firm's IT is reachable.
3. **Vendor monitor + onboarding gateway.** A new firm signs up; they need to: fill in a profile form, receive a one-time-use install command, register a telemetry token, then start phoning home health snapshots so the vendor can run the 4-hour SLA. None of this can run on the firm's hardware because it pre-dates the firm having any hardware.

All three need an internet-reachable service that the office Macs can poll, plus a dashboard the vendor can log into from a laptop. The service must be cheap (the vendor is solo / pre-revenue at start), reliable, geographically distributed, and operationally light.

## Decision

**Two Cloudflare Workers**, each with their own KV namespaces, both deployed under one CF account:

- **`locallyai-killswitch`** (`docs/kill-switch/cloudflare-worker/`) — single endpoint `GET /status.json` returning a signed JSON `{"status": "ok"|"halt", "scope": "all"|"firm:<id>", ...}`. Office Macs poll this on a 60s cadence; a "halt" status with a verifying Ed25519 signature (`LOCALLYAI_KILL_SWITCH_SIG_REQUIRED=1`) refuses to start the API.
- **`locallyai-monitor`** (`docs/monitor/cloudflare-worker/`) — multi-endpoint Worker handling: `/heartbeat` (firm token-auth, accepts health snapshots), `/onboarding/*` (form, mint-token, intake, deploy-key flows), `/api/alerts` (dashboard data), `/api/ack` (operator acknowledges alerts), `/dashboard/*` (static TOTP-gated dashboard assets), `/alert` (manual alert injection), `/sizing` (sizing-tool calculator). Cron `*/15 * * * *` walks unacknowledged criticals; SLA-escalation re-email disabled by default (`SLA_WARN_HOURS=0` — see the per-(firm,code) dedupe pattern).

Both Workers store state in KV (cheap, durable, eventually-consistent, ideal for the small per-firm records they hold). Secrets (`FIRM_TOKENS`, `ADMIN_TOTP_SECRET_BASE32`, `RESEND_API_KEY`, etc.) live in Wrangler-managed secrets, never in the source repo.

## Alternatives considered

- **Run our own VPS** (Hetzner, DigitalOcean) for the tiebreaker + kill switch + dashboard. Rejected because (a) operationally heavy — patching, monitoring, backups, the VPS itself becoming a SPOF — for what is structurally a small amount of edge logic, (b) more expensive than the Workers free tier at small scale, and (c) the "single point of vendor-side outage" is much smaller on Cloudflare's edge than on one VPS in one DC.
- **AWS Lambda + DynamoDB.** Comparable shape. Rejected on (a) cold-start latency (CF Workers are ~5 ms cold; Lambda is ~100+ ms), which matters for the kill-switch poll path, and (b) AWS's operational complexity is higher for the same outcome.
- **GitHub Pages + GitHub Actions** for the dashboard + a static `status.json`. Rejected because (a) GitHub Pages is static-only — the dashboard needs TOTP gating and the onboarding form needs `POST` handling, and (b) GitHub Actions cron has 15-minute minimum cadence which is too slow for kill-switch polls.
- **No external tiebreaker — use a simpler HA design.** Rejected because two-node HA without a witness can split-brain (each node thinks the other is dead → both serve writes → audit log forks). The witness is load-bearing.
- **Skip the kill switch entirely.** Considered. Rejected because the vendor's regulatory exposure (sub-processor breach, kompromat scenarios) needs an out-of-band stop. A documented kill switch is also a sales-conversation answer to "how do you protect us if YOU get compromised?"
- **Skip the vendor monitor — let firms call when something breaks.** Rejected because the 4-hour SLA is the commercial offer; firms don't always call (they assume vendor knows), and "we noticed before you did and fixed it" is the differentiator.

## Consequences

### Positive

- **Cheap.** Cloudflare Workers free tier handles ~50 firms before any paid tier is needed. KV reads/writes are well within limits for heartbeat + alert traffic.
- **Globally distributed.** Both Workers run at Cloudflare's edge — the office Mac in London hits the London PoP; KSA firms hit Jeddah / Bahrain. Sub-50ms latency for `/status.json` polls anywhere we serve.
- **Operationally light.** Wrangler deploys are one command; secrets rotation is one command. No VMs to patch.
- **TOTP-gated dashboard + IP-rate-limited onboarding** prevent script-kiddie attacks on the public endpoints. Recovery codes hashed (SHA-256) in a separate KV namespace; rate-limit counters in another (so one namespace's churn doesn't pollute another).
- **One CF account, two Workers, clean separation.** Kill-switch worker has no access to monitor KV (and vice versa) — if one Worker is compromised, the other is intact.

### Negative

- **Cloudflare is a sub-processor.** Disclosed in DPA + sub-processor SOP (`docs/vendor-sop/vendor-sub-processors.md`). The data CF observes is: firm_id hashes, health snapshot metadata (no audit content, no document content), and IP addresses of the office Macs polling. Documented as legitimate-interest processing.
- **Single-vendor lock-in for the edge layer.** If Cloudflare ever raises prices or changes terms, migration is real work — the dashboard SPA, the Worker code, the KV-to-X data move. Mitigation: the Workers' code is plain TypeScript with no Cloudflare-specific magic except KV; porting to Lambda + DynamoDB is doable in ~1 week.
- **The kill switch is a foot-gun.** A misconfigured deploy of a "halt" status would brick every firm at once. Mitigated by: (a) Ed25519-signed payloads (firms refuse unsigned by default), (b) scope-targeting (`"firm:<id>"` rather than `"all"` for per-firm halts), (c) a documented release procedure (`scripts/release_kill_switch.sh`) with mandatory dry-run.
- **The vendor monitor knows which firms exist** (by hashed firm_id + firm_name in the TELEMETRY_TOKENS KV). A breach of the monitor would expose the customer list (not their data). Mitigated by: per-firm tokens (rotation possible without re-onboarding), no plain-text firm contact details in KV, full vendor SOP on breach handling (`docs/vendor-sop/vendor-incidents-own-infra.md`).
- **CF account compromise is a vendor-level disaster.** Documented in vendor DR SOP with TOTP + recovery-code custody + GPG-signed release procedure as defence-in-depth.

### Neutral

- **The Workers' `your-cf-account.workers.dev` URLs are baked into operator docs** with the placeholder name (the real one was scrubbed during public-mirror prep — see ADR-0011). Per-firm deployments substitute their actual account at deploy time.
- **Pull-based health snapshots** (firms POST every 5 minutes) rather than push-from-CF — cleaner firewall posture for the firm (one outbound HTTPS connection, no inbound port opening).
- **Per-(firm, code) alert dedupe** means a sticky condition produces one email per incident, not one per heartbeat tick. Detailed in `docs/sop/vendor-monitoring.md`.

## References

- `docs/monitor/cloudflare-worker/src/worker.ts` — monitor Worker source
- `docs/monitor/cloudflare-worker/wrangler.toml` — KV bindings, crons, vars
- `docs/kill-switch/cloudflare-worker/src/worker.ts` — kill-switch Worker source
- `docs/kill-switch/cloudflare-worker/wrangler.toml` — single KILLSWITCH KV binding
- `kill_switch.py` — office-Mac-side poll + signature verify
- `telemetry.py` — heartbeat POST + alert dedupe persistence
- `docs/sop/vendor-monitoring.md` — alert dedupe + SLA flow
- `docs/sop/onboarding.md` — onboarding-form → install-token → first-heartbeat flow
- `docs/vendor-sop/vendor-incidents-own-infra.md` — CF account compromise IR procedure
- `scripts/release_kill_switch.sh` — signed kill-switch payload release
- ADR-0005 (HA tiebreaker dependency)

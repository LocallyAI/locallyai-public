# LocallyAI — Standard Operating Procedure (SOP)

This is the master operations document for LocallyAI deployments. It is
**click-by-click** by design: every procedure assumes you've never done
it before. If a procedure is unclear, that's a bug in this document —
file it the same way you'd file a code bug.

> **Scope:** the entire repository. Single-node deployments, 2-node HA
> Mac fleets, 2-node HA Windows fleets. Mac and Windows are never mixed
> in one fleet. (See *Choosing your topology* below if unsure.)

> **Audience:** the IT-ops person at the deploying firm, plus the
> LocallyAI vendor engineer. **No prior LocallyAI knowledge assumed.**

> **Compliance frame:** procedures cite GDPR (EU/UK), ISO 27001:2022,
> UAE PDPL (Federal Decree-Law 45/2021), KSA PDPL (Royal Decree M/19,
> 2023), DIFC DP Law 5/2020, ADGM DP Regs 2021. Where a step is
> regulator-required, the citation appears inline.

---

## How to read this document

The SOP is split into focused chapters. Read top-to-bottom for setup;
jump to the relevant chapter when an incident fires.

> **Operator runbooks come first.** If you're triaging an incident or
> running a scheduled operation, open [docs/runbooks/](runbooks/00-index.md)
> first. The runbooks are bounded, action-focused, and verified
> step-by-step. The SOP chapters below are reference material — the
> "why" — not the "do this now." A new operator runs from the runbooks
> and consults the SOP for context.

### Available operator runbooks

| Runbook | Use when |
|---|---|
| [`runbooks/dpo-monthly-snapshot.md`](runbooks/dpo-monthly-snapshot.md) | DPO needs to file the monthly compliance evidence |
| [`runbooks/api-down.md`](runbooks/api-down.md) | `/healthz` failing or firm reports app dead |
| [`runbooks/add-new-firm.md`](runbooks/add-new-firm.md) | A new firm signed; install required |
| [`runbooks/remove-firm.md`](runbooks/remove-firm.md) | Firm is leaving — decommission |
| [`runbooks/audit-chain-broken.md`](runbooks/audit-chain-broken.md) | `/admin/audit-verify` returned TAMPERED |
| [`runbooks/dashboard-locked-out.md`](runbooks/dashboard-locked-out.md) | Vendor monitor dashboard rejects TOTP |
| [`runbooks/failover-test.md`](runbooks/failover-test.md) | Quarterly HA failover-readiness drill (block primary, verify standby, attest) |
| [`runbooks/rotate-deploy-keys-pat.md`](runbooks/rotate-deploy-keys-pat.md) | Quarterly: rotate the GitHub PAT the vendor monitor uses to auto-create per-firm deploy keys |
| [`runbooks/conflict-check.md`](runbooks/conflict-check.md) | Run a conflict-of-interest check before opening a new matter |

If a runbook doesn't exist for what you're doing, **stop and call the founder**. Inventing procedures in the moment is how data gets lost.


| # | Chapter | When to read |
|---|---|---|
| 0 | [Repository access — SSH deploy keys](sop/repo-access.md) | Vendor-side: per-client deploy key on the GitHub repo. Read BEFORE cloning. |
| 1 | [Setup — Mac single-node](sop/setup-mac-single.md) | First-time install on one Mac |
| 2 | [Setup — Mac 2-node HA](sop/setup-mac-ha.md) | Pairing a second Mac for failover |
| 3 | [Setup — Windows](sop/setup-windows.md) | Windows / DGX Spark deployments |
| 3a | [Setup — Saudi (KSA / PDPL)](sop/setup-saudi.md) | Region-specific overrides for Saudi deployments (Arabic UI, RTL, Hijri, Saudi demo docs) |
| 4 | [Daily operations](sop/daily.md) | Every day. Running the deployment |
| 4a | [Bulk corpus ingestion](sop/bulk-ingest.md) | Loading the firm's archive (gigabyte-scale, drag a folder, resumable) |
| 4b | [One-click start & stop](sop/one-click-start.md) | The Mac/Windows launcher apps — daily start/stop without a terminal |
| 4c | [Client app install](sop/client-install.md) | IT distributing standalone Worker/Manager apps to staff laptops (no full install) |
| 4d | [System updates](sop/updates.md) | Vendor release flow + firm-side application: two channels, GPG signing, kill switch, atomic deploy, LLM model picker |
| 4e | [Data isolation](sop/data-isolation.md) | Per-firm isolation guarantee + every network call + egress allowlist (compliance evidence) |
| 4f | [Vendor monitoring](sop/vendor-monitoring.md) | Vendor-internal: fleet dashboard, alert triage, 4-hour SLA workflow, self-heal inventory |
| 4g | [Onboarding intake](sop/onboarding.md) | Vendor-internal: structured intake form for firm profile collection BEFORE office Mac install |
| 4h | [Remote staff access](sop/remote-access.md) | When fee-earners need to reach the office Mac from outside the office LAN: Tailscale / firm VPN / Cloudflare Tunnel options |
| 4i | [Install checklist](sop/install-checklist.md) | Tick-through for the engineer on-site. Pre-arrival, on-site, post-install verification, 7-day soak, 30-day acceptance. |
| 5 | [Maintenance](sop/maintenance.md) | Scheduled work: updates, salt rotation, cert renewal |
| 6 | [Compliance ops](sop/compliance.md) | Subject-access requests, erasure, audits, breach response (UK + master) |
| 6a | [Compliance ops — Saudi (PDPL)](sop/compliance-saudi.md) | KSA-specific procedures: SDAIA breach notification, PDPL subject access in Arabic, cross-border |
| 6b | [DPO compliance portal](sop/dpo-compliance-portal.md) | Reference: what the Manager UI Compliance tab is, what each section means, which Article each piece satisfies, when to use it |
| 6c | [Document access control (per-doc ACL)](sop/document-acl.md) | Per-document allowed_users + matter codes for partner-only / matter-restricted / ethical-wall material |
| 6d | [HA architecture — two-Mac active/standby pair](sop/ha-architecture.md) | What syncs (Syncthing for governance, rsync for corpus, per-node for logs), failover model (smart-client retry), what the vendor monitors |
| 7 | [Incidents — software](sop/incidents-software.md) | API down, audit chain TAMPERED, sync conflict, etc. |
| 8 | [Incidents — physical / environment](sop/incidents-physical.md) | Power outage, theft, hardware failure, AC failure, lockdown |
| 9 | [Incidents — security](sop/incidents-security.md) | Salt leak, ransomware, suspected unauthorised access |
| 10 | [Incidents — operator error](sop/incidents-operator.md) | Mis-typed env edit, lost admin key, accidental rm, sudo'd a destructive command |
| 11 | [Incidents — people](sop/incidents-people.md) | Sole IT-ops on holiday, DPO unreachable, vendor unreachable, key person leaves |
| 12 | [Incidents — legal / regulatory](sop/incidents-legal.md) | Court order, regulator inspection, litigation hold, mass SAR, cyber-insurance request |
| 13 | [Incidents — misuse / insider](sop/incidents-misuse.md) | User asking about other clients, key sharing, jailbreak attempts, ghost users |
| 14 | [Incidents — service quality](sop/incidents-service.md) | Latency, hallucinations, refusals, sources_retrieved=0, worker-ui crashes |
| 15 | [Incidents — supply chain / upstream](sop/incidents-supply.md) | HF down, Ollama API change, Python upgrade breaks deps, OS update breaks service |
| 16 | [Scale-out & migration](sop/scale-out.md) | When to grow, single→HA, 2-node→3-node+NAS, Mac↔Windows, cohort split |
| 17 | [Recovery & DR](sop/recovery.md) | Restoring from backup, post-incident reconstruction |
| 18 | [Decommission](sop/decommission.md) | Off-boarding a deployment, secure wipe |
| 19 | [Conflict checks](sop/conflict-checks.md) | New-matter intake conflict-of-interest engine: how it works, status badges, what it doesn't replace |
| 20 | [Document comparison](sop/document-comparison.md) | Two-doc redline + per-clause LLM significance commentary |
| 21 | [Citation checker](sop/citation-checker.md) | Extract + verify case-law and statute citations against firm corpus + BAILII (UK) + LLM on-point check |
| 22 | [Roadmap](sop/roadmap.md) | What's not yet shipped (time-entry, US case-law verify, KSA case-law verify, DMS connector, OCR ingest) and the workarounds in the meantime |
| – | [CHANGELOG](sop/CHANGELOG.md) | What changed in this SOP and when |

---

## Choosing your topology

| You have | You want | Read |
|---|---|---|
| One Mac Studio | Production with a known acceptable downtime window if the Mac dies | Chapter 1 |
| Two Mac Studios on one office LAN | Continuous service if one Mac dies — sub-5s blip per failed in-flight request | Chapters 1 + 2 |
| One Windows box (DGX Spark or other CUDA) | Production on Windows | Chapter 3 (single-node section) |
| Two Windows boxes | Continuous service on Windows | Chapter 3 (full) |
| Both Macs and Windows | **Not supported in one fleet.** Pick all-Mac or all-Windows. The model file formats differ; mixed fleets are refused at registration. | – |

---

## Hard rules that apply everywhere

These never change. Follow them or the deployment is non-compliant and
you cannot honestly produce evidence for an auditor.

1. **Never edit `audit.log` by hand.** The HMAC chain detects it. If you
   need to redact something, use `manage_users.py erase` (the right
   tool produces a tombstone in the chained log).
2. **Never share the `LOCALLYAI_AUDIT_HMAC_KEY`, `LOCALLYAI_AUDIT_SALT`,
   or `LOCALLYAI_ADMIN_KEY` over chat, email, or screenshots.** They
   live only in `.env` (chmod 600). If one leaks, treat as an incident
   ([sop/incidents-security.md](sop/incidents-security.md)) and rotate.
3. **Never commit `.env`, `users.json`, `tls/`, `data/`, `storage/`,
   `logs/` to git.** They are all gitignored; a human who manually
   `git add -f`s them is the failure mode. If you suspect this happened,
   read [sop/incidents-operator.md](sop/incidents-operator.md) §
   *Accidentally pushed secrets to git*.
4. **Always run `bash scripts/audit_install.sh`** (or `audit_install.ps1`
   on Windows) **after every change**. Pass=14, warn≤1, fail=0 is the
   green-light condition. Anything else: stop and read the relevant
   incident chapter.
5. **In HA mode, both nodes must be the same OS, same model, same
   backend (MLX *or* Ollama, never both).** The fleet registry refuses
   mixed registration; if you see two nodes with different `backend`
   values in `/admin/fleet/nodes`, one of them is mis-configured.
6. **Time sync must be on** (`chronyd` on Mac, `w32time` on Windows)
   against the same NTP source. The audit chain timestamps and the
   cross-node TTLs assume <1s clock skew.
7. **Disk encryption must be on.** FileVault (Mac) or BitLocker
   (Windows). The salt-and-name-list co-location risk is mitigated at
   the encryption boundary; without it, theft of the box is
   re-identification of every audit pseudonym ever written.

---

## Critical contact / credential register (template)

Operations need one piece of paper (or, more realistically, one
password-manager entry per item) somewhere a senior partner can find.
Fill these in at the end of setup and keep them current.

| Item | Where stored | Last updated | Last verified |
|---|---|---|---|
| `LOCALLYAI_ADMIN_KEY` | (e.g. firm 1Password vault under "LocallyAI / admin") |  |  |
| Each user's API key | (each user has their own; keys printed at issue, never reshown) |  |  |
| Mac/Windows local-account password (the OS user the service runs under) |  |  |  |
| FileVault recovery key (Mac) / BitLocker recovery key (Windows) |  |  |  |
| Syncthing Device IDs (HA only) |  |  |  |
| Qdrant cluster API key (HA only) |  |  |  |
| Off-site Qdrant snapshot location (cold backup) |  |  |  |
| LocallyAI vendor support contact |  |  |  |
| Internal IT escalation contact |  |  |  |

---

## Change-control discipline (this section is load-bearing)

This SOP **must** be updated when ANY of the following happens. No
exceptions. A code change that ships without a corresponding SOP update
is a regression — whoever did the merge owns the doc fix.

| If you change… | Update… |
|---|---|
| `install.sh` / `install.ps1` | sop/setup-mac-* / sop/setup-windows.md |
| `update.sh` / dependency versions | sop/maintenance.md |
| `supervisor.py` / `os_supervisor.py` / launchd plist / NSSM service | sop/setup-* (registration step), sop/daily.md (start/stop) |
| `api.py` (any endpoint added/removed/renamed) | sop/daily.md AND sop/compliance.md AND any incident playbook that referenced the endpoint |
| `config.py` (any new env var) | sop/setup-* (where it gets set), sop/maintenance.md (if rotatable) |
| `manage_users.py` (any new subcommand) | sop/daily.md AND sop/compliance.md |
| `inference_gate.py` (any new tunable) | sop/maintenance.md (env table) AND sop/incidents-software.md § *Gate at capacity* |
| `mlx_inference.py` / streaming path | sop/incidents-software.md § *Streaming wedge* |
| `fleet.py` / `sync_conflicts.py` / `shared_lock.py` | sop/incidents-software.md § *Sync conflict* / *Fleet desync* |
| Audit-chain logic (writer or verifier) | sop/incidents-software.md § *Audit chain TAMPERED* AND sop/compliance.md § *Audit-chain evidence* |
| Salt era logic / pseudonymisation | sop/compliance.md § *Subject-access* AND sop/maintenance.md § *Salt rotation* |
| Any new doc under `docs/` | This master index (the table at top) |

After every code change, append a one-liner to
[sop/CHANGELOG.md](sop/CHANGELOG.md) describing the operational impact.
The format is fixed — see the file.

A pre-commit reminder is installed at `.githooks/sop-reminder`. It does
not block — it just prints a yellow warning if a tracked code path
changed without a corresponding `docs/SOP*` file change. To enable
locally:

```bash
git config core.hooksPath .githooks
```

---

## Glossary (jargon the doc uses)

| Term | Meaning |
|---|---|
| **Single-node** | One Mac or Windows box runs the whole stack. No HA. |
| **HA / 2-node fleet** | Two homogeneous boxes, automatic failover via worker-ui smart client. |
| **Smart client** | The worker-ui app's logic that holds N node URLs, health-checks each, retries failed requests on the next healthy node. |
| **Idempotency token** | `client_request_id` UUID the smart client stamps on each chat send so a retry doesn't double-bill. |
| **Audit chain** | Per-node HMAC-SHA-256 chain over `audit.log`. Each entry's `_chain_hmac` covers everything before it. Tamper-evident. |
| **Salt era** | An 8-hex-char id (`SHA-256("era:" + salt)[:8]`) stamped into each audit entry so the verifier knows which historical salt to use for re-identification. |
| **Pseudonym** | 16-hex-char output of `SHA-256(salt:username)[:16]`. Stable per (salt, name); never the real name. |
| **Fleet dashboard** | `apps/fleet-ui/` admin SPA — per-node health, audit chain, Qdrant, sync conflicts, alerts, gate. |
| **Sentinel** | Background thread on every node — runs every 60s, rotates logs, detects breaches, refreshes fleet heartbeat, scans for sync conflicts. |
| **Resurrector / Heartbeat** | Watchdog mesh that probes `/healthz` and triggers staged recovery if the API goes silent. |
| **Inference gate** | `inference_gate.py` — bounded semaphore + queue cap. Limits concurrent in-flight chat completions so a burst of users can't OOM the box. |
| **Sync conflict** | Syncthing renamed-file marker (`*.sync-conflict-*`) when both nodes wrote the same path. Quarantined automatically; never auto-merged. |
| **TAMPERED** | `/admin/audit-verify` returned a non-`ok` status. Either the chain is genuinely broken (hostile or accidental) or operator action like a manual `> audit.log` truncated it. Always investigated, never ignored. |

---

Continue to [Setup — Mac single-node](sop/setup-mac-single.md) →

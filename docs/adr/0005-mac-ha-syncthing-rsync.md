# 0005 — Two-node Mac HA via Syncthing (governance) + rsync (corpus)

- **Status:** accepted
- **Date:** 2026-05-11
- **Deciders:** single-author
- **Tags:** infra, ha, replication

## Context

The default LocallyAI deployment is one Mac Studio in the firm's office. That's fine for firms that accept a few hours of downtime if the hardware fails. For firms that **cannot** accept that — and for the SRA/PDPL "service continuity" boxes some firms have to tick — there needs to be a high-availability topology.

Constraints unique to this product:

1. **Two Macs on one office LAN** is the realistic budget. No data centre, no cloud, no 5-node etcd cluster. Two boxes, side by side, in a server cupboard or under a desk.
2. **The data that needs to be consistent across both nodes** falls into three classes:
   - **Governance state**: `users.json`, `doc_acls.json`, `audit.log`, `billing.log`, `conflicts.log`. Small, mutates frequently, must be consistent for correctness (an ACL on Node A must apply to a query that lands on Node B).
   - **Corpus state**: ingested documents, Qdrant collection, BM25 index. Large (GBs), mutates on ingest, can lag by minutes without correctness impact.
   - **Per-node operational state**: TLS certs, launchd config, local logs. Different per node by design.
3. **Failover should be sub-5-second** for an in-flight chat request. The user notices a pause, not an error.
4. **No split-brain.** If both nodes think they're primary, the audit log forks irrecoverably.

Cloud-style HA primitives (Pacemaker, Keepalived, Raft consensus) all assume hardware/network properties — fast inter-node links, IPMI, low-latency consensus — that aren't reliably present in a firm's server cupboard.

The question: how to get sub-5-second failover on two Macs sharing a Cat-5 cable without inventing a distributed-systems research project.

## Decision

Smart-client active/standby with class-specific replication:

1. **Governance state** (small, must-be-consistent) → **Syncthing** between the two Macs. Conflict detection is built in (`*.sync-conflict-*` files), our `sync_conflicts.py` watcher rotates them into a structured queue for operator review. The audit-log HMAC chain ([ADR-0003](0003-hmac-chained-audit-log.md)) makes any sync-induced anomaly detectable.
2. **Corpus state** (large, can lag) → **rsync** over the LAN on a 5-minute timer from primary to standby. Re-ingest of any new document automatically appears on the standby within one tick.
3. **Per-node state** (TLS, launchd, local logs) → not replicated. Each node owns its own; the install script generates these per-node.
4. **Failover decision** → **smart client retry**. The worker UI and manager UI hold a ranked URL list (primary + standby). On 5xx / connection error against the primary, the client retries against the standby with the same auth token. No load balancer, no VIP, no Pacemaker. The client owns the failover.
5. **Split-brain prevention** → the external **Cloudflare Workers tiebreaker** ([ADR-0009](0009-cloudflare-workers-tiebreaker.md)). The kill-switch + vendor-monitor Workers know which node is currently considered primary; if both nodes claim primary, the Workers' view breaks the tie.

See `docs/sop/ha-architecture.md` for the full topology + failover-test runbook.

## Alternatives considered

- **Postgres streaming replication** for governance state. Standard tech, well-understood. Rejected because (a) introduces a Postgres dependency every single-Mac deployment also needs (and the API would otherwise not), and (b) governance state is JSON files small enough that Syncthing's overhead is genuinely lower.
- **DRBD / block-level replication** for corpus state. Stronger consistency, lower lag. Rejected because (a) DRBD is Linux-specific and the platform is Mac-first, (b) failover requires unmounting one side and remounting on the other — not a sub-5-second operation, and (c) it requires kernel-level setup that the firm's IT person can't troubleshoot.
- **S3 / cloud-backed shared corpus.** Both Macs read/write the same S3 bucket. Rejected because it defeats the on-premises thesis — corpus content leaves the office. (Even with KMS encryption, the access patterns and presence of objects leak matter information to AWS.)
- **A real load balancer + IP failover** (HAProxy, Keepalived, F5). Rejected because (a) it introduces a third device that itself becomes a SPOF, (b) IP failover on a LAN requires gratuitous ARP that some firm routers eat, and (c) the smart-client-retry approach is materially simpler and achieves the same effect at the only layer that matters (the UI).
- **Raft / etcd / Consul** consensus for primary election. Rejected as massive overkill for two nodes. With only two nodes, no consensus protocol can make progress under partition — you need a third witness. The Workers tiebreaker is that witness, but it's pull-based, not Raft-based, because the cadence (every few seconds) is enough for sub-5-second failover and is much simpler.
- **Three-node fleet** with quorum. Rejected on cost grounds — three Mac Studios in the office cupboard for a 5-lawyer firm is silly. The two-node + external-witness topology gets the same correctness properties with one less physical box.
- **Single-node + nightly cold-standby restore.** Rejected because RTO is hours, not seconds — the firm asked for HA precisely to avoid that.

## Consequences

### Positive

- **Failover is sub-5 seconds.** The UI's first failed request triggers the retry; the user sees a brief spinner.
- **Sync semantics match the data.** Governance state is consistent within seconds (Syncthing). Corpus is eventually consistent within ~5 minutes (rsync). Per-node state never syncs (it shouldn't).
- **No third local device.** Two Macs + an external Cloudflare Workers tiebreaker. No load balancer, no VIP, no quorum witness in the office.
- **Sync conflicts surface explicitly.** `*.sync-conflict-*` files are detected by `sync_conflicts.py` and queued for operator review with a runbook (`docs/runbooks/audit-chain-broken.md` covers the audit-log variant). The HMAC chain catches any conflict-induced governance-state divergence.
- **Single-node deployments are unaffected.** The HA story is opt-in; the install asks "single or fleet?" and skips all the HA wiring when single.

### Negative

- **Corpus lag is up to 5 minutes.** If the primary ingests a 100-doc batch and dies 30 seconds later, those documents aren't on the standby yet. Acceptable — re-ingest after failover is one button.
- **Syncthing can be slow** to converge after a node has been offline for hours. Documented behaviour; the failover test runbook validates it quarterly.
- **The smart-client retry only handles transient failures.** If both nodes are down, the client surfaces a clear error. Total-failure recovery is the DR runbook.
- **Browser-level retries can produce duplicate writes** for non-idempotent endpoints. Mitigated by making the write paths idempotent (the chat completion endpoint is idempotent on retry by virtue of being read-only against governance state; ingest endpoints use upload-IDs to de-dup).
- **Syncthing v1.x → v2.x migration** is a manual operator step. Documented in maintenance SOP.

### Neutral

- The Cloudflare Workers tiebreaker is **pull-based, not push-based** — each node periodically asks "am I currently the designated primary?" The answer is set by an operator action (the vendor monitor dashboard's "promote node B" button). For two-node HA in a small firm this is enough; for richer multi-node topologies a real consensus protocol would be needed.

## References

- `docs/sop/ha-architecture.md` — full topology, sync class table, failover semantics
- `docs/runbooks/failover-test.md` — quarterly drill: block primary, verify standby, attest
- `docs/runbooks/audit-chain-broken.md` — when sync conflicts produce a chain break
- `sync_conflicts.py` — `*.sync-conflict-*` watcher
- `shared_lock.py` — fcntl-based cross-process locking for shared state writes
- `fleet.py` — fleet identity + node enumeration
- ADR-0003 (HMAC chain — what makes sync-induced divergence detectable)
- ADR-0009 (CF Workers tiebreaker)

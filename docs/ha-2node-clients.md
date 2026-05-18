# LocallyAI — High Availability for First Clients

**Edition:** 2-node deployment (two Mac Studios in the same office, no NAS).
**Status:** First-client release. A larger 3+ node + shared storage edition is on the roadmap.

This page tells you exactly what failover guarantees you get with this edition,
where the limits are, and what would change once you upgrade.

## What you get

- **One node can fail and the firm keeps working.** If Mac Studio A dies — power
  loss, hard crash, kernel panic, network cable kicked — Mac Studio B continues
  serving chat and document search.
- **Per-node audit chains.** Each Mac keeps its own HMAC-chained `audit.log`.
  Any tampering on either box is detected by `/admin/audit-verify`. The fleet
  endpoint at `/admin/fleet/audit-verify` returns the integrity status of
  every node in one call.
- **Shared user list.** Adding, rotating, or removing an API key on Mac A
  becomes effective on Mac B within ~10 seconds (Syncthing sync interval).
- **Shared erasure ledger.** A GDPR-Article-17 erasure on Mac A is honoured
  on Mac B within ~10 seconds.
- **Shared document corpus.** Documents uploaded to either Mac are searchable
  from both within ~10 seconds (Syncthing replicates the file; the receiving
  node re-indexes into its local Qdrant).

## What you do NOT get (versus the full HA edition)

| Behaviour | This edition | Full HA edition |
|---|---|---|
| Failover during a single in-flight request | User sees a 2–5 second blip; the worker app retries on the surviving node and the answer regenerates | Invisible — the surviving node serves a cached partial response |
| Document upload visibility | Eventually consistent (~10s) | Atomic — visible to all nodes the moment the upload returns |
| User-key visibility | Eventually consistent (~10s) | Immediate |
| Erasure propagation | Eventually consistent (~10s) | Immediate |
| Network partition between the two Macs | Last-writer-wins; possible diverged state on reconnection (admin reconciles via fleet dashboard) | Quorum check refuses writes during partition; no divergence possible |
| Streaming responses | If a node dies mid-stream, the client restarts the request from scratch on the other node — user sees the answer regenerate | Surviving node continues the stream from where the dead node left off |
| Storage tier failure | If both Macs lose access to the Syncthing-managed directory, all writes stop | Storage is itself HA (TrueNAS pair) |

## What the user actually sees during a failure

- **Failure during idle (no in-flight request):** Worker app's red/green node
  indicator flips to "1 node available." Next request is served by the
  surviving node. No interruption, no error.
- **Failure during an in-flight non-streaming chat:** Request appears to hang
  for 2–5 seconds, then the worker app silently retries on the surviving
  node and the response arrives. User sees a slightly slower response.
- **Failure during a streaming chat (typing animation):** The animation
  freezes, then 2–5 seconds later the response restarts from the beginning
  on the surviving node. User sees the answer regenerate.
- **Failure during a document upload:** Upload may need to be retried by
  the user. Already-uploaded chunks are visible on the surviving node
  within ~10 seconds.

## Operational expectations

- **Both Macs must be on the same LAN** with low-latency connectivity (\<10ms).
- **Both Macs must run the same OS version, the same model file, and the same
  backend** (MLX-only or Ollama-only — never one of each in a single fleet).
- **Time sync:** both Macs must be configured against the same NTP server
  (Apple's `time.apple.com` is fine). HMAC chains and audit timestamps
  assume \<1s clock skew.
- **One Mac is not "primary."** Both serve traffic; the worker app
  alternates by least-loaded.
- **Operators monitor health** via the fleet dashboard at
  `https://<either-mac>:8000/fleet/`. It shows per-node API health, audit
  chain status, sync lag, and recent alerts.

## Compliance posture

- **ISO 27001 A.5.30 (ICT readiness for business continuity):** Met for
  single-node failure. Documented gap: storage tier is not itself HA in
  this edition.
- **ISO 27001 A.8.14 (redundancy):** Met for compute. Not met for storage
  in this edition.
- **GDPR Art. 32 (security of processing):** Met. Pseudonymisation,
  encryption-at-rest (FileVault), encryption-in-transit (TLS), and
  HMAC-chained audit logs continue to apply per node.
- **GDPR Art. 17 (erasure):** Met with ~10s propagation. Erasure is
  recorded in `erasure.log` and synchronised to both nodes; both nodes
  refuse new audit writes for erased pseudonyms within 10 seconds of the
  erasure call returning success.
- **GDPR Art. 33 (breach notification):** Each node's sentinel runs
  independent breach detection on its own `security.log`. The fleet
  dashboard aggregates alerts so the operator sees fleet-wide events
  in one place.

## Upgrade path

When the firm acquires a NAS (TrueNAS HA pair recommended) and a third
node, the upgrade is config-only — no code changes:

1. Repoint `LOCALLYAI_SHARED_DIR` from the Syncthing directory to the
   NFS mount.
2. Add the third node, change Qdrant replication factor to 3, enable
   `majority` write consistency.
3. Add an HAProxy + keepalived router pair, point the worker app at
   the floating VIP. The smart-client URL list still holds the
   individual node URLs as fallback.
4. Switch `manage_users.py` and erasure operations from
   last-writer-wins to quorum writes.

Each step is independent; you can stage the upgrade across maintenance
windows without an outage.

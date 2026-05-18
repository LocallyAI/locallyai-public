# Qdrant 2-node HA — operations playbook

This page covers the LocallyAI 2-Mac edition where both Macs run Qdrant
in cluster mode against a single replicated collection.

## What this gives you

- **Reads survive 1 node down.** Both replicas of every shard are queryable
  from either Mac. If Mac-A goes dark, the worker app keeps retrieving from
  Mac-B's local Qdrant via the smart-client URL list.
- **Writes survive 1 node down (with grace).** Each write is replicated to
  both nodes. With `write_consistency_factor=2`, both must acknowledge
  before the write is considered successful. If 1 node is down, writes
  block until it returns OR the operator drops consistency to 1 (see
  "Operating with one node down" below).
- **No silent divergence.** A network partition between Mac-A and Mac-B
  causes writes to refuse on both sides (consistency factor 2 cannot be
  satisfied). Reads continue locally. The operator notices via the fleet
  dashboard within seconds.

## What this does NOT give you

- **2-node Qdrant cannot vote a leader during a partition** (Raft needs a
  majority; 1 of 2 is not majority). When the partition heals, Qdrant
  resumes normally.
- **No automatic split-brain reconciliation if a node is wiped.** If
  Mac-A is reinstalled and rejoins fresh, you must manually re-add it
  to the cluster (procedure below). Qdrant does not auto-detect "this
  node lost its data."

## Bring-up

1. Pick LAN IPs for both Macs (e.g. `10.0.0.11`, `10.0.0.12`).
2. Pick a shared `QDRANT_API_KEY` (32+ random hex chars).
3. **On Mac-A** (the bootstrap node):
   ```bash
   QDRANT_NODE_BIND_IP=10.0.0.11 \
   QDRANT_API_KEY=<shared-secret> \
   bash scripts/qdrant_cluster_setup.sh
   ```
4. **On Mac-B** (joining the cluster):
   ```bash
   QDRANT_NODE_BIND_IP=10.0.0.12 \
   QDRANT_BOOTSTRAP_PEER=http://10.0.0.11:6335 \
   QDRANT_API_KEY=<shared-secret> \
   bash scripts/qdrant_cluster_setup.sh
   ```
5. **On both Macs**, in `.env`:
   ```
   QDRANT_URLS=http://10.0.0.11:6333,http://10.0.0.12:6333
   QDRANT_API_KEY=<shared-secret>
   LOCALLYAI_HA=1
   ```
6. Restart the LocallyAI service on both Macs.
7. Verify:
   ```bash
   curl -sk -H "Authorization: Bearer $ADMIN" \
        https://localhost:8000/admin/fleet/qdrant-health
   ```
   Expected from each node: `mode: "cluster"`, `peer_count: 2`.

## Operating with one node down

When Mac-A is offline (planned maintenance, hardware failure):

- **Reads** continue from Mac-B's local Qdrant.
- **Writes block** until Mac-A is back, because
  `write_consistency_factor=2` requires both replicas to acknowledge.
- **To allow writes during the outage**, temporarily relax consistency:
  ```bash
  curl -X PATCH "http://10.0.0.12:6333/collections/locallyai_legal_poc" \
       -H "api-key: $QDRANT_API_KEY" \
       -H 'Content-Type: application/json' \
       -d '{"params":{"write_consistency_factor":1}}'
  ```
  Document this as a "partial-availability event" — the writes made
  during this window will sync to Mac-A on its return, but the chain of
  custody during the window is single-replica.
- **After Mac-A returns**, set consistency back to 2:
  ```bash
  curl -X PATCH "http://10.0.0.12:6333/collections/locallyai_legal_poc" \
       -H "api-key: $QDRANT_API_KEY" \
       -H 'Content-Type: application/json' \
       -d '{"params":{"write_consistency_factor":2}}'
  ```

## Identifying split-brain

Split-brain = both nodes are up, network between them is broken, and
each thinks it is alone. Symptoms:

- `/admin/fleet/qdrant-health` on Mac-A shows `peer_count: 1` (only itself).
- Same on Mac-B.
- Both `mode: "cluster"` (cluster is enabled, just disconnected).
- Fleet dashboard alert: `qdrant_split_brain`.

Action: do **not** issue writes on either side until the network
partition is fixed. Reads on either side return the data each had at
the moment of partition. After the network is back, Qdrant Raft
reconverges automatically — typically within seconds.

## Re-adding a wiped node

If Mac-A's storage was wiped (reinstall, disk failure replaced) and
Mac-A is now empty:

1. **Stop** Qdrant on Mac-A (it would otherwise start as a fresh
   single-node cluster and fight Mac-B).
   ```bash
   docker stop locallyai-qdrant && docker rm locallyai-qdrant
   ```
2. **From Mac-B**, force-remove the old peer entry. List peers:
   ```bash
   curl -sk "http://10.0.0.12:6333/cluster" -H "api-key: $QDRANT_API_KEY"
   ```
   Find the dead peer-id, then:
   ```bash
   curl -X DELETE "http://10.0.0.12:6333/cluster/peer/<dead-peer-id>?force=true" \
        -H "api-key: $QDRANT_API_KEY"
   ```
3. **On Mac-A**, re-bootstrap as a joining node (NOT as the first member):
   ```bash
   QDRANT_NODE_BIND_IP=10.0.0.11 \
   QDRANT_BOOTSTRAP_PEER=http://10.0.0.12:6335 \
   QDRANT_API_KEY=<shared-secret> \
   bash scripts/qdrant_cluster_setup.sh
   ```
4. **Replicate the collection back to Mac-A**. Pick a shard and
   replicate it from the live peer:
   ```bash
   curl -sk "http://10.0.0.12:6333/collections/locallyai_legal_poc/cluster" \
        -H "api-key: $QDRANT_API_KEY"
   # For each shard not present on Mac-A, post a replicate operation:
   curl -X POST "http://10.0.0.12:6333/collections/locallyai_legal_poc/cluster" \
        -H "api-key: $QDRANT_API_KEY" -H 'Content-Type: application/json' \
        -d '{"replicate_shard":{"shard_id":<id>,"from_peer_id":<live>,"to_peer_id":<new mac-a>}}'
   ```
5. Wait until `/cluster_status` shows both peers Active for every shard.
6. Verify:
   ```bash
   curl -sk -H "Authorization: Bearer $ADMIN" \
        https://localhost:8000/admin/fleet/qdrant-health
   ```

## Backup posture

- Qdrant data lives at `<repo>/storage/qdrant` on each node.
- Cluster mode replicates every shard to both nodes — losing one node
  is not data loss.
- For point-in-time recovery (e.g. ransomware, accidental delete of
  a collection): take Qdrant snapshots nightly via cron:
  ```bash
  curl -X POST "http://localhost:6333/collections/locallyai_legal_poc/snapshots" \
       -H "api-key: $QDRANT_API_KEY"
  ```
  Snapshots land under `storage/qdrant/snapshots/`. Copy off-machine
  (TrueNAS, S3 with KMS) per the firm's retention policy.

## Compliance posture

- **ISO 27001 A.8.13 (information backup):** Snapshots above + per-node
  replicas satisfy the control. Document the snapshot cadence in the
  RoPA for the firm.
- **ISO 27001 A.8.14 (redundancy):** Met for retrieval (vector search).
  Compute redundancy is via the API itself (per-node).
- **GDPR Art. 32 (security of processing):** API key on the cluster
  prevents unauthenticated access. LAN traffic is unencrypted at the
  Qdrant transport — acceptable on a trusted office VLAN; if your
  threat model includes LAN sniffing, deploy in a WireGuard mesh.

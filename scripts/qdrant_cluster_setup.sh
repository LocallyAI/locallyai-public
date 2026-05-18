#!/usr/bin/env bash
# qdrant_cluster_setup.sh
#
# Bring up Qdrant in cluster mode on this Mac as a member of the 2-node HA
# fleet. Run once per Mac. Each node binds Qdrant to its LAN IP and joins
# the cluster via the bootstrap peer URL.
#
# Required env (per node):
#   QDRANT_NODE_BIND_IP    — this node's LAN IP (e.g. 10.0.0.11)
#   QDRANT_BOOTSTRAP_PEER  — the OTHER node's URL (e.g. http://10.0.0.12:6335),
#                            empty on the FIRST node bringing the cluster up
#   QDRANT_API_KEY         — shared secret read by every node
#
# After both nodes are up:
#   curl -s -H "api-key: $QDRANT_API_KEY" http://<this-ip>:6333/cluster | jq
# should report two healthy peers.
#
# Storage stays under storage/qdrant — local to each node by design (Qdrant
# replicates the data internally; you don't share its files between nodes).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

note()  { printf '\033[36m[qdrant-cluster]\033[0m %s\n' "$*"; }
fail()  { printf '\033[31m[qdrant-cluster]\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || \
  fail "Docker required: install Docker Desktop for Mac and ensure it is running"

: "${QDRANT_NODE_BIND_IP:?Set QDRANT_NODE_BIND_IP to the LAN IP of this Mac}"
: "${QDRANT_API_KEY:?Set QDRANT_API_KEY to a shared secret matching on both nodes}"
QDRANT_BOOTSTRAP_PEER="${QDRANT_BOOTSTRAP_PEER:-}"

mkdir -p storage/qdrant

CONTAINER_NAME="locallyai-qdrant"

# --- 1. Stop any existing single-node Qdrant container ---------------------
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}\$"; then
  note "Stopping existing ${CONTAINER_NAME} container"
  docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  docker rm   "${CONTAINER_NAME}" >/dev/null 2>&1 || true
fi

# --- 2. Build the cluster args ---------------------------------------------
CLUSTER_ARGS=(
  --uri "http://${QDRANT_NODE_BIND_IP}:6335"
)
if [ -n "$QDRANT_BOOTSTRAP_PEER" ]; then
  CLUSTER_ARGS+=( --bootstrap "$QDRANT_BOOTSTRAP_PEER" )
  note "This node will JOIN the cluster via $QDRANT_BOOTSTRAP_PEER"
else
  note "This node is the FIRST cluster member"
  note "On the second Mac, set QDRANT_BOOTSTRAP_PEER=http://${QDRANT_NODE_BIND_IP}:6335"
fi

# --- 3. Run the container ---------------------------------------------------
# Ports:
#   6333  REST API (clients connect here)
#   6334  gRPC API
#   6335  P2P consensus (cluster traffic — must be reachable between nodes)
note "Starting ${CONTAINER_NAME} (cluster mode)"
docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  -p "${QDRANT_NODE_BIND_IP}:6333:6333" \
  -p "${QDRANT_NODE_BIND_IP}:6334:6334" \
  -p "${QDRANT_NODE_BIND_IP}:6335:6335" \
  -v "$REPO_DIR/storage/qdrant:/qdrant/storage" \
  -e "QDRANT__SERVICE__API_KEY=${QDRANT_API_KEY}" \
  -e "QDRANT__CLUSTER__ENABLED=true" \
  -e "QDRANT__CLUSTER__P2P__PORT=6335" \
  qdrant/qdrant:v1.12.4 \
  ./qdrant "${CLUSTER_ARGS[@]}" >/dev/null

# --- 4. Wait for readiness --------------------------------------------------
note "Waiting for Qdrant to come up on ${QDRANT_NODE_BIND_IP}:6333"
for _ in $(seq 1 30); do
  if curl -fsS -H "api-key: ${QDRANT_API_KEY}" \
       "http://${QDRANT_NODE_BIND_IP}:6333/readyz" >/dev/null 2>&1; then
    note "Qdrant ready"
    break
  fi
  sleep 1
done

# --- 5. Print cluster state -------------------------------------------------
echo
note "Cluster state:"
curl -fsS -H "api-key: ${QDRANT_API_KEY}" \
     "http://${QDRANT_NODE_BIND_IP}:6333/cluster" || true
echo

cat <<EOF

──────────────────────────────────────────────────────────────────────
Qdrant cluster member started on ${QDRANT_NODE_BIND_IP}.

Next steps:
  1. On the SECOND Mac, run:
       QDRANT_NODE_BIND_IP=<that mac's IP> \\
       QDRANT_BOOTSTRAP_PEER=http://${QDRANT_NODE_BIND_IP}:6335 \\
       QDRANT_API_KEY=$QDRANT_API_KEY \\
       bash scripts/qdrant_cluster_setup.sh

  2. On BOTH macs, set in .env:
       QDRANT_URLS=http://10.0.0.11:6333,http://10.0.0.12:6333
       QDRANT_API_KEY=$QDRANT_API_KEY
       LOCALLYAI_HA=1

  3. Restart the LocallyAI service on both nodes.

  4. Verify:
       curl -sk -H "Authorization: Bearer \$ADMIN" \\
            https://localhost:8000/admin/fleet/qdrant-health

     Expect both peers reporting status:"alive".

If the second node refuses to join: check that port 6335 is reachable
between the two LAN IPs (firewall / pfctl). Cluster traffic is
unauthenticated by design within a trusted LAN — if you need
authenticated cluster traffic, deploy in a VPN.
──────────────────────────────────────────────────────────────────────
EOF

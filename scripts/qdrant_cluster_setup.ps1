# qdrant_cluster_setup.ps1 — Bring up Qdrant in cluster mode on this
# Windows node as a member of the 2-node HA fleet. Mirrors
# scripts/qdrant_cluster_setup.sh.
#
# Required env (per node):
#   QDRANT_NODE_BIND_IP    this node's LAN IP (e.g. 10.0.0.11)
#   QDRANT_BOOTSTRAP_PEER  the OTHER node's URL (e.g. http://10.0.0.12:6335),
#                          empty on the FIRST node bringing the cluster up
#   QDRANT_API_KEY         shared secret read by every node

[CmdletBinding()]
param(
    [string] $Repo               = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [string] $NodeBindIp         = "$env:QDRANT_NODE_BIND_IP",
    [string] $BootstrapPeer      = "$env:QDRANT_BOOTSTRAP_PEER",
    [string] $ApiKey             = "$env:QDRANT_API_KEY",
    [string] $ContainerName      = "locallyai-qdrant",
    [string] $Image              = "qdrant/qdrant:v1.12.4"
)
$ErrorActionPreference = "Stop"
function Note($m) { Write-Host "[qdrant-cluster] $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "[qdrant-cluster] $m" -ForegroundColor Red; exit 1 }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker required: install Docker Desktop for Windows and ensure it is running"
}
if (-not $NodeBindIp) { Fail "Set QDRANT_NODE_BIND_IP to this node's LAN IP" }
if (-not $ApiKey)     { Fail "Set QDRANT_API_KEY to a shared secret matching on both nodes" }

$storageDir = Join-Path $Repo "storage\qdrant"
New-Item -ItemType Directory -Force -Path $storageDir | Out-Null

# Stop any existing single-node Qdrant container.
$existing = & docker ps -a --format "{{.Names}}" 2>$null
if ($existing -contains $ContainerName) {
    Note "Stopping existing $ContainerName container"
    & docker stop $ContainerName | Out-Null
    & docker rm $ContainerName   | Out-Null
}

# Build the cluster args.
$clusterArgs = @("--uri", "http://${NodeBindIp}:6335")
if ($BootstrapPeer) {
    $clusterArgs += @("--bootstrap", $BootstrapPeer)
    Note "This node will JOIN the cluster via $BootstrapPeer"
} else {
    Note "This node is the FIRST cluster member"
    Note "On the second node, set QDRANT_BOOTSTRAP_PEER=http://${NodeBindIp}:6335"
}

# Run the container.
Note "Starting $ContainerName (cluster mode)"
$dockerRun = @(
    "run", "-d",
    "--name", $ContainerName,
    "--restart", "unless-stopped",
    "-p", "${NodeBindIp}:6333:6333",
    "-p", "${NodeBindIp}:6334:6334",
    "-p", "${NodeBindIp}:6335:6335",
    "-v", "${storageDir}:/qdrant/storage",
    "-e", "QDRANT__SERVICE__API_KEY=$ApiKey",
    "-e", "QDRANT__CLUSTER__ENABLED=true",
    "-e", "QDRANT__CLUSTER__P2P__PORT=6335",
    $Image,
    "./qdrant"
) + $clusterArgs
& docker @dockerRun | Out-Null

# Wait for readiness.
Note "Waiting for Qdrant to come up on ${NodeBindIp}:6333"
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        Invoke-WebRequest -Uri "http://${NodeBindIp}:6333/readyz" `
                          -Headers @{ "api-key" = $ApiKey } `
                          -TimeoutSec 1 -UseBasicParsing | Out-Null
        $ready = $true; break
    } catch {
        Start-Sleep -Seconds 1
    }
}
if ($ready) { Note "Qdrant ready" } else { Fail "Qdrant did not become ready" }

Write-Host ""
Note "Cluster state:"
try {
    Invoke-RestMethod -Uri "http://${NodeBindIp}:6333/cluster" `
                      -Headers @{ "api-key" = $ApiKey } -TimeoutSec 3 |
        ConvertTo-Json -Depth 6 | Write-Host
} catch {
    Write-Host "(could not read cluster state: $($_.Exception.Message))"
}

Write-Host ""
Write-Host "──────────────────────────────────────────────────────────────────────"
Write-Host "Qdrant cluster member started on $NodeBindIp."
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. On the SECOND node, run:"
Write-Host "       `$env:QDRANT_NODE_BIND_IP = '<that nodes IP>'"
Write-Host "       `$env:QDRANT_BOOTSTRAP_PEER = 'http://${NodeBindIp}:6335'"
Write-Host "       `$env:QDRANT_API_KEY = '$ApiKey'"
Write-Host "       PowerShell -ExecutionPolicy Bypass -File scripts\qdrant_cluster_setup.ps1"
Write-Host ""
Write-Host "  2. On BOTH nodes, set in .env:"
Write-Host "       QDRANT_URLS=http://10.0.0.11:6333,http://10.0.0.12:6333"
Write-Host "       QDRANT_API_KEY=$ApiKey"
Write-Host "       LOCALLYAI_HA=1"
Write-Host ""
Write-Host "  3. Restart the LocallyAIServer service on both nodes."
Write-Host "──────────────────────────────────────────────────────────────────────"

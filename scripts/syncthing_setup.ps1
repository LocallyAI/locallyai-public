# syncthing_setup.ps1 — Bring up Syncthing on this Windows node and pair
# it with the other node in the LocallyAI HA fleet. Mirrors
# scripts/syncthing_setup.sh.
#
# Run once per Windows node (Administrator).

[CmdletBinding()]
param(
    [string] $Repo      = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [string] $SharedDir = "$env:LOCALLYAI_SHARED_DIR"
)
$ErrorActionPreference = "Stop"
function Note($m) { Write-Host "[syncthing] $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "[syncthing] $m" -ForegroundColor Red; exit 1 }

if (-not $SharedDir) { $SharedDir = Join-Path $Repo "shared" }

# 1. Install Syncthing if missing.
if (-not (Get-Command syncthing -ErrorAction SilentlyContinue)) {
    Note "Installing Syncthing via winget"
    winget install --silent --accept-source-agreements --accept-package-agreements Syncthing.Syncthing | Out-Null
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
}

# 2. Shared dir.
New-Item -ItemType Directory -Force -Path $SharedDir | Out-Null
$me = [Security.Principal.WindowsIdentity]::GetCurrent()
& icacls $SharedDir /inheritance:r /grant "$($me.Name):(F)" | Out-Null
Note "Shared directory: $SharedDir"

# 3. Generate Syncthing identity (idempotent — only runs first time).
$stHome = "$env:LOCALAPPDATA\Syncthing"
if (-not (Test-Path "$stHome\config.xml")) {
    Note "Generating Syncthing config (one-time)"
    & syncthing --generate=$stHome | Out-Null
}

# 4. Register a per-user scheduled task as the keep-alive (NSSM-free path —
#    Syncthing runs in user context, not a system service, because it must
#    be able to read the LocallyAI repo dir owned by the operator account).
$taskName = "LocallyAI-Syncthing"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Note "Registering scheduled task: $taskName"
    $action = New-ScheduledTaskAction -Execute (Get-Command syncthing).Source `
              -Argument "--no-browser --no-restart --logflags=0"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $me.Name
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
                -DontStopOnIdleEnd -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
                           -Settings $settings -RunLevel Limited | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 3
}

# 5. Read API key + device id from config.xml
$cfg = Get-Content "$stHome\config.xml" -Raw
$apiKey   = ([regex]::Match($cfg, "<apikey>(.*?)</apikey>").Groups[1].Value)
$deviceId = (& syncthing --device-id 2>$null).Trim()
if (-not $apiKey)   { Fail "Could not read Syncthing API key" }
if (-not $deviceId) { Fail "Could not read Syncthing device id" }
$stUrl = "http://127.0.0.1:8384"

# 6. Add the locallyai-shared folder via REST (idempotent).
$folderId = "locallyai-shared"
try {
    Invoke-RestMethod -Uri "$stUrl/rest/config/folders/$folderId" `
        -Headers @{ "X-API-Key" = $apiKey } -ErrorAction Stop | Out-Null
    Note "Folder $folderId already configured"
} catch {
    Note "Configuring shared folder $folderId at $SharedDir"
    $body = @{
        id = $folderId; label = "LocallyAI HA shared"
        path = $SharedDir; type = "sendreceive"
        rescanIntervalS = 10; fsWatcherEnabled = $true; fsWatcherDelayS = 1
    } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri "$stUrl/rest/config/folders" -Method Post `
        -Headers @{ "X-API-Key" = $apiKey } -ContentType "application/json" `
        -Body $body | Out-Null
}

Write-Host ""
Write-Host "──────────────────────────────────────────────────────────────────────"
Write-Host "Syncthing is up on this Windows node."
Write-Host ""
Write-Host "  Device ID  : $deviceId"
Write-Host "  Folder ID  : $folderId"
Write-Host "  Folder Path: $SharedDir"
Write-Host "  Web GUI    : $stUrl"
Write-Host ""
Write-Host "Next steps for the SECOND node:"
Write-Host "  1. Run this script (or the .sh on a Mac) on the other node."
Write-Host "  2. Paste this Device ID on the other node's GUI under"
Write-Host "     'Add Remote Device', and vice versa."
Write-Host "  3. Accept the locallyai-shared folder share on each side."
Write-Host "  4. Wait until both folders show 'Up to Date'."
Write-Host "  5. Set LOCALLYAI_SHARED_DIR=$SharedDir in .env on both nodes."
Write-Host "  6. Restart the LocallyAIServer service on both nodes."
Write-Host "──────────────────────────────────────────────────────────────────────"

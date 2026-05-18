# LocallyAI install bootstrap (Windows / PowerShell).
#
# Mirrors src/dashboard/bootstrap (bash) for Windows installations
# running install.ps1 instead of install.sh.
#
# Invoked from the form's "Generate install command" panel. The
# command shown there pipes this script into PowerShell with the
# install token in $env:LOCALLYAI_INTAKE_URL — this script fetches
# the intake blob (single-use), decodes it, prompts IT for the
# private deploy key, clones the repo, writes .env, and runs
# install.ps1.
#
# Verify before running (recommended for production firms):
#   irm https://locallyai-monitor.<vendor-cf>.workers.dev/bootstrap.ps1 -OutFile $env:TEMP\bootstrap.ps1
#   irm https://locallyai-monitor.<vendor-cf>.workers.dev/bootstrap.ps1.sig -OutFile $env:TEMP\bootstrap.ps1.sig
#   gpg --verify $env:TEMP\bootstrap.ps1.sig $env:TEMP\bootstrap.ps1
#   # if "Good signature":
#   $env:LOCALLYAI_INTAKE_URL = "https://.../onboarding/intake?t=<TOKEN>"
#   & $env:TEMP\bootstrap.ps1

$ErrorActionPreference = 'Stop'

$BOOTSTRAP_VERSION = '1.0.0'
$INSTALL_DIR = if ($env:LOCALLYAI_INSTALL_DIR) { $env:LOCALLYAI_INSTALL_DIR } else { Join-Path $HOME 'locallyai' }
$REPO_URL    = if ($env:LOCALLYAI_REPO_URL)    { $env:LOCALLYAI_REPO_URL }    else { 'git@github.com:LocallyAI/locallyai.git' }
$REPO_BRANCH = if ($env:LOCALLYAI_REPO_BRANCH) { $env:LOCALLYAI_REPO_BRANCH } else { 'main' }

function Die($msg)  { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Info($msg) { Write-Host "  $msg" }
function Step($msg) { Write-Host ""; Write-Host "▸ $msg" -ForegroundColor Blue }
function Ok($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }

# ── Pre-flight ──────────────────────────────────────────────────────────
if (-not (Get-Command git  -ErrorAction SilentlyContinue)) { Die "git not found — install Git for Windows from https://git-scm.com/download/win" }
if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { Die "curl.exe not found — Windows 10 1803+ ships with it; check your PATH" }

# ── Fetch the intake blob (single-use, consumes the token) ──────────────
$intakeUrl = $env:LOCALLYAI_INTAKE_URL
if (-not $intakeUrl) {
    Die "LOCALLYAI_INTAKE_URL not set. Get the install command from the intake form at the onboarding URL your vendor sent."
}

Step "Fetching intake from $intakeUrl"
try {
    $blob = (Invoke-WebRequest -Uri $intakeUrl -UseBasicParsing -ErrorAction Stop).Content.Trim()
} catch {
    Die "Could not fetch intake blob: $($_.Exception.Message). The install link may have already been used (single-use, by design). Regenerate from the form."
}
if (-not $blob -or $blob -match '^This install link') {
    Die "Intake blob empty or expired. Regenerate the install command from the form."
}

# ── Decode + sanity-check ───────────────────────────────────────────────
try {
    $decoded = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($blob))
} catch {
    Die "Intake blob is not valid base64 — re-copy the install command from the form."
}
if (-not ($decoded -match 'LOCALLYAI_FIRM_NAME=')) {
    Die "Intake blob is malformed — no LOCALLYAI_FIRM_NAME line."
}
$firmName = ([regex]::Match($decoded, '(?m)^LOCALLYAI_FIRM_NAME=("?)(.*?)\1\s*$')).Groups[2].Value
$region   = ([regex]::Match($decoded, '(?m)^LOCALLYAI_DATA_REGION=(.*?)\s*$')).Groups[1].Value

# ── Show what we're about to do ─────────────────────────────────────────
Write-Host ""
Write-Host "  ════════════════════════════════════════════════════════════════════"
Write-Host "   LocallyAI install bootstrap v$BOOTSTRAP_VERSION (Windows)"
Write-Host ""
Write-Host "   Firm:        $firmName"
Write-Host "   Region:      $region"
Write-Host "   Install at:  $INSTALL_DIR"
Write-Host "   From:        $REPO_URL ($REPO_BRANCH)"
Write-Host ""
Write-Host "   This script will:"
Write-Host "     1. Prompt for the LocallyAI private SSH deploy key"
Write-Host "        (path or paste — never echoed or saved beyond install)"
Write-Host "     2. Clone the LocallyAI repo to the install dir"
Write-Host "     3. Write .env from the intake values + telemetry token"
Write-Host "     4. Launch PowerShell -File .\install.ps1 (elevated)"
Write-Host ""
Write-Host "   Ctrl-C now to abort. Press Enter to continue."
Write-Host "  ════════════════════════════════════════════════════════════════════"
Read-Host | Out-Null

# ── Deploy key — file path preferred, paste as fallback ─────────────────
Step "Deploy key — PRIVATE key issued by your LocallyAI vendor"

$DEPLOY_KEY_TEXT = $null

if ($env:LOCALLYAI_DEPLOY_KEY_FILE) {
    if (-not (Test-Path $env:LOCALLYAI_DEPLOY_KEY_FILE)) {
        Die "LOCALLYAI_DEPLOY_KEY_FILE='$($env:LOCALLYAI_DEPLOY_KEY_FILE)' is not a readable file"
    }
    Info "Reading deploy key from `$env:LOCALLYAI_DEPLOY_KEY_FILE"
    $DEPLOY_KEY_TEXT = Get-Content -LiteralPath $env:LOCALLYAI_DEPLOY_KEY_FILE -Raw
}

if (-not $DEPLOY_KEY_TEXT) {
    Write-Host "  Enter the FULL PATH to the private key file."
    Write-Host "    Example: C:\Users\$($env:USERNAME)\.ssh\locallyai_deploy"
    Write-Host "    (Tip: drag the file into this window — the path appears for you.)"
    Write-Host "    Or press Enter to paste the key contents instead."
    $keyPath = Read-Host "  Path"
    $keyPath = $keyPath.Trim().Trim('"').Trim("'")
    if ($keyPath) {
        if (-not (Test-Path $keyPath)) { Die "no file at: $keyPath" }
        $DEPLOY_KEY_TEXT = Get-Content -LiteralPath $keyPath -Raw
        Ok "Loaded key from $keyPath"
    }
}

if (-not $DEPLOY_KEY_TEXT) {
    Write-Host ""
    Write-Host "  Paste the WHOLE key — BEGIN line, all the gibberish, END line."
    Write-Host "  When done, type END on a new line and press Enter:"
    Write-Host ""
    $lines = New-Object System.Collections.Generic.List[string]
    while ($true) {
        $l = Read-Host
        if ($l -eq 'END') { break }
        $lines.Add($l)
    }
    $DEPLOY_KEY_TEXT = ($lines -join "`n")
}

# Validate the key
if (-not $DEPLOY_KEY_TEXT) {
    Die "no key provided. Re-run and either set LOCALLYAI_DEPLOY_KEY_FILE, enter the file path, or paste contents."
}
if ($DEPLOY_KEY_TEXT -match '^(ssh-(ed25519|rsa|dss)|ecdsa-) ') {
    Die "this is the PUBLIC key (starts with 'ssh-…'). We need the matching PRIVATE key — same filename without the .pub."
}
if ($DEPLOY_KEY_TEXT -notmatch '-----BEGIN.*PRIVATE KEY-----') {
    Die "no '-----BEGIN ... PRIVATE KEY-----' header found. Most common cause: pasted the public key (.pub) by mistake."
}
if ($DEPLOY_KEY_TEXT -notmatch '-----END.*PRIVATE KEY-----') {
    Die "key looks truncated (BEGIN header but no END footer). Try the file-path option."
}

# ── Telemetry token (already in the blob from auto-issuance) ────────────
$telemetryFromBlob = ([regex]::Match($decoded, '(?m)^LOCALLYAI_TELEMETRY_TOKEN=(.*?)\s*$')).Groups[1].Value
if (-not $telemetryFromBlob) {
    Step "Telemetry token (the 64-char hex from the form's panel — or press Enter to skip)"
    $telemetryFromBlob = Read-Host "  Token" -MaskInput
    if ($telemetryFromBlob -and $telemetryFromBlob -notmatch '^[0-9a-fA-F]{64}$') {
        Die "telemetry token is not 64 hex characters — re-copy from the form."
    }
}

# ── Stash deploy key in a temp file, configure git to use it via SSH ────
Step "Setting up SSH for git clone"
function New-TempFile {
    [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), [System.IO.Path]::GetRandomFileName())
}
$keyTmp = New-TempFile
$DEPLOY_KEY_TEXT | Out-File -LiteralPath $keyTmp -Encoding ascii -NoNewline
# Lock down the key file ACL — only current user can read.
$acl = Get-Acl $keyTmp
$acl.SetAccessRuleProtection($true, $false)
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule($env:USERNAME, 'Read', 'Allow')
$acl.AddAccessRule($rule)
Set-Acl -Path $keyTmp -AclObject $acl

# Pin github.com host key
$sshDir = Join-Path $HOME '.ssh'
if (-not (Test-Path $sshDir)) { New-Item -ItemType Directory -Path $sshDir -Force | Out-Null }
$knownHosts = Join-Path $sshDir 'known_hosts'
if (-not (Test-Path $knownHosts) -or -not ((Get-Content $knownHosts -ErrorAction SilentlyContinue) -match '^github\.com')) {
    try {
        & ssh-keyscan -t ed25519,rsa github.com 2>$null | Add-Content -Path $knownHosts
        Ok "github.com pinned in known_hosts"
    } catch {
        Info "Could not run ssh-keyscan — known_hosts may need manual setup if clone fails"
    }
}

$env:GIT_SSH_COMMAND = "ssh -i `"$keyTmp`" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
Ok "Deploy key staged at $keyTmp (read-only to current user)"

# ── Clone the repo ──────────────────────────────────────────────────────
Step "Cloning $REPO_URL → $INSTALL_DIR"
try {
    if (Test-Path (Join-Path $INSTALL_DIR '.git')) {
        Info "Repo already exists; fetching latest from $REPO_BRANCH"
        Push-Location $INSTALL_DIR
        & git fetch origin $REPO_BRANCH
        & git checkout $REPO_BRANCH
        & git pull --ff-only origin $REPO_BRANCH
        Pop-Location
    } else {
        $parent = Split-Path -Parent $INSTALL_DIR
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        & git clone --branch $REPO_BRANCH $REPO_URL $INSTALL_DIR
    }
    if ($LASTEXITCODE -ne 0) { throw "git failed (exit $LASTEXITCODE)" }
    Ok "Repo ready at $INSTALL_DIR"
} finally {
    # Always remove the temp key file, even on git failure
    Remove-Item -LiteralPath $keyTmp -Force -ErrorAction SilentlyContinue
}

# ── Write .env (mode 0600 equivalent: ACL restricted to current user) ──
Step "Writing $INSTALL_DIR\.env"
$envLines = @(
    "# Generated by LocallyAI install bootstrap.ps1 v$BOOTSTRAP_VERSION on $(Get-Date -Format o)"
    "# Source: intake form on the LocallyAI monitor Worker"
    ""
    $decoded.TrimEnd("`r","`n")
)
if ($telemetryFromBlob -and -not ($decoded -match 'LOCALLYAI_TELEMETRY_TOKEN=')) {
    $envLines += "LOCALLYAI_TELEMETRY_TOKEN=$telemetryFromBlob"
}
$envPath = Join-Path $INSTALL_DIR '.env'
$envLines -join "`r`n" | Out-File -LiteralPath $envPath -Encoding ascii -NoNewline

$envAcl = Get-Acl $envPath
$envAcl.SetAccessRuleProtection($true, $false)
$envRule = New-Object System.Security.AccessControl.FileSystemAccessRule($env:USERNAME, 'Read,Write', 'Allow')
$envAcl.AddAccessRule($envRule)
Set-Acl -Path $envPath -AclObject $envAcl
Ok ".env written (ACL restricted to $env:USERNAME)"

# ── Run install.ps1 ─────────────────────────────────────────────────────
Step "Launching install.ps1 in an elevated PowerShell window"
$installPs1 = Join-Path $INSTALL_DIR 'install.ps1'
if (-not (Test-Path $installPs1)) { Die "install.ps1 not found at $installPs1 — clone may have failed silently" }

# install.ps1 requires Administrator. Re-launch in an elevated window.
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "powershell.exe"
$psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$installPs1`""
$psi.WorkingDirectory = $INSTALL_DIR
$psi.Verb = "runas"   # triggers UAC prompt
$psi.UseShellExecute = $true
[System.Diagnostics.Process]::Start($psi) | Out-Null
Ok "install.ps1 launched in a new elevated window. Watch that window for prompts."

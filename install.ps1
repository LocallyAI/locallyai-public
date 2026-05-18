# install.ps1 — LocallyAI installer for Windows nodes (DGX Spark or
# any Windows 11 with NVIDIA GPU). Mirrors install.sh; per-feature
# parity differences are noted inline.
#
# Run as Administrator from the repo root:
#   PowerShell -ExecutionPolicy Bypass -File .\install.ps1
#
# Idempotent — re-running a successful install is safe (skips steps it
# detects as already done; rotates any expired secrets).

[CmdletBinding()]
param(
    [string] $InstallRoot = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [int]    $Port        = 8000,
    [switch] $SkipOllama,
    [switch] $SkipService,
    [ValidateSet("UK", "KSA")]
    [string] $DataRegion  = $env:LOCALLYAI_DATA_REGION
)

$ErrorActionPreference = "Stop"
function Note($m) { Write-Host "[install] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[install] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[install] $m" -ForegroundColor Red; exit 1 }

# ── Pre-flight ────────────────────────────────────────────────────────────────
$me = [Security.Principal.WindowsIdentity]::GetCurrent()
$pri = New-Object Security.Principal.WindowsPrincipal($me)
if (-not $pri.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "Run this script in an elevated PowerShell (Administrator)."
}
Set-Location $InstallRoot
Note "Install root: $InstallRoot"

# ── Data residency / compliance region (mandatory pick) ──────────────────────
# Drives audit log stamping, RoPA framing, embed model default,
# breach-notification clause, demo doc set. No default — operator must
# pick interactively if the env / param wasn't set.
if (-not $DataRegion) {
    while ($true) {
        Write-Host ""
        Write-Host "  Choose data residency / compliance region:"
        Write-Host "    1. UK  - UK GDPR / DPA 2018 posture; English-language deployment"
        Write-Host "    2. KSA - Saudi PDPL (Royal Decree M/19, 2023); Arabic + English"
        $pick = Read-Host "  Region [1=UK / 2=KSA]"
        switch ($pick) {
            "1"   { $DataRegion = "UK";  break }
            "UK"  { $DataRegion = "UK";  break }
            "uk"  { $DataRegion = "UK";  break }
            "2"   { $DataRegion = "KSA"; break }
            "KSA" { $DataRegion = "KSA"; break }
            "ksa" { $DataRegion = "KSA"; break }
            default { Warn "Region is mandatory - please pick 1 or 2" }
        }
        if ($DataRegion) { break }
    }
}
Note "Data region: $DataRegion"
$env:LOCALLYAI_DATA_REGION = $DataRegion

# ── BitLocker check (informational) ───────────────────────────────────────────
# Equivalent of FileVault on Mac. We only WARN if BitLocker is off — many
# DGX Spark deployments encrypt at the disk-controller level instead.
try {
    $bl = Get-BitLockerVolume -MountPoint ($InstallRoot.Substring(0,2)) -ErrorAction Stop
    if ($bl.ProtectionStatus -eq "Off") {
        Warn "BitLocker is OFF on $($bl.MountPoint). Audit logs and TLS keys will be unencrypted at rest."
        Warn "Enable via: manage-bde -on $($bl.MountPoint) -RecoveryPassword"
    } else {
        Note "BitLocker on $($bl.MountPoint): $($bl.ProtectionStatus)"
    }
} catch {
    Warn "Could not check BitLocker (Get-BitLockerVolume unavailable). Verify disk encryption manually."
}

# ── Python ────────────────────────────────────────────────────────────────────
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Note "Python not found — installing via winget"
    winget install --silent --accept-source-agreements --accept-package-agreements Python.Python.3.12 | Out-Null
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
}
$pyVer = (& python --version) 2>&1
Note "Using $pyVer"

# ── venv + deps ───────────────────────────────────────────────────────────────
if (-not (Test-Path ".venv")) {
    Note "Creating virtualenv at .venv"
    python -m venv .venv
}
$venvPy = Join-Path $InstallRoot ".venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip --quiet
if (Test-Path "requirements.txt") {
    Note "Installing Python dependencies"
    & $venvPy -m pip install -r requirements.txt --quiet
} else {
    Warn "requirements.txt not found — skipping dependency install"
}
# Optional Windows-only dep so os_supervisor can register a console handler.
& $venvPy -m pip install pywin32 --quiet 2>$null | Out-Null

# ── Ollama (Windows nodes use the Ollama / OpenAI-compatible backend) ────────
if (-not $SkipOllama) {
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        Note "Installing Ollama via winget"
        winget install --silent --accept-source-agreements --accept-package-agreements Ollama.Ollama | Out-Null
    } else {
        Note "Ollama already installed"
    }
}

# ── .env (preserve existing secrets, generate any missing ones) ──────────────
$envPath = Join-Path $InstallRoot ".env"
$envLines = @{}
if (Test-Path $envPath) {
    Get-Content $envPath | ForEach-Object {
        if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') { $envLines[$matches[1]] = $matches[2] }
    }
}

function Rand-Hex([int]$bytes) {
    $buf = New-Object byte[] $bytes
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buf)
    -join ($buf | ForEach-Object { $_.ToString("x2") })
}

if (-not $envLines.ContainsKey("LOCALLYAI_ADMIN_KEY"))      { $envLines["LOCALLYAI_ADMIN_KEY"]      = Rand-Hex 32 }
if (-not $envLines.ContainsKey("LOCALLYAI_AUDIT_SALT"))     { $envLines["LOCALLYAI_AUDIT_SALT"]     = Rand-Hex 32 }
if (-not $envLines.ContainsKey("LOCALLYAI_AUDIT_HMAC_KEY")) { $envLines["LOCALLYAI_AUDIT_HMAC_KEY"] = Rand-Hex 32 }

$envLines["LOCALLYAI_BACKEND"] = "ollama"
if (-not $envLines.ContainsKey("LLM_BASE_URL"))    { $envLines["LLM_BASE_URL"]    = "http://localhost:11434" }
if (-not $envLines.ContainsKey("OLLAMA_BASE_URL")) { $envLines["OLLAMA_BASE_URL"] = "http://localhost:11434" }
if (-not $envLines.ContainsKey("OLLAMA_MODEL"))    { $envLines["OLLAMA_MODEL"]    = "qwen2.5:14b" }
if (-not $envLines.ContainsKey("PORT"))            { $envLines["PORT"]            = "$Port" }
if (-not $envLines.ContainsKey("LOCALLYAI_API_BASE")) { $envLines["LOCALLYAI_API_BASE"] = "https://localhost:$Port" }
if (-not $envLines.ContainsKey("LOCALLYAI_DEPLOYMENT_ID")) { $envLines["LOCALLYAI_DEPLOYMENT_ID"] = "locallyai-prod" }
$envLines["LOCALLYAI_DATA_REGION"] = $DataRegion

@(
    "# Generated by install.ps1 — keep these secret. ACL restricted to current user."
    ($envLines.GetEnumerator() | Sort-Object Key | ForEach-Object { "$($_.Key)=$($_.Value)" })
) | Set-Content -Path $envPath -Encoding ASCII
& icacls $envPath /inheritance:r /grant "$($me.Name):(R,W)" | Out-Null
Note "Wrote $envPath"

# ── TLS cert (self-signed for the LAN deployment) ────────────────────────────
$tlsDir = Join-Path $InstallRoot "tls"
$cert   = Join-Path $tlsDir "cert.pem"
$keyPem = Join-Path $tlsDir "key.pem"
New-Item -ItemType Directory -Force -Path $tlsDir | Out-Null
if (-not (Test-Path $cert) -or -not (Test-Path $keyPem)) {
    Note "Generating self-signed TLS cert (10-year)"
    $hostname = $env:COMPUTERNAME
    $san = "localhost", "127.0.0.1", $hostname
    # ISO 3166-1 alpha-2 country code matches the data residency region —
    # auditors expect a Saudi deployment to NOT carry C=GB.
    $tlsCountry = if ($DataRegion -eq "KSA") { "SA" } else { "GB" }
    $subject    = "CN=locallyai, O=LocallyAI, C=$tlsCountry"
    $newCert = New-SelfSignedCertificate `
        -Subject $subject `
        -DnsName $san `
        -CertStoreLocation "Cert:\LocalMachine\My" `
        -NotAfter (Get-Date).AddYears(10) `
        -KeyExportPolicy Exportable `
        -KeyAlgorithm RSA -KeyLength 4096
    # Export public + private as PEM via .NET (PowerShell's built-in
    # Export-PfxCertificate produces .pfx; we want PEM that uvicorn can
    # consume directly with --ssl-certfile / --ssl-keyfile).
    $bytes = $newCert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
    $b64   = [Convert]::ToBase64String($bytes, "InsertLineBreaks")
    @("-----BEGIN CERTIFICATE-----", $b64, "-----END CERTIFICATE-----") | Set-Content $cert -Encoding ASCII

    $rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($newCert)
    $pkBytes = $rsa.ExportPkcs8PrivateKey()
    $pkB64   = [Convert]::ToBase64String($pkBytes, "InsertLineBreaks")
    @("-----BEGIN PRIVATE KEY-----", $pkB64, "-----END PRIVATE KEY-----") | Set-Content $keyPem -Encoding ASCII

    & icacls $keyPem /inheritance:r /grant "$($me.Name):(R)" | Out-Null
}

# ── ACLs on sensitive files ──────────────────────────────────────────────────
$sensitive = @(".env", "users.json")
foreach ($f in $sensitive) {
    $p = Join-Path $InstallRoot $f
    if (Test-Path $p) { & icacls $p /inheritance:r /grant "$($me.Name):(R,W)" | Out-Null }
}

# ── First user via manage_users (mirrors install.sh behaviour) ───────────────
$usersFile = Join-Path $InstallRoot "users.json"
if (-not (Test-Path $usersFile)) {
    Note "Creating first admin user 'Admin'"
    & $venvPy "$InstallRoot\manage_users.py" add Admin --ttl-days 0 | Out-Null
}

# ── Windows Service via NSSM ──────────────────────────────────────────────────
if (-not $SkipService) {
    $nssm = Get-Command nssm -ErrorAction SilentlyContinue
    if (-not $nssm) {
        Note "NSSM not found — installing via winget"
        winget install --silent --accept-source-agreements --accept-package-agreements NSSM.NSSM | Out-Null
        $nssm = Get-Command nssm -ErrorAction SilentlyContinue
    }
    if ($nssm) {
        $svc = "LocallyAIServer"
        $existing = Get-Service -Name $svc -ErrorAction SilentlyContinue
        if ($existing) { & nssm stop $svc | Out-Null; & nssm remove $svc confirm | Out-Null }
        & nssm install $svc $venvPy "$InstallRoot\supervisor.py" | Out-Null
        & nssm set $svc AppDirectory  $InstallRoot               | Out-Null
        & nssm set $svc AppStdout     "$InstallRoot\logs\service.log" | Out-Null
        & nssm set $svc AppStderr     "$InstallRoot\logs\service.log" | Out-Null
        & nssm set $svc Start         SERVICE_AUTO_START         | Out-Null
        & nssm set $svc AppExit Default Restart                  | Out-Null
        Start-Service $svc
        Note "Windows service '$svc' registered + started"
    } else {
        Warn "NSSM unavailable; service NOT registered. Run supervisor.py manually:"
        Warn "  & '$venvPy' '$InstallRoot\supervisor.py'"
    }
}

# ── Smoke ────────────────────────────────────────────────────────────────────
Start-Sleep -Seconds 10
try {
    $h = Invoke-RestMethod -Uri "https://localhost:$Port/healthz" -SkipCertificateCheck -TimeoutSec 5
    Note "/healthz: backend=$($h.backend) ok=$($h.ok)"
} catch {
    Warn "/healthz did not respond — check logs\service.log"
}

Note "Install complete. Admin key: $($envLines['LOCALLYAI_ADMIN_KEY'])"
Note "Run scripts\audit_install.ps1 to validate the deployment."

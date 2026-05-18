# audit_install.ps1 — health audit for a deployed LocallyAI Windows node.
# Mirrors scripts/audit_install.sh check-for-check, mapped to the Windows
# tooling: BitLocker for FileVault, icacls for chmod, sc.exe for launchctl.
#
# Output: logs\install_audit_YYYY-MM-DD.log (Markdown, like the Mac script).

[CmdletBinding()]
param(
    [string] $Repo = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [int]    $Port = 8000
)
$ErrorActionPreference = "Continue"
$ts   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$date = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")
$logDir = Join-Path $Repo "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$report = Join-Path $logDir "install_audit_$date.log"
"# LocallyAI install audit — $ts" | Set-Content $report
"" | Add-Content $report

$pass = 0; $warn = 0; $fail = 0
function Emit($status, $title, $detail) {
    $colour = @{ pass = "Green"; warn = "Yellow"; fail = "Red" }[$status]
    Write-Host ("[{0}] {1} — {2}" -f $status, $title, $detail) -ForegroundColor $colour
    "- **[{0}]** {1} — {2}" -f $status, $title, $detail | Add-Content $report
    Set-Variable -Name "${status}Count" -Scope Script -Value ((Get-Variable -Name "${status}Count" -Scope Script -ValueOnly) + 1) -ErrorAction SilentlyContinue
    if ($status -eq "pass") { $script:pass++ }
    elseif ($status -eq "warn") { $script:warn++ }
    else { $script:fail++ }
}

# 1. Service running?
$svc = Get-Service -Name "LocallyAIServer" -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Emit pass "1. Windows service" "LocallyAIServer Status=$($svc.Status)"
} elseif ($svc) {
    Emit fail "1. Windows service" "LocallyAIServer is $($svc.Status) — start with: Start-Service LocallyAIServer"
} else {
    Emit fail "1. Windows service" "LocallyAIServer not registered — run install.ps1"
}

# 2. /healthz
try {
    $h = Invoke-RestMethod -Uri "https://localhost:$Port/healthz" -SkipCertificateCheck -TimeoutSec 5
    Emit pass "2. /healthz" "ok=$($h.ok) backend=$($h.backend)"
} catch {
    Emit fail "2. /healthz" "no response on port $Port"
}

# 3. /admin/audit-verify
$envFile = Join-Path $Repo ".env"
$adminKey = $null
if (Test-Path $envFile) {
    $line = Select-String -Path $envFile -Pattern "^LOCALLYAI_ADMIN_KEY=" | Select-Object -First 1
    if ($line) { $adminKey = $line.Line.Split("=", 2)[1] }
}
if (-not $adminKey) {
    Emit warn "3. audit chain" "LOCALLYAI_ADMIN_KEY not set in .env — cannot verify"
} else {
    try {
        $av = Invoke-RestMethod -Uri "https://localhost:$Port/admin/audit-verify" `
              -Headers @{ Authorization = "Bearer $adminKey" } `
              -SkipCertificateCheck -TimeoutSec 5
        if ($av.status -eq "ok") {
            Emit pass "3. audit chain" "status=ok entries=$($av.entries) node=$($av.node_id)"
        } elseif ($av.status -eq "skipped") {
            Emit warn "3. audit chain" "HMAC chain disabled — set LOCALLYAI_AUDIT_HMAC_KEY in .env"
        } else {
            Emit fail "3. audit chain" "TAMPERED at $($av.source):$($av.broken_at_line)"
        }
    } catch {
        Emit fail "3. audit chain" $_.Exception.Message
    }
}

# 4. Monitor alerts
if ($adminKey) {
    try {
        $mon = Invoke-RestMethod -Uri "https://localhost:$Port/admin/monitor/alerts" `
               -Headers @{ Authorization = "Bearer $adminKey" } `
               -SkipCertificateCheck -TimeoutSec 5
        if ((@($mon.alerts).Count) -eq 0) {
            Emit pass "4. monitor alerts" "all green"
        } else {
            Emit warn "4. monitor alerts" "$(@($mon.alerts).Count) active"
        }
    } catch {
        Emit warn "4. monitor alerts" "could not fetch"
    }
}

# 5. Heartbeat tail
$hb = Join-Path $logDir "heartbeat.log"
if (Test-Path $hb) {
    $tail = Get-Content $hb -Tail 50
    $failed = ($tail | Where-Object { $_ -match "probe_failed" }).Count
    Emit pass "5. heartbeat tail" "$failed probe_failed in last 50 lines"
} else {
    Emit warn "5. heartbeat tail" "no heartbeat.log yet"
}

# 6. Ollama models
try {
    $tags = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 3
    $names = ($tags.models | ForEach-Object { $_.name }) -join ", "
    if ($names) {
        Emit pass "6. ollama models" $names
    } else {
        Emit warn "6. ollama models" "no models pulled — run: ollama pull qwen2.5:14b"
    }
} catch {
    Emit fail "6. ollama backend" "Ollama not reachable on 127.0.0.1:11434"
}

# 7. users.json
$uf = Join-Path $Repo "users.json"
if (Test-Path $uf) {
    try {
        $u = Get-Content $uf -Raw | ConvertFrom-Json
        $count = ($u.PSObject.Properties | Measure-Object).Count
        Emit pass "7. users.json" "$count user(s) configured"
    } catch {
        Emit fail "7. users.json" "exists but unparseable"
    }
} else {
    Emit fail "7. users.json" "missing — run install.ps1 to create"
}

# 8a. BitLocker (parity with FileVault check)
try {
    $bl = Get-BitLockerVolume -MountPoint ($Repo.Substring(0,2)) -ErrorAction Stop
    if ($bl.ProtectionStatus -eq "On") {
        Emit pass "8a. BitLocker" "On — disk encryption protects logs and TLS keys at rest"
    } else {
        Emit warn "8a. BitLocker" "Off — enable with manage-bde -on $($bl.MountPoint)"
    }
} catch {
    Emit warn "8a. BitLocker" "could not query (Get-BitLockerVolume unavailable)"
}

# 8b. tls\key.pem ACL
$keyPem = Join-Path $Repo "tls\key.pem"
if (Test-Path $keyPem) {
    $acl = (Get-Acl $keyPem).Access | Where-Object { $_.IdentityReference -notlike "*$($env:USERNAME)*" -and $_.IdentityReference -notlike "*Administrators*" -and $_.IdentityReference -notlike "*SYSTEM*" }
    if (-not $acl) {
        Emit pass "8b. tls\key.pem ACL" "restricted to current user / Administrators / SYSTEM"
    } else {
        $extra = ($acl | ForEach-Object { $_.IdentityReference.Value }) -join ", "
        Emit warn "8b. tls\key.pem ACL" "extra principals: $extra"
    }
} else {
    Emit warn "8b. tls\key.pem ACL" "key file not found"
}

# 8c. Log file ACLs (audit / billing must not be world-readable)
foreach ($f in @("audit.log", "billing.log")) {
    $p = Join-Path $logDir $f
    if (Test-Path $p) {
        $acl = (Get-Acl $p).Access | Where-Object { $_.IdentityReference -like "*Everyone*" -or $_.IdentityReference -like "*Users*" }
        if ($acl) {
            Emit warn "8c. logs\$f ACL" "Everyone or Users group has access"
        } else {
            Emit pass "8c. logs\$f ACL" "no world / Users access"
        }
    }
}

# 8d. data\ in .gitignore
$gi = Join-Path $Repo ".gitignore"
if ((Test-Path $gi) -and (Select-String -Path $gi -Pattern "^data/?$" -Quiet)) {
    Emit pass "8d. data\ gitignore" "data/ ignored — client documents won't be committed"
} else {
    Emit warn "8d. data\ gitignore" "data/ not explicitly ignored; verify .gitignore"
}

# 8e. .env ACL
if (Test-Path $envFile) {
    $acl = (Get-Acl $envFile).Access | Where-Object { $_.IdentityReference -like "*Everyone*" -or $_.IdentityReference -like "*Users*" }
    if ($acl) {
        Emit warn "8e. .env ACL" "Everyone or Users group can read .env"
    } else {
        Emit pass "8e. .env ACL" "restricted"
    }
}

"" | Add-Content $report
"---" | Add-Content $report
"Audit complete: pass=$script:pass warn=$script:warn fail=$script:fail" | Add-Content $report

Write-Host ""
Write-Host "─────────────────────────────────────"
Write-Host (" Audit complete: pass={0} warn={1} fail={2}" -f $script:pass, $script:warn, $script:fail)
Write-Host (" Report: {0}" -f $report)
Write-Host "─────────────────────────────────────"
if ($script:fail -gt 0) { exit 1 } else { exit 0 }

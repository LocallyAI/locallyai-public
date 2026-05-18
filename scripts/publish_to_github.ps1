<#
.SYNOPSIS
    Create a GitHub repository and push the production/ folder in one shot.

.DESCRIPTION
    Windows-friendly PowerShell port of publish_to_github.sh. Works on Windows
    PowerShell 5.1 and PowerShell 7+. Robust against the silent-window-close
    crash mode: any failure is caught, displayed, and the window pauses for
    Enter so you can read the error.

.PARAMETER Name
    The name of the GitHub repository to create. Required.

.PARAMETER Public
    Create the repo as public. Default is private.

.EXAMPLE
    pwsh scripts/publish_to_github.ps1 -Name locallyai
    pwsh scripts/publish_to_github.ps1 -Name locallyai -Public

.EXAMPLE
    # Windows PowerShell 5.1 (default on Windows):
    powershell -ExecutionPolicy Bypass -File scripts\publish_to_github.ps1 -Name locallyai

.NOTES
    Requires the GitHub CLI:
        winget install GitHub.cli
        gh auth login
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Name,

    [switch]$Public,

    # Use this when the remote has commits your local doesn't (typically a
    # GitHub-auto-generated README): the script will pull --rebase first and,
    # if that still leaves divergence, push with --force-with-lease.
    [switch]$Force
)

# Don't let PowerShell auto-terminate on native command stderr writes.
# In PS 7.4+ this preference is $true by default and turns gh's normal
# stderr output into a terminating exception that closes the window.
$ErrorActionPreference = 'Continue'
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Write-Ok    { param($msg) Write-Host "[ OK   ] $msg" -ForegroundColor Green }
function Write-Info  { param($msg) Write-Host "[ INFO ] $msg" -ForegroundColor Cyan }
function Write-Warn  { param($msg) Write-Host "[ WARN ] $msg" -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "[ FAIL ] $msg" -ForegroundColor Red }

function Pause-OnExit {
    # Keeps the window open so the user can read the error message.
    Write-Host ''
    Write-Host 'Press Enter to close this window...' -ForegroundColor DarkGray
    [void](Read-Host)
}

# Wrap everything so any unhandled error is shown and the window stays open.
try {
    # Resolve production/ -- script lives at production/scripts/publish_to_github.ps1
    $ScriptDir     = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $ProductionDir = (Resolve-Path (Join-Path $ScriptDir '..')).Path
    $ProductionDirFwd = $ProductionDir -replace '\\', '/'   # gh prefers forward slashes

    Write-Info "Repo root: $ProductionDir"
    Write-Info ("Visibility: " + $(if ($Public) { 'public' } else { 'private' }))

    # 1. GitHub CLI present?
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        Write-Fail "GitHub CLI (gh) not installed."
        Write-Host "  Install it with:  winget install GitHub.cli"
        Write-Host "  Then authenticate:  gh auth login"
        Pause-OnExit
        exit 1
    }
    Write-Ok ("Found gh at " + (Get-Command gh).Source)

    # 2. Authenticated?
    $authOutput = & gh auth status 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "GitHub CLI is not authenticated."
        Write-Host '  Run:  gh auth login'
        Write-Host ''
        Write-Host '--- gh auth status output ---' -ForegroundColor DarkGray
        Write-Host $authOutput.Trim() -ForegroundColor DarkGray
        Pause-OnExit
        exit 1
    }
    Write-Ok 'gh is authenticated'

    # 3. Move to production/
    Set-Location -LiteralPath $ProductionDir

    # 4. git installed?
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Fail "git is not installed. Install Git for Windows: https://git-scm.com/download/win"
        Pause-OnExit
        exit 1
    }

    # 5. git init if needed
    if (-not (Test-Path '.git' -PathType Container)) {
        $initOut = & git init -b main 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "git init failed: $initOut"
            Pause-OnExit
            exit 1
        }
        Write-Ok 'Initialised git repository'
    } else {
        Write-Info 'Existing git repository detected -- keeping history'
    }

    # 6. Configure user.email/user.name if missing (commits will fail otherwise)
    $userEmail = & git config user.email 2>$null
    $userName  = & git config user.name 2>$null
    if (-not $userEmail) {
        Write-Warn 'git user.email is not set globally. Using a placeholder for this repo only.'
        & git config user.email 'locallyai@noreply.local' | Out-Null
    }
    if (-not $userName) {
        & git config user.name 'LocallyAI Operator' | Out-Null
    }

    # 7. Stage everything (.gitignore handles secrets)
    $addOut = & git add . 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "git add failed:`n$addOut"
        Pause-OnExit
        exit 1
    }

    # 8. Commit, but only if there's something staged
    $diff = & git diff --cached --name-only
    if ([string]::IsNullOrWhiteSpace($diff)) {
        Write-Info 'No changes to commit'
    } else {
        $commitOut = & git commit -m 'LocallyAI - on-prem AI for regulated industries' 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "git commit failed:`n$commitOut"
            Pause-OnExit
            exit 1
        }
        Write-Ok 'Committed staged files'
    }

    # 9. Determine current branch
    $CurrentBranch = (& git branch --show-current 2>$null)
    if ($CurrentBranch) { $CurrentBranch = $CurrentBranch.Trim() }
    if ([string]::IsNullOrWhiteSpace($CurrentBranch)) { $CurrentBranch = 'main' }

    # 10. Already has a remote? Just push. Otherwise create the repo.
    $hasRemote = $false
    & git remote get-url origin *> $null
    if ($LASTEXITCODE -eq 0) { $hasRemote = $true }

    if ($hasRemote) {
        $existing = (& git remote get-url origin).Trim()
        Write-Info "Remote 'origin' already set: $existing -- pushing"
        $pushOut = & git push -u origin $CurrentBranch 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            $rejected = $pushOut -match '\[rejected\]|fetch first|non-fast-forward'
            if ($rejected -and $Force) {
                Write-Warn 'Push rejected (remote has commits you do not). -Force was supplied: rebasing then pushing with --force-with-lease.'
                $rebaseOut = & git pull --rebase origin $CurrentBranch 2>&1 | Out-String
                if ($LASTEXITCODE -ne 0) {
                    Write-Fail "git pull --rebase failed:`n$rebaseOut"
                    Write-Host 'Resolve conflicts manually, then run: git push --force-with-lease origin main' -ForegroundColor Yellow
                    Pause-OnExit
                    exit 1
                }
                $forcePushOut = & git push --force-with-lease -u origin $CurrentBranch 2>&1 | Out-String
                if ($LASTEXITCODE -ne 0) {
                    Write-Fail "git push --force-with-lease failed:`n$forcePushOut"
                    Pause-OnExit
                    exit 1
                }
                Write-Ok 'Force-pushed successfully'
            } elseif ($rejected) {
                Write-Fail "git push rejected -- remote has commits your local does not."
                Write-Host ''
                Write-Host 'This usually means GitHub auto-created a README/.gitignore when you made the repo.' -ForegroundColor Yellow
                Write-Host ''
                Write-Host 'Pick one:' -ForegroundColor Yellow
                Write-Host '  Option A (overwrite remote with local -- recommended for first publish):' -ForegroundColor Yellow
                Write-Host "    pwsh scripts\publish_to_github.ps1 -Name $Name -Force"
                Write-Host '  Option B (keep both -- only if you wrote something on GitHub web UI):' -ForegroundColor Yellow
                Write-Host "    git pull --rebase origin $CurrentBranch ; git push origin $CurrentBranch"
                Pause-OnExit
                exit 1
            } else {
                Write-Fail "git push failed:`n$pushOut"
                Pause-OnExit
                exit 1
            }
        }
    } else {
        $visibility = if ($Public) { '--public' } else { '--private' }
        Write-Info "Creating GitHub repo '$Name' ($visibility) and pushing..."
        $createOut = & gh repo create $Name $visibility `
            --source=$ProductionDirFwd `
            --description "LocallyAI - on-premises AI for regulated industries (Apple Silicon, RAG, hybrid retrieval, audit-logged)" `
            --push 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "gh repo create failed:`n$createOut"
            Write-Host ''
            Write-Host 'Common fixes:' -ForegroundColor Yellow
            Write-Host '  - The repo name already exists on your account: pick another or delete it first.'
            Write-Host '  - You are pushing to an org you do not have access to: use the form OWNER/NAME.'
            Write-Host '  - Network/firewall: try `gh auth status` to confirm connectivity.'
            Pause-OnExit
            exit 1
        }
        Write-Ok 'Repo created and pushed'
    }

    # 11. Print the URL
    $url = ''
    try { $url = (& gh repo view --json url -q .url 2>$null).Trim() } catch { $url = '' }

    Write-Host ''
    if ($url) {
        Write-Ok "Published: $url"
        Write-Host ''
        Write-Host '  Anyone with access can now run:'
        Write-Host "    git clone $url"
        $cloneDir = [System.IO.Path]::GetFileNameWithoutExtension($url)
        Write-Host "    cd $cloneDir"
        Write-Host '    bash install.sh        # on the Apple Silicon Mac'
        Write-Host ''
        Write-Host '  Want to make a tagged release?'
        Write-Host "    git tag -a v1.0.0 -m 'First release'"
        Write-Host '    git push origin v1.0.0'
    } else {
        Write-Warn "Push completed but couldn't fetch the URL -- run 'gh repo view' to see it."
    }

    # Successful run -- pause so the user can read the URL.
    Pause-OnExit
}
catch {
    Write-Fail "Unhandled error: $($_.Exception.Message)"
    Write-Host ''
    Write-Host '--- Stack trace ---' -ForegroundColor DarkGray
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    Pause-OnExit
    exit 1
}

# Setup — Windows (single-node + 2-node HA)

End state: one or two Windows boxes (DGX Spark or any Win 11 with NVIDIA
GPU) running LocallyAI as a Windows Service `LocallyAIServer`,
accessible at `https://localhost:8000`, audit chain verified.

Time required: 30–60 min single-node, +30 min for the second box.

> Mac and Windows are not mixed in one fleet. If you have both,
> deploy two separate single-firm fleets, one per OS.

---

## 0. Pre-flight (10 min)

### 0.1 Hardware

- **GPU:** NVIDIA. Any DGX Spark, RTX 30/40/50 series, or A-series
  works. Driver version: latest stable from NVIDIA's site.
- **RAM:** 32 GB minimum, 64+ GB for >7B models.
- **Disk:** 50 GB free.
- **OS:** Windows 11 22H2 or Windows Server 2022.

### 0.2 BitLocker (compliance-critical, equivalent to FileVault on Mac)

Right-click **Start** → **Settings** → **Privacy & security** → **Device
encryption** (or **BitLocker drive encryption** on Pro/Enterprise).

Status should read **"On"** for the system drive.

If OFF: click **Turn on** and follow prompts. **Save the recovery key
to your firm's password vault**, not to your Microsoft account, not to
a USB stick that lives in the same drawer as the Mac.

The deployment is **not GDPR Art. 32 compliant without BitLocker** —
anyone who pulls the drive out of the box can read every audit
pseudonym alongside the salt that re-identifies them.

### 0.3 Time sync

Open **Settings** → **Time & language** → **Date & time**. Confirm
"Set time automatically" is **On**, and "Time zone" is your firm's.

If you're in a corporate domain, ensure `w32time` syncs against the DC
or a designated NTP source — not random `time.windows.com` if your
network policy blocks it.

### 0.4 Firewall plan

Decide which ports must be open on the host firewall:

- **8000 (TCP)** — LocallyAI API. Inbound from LAN (or just the
  worker-app subnets).
- **11434 (TCP)** — Ollama. Inbound from localhost only (default).
- **6333, 6334, 6335 (TCP)** — Qdrant cluster. **HA only.** Open to the
  peer node's IP only.
- **22000 (TCP), 21027 (UDP)** — Syncthing. **HA only.** Open to the
  peer node's IP.
- **8384 (TCP)** — Syncthing GUI. Localhost only — never expose.

We'll add the rules in §6 (HA) or skip them for single-node.

### 0.5 Open an elevated PowerShell

Press **Win + X**, click **Terminal (Admin)**. Click **Yes** on the UAC
prompt.

If your Windows version doesn't have Terminal, search **PowerShell** in
Start → right-click → **Run as administrator**.

The window title bar must include **(Administrator)**. If not, you're
not elevated and the install will fail — close it and retry.

---

## 1. Clone the repository (1 min)

> **Read [repo-access.md](repo-access.md) first** if a vendor is
> delivering this to you. The vendor's pattern is per-client SSH
> deploy key (read-only, per-box, separately revocable). The vendor
> either pre-installs the key as part of delivery or walks through
> generating it on this box.

In the elevated PowerShell:

If `git` is missing, install first:

```powershell
winget install --silent --accept-source-agreements --accept-package-agreements Git.Git
# Then close PowerShell, reopen as Admin, retry.
```

**If a vendor set up the deploy-key alias** (per `repo-access.md`):

```powershell
cd C:\
git clone git@github-locallyai:<vendor-or-firm>/locallyai.git
cd locallyai
```

**If cloning from your own fork** without a deploy key:

```powershell
cd C:\
git clone https://github.com/<vendor-or-firm>/locallyai.git
cd locallyai
```

Verify:

```powershell
Test-Path install.ps1
# Expected: True
```

---

## 2. Run the installer (10–30 min — most of it is the model download)

```powershell
PowerShell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer:

1. Verifies you're Administrator. (Aborts if not.)
2. Checks BitLocker; warns if off.
3. Installs Python 3.12 via winget if missing.
4. Creates `.venv\` and installs Python deps + `pywin32`.
5. Installs Ollama via winget.
6. Generates `.env` with admin key, audit salt, HMAC chain key. ACL
   restricted to current user via `icacls /inheritance:r /grant
   <user>:(R,W)`.
7. Generates a self-signed TLS cert via
   `New-SelfSignedCertificate`, exported to `tls\cert.pem` and
   `tls\key.pem`.
8. Creates the first admin user via `manage_users.py add Admin`.
9. Installs **NSSM** via winget (if missing) and registers
   `LocallyAIServer` as a Windows Service. Auto-restart on exit.
10. Probes `/healthz`.

The **last lines** print the admin key. **Copy it now** into the firm
password vault — see [setup-mac-single.md § 3.5](setup-mac-single.md#35-save-the-admin-key)
for vault entry conventions.

---

## 3. Pull a model (5–25 min)

The installer doesn't pre-pull a model. From an elevated PowerShell:

```powershell
ollama pull qwen2.5:14b           # for 32 GB box
# or
ollama pull llama3.3:70b          # for 64+ GB box / DGX Spark
```

Then update `.env`:

```powershell
notepad C:\locallyai\.env
# Change OLLAMA_MODEL=qwen2.5:14b   (or whatever you pulled)
# Save and close.
```

Restart the service to pick up the new model:

```powershell
Restart-Service LocallyAIServer
```

---

## 4. Verify (5 min)

### 4.1 Health

```powershell
Invoke-RestMethod -Uri https://localhost:8000/healthz -SkipCertificateCheck
```

Expected:

```
ok      backend
--      -------
True    ollama
```

If error: `Get-Service LocallyAIServer | Select Status` — should be
`Running`. If `Stopped`, `Start-Service LocallyAIServer`. If still no:
`Get-Content C:\locallyai\logs\service.log -Tail 50` for clues. See
[incidents-software.md § "API not responding"](incidents-software.md#api-not-responding).

### 4.2 Audit chain

```powershell
$adminKey = (Get-Content C:\locallyai\.env | Select-String '^LOCALLYAI_ADMIN_KEY=').ToString().Split('=',2)[1]
Invoke-RestMethod -Uri https://localhost:8000/admin/audit-verify `
  -Headers @{ Authorization = "Bearer $adminKey" } -SkipCertificateCheck
```

Expected: `status: ok`, `entries: 0`.

### 4.3 First chat

```powershell
.venv\Scripts\python.exe manage_users.py add "TestUser"
# Copy the printed API key.

$userKey = "<paste>"
$body = @{
  messages = @(@{ role = "user"; content = "hello" })
  max_tokens = 20
} | ConvertTo-Json

Invoke-RestMethod -Uri https://localhost:8000/v1/chat/completions `
  -Method Post -ContentType "application/json" `
  -Headers @{ Authorization = "Bearer $userKey" } `
  -Body $body -SkipCertificateCheck
```

Should print a chat response.

### 4.4 Run the audit script

```powershell
PowerShell -ExecutionPolicy Bypass -File .\scripts\audit_install.ps1
```

Expected: `pass=14 warn=0 fail=0`. (Same warn-1 tolerance as Mac for
heartbeat noise on a fresh boot.)

If failures: find the relevant chapter in
[SOP.md](../SOP.md#incidents). Common Windows-specific failures:

- `8b. tls\key.pem ACL` warn → run `icacls C:\locallyai\tls\key.pem
  /inheritance:r /grant "$env:USERNAME:(R)"`.
- `8e. .env ACL` warn → same pattern, target `.env`.
- `6. ollama models` fail → `ollama list` to see what's actually pulled,
  pull the right model.

---

## 5. Single-node done

If you're not deploying HA, you're done. Read:

- [daily.md](daily.md) for daily ops (mostly identical to Mac, but
  `Restart-Service LocallyAIServer` instead of `launchctl kickstart`).
- [maintenance.md § "Windows-specific"](maintenance.md#windows-specific).

For HA, continue.

---

## 6. HA — second Windows box (30 min)

Prerequisites: §1–§4 completed on **both** Windows boxes separately.

### 6.1 Pick LAN IPs

On each box: `ipconfig`. Note the IPv4 addresses. Substitute below:

- **Win-A IP**: `10.0.0.21`
- **Win-B IP**: `10.0.0.22`

### 6.2 Open firewall ports

On **both** boxes, in elevated PowerShell:

```powershell
New-NetFirewallRule -DisplayName "LocallyAI API"     -Direction Inbound -Protocol TCP -LocalPort 8000             -Action Allow -Profile Any
New-NetFirewallRule -DisplayName "Qdrant cluster"    -Direction Inbound -Protocol TCP -LocalPort 6333,6334,6335   -Action Allow -Profile Any
New-NetFirewallRule -DisplayName "Syncthing"         -Direction Inbound -Protocol TCP -LocalPort 22000            -Action Allow -Profile Any
New-NetFirewallRule -DisplayName "Syncthing-disco"   -Direction Inbound -Protocol UDP -LocalPort 21027            -Action Allow -Profile Any
```

(For tighter security, replace `-Profile Any` with `-RemoteAddress
10.0.0.0/24` matching your office LAN.)

### 6.3 Syncthing on each box

```powershell
PowerShell -ExecutionPolicy Bypass -File .\scripts\syncthing_setup.ps1
```

This installs Syncthing via winget, creates `C:\locallyai\shared\`,
generates the Syncthing identity, registers a per-user scheduled task
(operator account, not SYSTEM, so it can read the repo dir), prints
the **Device ID**.

Open the GUI on each box: `http://127.0.0.1:8384`. Pair the two boxes
following the same flow as
[setup-mac-ha.md § 1.4–1.6](setup-mac-ha.md#14-on-mac-a-add-mac-b-as-a-remote-device).

Wait until both GUIs show the folder **Up to Date**.

### 6.4 Move users.json to shared (Win-A only)

```powershell
Move-Item C:\locallyai\users.json C:\locallyai\shared\users.json
```

### 6.5 Qdrant cluster

Pick a `<shared-secret>` (32 hex):

```powershell
$secret = -join ((1..64) | ForEach { '{0:x}' -f (Get-Random -Max 16) })
Write-Host "Save this in the password vault as 'LocallyAI / Qdrant API key':"
Write-Host $secret
```

On **Win-A** (bootstrap):

```powershell
$env:QDRANT_NODE_BIND_IP = "10.0.0.21"
$env:QDRANT_API_KEY = "<shared-secret>"
PowerShell -ExecutionPolicy Bypass -File .\scripts\qdrant_cluster_setup.ps1
```

On **Win-B** (joining):

```powershell
$env:QDRANT_NODE_BIND_IP = "10.0.0.22"
$env:QDRANT_BOOTSTRAP_PEER = "http://10.0.0.21:6335"
$env:QDRANT_API_KEY = "<shared-secret>"
PowerShell -ExecutionPolicy Bypass -File .\scripts\qdrant_cluster_setup.ps1
```

Verify two-peer cluster:

```powershell
Invoke-RestMethod -Uri http://10.0.0.21:6333/cluster `
  -Headers @{ "api-key" = "<shared-secret>" }
```

### 6.6 Wire HA into `.env` on both boxes

Append to `C:\locallyai\.env`:

```
LOCALLYAI_SHARED_DIR=C:\locallyai\shared
LOCALLYAI_NODE_ID=win-a               # use 'win-b' on Win-B
QDRANT_URLS=http://10.0.0.21:6333,http://10.0.0.22:6333
QDRANT_API_KEY=<shared-secret>
LOCALLYAI_HA=1
```

Re-tighten ACL (notepad sometimes resets):

```powershell
icacls C:\locallyai\.env /inheritance:r /grant "$env:USERNAME:(R,W)"
```

Restart on each box:

```powershell
Restart-Service LocallyAIServer
```

### 6.7 Verify HA

```powershell
$adminKey = (Get-Content C:\locallyai\.env | Select-String '^LOCALLYAI_ADMIN_KEY=').ToString().Split('=',2)[1]
Invoke-RestMethod -Uri https://localhost:8000/admin/fleet/nodes `
  -Headers @{ Authorization = "Bearer $adminKey" } -SkipCertificateCheck
```

Expected: 2 nodes, both `alive: true`.

```powershell
Invoke-RestMethod -Uri https://localhost:8000/admin/fleet/qdrant-health `
  -Headers @{ Authorization = "Bearer $adminKey" } -SkipCertificateCheck
```

Expected: `mode: cluster`, `peer_count: 2`.

### 6.8 Smoke-test failover

Same procedure as Mac §8: stop Win-A's service, hit Win-B directly,
confirm `node_id: win-b` in the response. Bring Win-A back. Verify
both alive.

```powershell
# Stop Win-A:
Stop-Service LocallyAIServer

# From a third device or Win-B, hit Win-B:
Invoke-RestMethod -Uri https://10.0.0.22:8000/v1/chat/completions ...

# Bring Win-A back:
Start-Service LocallyAIServer
```

If failover fails: [incidents-software.md § "Fleet desync"](incidents-software.md#fleet-desync)
and [incidents-physical.md § "Network partition"](incidents-physical.md#network-partition-between-macs)
(applies equally to Windows boxes).

---

## 7. Worker-ui smart client + fleet dashboard

Same as Mac §6–§7. Use Win-A and Win-B IPs in `VITE_API_BASE_URLS`.

---

## 8. Update credential register

- [ ] `LocallyAI / admin key`
- [ ] `LocallyAI / BitLocker recovery key`
- [ ] `LocallyAI / Windows local-account password`
- [ ] `LocallyAI / Qdrant API key` (HA)
- [ ] `LocallyAI / Win-A Syncthing Device ID` (HA)
- [ ] `LocallyAI / Win-B Syncthing Device ID` (HA)
- [ ] One per user

---

## 9. Done

Continue to [daily.md](daily.md). Where Mac says `launchctl …`, Windows
says `Start-Service` / `Stop-Service` / `Restart-Service
LocallyAIServer` — that's the only daily-ops difference.

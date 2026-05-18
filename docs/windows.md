# LocallyAI on Windows

LocallyAI runs on Windows 11 nodes (DGX Spark or any Windows box with an
NVIDIA GPU and Docker Desktop). Same code, same fleet, same dashboard;
the OS-specific scaffolding lives in `os_supervisor.py`,
`platform_compat.py`, `install.ps1`, and the `.ps1` setup scripts.

> **Fleet rule** — a single fleet must be **all-Mac OR all-Windows**.
> The model formats differ (MLX vs Ollama GGUF), so cross-OS members
> would diverge on inference. Mixed fleets are refused at registration.

## Requirements

| Component                | Minimum                                     |
|--------------------------|---------------------------------------------|
| OS                       | Windows 11 22H2 (server-grade Win Server OK)|
| Python                   | 3.12 (winget installs)                      |
| GPU                      | NVIDIA with current driver (DGX Spark fine) |
| Disk encryption          | BitLocker enabled (audited at install)      |
| Docker Desktop           | required for the Qdrant cluster             |
| Open ports               | 8000 (API), 6333/6334/6335 (Qdrant cluster) |
| Admin                    | All install steps require elevated PowerShell |

## Install

In an **elevated** PowerShell at the repo root:

```powershell
PowerShell -ExecutionPolicy Bypass -File .\install.ps1
```

This:

1. Verifies BitLocker (warns, doesn't fail, since some deployments use
   self-encrypting drives at the controller level).
2. Installs Python 3.12 via winget if missing.
3. Creates `.venv`, installs `requirements.txt` + `pywin32`.
4. Installs Ollama via winget.
5. Generates `.env` with admin key, audit salt, HMAC chain key
   (preserves existing values on a re-run).
6. Generates a self-signed TLS cert into `tls\cert.pem` + `tls\key.pem`.
7. Restricts ACLs on `.env`, `users.json`, and `tls\key.pem` via
   `icacls /inheritance:r /grant <user>:(R,W)`.
8. Creates the first admin user via `manage_users.py add Admin`.
9. Registers a Windows Service `LocallyAIServer` via NSSM (installed
   via winget if missing). Auto-restart on exit.
10. Smokes `/healthz`.

## Validate

```powershell
PowerShell -ExecutionPolicy Bypass -File .\scripts\audit_install.ps1
```

Same checks as `audit_install.sh`, mapped to Windows tooling:

| Mac check                  | Windows equivalent                      |
|----------------------------|------------------------------------------|
| launchd service running    | `Get-Service LocallyAIServer`           |
| `/healthz`                 | identical                               |
| audit chain                | identical                               |
| FileVault on               | BitLocker on                            |
| `chmod 600` on .env / key  | ACL via `icacls`                        |
| `lsof` for stale listener  | `netstat -ano` + `tasklist` (in `os_supervisor.py`) |
| `launchctl stop` hint      | `sc stop LocallyAIServer` hint          |

Pass=14 warn≤1 fail=0 expected on a clean install.

## HA bring-up (2-Windows-node edition)

Same flow as the Mac edition, just with `.ps1` scripts:

```powershell
# On EACH node (Administrator):
PowerShell -ExecutionPolicy Bypass -File .\scripts\syncthing_setup.ps1

# On node 1 (bootstrap):
$env:QDRANT_NODE_BIND_IP = '10.0.0.11'
$env:QDRANT_API_KEY = '<shared-secret>'
PowerShell -ExecutionPolicy Bypass -File .\scripts\qdrant_cluster_setup.ps1

# On node 2 (joining):
$env:QDRANT_NODE_BIND_IP = '10.0.0.12'
$env:QDRANT_BOOTSTRAP_PEER = 'http://10.0.0.11:6335'
$env:QDRANT_API_KEY = '<shared-secret>'
PowerShell -ExecutionPolicy Bypass -File .\scripts\qdrant_cluster_setup.ps1
```

Then on both nodes, in `.env`:

```
LOCALLYAI_SHARED_DIR=C:\LocallyAI\shared
QDRANT_URLS=http://10.0.0.11:6333,http://10.0.0.12:6333
QDRANT_API_KEY=<shared-secret>
LOCALLYAI_HA=1
```

Restart the service: `Restart-Service LocallyAIServer` on each node.

## Inference backend

Windows nodes always use the OpenAI-compatible Ollama backend (no MLX —
MLX is Apple Silicon only). The api.chat handler streams from Ollama
via SSE the same way it streams from MLX, so worker-ui's typing UX is
identical on both platforms.

To pull the default model (or whatever `OLLAMA_MODEL` in `.env` says):

```powershell
ollama pull qwen2.5:14b
```

## Differences from the Mac edition

- **No `mlx_inference`**: backend is always Ollama, set in `.env` by
  `install.ps1`.
- **Service registration**: NSSM-managed Windows Service, not launchd.
  Service name `LocallyAIServer`. Logs at `logs\service.log`.
- **Singleton lock & port-cleanup**: cross-platform via
  `os_supervisor.py`. Stops orphan listeners with `taskkill /F`,
  enumerates with `netstat -ano`.
- **Permissions**: `icacls /inheritance:r /grant` instead of `chmod`.
  Same intent: only the service account can read sensitive files.
- **Disk encryption**: BitLocker, checked at install + audit.
- **Syncthing**: registered as a per-user scheduled task (not a system
  service) so it runs in the operator's context with access to the
  repo dir owned by the operator account.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/healthz` 503 on first request | Ollama still loading the model | wait ~30s; first request triggers load |
| `audit_install.ps1` warns on `8b`, `8c`, `8e` | Inherited ACLs from Domain Users | `icacls <file> /inheritance:r /grant <user>:(R,W)` |
| Service stops repeatedly | Port conflict | `netstat -ano | findstr :8000`; kill the foreign holder |
| Syncthing conflicts on `users.json` | Both nodes wrote within the sync window | review `SHARED_DIR\conflicts\` from the fleet dashboard; never auto-merge |
| Qdrant peer not joining | Port 6335 firewall block | `New-NetFirewallRule -DisplayName "Qdrant cluster" -Direction Inbound -LocalPort 6333,6334,6335 -Protocol TCP -Action Allow` |

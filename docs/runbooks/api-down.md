# Runbook: API down

**When**: A firm reports the LocallyAI app is "not working", "broken", or shows a connection error. OR the vendor monitor dashboard shows the firm with `healthz=✗`. OR the heartbeat is older than 10 minutes.

**Time budget**: 10 minutes to triage, 30 minutes to recover. If you've been here longer than 30 minutes, **escalate**.

**Risk if you stop midway**: The firm cannot use LocallyAI. They cannot draft, query, or ingest. Their existing documents are safe (FileVault-encrypted on the Mac), but the service is unavailable.

**Prerequisites**:
- SSH or Tailscale access to the office Mac (see `docs/sop/remote-access.md`)
- The firm's admin key (Manager UI sign-in)
- Physical access OR a contact at the firm who can reboot if needed

## Decision tree

Run **A.1** first, then follow the branch:

| If A.1 returns | Go to |
|---|---|
| `{"ok": true, "backend": "..."}` | The API IS running. The firm's problem is elsewhere — check `client_app_blank` runbook (in roadmap) or call the firm to clarify what they actually see |
| Connection refused / no route to host | Step B (process not running) |
| 500 / timeout / hangs | Step C (process running but unhealthy) |
| TLS error | Step D (cert problem) |

## A.1 — Is the API alive?

```bash
curl -k --max-time 5 https://<office-mac-host>:8000/healthz
```

Expected:
```json
{"ok": true, "backend": "mlx"}
```

## Step B — Process not running

### B.1 Check launchd

```bash
launchctl list | grep locallyai
```

Expected:
```
PID  STATUS  app.locallyai.api
```

If no row at all: launchd doesn't know about the service. The plist may have been deleted.

```bash
ls -la ~/Library/LaunchAgents/app.locallyai.api.plist
```

If missing → re-run `bash install.sh` (it'll detect the existing install and re-register the plist; no data loss).

If row shows `-` for PID and a non-zero status: the service crashed on boot. Move to B.2.

### B.2 Inspect the launchd error log

```bash
tail -50 ~/locallyai/logs/launchd_error.log
```

The most common patterns:

| Output | Cause | Fix |
|---|---|---|
| `[startup-gate] LOCALLYAI_AUDIT_HMAC_KEY is empty` | `.env` was edited and the key wiped | Restore `.env` from the firm's encrypted backup, or `manage_users.py rotate-admin` to mint a fresh one |
| `[startup-gate] LOCALLYAI_KILL_SWITCH_URL is the placeholder default` | Same as above | Restore `.env` |
| `[startup-gate] LOCALLYAI_AUDIT_SALT is empty or too short` | Same as above | Restore `.env` |
| `MODEL INTEGRITY DRIFT` | Pinned model commit changed | See `docs/sop/maintenance.md` "MLX pin drift"; either re-pin or set `LOCALLYAI_MODEL_DRIFT_ACK=1` |
| `Address already in use` | Another process owns :8000 | `lsof -i :8000` to find it; usually a stale Python from a manual run |
| `ModuleNotFoundError` | Venv broken | `cd ~/locallyai && rm -rf .venv && bash install.sh` will rebuild the venv without touching data |

### B.3 Restart

```bash
launchctl kickstart -k "gui/$(id -u)/app.locallyai.api"
```

Wait 10 seconds. Re-run **A.1**. Expected: 200 OK.

If still failing, **escalate**.

## Step C — Process running but unhealthy

### C.1 Inspect supervisor + service logs

```bash
tail -100 ~/locallyai/logs/service.log
```

Look for the most recent traceback. Common patterns:

| Output | Cause | Fix |
|---|---|---|
| `OSError: [Errno 28] No space left on device` | Disk full | C.2 below |
| `qdrant_client...ConnectionError` | Qdrant crashed | Restart api (kickstart -k); Qdrant restarts in-process |
| `Worker timeout` | A long inference is blocking the gate | Check `/monitor/health/detailed` for `inference_gate.in_flight`; restart will release |
| `permission denied: logs/audit.log` | File perms regressed | `chmod 640 ~/locallyai/logs/audit.log ~/locallyai/logs/billing.log` |

### C.2 Check disk

```bash
df -h ~/locallyai
```

If `<5GB free`:
- Run retention rotation manually: `cd ~/locallyai && .venv/bin/python -c "from watchdog.sentinel import _run_retention_rotation; _run_retention_rotation()"`
- If still tight after rotation, the firm has been ingesting heavily; size up the disk OR ask them what they ingested.

### C.3 Check inference backend

If MLX:
```bash
ps aux | grep mlx_inference | grep -v grep
```

If Ollama:
```bash
ollama list
ollama ps
```

If the backend is dead, restart it. Then `launchctl kickstart -k "gui/$(id -u)/app.locallyai.api"` for the API.

## Step D — TLS cert problem

### Symptom on A.1

```
curl: (60) SSL certificate problem
```

### D.1 Check expiry

```bash
openssl x509 -in ~/locallyai/tls/cert.pem -noout -enddate
```

If past `notAfter`: cert expired. Renew per `docs/sop/maintenance.md` "TLS cert renewal" — runs `install.sh` cert-only path.

### D.2 Check the error more carefully

If `curl -k` (skip-verify) succeeds but the user's browser fails: the cert was renewed but not re-trusted in the user's keychain. Re-import per `docs/sop/maintenance.md`.

## Things that go wrong

| Symptom | Cause | Fix |
|---|---|---|
| Restart succeeds for 30s then crashes | Crash loop — usually OOM under load | Check `Activity Monitor`; if RAM < 16 GB free, the model is too big for available headroom; switch to a smaller model temporarily |
| `kickstart` returns "Could not find specified service" | launchctl plist not loaded | `launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/app.locallyai.api.plist` |
| Heartbeat resumes but firm still reports broken | Likely a client-app issue, not API | Have the firm reload the worker-ui in their browser; if Tauri client, fully quit + relaunch |

## When to escalate

- 30 minutes elapsed and `/healthz` still fails → founder by phone
- Audit log shows entries from a different deployment_id (sign of a misrouted backup restore) → founder, do not start the service
- Disk is full AND retention rotation already ran cleanly → founder (capacity decision)
- Cert renewal fails because the install script reports the firm's hostname has changed → founder (DNS/networking change at the firm)

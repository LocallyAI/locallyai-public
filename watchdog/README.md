# Watchdog Agents

Four agents that monitor, predict, recover, and diagnose the LocallyAI server.

## Agents

| Agent | File | Runs as |
|---|---|---|
| Sentinel | `sentinel.py` | Background thread inside `api.py` |
| Heartbeat | `heartbeat.py` | Separate process via `supervisor.py` |
| Resurrector | `resurrector.py` | Spawned by Heartbeat on failure |
| Diagnostician | `diagnostician.py` | FastAPI router at `/diagnostician/*` |

## How they connect

```
supervisor.py
  ├── api.py
  │     └── Sentinel (thread) — predicts failures, posts to /monitor/alerts
  ├── heartbeat.py — probes /v1/models every 30s
  │     └── resurrector.py (spawned on 3 consecutive failures)
  │           Stage 1: soft restart
  │           Stage 2: deps check + restart
  │           Stage 3: safe mode (SAFE_MODE=1)
  │           Stage 4: Telegram alert + stand down
  └── telegram_approvals.py — polls /diagnostician/pending, sends Approve/Reject buttons
```

## Setup

1. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=your-bot-token
   TELEGRAM_CHAT_ID=your-chat-id
   LOCALLYAI_API_BASE=http://localhost:8000
   ```

2. Start everything:
   ```
   python supervisor.py
   ```

3. On a client site, install as a Windows service:
   ```
   python service.py install
   net start LocallyAI
   ```

## Autonomous fixes (no approval needed)
- Qdrant `.lock` file removal
- Port 8000 conflict (kills blocking process)

## Human-approved fixes
All other remediations are queued at `/diagnostician/pending` and sent to Telegram.
Emmanuel approves or rejects via inline buttons — no terminal access required.

## Logs
| File | Contains |
|---|---|
| `logs/sentinel.log` | Predictive alerts |
| `logs/heartbeat.log` | Every probe result (uptime record) |
| `logs/resurrector.log` | Recovery events and stage outcomes |
| `logs/diagnostician.log` | Signature matches, approvals, rejections |
| `logs/service.log` | Windows service stdout |
| `logs/crash_dump_*.txt` | Full diagnostic dumps on Stage 4 |
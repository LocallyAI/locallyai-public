# LocallyAI — public mirror

> **This is a sanitised public mirror of an active commercial project.** Operator identifiers, customer references, and infrastructure IDs have been scrubbed; the engineering substance is intact. It exists primarily as a portfolio artifact for engineering managers and recruiters evaluating the author. If you'd like to use the project itself, see [Contact](#contact) below.

## What LocallyAI is

A private, on-premises retrieval-augmented AI platform for regulated industries — UK law firms (SRA / ICO), KSA law firms (PDPL / SDAIA), and any organisation whose data-protection obligations forbid sending client material to cloud AI providers. Everything — LLM inference, document embeddings, vector store, OpenAI-compatible API, audit log, self-healing watchdog — runs on a single Apple Silicon Mac, or on a two-node Mac / Windows fleet with automatic failover. After install the deployment can be fully air-gapped.

## The problem it exists to solve

Law firms (and similar regulated industries) want LLM productivity — drafting, conflict checks, citation verification, document comparison — but their data-protection posture forbids cloud AI. The constraints are non-negotiable: SRA Code of Conduct on confidentiality, GDPR Art. 5 / 25 / 32, ISO 27001:2022 A.5–A.8, and (in KSA) PDPL with cross-border-transfer restrictions. Off-the-shelf RAG-as-a-service products either ship client data to OpenAI / Anthropic, retain it in a vendor's cloud, or both. LocallyAI is the answer to "we want the productivity but the data cannot leave the office Mac."

## What this repo demonstrates

The codebase has been built across roughly 150 commits as a single-author commercial product. The patterns most relevant to an interview signal are:

- **Hybrid retrieval with RRF fusion + cross-encoder rerank.** Qdrant dense + BM25 sparse over the firm's corpus, fused via reciprocal rank fusion, then re-scored by `BAAI/bge-reranker-v2-m3` (multilingual, MPS-accelerated on Apple Silicon). Per-doc ACLs applied *before* the expensive rerank to keep cost down at 50K+ documents. See [`retrieval.py`](retrieval.py), [`reranker.py`](reranker.py).
- **HMAC-chained tamper-evident audit logs** (ISO 27001 A.8.15). Every chat call appends a SHA-256-over-`(prev_hash || entry)` record to `logs/audit.log`; a separate verifier can re-walk the chain offline and detect a single-byte mutation. Same pattern reused for the billing log and the new conflicts log. See [`api.py`](api.py) `_chain_hmac` + [`audit_reader.py`](audit_reader.py).
- **Multi-backend embedding pipeline** with strict mixing prevention. Supports Ollama / LM Studio / in-process `sentence-transformers` / MLX. The choice is asserted at startup because mixed-backend vectors in the same Qdrant collection silently destroy retrieval quality. See [`ingest.py`](ingest.py), [`config.py`](config.py).
- **Two-node HA with a Cloudflare Workers tiebreaker.** Active/standby Mac pair with Syncthing-replicated governance state (users, ACLs, audit log) and rsync-replicated corpus. Smart-client retry on the worker UI means failover is sub-5-second per in-flight request. The kill-switch + vendor-monitor Workers act as the external tiebreaker so neither Mac can split-brain. See [`docs/sop/ha-architecture.md`](docs/sop/ha-architecture.md), [`docs/monitor/cloudflare-worker/`](docs/monitor/cloudflare-worker/), [`docs/kill-switch/cloudflare-worker/`](docs/kill-switch/cloudflare-worker/).
- **Native macOS desktop wrappers** (Swift + WKWebView) at [`apps/manager-desktop/`](apps/manager-desktop/) and [`apps/worker-desktop/`](apps/worker-desktop/) for a dock-icon experience without Electron's footprint. A Tauri-based set lives under [`apps/clients/`](apps/clients/) for the Windows path.
- **Bilingual UK + KSA deployment** with regional defaults (UK GDPR vs KSA PDPL DPA templates, English vs Arabic UI + RTL, Hijri date formatting, regulator-specific compliance snapshots). See [`docs/sop/setup-saudi.md`](docs/sop/setup-saudi.md), [`apps/worker-ui/src/lib/i18n.ts`](apps/worker-ui/src/lib/i18n.ts).
- **Air-gapped operation with verifiable network behaviour.** After install, the office Mac can run with zero outbound connections; the data-isolation SOP enumerates every network call the platform makes and the egress allowlist enforces it. See [`docs/sop/data-isolation.md`](docs/sop/data-isolation.md).
- **OpenAI-compatible API** (`/v1/chat/completions`, `/v1/models`, `/v1/embeddings`) so existing tools and SDKs work without modification. Custom endpoints layer on top: `/v1/conflicts/check`, `/v1/documents/compare`, `/v1/citations/verify`. See [`api.py`](api.py).
- **DPO compliance portal** (manager-ui `/compliance` route) generates an HMAC-signed monthly snapshot covering RoPA + DPIA + audit-chain verification + key-material posture + sub-processor inventory + retention status + incident register + breach events + conflict checks. Verifiable offline via [`scripts/verify_compliance_snapshot.py`](scripts/verify_compliance_snapshot.py).
- **Operational discipline.** ~30 SOP chapters covering install, maintenance, incident response (8 incident classes), recovery, decommission, sub-processor governance, vendor-side disaster recovery, and a dedicated vendor-internal SOP for the founder's own ops cadence. See [`docs/SOP.md`](docs/SOP.md), [`docs/VENDOR_SOP.md`](docs/VENDOR_SOP.md), [`docs/runbooks/`](docs/runbooks/).

## Architectural decisions

Selected design rationales live under [`docs/adr/`](docs/adr/) (in progress — see the index there for the planned set). The README below covers operator-facing usage; the ADRs cover *why* the architecture is what it is.

## Contact

This mirror does not accept pull requests (see [CONTRIBUTING.md](CONTRIBUTING.md)). For collaboration enquiries, licensing the commercial version, or to discuss the engineering work, please open an issue.

---

# LocallyAI

Private, on-premises AI for regulated industries — UK law firms, financial-services firms, and any organisation that cannot send client data to cloud AI providers.

Everything runs locally on a single Apple Silicon Mac (Studio or MacBook), or on a 2-node fleet of Macs / Windows boxes for high availability. The LLM, document embeddings, vector store, OpenAI-compatible API, audit log, and self-healing watchdog mesh all run on-prem. **No internet egress required after install.**

> **HA available.** Single-node is the default. To deploy two boxes with automatic failover and a fleet dashboard, jump to [High Availability — 2-node fleet](#high-availability--2-node-fleet) after the basic install works.

---

## What you need

- **Apple Silicon Mac** — M1, M2, M3, or M4. Intel Macs are not supported (the installer hard-stops).
- **macOS** — any modern version.
- **Homebrew** — install with one line if missing:
  ```bash
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  ```
- **~10 GB free disk** for the default model + venv + Qdrant + Ollama runtime.
- **Internet, once** — to download Python (if missing), Ollama, and the model. After install, the Mac can be air-gapped.
- **Optional: bun or Node 20+** — only if you want the browser workspace at `apps/worker-ui/`. The installer auto-builds it when either is on PATH; otherwise it skips silently and you can install it later.

---

## Quick start (3 commands)

```bash
git clone https://github.com/<your-org>/locallyai.git
cd locallyai
bash install.sh
```

The installer takes 5–15 minutes (most of it is the model download). It will:

1. Verify Apple Silicon and Python ≥ 3.10 (installs Python via Homebrew if missing).
2. Pre-flight check that port 8000 is free.
3. **Prompt you to pick a deployment mode** — production (empty knowledge base) or demo (pre-seeded with 5 sample legal documents). See *Two install modes* below.
4. Create a venv at `.venv/` and `pip install -r requirements.txt`.
5. Install Ollama via Homebrew and start it as a brew service.
6. **Prompt you to pick one or more open-source LLMs** from a curated list.
7. Pull the embedding model (`nomic-embed-text`).
8. Generate three secrets: `LOCALLYAI_ADMIN_KEY`, `LOCALLYAI_AUDIT_SALT`, `LOCALLYAI_AUDIT_HMAC_KEY`. Written to `.env` (chmod 600).
9. Generate a self-signed TLS cert (10-year, RSA-4096) at `tls/`.
10. **Prompt you to trust that cert in the macOS System keychain** (`Y/n`, default Y). Accepting it removes the per-browser "self-signed certificate" warning at `https://localhost:8000` so the worker UI loads silently. Set `LOCALLYAI_TRUST_CERT=yes|no` to skip the prompt for unattended installs.
11. Register a launchd service (`com.locallyai.server`) that auto-starts on login and self-restarts on crash.
12. Create the first admin user via `manage_users.py`.
13. (Demo mode) Copy `demo/data/*.md` into `data/`.
14. Ingest everything in `data/` so RAG works on first chat.
15. Build the worker UI (`apps/worker-ui/`) so the first `launch.sh` is instant. Skipped if neither `bun` nor `npm` is on PATH.
16. Probe `/healthz` to confirm the API is alive.

When it finishes, **save the printed admin key**:

```
==================================================================
  LocallyAI installed
  --------------------------------------------------------------
  Folder:    /Users/you/locallyai/production
  API:       https://localhost:8000
  Health:    https://localhost:8000/healthz
  Logs:      /Users/you/locallyai/production/logs
  Config:    /Users/you/locallyai/production/.env
  Admin key: <64-character hex string — save this>
  --------------------------------------------------------------
==================================================================
```

The key is **not** shown again. If you lose it, run:

```bash
python manage_users.py rotate Admin
```

---

## Two install modes

The installer asks early on:

```
Choose deployment mode:
  1. Production — empty knowledge base; you ingest your own documents.
  2. Demo       — copy 5 sample legal documents into data/ and ingest them.

Mode [1=production / 2=demo, default 1]:
```

| Mode | What lands in `data/` | Best for |
|---|---|---|
| **Production** (default) | only `welcome.md` (one tiny seed) | Real client deployments — you `cp` your own PDFs/Word/Markdown in and run `python ingest.py` |
| **Demo** | `welcome.md` + 5 synthetic UK law-firm samples (NDA, GDPR policy, conflict-check procedure, lease clauses, engagement letter) | First-run testing, sales demos, dev iteration without uploading client data |

Skip the prompt for unattended installs:

```bash
DEPLOY_MODE=demo bash install.sh        # auto-pick demo
DEPLOY_MODE=production bash install.sh  # auto-pick production
```

After install in demo mode, run the end-to-end demo:

```bash
python demo/run_demo.py --key <paste-admin-key>
```

It sends 5 questions targeted at the seeded documents and reports source-retrieval counts. See `demo/README.md` for the full demo guide and how to switch from demo to production after install.

---

## Trusting the TLS cert (one-time, per Mac)

`install.sh` offers to add `tls/cert.pem` to the macOS System keychain so Safari/Chrome/Edge stop warning on `https://localhost:8000`. Until you do this, every browser on the machine pops a "self-signed certificate" interstitial the first time it hits the API, and the **Worker UI silently fails** (its `fetch()` calls reject before reaching the server).

If you skipped the prompt or want to do it later:

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain \
  /path/to/locallyai/tls/cert.pem
```

That's once per Mac for the cert's 10-year lifetime — no per-browser click-through after.

Reverse it any time:

```bash
sudo security delete-certificate -c locallyai /Library/Keychains/System.keychain
```

For unattended installs, `LOCALLYAI_TRUST_CERT=yes bash install.sh` accepts the prompt non-interactively (still needs sudo); `LOCALLYAI_TRUST_CERT=no` declines.

---

## Worker UI (browser workspace)

`apps/worker-ui/` is a TanStack Start app that gives end-users a chat + audit UI in their browser. It builds during `install.sh` so the launcher is instant on first run:

```bash
bash apps/worker-ui/launch.sh    # macOS / Linux
apps\worker-ui\launch.bat        # Windows
```

The launcher is self-healing — if the LocallyAI API isn't running, it boots `com.locallyai.server` (or starts `supervisor.py` directly), waits up to 120 s for `/healthz`, then opens `http://localhost:5174` in your default browser. It also rebuilds the bundle automatically if `src/` is newer than `dist/`.

Override the API URL or port:

```bash
LOCALLYAI_API_BASE=https://locallyai.example.lan:8000 \
LOCALLYAI_WORKER_UI_PORT=5180 \
bash apps/worker-ui/launch.sh
```

---

## Start chatting (one command)

```bash
python chat.py --key <paste-admin-key>
```

You'll see:

```
LocallyAI — connected to https://localhost:8000 (backend: ollama)
  Models installed: qwen2.5:7b, nomic-embed-text:latest
  Active model: (server default)
  Type /help for commands.

you> What is LocallyAI?
…thinking…
assistant> LocallyAI is an on-premises AI deployment platform...
  (3 source chunks retrieved)

you>
```

Slash commands inside the REPL:

| Command | Effect |
|---|---|
| `/help` | Show help |
| `/models` | List installed Ollama models |
| `/model <name>` | Switch model (must already be `ollama pull`-ed) |
| `/clear` | Clear conversation history |
| `/sources` | Show source-chunk count for the last response |
| `/quit` (or Ctrl+D) | Exit |

### Other ways to inference

```bash
KEY="<paste-admin-key>"

# Health (unauth)
curl -sk https://localhost:8000/healthz

# List installed models
curl -sk -H "Authorization: Bearer $KEY" https://localhost:8000/v1/models

# One-shot chat
curl -sk -X POST -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
     -d '{"messages":[{"role":"user","content":"Summarise our GDPR policy."}]}' \
     https://localhost:8000/v1/chat/completions

# 4-check smoke test
python test.py --key "$KEY"
```

Any OpenAI-compatible client (Open WebUI, LibreChat, the OpenAI Python SDK, Cursor, etc.) works — point its API base URL at `https://<mac-local-ip>:8000` and use the API key.

---

## Daily operations

### Add documents to the knowledge base

```bash
# Drop PDFs / Word / Markdown / plain text into data/
cp ~/Documents/contracts/*.pdf data/

# Re-index (incremental — only new/changed files)
python ingest.py

# Force a full re-index
python ingest.py --force
```

### User management

```bash
python manage_users.py add "Jane Smith"        # prints the API key once
python manage_users.py list                    # show all users
python manage_users.py rotate "Jane Smith"     # generate a new key for an existing user
python manage_users.py remove "Jane Smith"     # revoke immediately

# Hot-reload the in-memory user table without restarting (uses ADMIN key, not user key)
ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' .env | cut -d= -f2)
curl -sk -H "Authorization: Bearer $ADMIN_KEY" -X POST https://localhost:8000/admin/reload-users
```

### Service control

```bash
launchctl stop  com.locallyai.server          # stop
launchctl start com.locallyai.server          # start
launchctl list | grep com.locallyai           # is it running? (PID > 0 = yes)

# View live logs
tail -f logs/launchd.log         # uvicorn stdout
tail -f logs/launchd_error.log   # uvicorn stderr
tail -f logs/heartbeat.log       # watchdog probes
tail -f logs/audit.log           # query audit (pseudonymised)
```

### Update / uninstall

```bash
bash update.sh        # stop, upgrade pip deps, restart
bash uninstall.sh     # remove the launchd job; prompts before wiping state
```

### Health audit

```bash
bash scripts/audit_install.sh
# Expect: pass=8 warn=0 fail=0
# Report at logs/install_audit_YYYY-MM-DD.log
```

To run the audit weekly via launchd:

```bash
sed -i '' "s|DIR_PLACEHOLDER|$(pwd)/..|g" com.locallyai.audit.plist
cp com.locallyai.audit.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.locallyai.audit.plist
```

---

## Verifying compliance

```bash
ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' .env | cut -d= -f2)

# Verify the audit log's HMAC chain (ISO 27001 A.12.4 — tamper-evidence)
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/audit-verify
# Expect: {"status": "ok", "entries": <n>}

# Check sentinel & monitor alerts
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/monitor/alerts
# Expect: {"alerts": [], "status": "ok"}

# Detailed health (Ollama, disk, audit log, watchdog)
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/monitor/health/detailed
```

---

## High Availability — 2-node fleet

LocallyAI ships with a 2-node HA edition: two Macs (or two Windows boxes) on the same office LAN. If one node fails, the worker app retries the in-flight request on the surviving node and the firm keeps working. Audit chains stay per-node by design (each box is independently auditable); the user list, GDPR erasure ledger, and document corpus replicate via Syncthing; the vector store runs as a 2-node Qdrant cluster.

**Read first** — these one-pagers are the canonical reference:

- [`docs/ha-2node-clients.md`](docs/ha-2node-clients.md) — what the 2-node edition gives you, what it doesn't (vs. the full 3+ node + NAS edition), failover UX, sync lag SLA, upgrade path.
- [`docs/syncthing-setup.md`](docs/syncthing-setup.md) — pairing the two nodes' shared store.
- [`docs/qdrant-ha.md`](docs/qdrant-ha.md) — bring-up, operating with one node down, split-brain recovery, snapshot/backup posture.
- [`docs/iso27001-controls.md`](docs/iso27001-controls.md) — Annex A control map with the verification command auditors run.
- [`docs/windows.md`](docs/windows.md) — Windows-only fleets (DGX Spark, etc.). Mac and Windows must not be mixed in one fleet.

### Bring-up (Mac edition, both nodes on the same LAN)

A fleet must be **all-Mac OR all-Windows** — the model file formats differ (MLX vs Ollama GGUF), so cross-OS members would diverge on inference. Mixed fleets are refused at registration.

**On both Macs:**

```bash
# 1. Install LocallyAI as normal.
git clone https://github.com/<your-org>/locallyai.git && cd locallyai
bash install.sh

# 2. Set up Syncthing (installs via Homebrew, registers a launchd job,
#    prints this Mac's Syncthing Device ID).
bash scripts/syncthing_setup.sh
```

Pair the two Macs in each one's Syncthing GUI (`http://127.0.0.1:8384`):

1. On Mac-A's GUI: **Add Remote Device** → paste Mac-B's Device ID. Tick the `locallyai-shared` folder.
2. On Mac-B's GUI: **Add Remote Device** → paste Mac-A's Device ID.
3. Accept the folder share when prompted, pointing at `<repo>/shared` on each side.
4. Wait until both Syncthing GUIs show the folder **Up to Date** (under 30s on a LAN).

**Then on both Macs:**

```bash
# 3. Stand up the 2-node Qdrant cluster.
#    On Mac-A (the bootstrap node):
QDRANT_NODE_BIND_IP=10.0.0.11 \
QDRANT_API_KEY=<shared-secret> \
bash scripts/qdrant_cluster_setup.sh

#    On Mac-B (joining):
QDRANT_NODE_BIND_IP=10.0.0.12 \
QDRANT_BOOTSTRAP_PEER=http://10.0.0.11:6335 \
QDRANT_API_KEY=<shared-secret> \
bash scripts/qdrant_cluster_setup.sh

# 4. Wire HA into .env on BOTH Macs:
cat >> .env <<EOF
LOCALLYAI_SHARED_DIR=$(pwd)/shared
QDRANT_URLS=http://10.0.0.11:6333,http://10.0.0.12:6333
QDRANT_API_KEY=<shared-secret>
LOCALLYAI_HA=1
EOF

# 5. Restart and verify.
launchctl kickstart -k gui/$(id -u)/com.locallyai.server
ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' .env | cut -d= -f2)
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/admin/fleet/audit-verify
# Expect both nodes listed with status:"ok".
```

### Bring-up (Windows edition)

Same flow, PowerShell scripts. See [`docs/windows.md`](docs/windows.md) for prerequisites (BitLocker, Docker Desktop, NSSM) and the `.ps1` equivalents:

```powershell
# Run elevated on each Windows node.
PowerShell -ExecutionPolicy Bypass -File .\install.ps1
PowerShell -ExecutionPolicy Bypass -File .\scripts\syncthing_setup.ps1
# Bootstrap node 1, then join from node 2 — same env-var pattern as Mac:
PowerShell -ExecutionPolicy Bypass -File .\scripts\qdrant_cluster_setup.ps1
```

### Worker UI in HA mode

Tell the worker app about both nodes. It health-checks each every 5s, prefers the most-recently-healthy, and retries failed requests on the next node — chats survive a node death with a brief stutter; the server's per-node 120s idempotency cache prevents double-billing when the original node had actually completed.

```bash
# In apps/worker-ui/.env (or .env.local):
VITE_API_BASE_URLS=https://10.0.0.11:8000,https://10.0.0.12:8000
```

### Fleet dashboard

A small admin SPA in `apps/fleet-ui` shows per-node API health, audit chain status, Qdrant cluster health, sync conflicts, and aggregated alerts. Auto-refreshes every 5s. Sign in with `LOCALLYAI_ADMIN_KEY`.

```bash
cd apps/fleet-ui
npm install
npm run dev      # http://127.0.0.1:5175
# Or build for production:
npm run build && npm run preview
```

### Compliance

`/admin/processing-record` (admin-only) returns the live RoPA — version 1.1 includes a `high_availability` block describing the active node list, shared storage path, Qdrant topology, audit-chain model, failover model, and sync layer. Hand to your DPO.

The chaos suite asserts the failure-mode invariants in CI:

```bash
.venv/bin/python tests/ha_chaos.py
# Expect: pass=9 fail=0
```

---

## Switching to a Mac Studio with the 70B model

The default install picks `qwen2.5:7b` (fits a 16 GB MacBook). On a Mac Studio with 64 GB+ RAM you can run far bigger models:

```bash
ollama pull llama3.3:70b
# Edit .env — change the OLLAMA_MODEL line:
sed -i '' 's/^OLLAMA_MODEL=.*/OLLAMA_MODEL=llama3.3:70b/' .env
launchctl stop com.locallyai.server && launchctl start com.locallyai.server
```

`/v1/models` lists every locally-installed model immediately — no service restart required for clients to *see* a new model. Only the **default** requires the `.env` + restart.

---

## Publishing your own fork to GitHub

One command creates a new GitHub repo and pushes everything in `production/` to it (respecting `.gitignore`, so no secrets leak). Two equivalent scripts are provided:

| Platform | Script | Setup |
|---|---|---|
| macOS / Linux / Git Bash on Windows | `scripts/publish_to_github.sh` | `brew install gh && gh auth login` |
| Windows PowerShell (5.1 or 7+) | `scripts/publish_to_github.ps1` | `winget install GitHub.cli && gh auth login` |

### macOS / Linux / Git Bash (Windows)

```bash
# One-time setup:
brew install gh        # macOS
gh auth login

# From inside production/:
bash scripts/publish_to_github.sh locallyai            # private repo (default)
bash scripts/publish_to_github.sh locallyai --public   # public repo
```

### Windows PowerShell

```powershell
# One-time setup (open PowerShell as Administrator for the install):
winget install --id GitHub.cli
gh auth login                # follow the browser flow

# From inside production\ :
# PowerShell 7+ (preferred):
pwsh scripts\publish_to_github.ps1 -Name locallyai
pwsh scripts\publish_to_github.ps1 -Name locallyai -Public

# Or Windows PowerShell 5.1 (the one that ships with Windows by default):
powershell -ExecutionPolicy Bypass -File scripts\publish_to_github.ps1 -Name locallyai
```

If you'd rather use Git Bash on Windows, the `.sh` script also works there — Git for Windows ships with bash built in.

### What both scripts do

1. Verify `gh` is installed and authenticated.
2. `git init` if there's no repo yet (or use the existing one).
3. Stage every tracked file and commit (`.gitignore` blocks `.env`, `tls/`, `users.json`, `storage/`, `logs/`, etc.).
4. Create the GitHub repository and push `main` via `gh repo create --source=. --push`.
5. Print the resulting URL plus the clone-and-install snippet for the next user.

After publishing, anyone with access can clone and run on an Apple Silicon Mac:

```bash
git clone <url-the-script-printed>
cd <repo-name>
bash install.sh
```

If the repo already exists on GitHub (e.g. you ran the script before), the script just pushes new commits to the existing remote — no errors, no duplicates.

> **Note for Windows publishers:** the `install.sh` itself only runs on macOS Apple Silicon. You can publish from Windows fine (the publishing scripts are platform-aware), but the actual deployment target is a Mac.

---

## File layout

| Path | Purpose |
|---|---|
| `install.sh` | One-click installer (run this first) |
| `update.sh` | Stops service, upgrades deps, restarts |
| `uninstall.sh` | Removes the launchd job (optionally wipes state) |
| `chat.py` | Interactive terminal REPL |
| `test.py` | 4-check smoke test |
| `manage_users.py` | CLI for users.json |
| `api.py` | FastAPI app — `/healthz`, `/v1/chat/completions`, `/v1/models`, `/v1/ingest`, `/admin/*` |
| `supervisor.py` | launchd entrypoint — TLS gate + heartbeat fork + auto-restart |
| `service.py` | Windows pywin32 wrapper (unused on macOS) |
| `os_supervisor.py` | Cross-platform helpers used by `supervisor.py` (POSIX `lsof`/`ps` vs Windows `netstat`/`tasklist`, signal handling) |
| `platform_compat.py` | `chmod_safe()` — POSIX `chmod` on Mac, `icacls` on Windows |
| `shared_lock.py` | Cross-platform file lock (POSIX `flock` / Windows `msvcrt.locking`) used for shared-store writes |
| `fleet.py` | `fleet.json` registry — register / heartbeat / deregister / active_nodes |
| `sync_conflicts.py` | Detects Syncthing `*.sync-conflict-*` files and quarantines them under `SHARED_DIR/conflicts/` |
| `config.py` | Constants, env loading, user lookup, **mtime-cached re-reads** of users.json + erasure ledger for HA |
| `ingest.py` | PDF / Word / MD → chunks → Qdrant + BM25 |
| `retrieval.py` | Hybrid retrieval (Qdrant dense + BM25 sparse, RRF fusion) |
| `bm25.py` | Local BM25 index (no external lib) |
| `mlx_inference.py` | Optional MLX/Metal backend (Apple Silicon native) |
| `scripts/audit_install.sh` | 14-check install audit (Mac) |
| `scripts/audit_install.ps1` | 14-check install audit (Windows) |
| `scripts/syncthing_setup.sh` / `.ps1` | Per-node Syncthing bring-up for the HA shared store |
| `scripts/qdrant_cluster_setup.sh` / `.ps1` | Bootstrap / join the 2-node Qdrant cluster |
| `install.ps1` | Windows installer (winget + Ollama + NSSM service + self-signed TLS + first user) |
| `tests/ha_chaos.py` | 8 in-process chaos invariants — idempotency, cross-node retry, tail truncation, fleet aggregation, sync-conflict quarantine, erasure ledger, streaming SSE |
| `apps/fleet-ui/` | Admin dashboard — per-node health, audit chain, Qdrant, sync conflicts, alerts |
| `scripts/publish_to_github.sh` | One-shot publish (macOS / Linux / Git Bash) |
| `scripts/publish_to_github.ps1` | One-shot publish (Windows PowerShell) |
| `com.locallyai.audit.plist` | Weekly launchd template for the audit |
| `data/welcome.md` | Seed document — proves the RAG pipeline end-to-end |
| `demo/` | Demo-mode kit: 5 sample legal docs + `run_demo.py` end-to-end runner. See `demo/README.md`. |
| `apps/worker-ui/` | TanStack Start browser workspace + `launch.sh` / `launch.bat` self-healing launcher |
| `audit_export/` | `/export/*` router for compliance exports |
| `billing/` | `/billing/*` router for per-user usage and invoices |
| `monitoring/` | `/monitor/*` router for health and alerts |
| `watchdog/` | Sentinel, Heartbeat, Resurrector, Diagnostician |
| `DPA_DRAFT.md` | Data processing agreement template |

After install, these directories appear (all gitignored):

| Path | Contents |
|---|---|
| `.env` | Admin/audit secrets, model config (chmod 600) |
| `.venv/` | Python virtualenv |
| `tls/` | Self-signed cert + private key (chmod 600) |
| `users.json` | `{"<name>": "<api_key>"}` (chmod 600). HA: lives under `LOCALLYAI_SHARED_DIR/`; Syncthing-replicated. |
| `fleet.json` | HA fleet membership registry (under `LOCALLYAI_SHARED_DIR/`). 1 entry in single-node. |
| `erasure.log` | GDPR Art. 17 tombstones (under `LOCALLYAI_SHARED_DIR/` in HA). |
| `conflicts/` | HA only — Syncthing conflict files quarantined here for operator review. |
| `storage/` | Qdrant vector DB and BM25 index |
| `logs/` | `api.log`, `audit.log`, `billing.log`, `heartbeat.log`, `resurrector.log`, `sentinel.log`, `security.log`, `launchd*.log` (per-node — never on shared storage) |
| `logs/.audit_chain` | HMAC chain head (tamper-evidence state, per-node) |
| `.ingest_state.json` | File-hash state for incremental re-ingest |

---

## Compliance properties

- **GDPR Article 25** (data minimisation): `audit.log` stores only a salted SHA-256 hash of usernames; the salt is private to the deployment.
- **GDPR Article 17** (erasure): `manage_users.py erase` removes the user, redacts `billing.log`, writes a tombstone to the shared `erasure.log`, and (in HA) fan-outs `/admin/fleet/refresh` to every peer so erasure is honoured fleet-wide within one network round-trip.
- **GDPR Article 30** (RoPA): `GET /admin/processing-record` returns the live record, version 1.1 — includes the `high_availability` block when HA is enabled.
- **ISO 27001 A.5.30 / A.8.14** (business-continuity / redundancy): 2-node fleet; smart-client failover; 2-node Qdrant cluster (RF=2, write_consistency=2 — partitions go read-only, no silent divergence).
- **ISO 27001 A.5.33 / A.8.15** (records / logging integrity): every audit-log entry is HMAC-chained to the previous entry per node. `GET /admin/audit-verify` re-validates the chain (replays archives + tail-truncation check). `GET /admin/fleet/audit-verify` aggregates across the fleet.
- **ISO 27001 A.9 / A.8.3** (access control): per-user API keys with TTL, IP-based lockout after N failed auth attempts, separate admin key for management endpoints, salted-hash failed-auth fingerprints (no credential material in logs).
- Real usernames live in `billing.log` (admin-only) for invoicing; never in `audit.log`.
- Query content is never logged — only a SHA-256 hash, timestamp, pseudonymised user, model, sources retrieved, and `node_id` (HA).
- See `DPA_DRAFT.md` for the DPA template and [`docs/iso27001-controls.md`](docs/iso27001-controls.md) for the full Annex A control map with verification commands.

---

## Network requirements

- Static internal IP recommended.
- Ports 8000 (API) and 11434 (Ollama) — internal LAN only, never internet-facing.
- Read-only access to document storage (file server / SharePoint) for ingestion.
- **No outbound or inbound internet required after install.** The Mac can be air-gapped.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `[FAIL] Apple Silicon required` | Running on Intel Mac, Linux, or Windows | Use an M1/M2/M3/M4 Mac |
| `Port 8000 is already held by ...` | AirPlay Receiver or a dev server | Quit the holder, OR `PORT=8001 bash install.sh` |
| `[FAIL] Homebrew not found` | brew not installed | Install Homebrew (one-line command in *What you need* above) |
| `chat.py` says "Cannot reach https://localhost:8000" | Service not running | `launchctl list \| grep com.locallyai.server` — if missing, `launchctl load ~/Library/LaunchAgents/com.locallyai.server.plist` |
| `chat.py` says "Invalid API key" | Wrong/expired key | `python manage_users.py list`, then `rotate <name>` |
| Chat returns 502 | Ollama down or model not pulled | `brew services restart ollama`, then `ollama list` to verify the model |
| `sources_retrieved=0` on every chat | Ingest hasn't run, or `data/` is empty | Drop docs into `data/`, then `python ingest.py` |
| `audit-verify` returns `"status":"skipped"` | `LOCALLYAI_AUDIT_HMAC_KEY` not set | Re-run install.sh on a fresh box, or generate one and add to `.env` |
| Slow first response, fast later | Cold model load — Ollama lazy-loads | Normal; will be fast for ~10 minutes after last use |
| Heartbeat log shows repeated `probe_failed` | API can't bind, or supervisor crashed | `tail logs/launchd_error.log` for the real error |
| Worker UI says "Could not reach the LocallyAI server" | Browser doesn't trust `tls/cert.pem`, so its `fetch()` to `https://localhost:8000` is silently rejected | Run the `security add-trusted-cert` command in *Trusting the TLS cert* above (or click through `https://localhost:8000/healthz` once per browser as a fallback) |
| (HA) Fleet dashboard shows a node "offline" but it's actually up | Sentinel hasn't refreshed `fleet.json` last_seen yet (60s tick) OR the peer's `api_url` is wrong | Wait 90s; if still offline check `cat $LOCALLYAI_SHARED_DIR/fleet.json` and that the URL there is reachable from the other node |
| (HA) `/admin/fleet/audit-verify` returns `degraded` with a peer "unreachable" | Self-signed TLS cert mismatch, or peer firewall blocks 8000 | Verify direct curl from this node to the peer's `/healthz`; check Windows firewall or pfctl rules |
| (HA) `audit-verify` on one node = TAMPERED after operator wiped audit.log | Tail-truncation detector — audit.log was emptied while `.audit_chain` still held a head | Working as designed (defends against silent log deletion). To start a clean chain era: `rm logs/.audit_chain logs/audit.log` and restart |
| (HA) Sync-conflict alert fires | Both nodes wrote `users.json` / `erasure.log` in the same window | Open the fleet dashboard's *Sync conflicts* section, review files in `$LOCALLYAI_SHARED_DIR/conflicts/`, decide which version is canonical, never auto-merge |

If anything looks off, run the audit:

```bash
bash scripts/audit_install.sh
# Read the report at logs/install_audit_<today>.log
```

---

## Going to production at a client site

1. **Order the hardware.** A Mac Studio with at least 64 GB unified RAM for serious workloads (Llama 3.3 70B FP16 wants ~140 GB; quantised variants run in 64 GB; 7B models run in 16 GB).
2. **Install on the box.** `git clone && bash install.sh`. Pick the model that fits the RAM.
3. **Connect to the firm's documents.** Mount the file server / SharePoint export as read-only into `data/`, then `python ingest.py`.
4. **Add the firm's users.** `python manage_users.py add "First Last"` for each fee earner. Distribute keys.
5. **Lock down the network.** Block all outbound from the Mac at the firewall after the model is downloaded. Allow inbound only on 8000 from the internal LAN.
6. **Set up the weekly audit.** Activate `com.locallyai.audit.plist` (instructions above). Optionally wire `LOCALLYAI_ALERT_WEBHOOK_URL` in `.env` to a Slack/Teams hook so failures page someone.
7. **Sign the DPA.** Use `DPA_DRAFT.md` as the starting point.

---

## Optional MLX backend (Apple Silicon native)

The default backend is Ollama (works on any platform Ollama supports). For pure Metal acceleration without the Ollama runtime, set `LOCALLYAI_BACKEND=mlx` in `.env` and provide an `MLX_MODEL` path. See `mlx_inference.py`. Embeddings always go through Ollama regardless — they're decoupled from the chat backend.

---

## Operations runbook

The full click-by-click SOP — setup, daily ops, maintenance, compliance, every incident playbook, recovery, decommission — lives at [docs/SOP.md](docs/SOP.md). Read it before going live with a client.

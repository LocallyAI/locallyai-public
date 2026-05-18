# Incident playbooks — supply chain & upstream dependencies

When something we depend on (Hugging Face, Ollama, Apple, Microsoft,
Docker, Python, GitHub, npm, brew) breaks, changes terms, or goes
hostile. LocallyAI is "no internet egress required after install,"
but **install** and **updates** do depend on these — so this chapter
matters mostly during maintenance windows.

---

## Hugging Face down

**Trigger:** `ollama pull <model>` fails (Ollama hits HF for some
models) or MLX model load fails fetching `huggingface.co`.

### Diagnose

```bash
curl -sf https://huggingface.co/api/models/<repo>/<model> | head -5
```

If 5xx or no response: HF is down. https://status.huggingface.co
confirms.

### Action

If you're not in the middle of a model swap or a fresh install,
you don't care — LocallyAI runs locally.

If you ARE installing or pulling a new model:

- Wait. HF outages typically clear in <1 hour.
- If urgent, the firm has the option to **vendor the model
  artifacts** to a firm-controlled S3 bucket / NAS. One-time setup;
  then pulls go to the internal mirror, not HF.
- Or ask the LocallyAI vendor for a one-shot signed model bundle
  delivered out of band.

### Prevention

For production deployments where model swaps must be reliable: yes,
mirror models internally. For pilots, accept HF as a dependency.

---

## Ollama upstream API changes

**Trigger:** an Ollama upgrade lands; LocallyAI's API integration
breaks. Symptoms: 502 from `/v1/chat/completions`, weird responses,
log lines mentioning Ollama in the API.

### Diagnose

```bash
ollama --version
curl -sf http://localhost:11434/v1/models
curl -sf http://localhost:11434/api/tags
```

LocallyAI uses Ollama's **OpenAI-compatible** `/v1/chat/completions`
endpoint. Ollama has been stable on this endpoint, but breaking
changes happen in major versions.

### Fix

**Pin Ollama version**:

```bash
brew uninstall ollama
brew install ollama@<known-good-version>   # if available
# Or — install from the GitHub release:
curl -L https://github.com/ollama/ollama/releases/download/v0.5.0/ollama-darwin -o /tmp/ollama
sudo mv /tmp/ollama /usr/local/bin/ollama
sudo chmod +x /usr/local/bin/ollama
brew services restart ollama
```

Test the LocallyAI integration:

```bash
ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' .env | cut -d= -f2)
USER=<a real user key>
curl -sk -X POST -H "Authorization: Bearer $USER" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"hi"}],"max_tokens":5}' \
  https://localhost:8000/v1/chat/completions
```

### Prevention

In `requirements.txt` for Python, the LocallyAI codebase can pin
Ollama via a system-level note (Homebrew doesn't pin transitive
dependencies). Document the known-good Ollama version in
[maintenance.md § "Local customisations"](maintenance.md) for the
firm's specific deployment.

---

## Python release breaks dependencies

**Trigger:** the firm upgrades macOS / Homebrew, Python jumps from
3.12 to 3.13, the venv breaks because some package isn't 3.13-ready
yet.

### Diagnose

```bash
.venv/bin/python --version
# 3.13.x?
.venv/bin/python -c "import api"
# ImportError or distinct binary mismatch?
```

The supervisor's launchd plist references the absolute Python path.
A Homebrew Python upgrade can change `/opt/homebrew/bin/python3`'s
target.

### Fix

**Rebuild the venv** with a known-good Python:

```bash
launchctl bootout gui/$(id -u)/com.locallyai.server
mv .venv .venv.broken
/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.locallyai.server.plist
```

**Pin Python** for the deployment by referencing the explicit
versioned path in launchd plist. The install.sh writes
`PYTHON=$(which python3)` which can drift across system upgrades —
worth changing to `python3.12` explicitly.

### Prevention

Test on a dev box BEFORE upgrading the deployment Mac's Python /
macOS / Homebrew. Maintain a snapshot of working `.venv` for
rollback.

---

## macOS update breaks launchd plist

**Trigger:** macOS major version upgrade (e.g. Sonoma → Sequoia →
Tahoe). The launchd job's `LimitLoadToSessionType` or
`MachServices` semantics changed, OR System Integrity Protection
moved a path the plist depends on.

### Diagnose

```bash
launchctl print gui/$(id -u)/com.locallyai.server | head -30
# Look for "state = exited" or "spawn failed" messages.
```

```bash
log show --predicate 'subsystem == "com.apple.xpc.launchd"' \
  --info --last 1h | grep -i locallyai
```

### Fix

The plist is at `~/Library/LaunchAgents/com.locallyai.server.plist`.
Re-render via `install.sh` (which knows the current macOS conventions):

```bash
launchctl bootout gui/$(id -u)/com.locallyai.server 2>/dev/null
rm ~/Library/LaunchAgents/com.locallyai.server.plist
cd ~/locallyai
bash install.sh    # detects existing .env, only re-creates plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.locallyai.server.plist
```

### Prevention

Don't auto-upgrade macOS on production deployment Macs. Disable
auto-update under System Settings → General → Software Update →
Automatic Updates → off. Schedule manual upgrades during
maintenance windows.

---

## Windows update breaks NSSM service

**Trigger:** Windows Update lands; the `LocallyAIServer` service
won't start; Event Log shows the wrapper failed to spawn the
process.

### Diagnose

```powershell
Get-Service LocallyAIServer
Get-EventLog -LogName Application -Source LocallyAIServer -Newest 20
nssm get LocallyAIServer Application
```

### Fix

```powershell
# Re-register the service (preserves config; just re-binds NSSM):
nssm stop LocallyAIServer
nssm remove LocallyAIServer confirm
PowerShell -ExecutionPolicy Bypass -File C:\locallyai\install.ps1
Start-Service LocallyAIServer
```

### Prevention

Defer feature updates on the deployment Windows box. Use Windows
Update for Business policies if your firm has them; otherwise:

```powershell
# Defer feature updates by 30 days (LTSC / Pro):
Set-WUSettings -DeferFeatureUpdatesPeriodInDays 30
```

---

## Docker Desktop license terms change

**Trigger:** Docker, Inc. changes commercial terms for Docker
Desktop; firm's legal flags it.

### Action

LocallyAI uses Docker for Qdrant. Replacement options:

- **Colima** (Mac, free): `brew install colima`. Provides a Docker
  daemon equivalent. Most Docker commands work unchanged.
- **Rancher Desktop** (Mac/Win, free): drop-in.
- **Run Qdrant natively** (no container): download the binary,
  manage as a launchd / Windows service yourself. More fiddly but
  zero container runtime.

### Migration

For Colima:

```bash
brew install colima
colima start --cpu 4 --memory 8 --disk 50
# Existing Docker images and containers are NOT preserved across
# Docker Desktop → Colima switch. Re-pull and re-create:
docker stop locallyai-qdrant && docker rm locallyai-qdrant
bash scripts/qdrant_cluster_setup.sh   # (or single-node setup)
```

The Qdrant data directory under `storage/qdrant/` is preserved
because we mount it; only the runtime moves. So no re-ingest
needed.

### After-action

Update [maintenance.md § "Local customisations"](maintenance.md) to
record the container runtime switch.

---

## GitHub down (can't pull updates)

**Trigger:** `git pull` fails; https://www.githubstatus.com
confirms outage.

### Action

You don't need GitHub to operate the deployment. Skip the update.

If the update is **time-critical** (a CVE patch in a dependency),
the firm has options:

- Pull from the LocallyAI vendor's mirror if they maintain one.
- Have the vendor send the patch as a tarball out of band.
- Wait for GitHub.

For non-critical: GitHub outages typically clear in <4 hours.

### Prevention

For air-gapped deployments: mirror the LocallyAI git repo into a
firm-controlled Gitea / GitLab on the LAN. The deployment Mac pulls
from that, never from github.com.

---

## pip / npm registry compromise advisory

**Trigger:** a security advisory lands: a package in your dependency
tree was compromised or removed.

### Action

For pip:

```bash
.venv/bin/pip install pip-audit
.venv/bin/pip-audit
# Lists vulnerable packages with CVEs.
.venv/bin/pip install --upgrade <vulnerable-package>
launchctl kickstart -k gui/$(id -u)/com.locallyai.server
bash scripts/audit_install.sh
```

For npm (worker-ui / manager-ui / fleet-ui):

```bash
cd apps/worker-ui
npm audit
npm audit fix
npm run build
# repeat for manager-ui and fleet-ui
```

### When the advisory is more serious than upgrade-fixable

If the package was malicious (typo-squatting, compromised
maintainer), `npm audit fix` may not resolve. Manual procedure:

1. Identify which of LocallyAI's direct deps pulled the malicious
   transitive.
2. Check whether the deployment was running the affected version
   ever — `pip show <package>` shows the installed version; compare
   against the advisory's vulnerable range.
3. If yes: this is a security incident. See
   [incidents-security.md § "Suspected unauthorised access"](incidents-security.md#suspected-unauthorised-access)
   — assume the malicious package may have exfiltrated env vars.

### Prevention

Pin transitive deps in `requirements.txt` (`pip freeze >
requirements.txt`); periodically `pip-audit` (quarterly per
[maintenance.md § Quarterly](maintenance.md#quarterly)).

---

## Apple Developer ID changes / cert revoked

**Trigger:** macOS shows "Cannot verify developer" for a tool
LocallyAI depends on (Ollama, Docker Desktop, Syncthing). Apple
revoked the developer's signing cert.

### Action

The tool still works locally, but new installs of it on other Macs
fail.

If the cert is revoked because of malware: take that tool off the
deployment, find a replacement.

If revoked for administrative reasons: wait for the developer to
re-sign and ship an update.

---

## Self-signed cert distrusted by browser update

**Trigger:** a browser update tightens self-signed cert acceptance;
worker-ui stops loading from `https://localhost:8000`.

### Diagnose

In the browser dev tools (Cmd-Opt-J), the network panel shows the
fetch failing with a TLS reason.

### Fix

The cert needs to be re-trusted in the System keychain (it may have
been silently downgraded). See
[setup-mac-single.md § 3.4](setup-mac-single.md#34-prompt-trust-the-tls-cert).

If the browser refuses self-signed certs entirely (Safari has
gotten stricter): the firm needs to deploy a real cert from an
internal CA. That's a network-engineering project — out of scope
for this SOP, but contact your firm's IT for an internal CA-signed
cert if available.

---

## Embed model goes EOL

**Trigger:** Hugging Face / vendor publishes a notice that
`nomic-embed-text` (the default LocallyAI embedding model) is being
retired or replaced.

### Action

The embedding model is what turns docs and queries into vectors.
Switching it requires a full re-index because old vectors and new
vectors are in different spaces.

```bash
# 1. Pull the new embedding model:
ollama pull <new-embed-model>
# 2. Update .env: EMBED_MODEL=<new-embed-model>
# 3. Force re-index:
python ingest.py --force
# 4. Verify a sample query returns sources:
curl -sk -X POST -H "Authorization: Bearer $USER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"test phrase from a doc"}]}' \
  https://localhost:8000/v1/chat/completions
```

### Prevention

Subscribe to the embed-model vendor's release notes. Plan a swap
during a maintenance window — re-indexing a 1000-doc corpus takes
30+ minutes.

---

## Annual supply-chain review

Once a year:

- [ ] Pin known-good versions of Python, Ollama, Docker (or
      replacement), Syncthing, Qdrant.
- [ ] Verify the firm has internal mirrors / vendored copies of
      anything air-gap-critical (LocallyAI repo, models, deps).
- [ ] Run `pip-audit` and `npm audit`; address findings.
- [ ] Review terms-of-service of every commercial component.
- [ ] Confirm the firm has rollback paths for each upstream change
      (e.g. previous Ollama binaries archived, previous Python venv
      preserved).

# Decommission

When a deployment is being shut down for good — firm changes vendor,
project ends, hardware retires. Do this carefully: regulators care
about end-of-processing as much as start-of-processing.

---

## Before you start

### Reasons people decommission

- **Vendor change** — the firm picks a different AI tool. LocallyAI is
  off-boarded.
- **Project end** — pilot finished, go/no-go was no.
- **Hardware EOL** — Mac/PC retired, replaced. (If replaced with new
  hardware running LocallyAI, this isn't decommission — see
  [incidents-physical.md § "Hardware replacement"](incidents-physical.md#hardware-replacement).)
- **Firm closure** — the firm itself is winding up.

### Decisions the DPO must sign off

Before any technical action:

1. **Retention obligations.** Some logs must be kept past the
   deployment's life — typically 6 years for legal-services billing
   records (SRA). Decide what's archived to cold storage and what's
   destroyed.
2. **Data-subject notifications.** Every active user should be told
   their access is being removed and given a deadline to export
   anything they need (which, in LocallyAI, is essentially nothing —
   query content was never stored — but they should know).
3. **Data-controller-to-controller handoff** if the firm is migrating
   to a new vendor. The firm controls; LocallyAI was an on-prem tool;
   no transfer of personal data to the new vendor is automatic.

---

## Procedure (single-node)

### 1. Notify users

At least 7 days before decommission. Email + worker-app banner. Tell
users:

- The date access stops.
- That nothing they typed has been stored as text — only metadata.
- How to export anything they personally maintain elsewhere.

### 2. Stop accepting new chats

On decommission day:

```bash
launchctl bootout gui/$(id -u)/com.locallyai.server
```

User keys now 401. The `/healthz` endpoint will fail too. The worker
app will show "Could not reach LocallyAI server."

### 3. Final audit-verify

Capture the last verifiable chain state for the evidence pack:

```bash
# Service is down, so use the verifier directly via Python.
.venv/bin/python <<'PY'
import json, hmac, hashlib
from dotenv import load_dotenv; load_dotenv('.env')
import os
key = os.environ['LOCALLYAI_AUDIT_HMAC_KEY'].encode()
prev = '0'*64
for line in open('logs/audit.log'):
    e = json.loads(line)
    stored = e.pop('_chain_hmac', None)
    if not stored: continue
    payload = json.dumps(e, sort_keys=True)
    expected = hmac.new(key, (prev + payload).encode(), hashlib.sha256).hexdigest()
    if stored != expected:
        print("CHAIN BROKE AT:", e.get('timestamp'))
        break
    prev = stored
print("Chain end:", prev)
PY
```

Save the output. This is the "as of decommission, the chain was
intact" evidence.

### 4. Build the final evidence pack

```bash
mkdir -p ~/locallyai-final-<date>
cd ~/locallyai-final-<date>

# Compliance docs
cp -r ~/locallyai/docs .
cp ~/locallyai/SOP.md . 2>/dev/null || true
cp ~/locallyai/docs/SOP.md .

# Logs (audit + billing + erasure for retention)
cp -r ~/locallyai/logs/audit-*.log.gz .
cp -p ~/locallyai/logs/audit.log .
cp -p ~/locallyai/logs/billing.log .
cp -p ~/locallyai/erasure.log . 2>/dev/null || true       # single-node
cp -p $LOCALLYAI_SHARED_DIR/erasure.log . 2>/dev/null || true   # HA

# Configuration as it was at end-of-life (DO NOT include keys in the
# off-the-firm pack; sanitize first)
cp ~/locallyai/.env .env.full
sed -E 's/^(LOCALLYAI_ADMIN_KEY=).*$/\1<redacted>/;
        s/^(LOCALLYAI_AUDIT_HMAC_KEY=).*$/\1<redacted>/;
        s/^(LOCALLYAI_AUDIT_SALT.*=).*$/\1<redacted>/;
        s/^(QDRANT_API_KEY=).*$/\1<redacted>/' \
   .env.full > .env.redacted
rm .env.full

# Final RoPA snapshot
cp ~/locallyai-final-<date>/RoPA_decommission_<date>.json /tmp/ropa.json 2>/dev/null
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.locallyai.server.plist
sleep 30
ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' ~/locallyai/.env | cut -d= -f2)
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/processing-record \
  > RoPA_decommission_<date>.json
launchctl bootout gui/$(id -u)/com.locallyai.server
```

(The brief restart is just to get the live RoPA. Don't accept any
new chats during this window — kill the worker-app launcher first if
in doubt.)

### 5. Hand off to the firm's archive

The DPO decides where the evidence pack goes:

- **Compliance archive** (mandatory): per the firm's retention
  schedule (typically 6 years for legal-services). Options: the
  firm's existing immutable archive, the firm's secure file server, an
  S3 bucket with Object Lock.
- **Operational archive** (optional): the audit/billing logs go into
  the firm's general document-retention store.

Encrypt the pack at rest:

```bash
tar czf locallyai-final-<date>.tar.gz ~/locallyai-final-<date>
gpg -c --cipher-algo AES256 locallyai-final-<date>.tar.gz
# Save the GPG passphrase in the firm vault.
rm locallyai-final-<date>.tar.gz   # only after gpg succeeded
```

### 6. Crypto-erase the live data

`logs/audit.log`, `users.json`, `data/`, `storage/qdrant`, `tls/key.pem`,
`.env` all contain regulated data. The disk is encrypted (FileVault /
BitLocker), so the cleanest erasure is **destroying the encryption
key**:

**Mac (FileVault):**

The disk is already encrypted with a per-volume key. Erasing the
volume securely:

```bash
# Option A — full secure erase via Disk Utility (recommended)
# 1. Boot the Mac into Recovery (Apple Silicon: hold Power on shutdown).
# 2. Open Disk Utility.
# 3. Select the volume → Erase → Security Options → most secure.
# Note: takes hours on large drives.

# Option B — cryptographic erase (instant)
# In Recovery → Disk Utility → Erase the encrypted volume.
# FileVault's master key is destroyed; the data is unrecoverable
# even if the disk is physically read by an attacker.
```

If the Mac is going back into use under the firm (not LocallyAI):
just delete the LocallyAI files and rotate FileVault.

**Windows (BitLocker):**

```powershell
# Cryptographic erase via:
manage-bde -off C:    # decrypt first if you want to reformat
# OR — for full secure erase:
cipher /w:C:\         # zeros free space; multi-pass
# OR — destroy the BitLocker key (instant equivalent):
manage-bde -forcerecovery C:
# then revoke the recovery key in your password vault.
```

### 7. Uninstall

```bash
# Mac:
launchctl bootout gui/$(id -u)/com.locallyai.server 2>/dev/null
launchctl bootout gui/$(id -u)/com.locallyai.audit 2>/dev/null
launchctl bootout gui/$(id -u)/com.locallyai.syncthing 2>/dev/null
launchctl bootout gui/$(id -u)/com.locallyai.fleet-ui 2>/dev/null
rm -rf ~/Library/LaunchAgents/com.locallyai.*.plist

# Reverse the trusted cert (if you added it):
sudo security delete-certificate -c locallyai /Library/Keychains/System.keychain

# Reverse the install:
bash uninstall.sh
```

```powershell
# Windows:
Stop-Service LocallyAIServer
nssm remove LocallyAIServer confirm
nssm remove LocallyAI-Syncthing confirm 2>$null

# Remove scheduled tasks:
Unregister-ScheduledTask -TaskName "LocallyAI-Syncthing" -Confirm:$false 2>$null

# Remove the trusted cert if you added one:
$cert = Get-ChildItem Cert:\LocalMachine\My | Where-Object { $_.Subject -like "*locallyai*" }
$cert | Remove-Item

# Remove the install:
Remove-Item -Recurse -Force C:\locallyai

# Remove firewall rules:
Get-NetFirewallRule | Where-Object DisplayName -like "*LocallyAI*" | Remove-NetFirewallRule
Get-NetFirewallRule | Where-Object DisplayName -like "*Qdrant*"    | Remove-NetFirewallRule
Get-NetFirewallRule | Where-Object DisplayName -like "*Syncthing*" | Remove-NetFirewallRule
```

### 8. Decommission Qdrant + Ollama (HA only)

If they were installed for LocallyAI specifically, remove them:

```bash
# Mac:
docker stop locallyai-qdrant && docker rm locallyai-qdrant
docker rmi qdrant/qdrant:v1.12.4
brew services stop ollama
brew uninstall ollama
brew services stop syncthing
brew uninstall syncthing
```

If Ollama is used by other things on the box, leave it.

### 9. Update credential register

In your firm's password vault:

- Mark every LocallyAI entry as `DECOMMISSIONED — <date>`. Don't
  delete; the audit pack references admin actions and DPO review may
  reference the entries.
- Move the entries to an "archive" folder per your firm's vault
  retention rules.

### 10. Notify regulators (if required)

Most decommissions don't trigger a regulator notification — you're
just stopping a processing activity. But if the deployment processed
high-volume sensitive personal data, the DPO may include the
decommission in the firm's annual processing report.

### 11. File the close-out report

A 1-page document filed with the DPO:

- Deployment ID and date range.
- Reason for decommission.
- Evidence pack location.
- Crypto-erase verification (timestamp + signature of the IT-ops
  person who did it).
- Any outstanding items (e.g. "audit archives moved to cold storage
  on <date>; retention timer set in firm's archive system to delete
  on <date>").

---

## Procedure (HA — both nodes)

Same as single-node, but per-node:

1. Notify users.
2. **Stop both nodes' API services.** (Don't stop one then the other —
   that triggers a failover and confuses things.)
3. Build the evidence pack from the SHARED store + each node's local
   audit log:

   ```bash
   mkdir -p ~/locallyai-final-<date>/{shared,mac-a,mac-b}
   cp -r $LOCALLYAI_SHARED_DIR/. ~/locallyai-final-<date>/shared/
   cp -r ~/locallyai/logs/.       ~/locallyai-final-<date>/mac-a/   # on Mac-A
   # …and same for Mac-B from its perspective.
   ```

4. Crypto-erase BOTH boxes.
5. Uninstall on BOTH boxes.
6. Decommission Qdrant on BOTH boxes (cluster goes away).
7. Stop Syncthing on BOTH boxes.
8. Update credential register; mark all per-node entries decommissioned.
9. Same close-out report as single-node, plus a note that BOTH nodes
   were retired together.

---

## What to keep, what to discard

After decommission, the firm will hold the encrypted evidence pack
(`gpg`-encrypted tarball + GPG passphrase in the vault) and **nothing
else** related to LocallyAI on the live boxes.

A regulator inspection 2 years later asks "did you process X user's
data?" The DPO unlocks the evidence pack, runs:

```bash
# Locate the pseudonym across eras using the era ids in the pack
.venv/bin/python <<'PY'
# Use the redacted .env to identify era ids; the actual salts are
# under separate-control-set retention; the DPO retrieves the
# specific era's salt only when re-identification is justified.
PY
```

The point of all of this: the firm CAN re-identify a specific user's
audit history when legally required, and CAN'T enumerate users
casually because the salt is held separately from the pseudonyms.

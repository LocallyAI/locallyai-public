# Recovery & Disaster Recovery

When the worst has already happened, this is the click-by-click for
getting back online.

> **Critical:** practise a DR drill at least once a year. The first
> time you do this in anger should not be the first time you do it
> at all.

---

## What's recoverable from what

| If you've lost… | Recover from… | Notes |
|---|---|---|
| The running service | Restart it. See `daily.md`. | 30 seconds. |
| `.env` | Backup, OR regenerate (per `incidents-operator.md § "Lost admin key AND .env"`). | Regeneration loses the audit chain key continuity. |
| `users.json` | `$LOCALLYAI_SHARED_DIR/users.json` (HA), peer node, Time Machine, or regenerate per-user. | HA recovery is fastest. |
| `tls/cert.pem` + `tls/key.pem` | Re-run install (regenerates cert; users re-trust). | 10 min including re-trust on user devices. |
| `audit.log` | **Not directly recoverable** — gone is gone. Start a new chain era; preserve the surviving archive (gz files). | Archives in `logs/audit-*.log.gz` are the historical record; treat them as forensic-frozen. |
| `data/` (the firm's documents) | Source-of-truth backup the firm maintains. LocallyAI doesn't own document storage. | Firm has its own DMS or file-server for the originals. |
| `storage/qdrant` (the index) | Re-create from `data/` via `python ingest.py --force`. | Fast. The vector index is derivable. |
| The Mac/PC itself | Replace hardware; restore other items as above. | See `incidents-physical.md § "Hardware replacement"`. |
| Both Macs (HA, total loss) | Restore from cold backup; pair fresh. | Worst case; this is the DR drill scenario. |

---

## Backup strategy (set this up day-1, not day-of-incident)

### What to back up — daily, automated, off-host

- `users.json` (or `$LOCALLYAI_SHARED_DIR/users.json` in HA).
- `.env` — encrypted in transit and at rest. **Never** commit to git.
- `tls/cert.pem` and `tls/key.pem`. (Cert can be regenerated; the
  trust footprint on user devices is the friction.)
- `data/` if the firm wants point-in-time recovery of the corpus
  (otherwise the firm's DMS already does this).
- `logs/audit-*.log.gz` and `logs/.audit_chain` — for compliance
  retention.
- `logs/billing.log` — for invoicing-history continuity.
- `logs/erasure.log` — for GDPR Art. 17 evidence.
- `fleet.json` — non-critical (auto-rebuilds on restart).

### Where

- **At minimum**: a Time Machine destination on a separate disk that
  IS NOT plugged into the same Mac all the time (cycle daily/weekly).
- **Better**: the firm's NAS or backup appliance, encrypted at rest.
- **For compliance evidence**: a separate, **immutable** off-site
  backup (write-once / S3 Object Lock / equivalent) — so a ransomware
  attack that encrypts the local backup can't reach the off-site one.

### Qdrant snapshots — set this up explicitly

Qdrant doesn't snapshot itself. Schedule a daily cron:

```bash
# crontab -e (Mac)
# 03:00 every day, snapshot the collection.
0 3 * * * /opt/homebrew/bin/curl -sk -X POST \
  -H "api-key: $QDRANT_API_KEY" \
  http://localhost:6333/collections/locallyai_legal_poc/snapshots \
  > /Users/<you>/locallyai/storage/qdrant/snapshots/locallyai-`date +\%Y-\%m-\%d`.snapshot
```

Windows equivalent: a scheduled task hitting the Qdrant `/snapshots`
endpoint with `Invoke-WebRequest`.

Copy the `storage/qdrant/snapshots/` directory off-host as part of
the daily backup.

---

## Restore from cold backup (single-node, full restore)

**Trigger:** the box is gone or unrecoverable. You have a backup tarball
from yesterday (or earlier) on a different disk.

### Procedure

1. **Pre-flight:** stand up fresh hardware per
   [setup-mac-single.md § 0–§3](setup-mac-single.md). DO NOT generate
   user keys or run ingest yet.
2. **Stop the service:**
   ```bash
   launchctl bootout gui/$(id -u)/com.locallyai.server
   ```
3. **Restore the secrets:**
   ```bash
   cp /backup/locallyai/.env .
   chmod 600 .env
   cp /backup/locallyai/users.json .
   chmod 600 users.json
   cp -r /backup/locallyai/tls .
   chmod 600 tls/key.pem
   ```
4. **Restore the audit + billing logs (read-only forensic value):**
   ```bash
   mkdir -p logs
   cp /backup/locallyai/logs/audit.log     logs/      2>/dev/null || true
   cp /backup/locallyai/logs/billing.log   logs/      2>/dev/null || true
   cp /backup/locallyai/logs/erasure.log   .          2>/dev/null || true   # in HA: shared/ instead
   cp /backup/locallyai/logs/.audit_chain  logs/      2>/dev/null || true
   chmod 640 logs/*.log logs/.audit_chain
   ```
5. **Restore `data/`** (if not already present from the firm's DMS):
   ```bash
   mkdir -p data
   cp -r /backup/locallyai/data/. data/
   ```
6. **Restore Qdrant** — take the latest snapshot:
   ```bash
   docker stop locallyai-qdrant 2>/dev/null
   docker rm locallyai-qdrant 2>/dev/null
   # Restore the storage dir entirely (snapshots include the index)
   rm -rf storage/qdrant/*
   cp -r /backup/locallyai/storage/qdrant/. storage/qdrant/
   # Or — if you have a clean snapshot file but no full storage dir,
   # restart Qdrant fresh and use Qdrant's API to upload the snapshot:
   # See https://qdrant.tech/documentation/concepts/snapshots/
   ```
7. **Restart the service:**
   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.locallyai.server.plist
   ```
8. **Verify:**
   ```bash
   curl -sk https://localhost:8000/healthz
   ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' .env | cut -d= -f2)
   curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/audit-verify
   ```

If `audit-verify` returns TAMPERED with a "tail truncated" reason:
the backed-up `.audit_chain` doesn't match the backed-up `audit.log`
(point-in-time skew between when each was copied). Recover by:
```bash
# Either: lop the chain head off (continue chain from most-recent
# valid entry):
.venv/bin/python -c "
import json, hmac, hashlib, os
key = bytes.fromhex(open('.env').read().split('LOCALLYAI_AUDIT_HMAC_KEY=')[1].split()[0])
prev = '0'*64
last = None
for line in open('logs/audit.log'):
    e = json.loads(line)
    stored = e.pop('_chain_hmac', None)
    payload = json.dumps(e, sort_keys=True)
    h = hmac.new(key, (prev + payload).encode(), hashlib.sha256).hexdigest()
    if h == stored: prev = stored; last = stored
    else: break
print(last)
" > logs/.audit_chain
chmod 640 logs/.audit_chain
```

Document the recovery — the chain has a discontinuity between backup
time and now; that's evidence of restoration.

---

## Restore from cold backup (HA, full restore)

Same as single-node restore on Mac-A. Then bring Mac-B online per
[setup-mac-ha.md](setup-mac-ha.md). Mac-B will sync `users.json`,
`fleet.json`, `erasure.log` from Mac-A via Syncthing on its first
boot. Qdrant: re-bootstrap; Mac-B joins as a fresh peer; replicate
shards from Mac-A per [../qdrant-ha.md § "Re-adding a wiped
node"](../qdrant-ha.md#re-adding-a-wiped-node).

---

## Restore from sync peer (HA, one Mac lost)

If only one Mac is gone (the survivor is intact):

1. Stand up new hardware per
   [setup-mac-single.md](setup-mac-single.md) — single-node install
   first.
2. Pair Syncthing with the survivor per
   [setup-mac-ha.md § 1](setup-mac-ha.md#1-set-up-the-shared-store-syncthing--10-min-per-mac).
   Wait for "Up to Date" — `users.json`, `erasure.log`, `fleet.json`
   sync over.
3. Set `LOCALLYAI_NODE_ID` to a new id (`mac-c` if `mac-a` died and
   `mac-b` survived); copy salt + ERA env values from the survivor's
   `.env` (these are per-deployment, not per-node).
4. Re-bootstrap Qdrant peer per
   [../qdrant-ha.md § "Re-adding a wiped node"](../qdrant-ha.md#re-adding-a-wiped-node).
5. Verify.

---

## Restore from scratch (no backups)

**The bad case.** You have nothing — disks gone, backups gone, vault
unreachable.

### What's recoverable

- Anything still in the firm's git of LocallyAI (the code itself).
- The firm's DMS (the source documents) — assuming that wasn't on the
  Mac too.
- The user list (the firm knows who its lawyers are).

### What's NOT recoverable

- Audit history. Gone.
- Billing history. Gone.
- Pseudonym mappings. Gone.

### Procedure

1. **Tell the DPO immediately** — this is a major Art. 32 evidence
   loss. Likely Art. 33-eligible.
2. Fresh install on fresh hardware per
   [setup-mac-single.md](setup-mac-single.md).
3. Add users from scratch with `manage_users.py add`. Keys distribute
   to users (notify each).
4. Re-ingest `data/` from the firm's DMS.
5. Document everything in the incident register: what was lost,
   when, how, what's been reconstructed, what's irrevocably gone.

### After-action

- Find out why backups failed. Was it the backup process? The backup
  destination? Both lost in the same incident (e.g. fire)? Off-site
  was misconfigured?
- Fix that first. The next incident isn't the time to figure out
  backups don't work.

---

## Post-incident audit-chain verification

After any restore, verify the chain end-to-end:

```bash
ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' .env | cut -d= -f2)
curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/audit-verify
```

For the surviving HISTORICAL audit log (from before the incident), it
should still say `ok` — the verifier replays archives in date order
and checks each chain link.

If it says TAMPERED at a specific archive: that archive was corrupted
during the incident. Document; preserve in cold storage.

For the FRESH audit log (post-restore), it starts fresh from `0000…`
and grows clean.

---

## DR drill (annual)

A practice run. **Critical** — do this in a staging environment, NOT
production.

### Scenario A — single Mac dies

1. On the production Mac (HA, with a healthy peer): pretend Mac-A is
   dead. `launchctl bootout gui/$(id -u)/com.locallyai.server`.
2. Confirm the firm can still chat through Mac-B.
3. Bring Mac-A back: `launchctl bootstrap …`.
4. Document the time-to-detect, time-to-failover, time-to-restore.

### Scenario B — both Macs die

1. In a staging environment with two test Macs, simulate "fire" by
   `launchctl bootout` on both.
2. Stand up two fresh staging Macs.
3. Restore from yesterday's cold backup per
   [Restore from cold backup](#restore-from-cold-backup-single-node-full-restore).
4. Verify users can chat. Audit chain ok. Billing log present.
5. Document the time-to-restore.

### Scenario C — backup is corrupt

1. Open yesterday's backup tarball; randomly corrupt a few files.
2. Try the restore.
3. Document what fails and what alternatives exist (older backup,
   per-file recovery, regenerate).

### What to write down

For each scenario:
- Total time elapsed.
- Steps that didn't go to plan.
- Documents in this SOP that were unclear or wrong.
- File a list of doc-fixes against the SOP. Update before the next
  drill.

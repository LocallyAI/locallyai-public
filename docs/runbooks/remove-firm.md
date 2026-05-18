# Runbook: Remove a firm (decommission)

**When**: A firm has terminated their contract OR is migrating to different hardware. The DPA's Clause 10 (Term and termination) governs what happens next.

**Time budget**: 30 minutes vendor-side. Hardware-side depends on the firm's preference (data return vs secure erase).

**Risk if you stop midway**: The firm believes they're decommissioned but their telemetry continues OR their data sits on hardware you can't account for. **Both are DPA breaches.** Complete every step or back out fully.

**Prerequisites**:
- Written confirmation from the firm's authorised signatory that they are terminating
- DPA Clause 10 election: data return (10.2.a) OR secure erase + certificate (10.2.b) — get this in writing
- Access to vendor-records repo
- Cloudflare Workers admin TOTP

## Decision tree

| Firm chose (per Clause 10.2) | Procedure |
|---|---|
| Data return (10.2.a) | Step A — full extraction first, then Step B (vendor-side cleanup) |
| Secure erase + certificate (10.2.b) | Step B then Step C (erasure certificate) |
| Did not specify | **STOP** — go back to the firm and force a choice in writing. Do not assume |

## Step A — Data return (firm chose 10.2.a)

### A.1 Extract everything that's not encrypted-at-rest-only

The firm gets:

| What | Source | Format |
|---|---|---|
| Documents | `~/locallyai/data/uploads/` | original files (PDFs, DOCX, etc) |
| Conversation history | each user's browser localStorage | the firm exports per-user via Manager UI's export |
| Audit log | `~/locallyai/logs/audit.log` (+ rotated archives) | JSON-lines, GPG-sign for transit |
| Billing log | `~/locallyai/logs/billing.log` (+ rotated archives) | JSON-lines, GPG-sign for transit |
| Users record | `~/locallyai/users.json` (admin-key removed) | JSON |
| Vector store | `~/locallyai/storage/` | tarball (Qdrant snapshot) |

### A.2 Package

On the firm's Mac, with you watching (via screenshare or in person):

```bash
cd ~
sudo tar -czf locallyai-handover-$(date +%Y%m%d).tar.gz \
  locallyai/data \
  locallyai/storage \
  locallyai/logs \
  locallyai/users.json
```

Encrypt the tarball with the firm's PGP key (the same one in their DPA-listed contact details).

```bash
gpg --encrypt --recipient <firm-pgp-fingerprint> --output locallyai-handover.tar.gz.gpg locallyai-handover-*.tar.gz
shred -u locallyai-handover-*.tar.gz   # remove the unencrypted intermediate
```

Hand over the encrypted file via the firm's preferred secure channel. Confirm receipt + decryption in writing before proceeding to A.3.

### A.3 Confirm written acknowledgement of receipt

Don't move to Step B until the firm's authorised signatory confirms in writing they received and successfully decrypted the handover. File the acknowledgement in `vendor-records/firms/<firm-slug>/decommission/`.

## Step B — Vendor-side cleanup (always do this)

This part runs whether the firm chose return or erasure.

### B.1 Stop the service

```bash
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/app.locallyai.api.plist
launchctl unload ~/Library/LaunchAgents/app.locallyai.api.plist 2>/dev/null
rm ~/Library/LaunchAgents/app.locallyai.api.plist
```

Verify:
```bash
launchctl list | grep locallyai
```

Expected: no rows. If a row remains, the bootout didn't take — `pkill -f uvicorn` then re-try.

### B.2 Disable telemetry

```bash
sed -i.bak 's/^LOCALLYAI_TELEMETRY=.*/LOCALLYAI_TELEMETRY=off/' ~/locallyai/.env
```

(Belt-and-braces — the service is already down, but if it ever came back this prevents accidental telemetry.)

### B.3 Remove the firm from the vendor monitor

Delete from FIRM_STATE (heartbeat state):
```bash
cd /path/to/locallyai/docs/monitor/cloudflare-worker
npx wrangler kv key delete "<firm-id-16-hex>" --binding FIRM_STATE --remote
```

Delete from TELEMETRY_TOKENS (auto-issuance record):
```bash
npx wrangler kv key delete "firm:<firm-id-16-hex>" --binding TELEMETRY_TOKENS --remote
```

Verify:
```bash
npx wrangler kv key list --binding FIRM_STATE --remote | grep "<firm-id-16-hex>"
npx wrangler kv key list --binding TELEMETRY_TOKENS --remote | grep "<firm-id-16-hex>"
```

Both expected: empty (no match).

### B.4 Remove FIRM_TOKENS legacy entry (if present)

If the firm was originally onboarded via the legacy `scripts/onboard_firm.sh` path:

```bash
# Read the current secret
npx wrangler secret list | grep FIRM_TOKENS
# Edit the JSON locally, removing this firm's row
# Then re-put as a Worker secret
echo '<edited JSON>' | npx wrangler secret put FIRM_TOKENS
```

If the firm was auto-mint, skip — no FIRM_TOKENS entry exists for them.

### B.5 Archive the firm's vendor-records

```bash
cd vendor-records/firms
mv <firm-slug> _archived/<firm-slug>-decommissioned-YYYY-MM-DD
git add _archived/<firm-slug>-decommissioned-YYYY-MM-DD <firm-slug>
git commit -m "decommission: <firm-slug> archived YYYY-MM-DD per Clause 10"
git push
```

Don't `git rm`. The vendor's own audit obligations require keeping the file on hand for 6 years (DPA Clause 5.4 / SRA Code).

## Step C — Erasure certificate (only if Step B + firm chose 10.2.b)

### C.1 Issue erasure on the Mac

Per `manage_users.py erase`:

```bash
cd ~/locallyai
.venv/bin/python manage_users.py erase <each-user>   # for every user in users.json
```

This adds tombstone entries to `~/locallyai/erasure.log`. The audit log retains the historical pseudonymised entries (it must, per Clause 5.4) but new queries from those users (impossible — service is down) would be refused.

### C.2 Wipe the document corpus

```bash
cd ~/locallyai
sudo rm -rf data/uploads/* storage/qdrant/*
```

### C.3 Wipe the encrypted volume

The hardest part. The firm's preference per DPA 10.2.b:

```bash
# Unmount any external volumes first
diskutil eraseDisk "Free Space" Untitled disk0   # CAREFUL: this wipes the boot disk
```

In practice, the firm's option is usually:
- Return the Mac to LocallyAI for secure-wipe at our facility (preferred — we can document and certify)
- They keep the Mac and we provide certified erasure firmware tools

Either way, the certificate template lives at `vendor-records/templates/erasure-certificate.md`. Fill it in, sign with vendor PGP, send to firm's authorised signatory.

### C.4 File certificate

```
vendor-records/firms/_archived/<firm-slug>-decommissioned-YYYY-MM-DD/erasure-certificate.pdf
```

Commit + push.

## Things that go wrong

| Symptom | Cause | Fix |
|---|---|---|
| `wrangler kv key delete` returns "Resource not found" | Wrong namespace OR wrong firm_id hex | Re-list to find the correct key |
| Mac won't unload launchd plist | The service is mid-restart | Wait 30s, retry; if still stuck, `pkill -f uvicorn` + `pkill -f supervisor.py` |
| Tarball encryption fails because firm's PGP fingerprint is wrong | Use of expired key, or fingerprint mistyped | Don't proceed with wrong key — get a fresh fingerprint in writing |
| Firm's IT person tries to "speed up" by deleting `~/locallyai` themselves | They wipe the audit log we need for our own 6-year retention | **Stop them**. Re-extract audit/billing logs from any backups. If unrecoverable, document the gap in the decommission file and **escalate** |

## When to escalate

- Firm refuses to elect 10.2.a or 10.2.b → founder + legal counsel
- The Mac is missing or unaccounted-for → founder, immediately (this is a notifiable incident)
- Audit/billing logs are corrupted or unreadable on the Mac → founder, before final wipe
- Firm requests something not covered by Clause 10 (e.g. selective data return) → founder + DPO conversation

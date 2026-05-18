# Runbook: DPO monthly compliance snapshot

**When**: First business day of each month, the DPO needs to file evidence for their internal-audit cycle. Also: ad-hoc when a regulator or insurance auditor asks for a compliance summary.

**Time budget**: 10 minutes per firm.

**Risk if you stop midway**: None — the snapshot is read-only on the deployment. You can stop and resume any time.

**Prerequisites**:
- A LocallyAI admin key for the firm (the same one Manager UI uses)
- Network access to the office Mac (LAN or Tailscale)
- Python 3 + access to this repo (for the verification step)

## Decision tree

1. Is this an automated monthly run? → Step A
2. Did a regulator just ask for evidence? → Step A (same procedure, file under that ticket instead)
3. Did the snapshot endpoint return an error? → Step B (recovery)

## Step A — Generate, download, file

### A.1 Open Manager UI

```
https://<firm-office-host>:8000
```

Sign in with the admin key. You should land on Dashboard.

### A.2 Navigate to Compliance

Click **Compliance** in the sidebar (between Audit Log and System).

Expected: a page titled "Compliance — DPO monthly snapshot…" with cards for Audit chain, Key-material, Erasures, Breach events, plus the RoPA / Sub-processors / Telemetry / Retention sections below.

If you don't see this: the firm is on an older release. Check `release_manifest.json` — must be ≥ the release that shipped this runbook. If older, schedule an update before continuing.

### A.3 Verify at-a-glance is healthy

Look at the four stat cards at the top. Acceptable for a normal month:

| Stat | Expected | If unexpected |
|---|---|---|
| Audit chain | `OK` | If `TAMPERED`, **stop** and use `audit-chain-broken.md` |
| Key-material non-OK | `0 / N` | If >0, click through to see the findings; warn-level can ship; fail-level needs investigation |
| Erasures (lifetime) | unchanged from last month, or +n if there were Art-17 requests | If unexpectedly high, ask the DPO whether they made requests you don't know about |
| Breach events (30d) | `0` for most firms | If non-zero, the breach detector caught something — review the bucketed list before filing |

### A.4 Download the snapshot

Click **Download monthly snapshot**. The browser saves an HTML file named `compliance-snapshot-<deployment_id>-<YYYY-MM-DD>.html`.

Expected: file in your Downloads folder, around 12-25 KB.

### A.5 Verify the snapshot signature

Before filing, prove the snapshot wasn't altered between download and now:

```bash
cd ~/locallyai  # or wherever this repo lives
set -a && source .env && set +a   # load LOCALLYAI_AUDIT_HMAC_KEY
.venv/bin/python scripts/verify_compliance_snapshot.py ~/Downloads/compliance-snapshot-*.html
```

Expected output:
```
VERIFIED. Snapshot from <deployment_id> (<region>)
          generated_at  = 2026-XX-XXTXX:XX:XX...
          firm_id       = ...
          node_id       = ...
          version       = ...
```

If you get `MISMATCH`: the file was modified. Re-download from the dashboard. If a fresh download still mismatches, **escalate**.

If you get `LOCALLYAI_AUDIT_HMAC_KEY is not set`: you didn't `source .env`. Re-run the source line.

### A.6 File the snapshot

1. Print to PDF (Cmd-P, "Save as PDF").
2. File under `vendor-records/firms/<firm-slug>/compliance/snapshot-YYYY-MM.pdf`.
3. Commit + push the vendor-records repo with message `compliance: <firm-slug> monthly snapshot YYYY-MM`.
4. Reply to the firm's DPO with the PDF attached, confirming evidence is filed.

Done.

## Step B — Recovery (snapshot endpoint errored)

### Symptom: 401 Unauthorized

The admin key is wrong or rotated. Check `vendor-records/firms/<firm-slug>/credentials.gpg` for the current key. If recently rotated and not updated in your records, **escalate**.

### Symptom: 500 Internal Server Error

The snapshot endpoint failed. Likely causes:
- `/admin/audit-verify` is failing (which the snapshot calls). See `audit-chain-broken.md`.
- The audit log file is missing or unreadable. Check disk space + file permissions on `~/locallyai/logs/audit.log`.

### Symptom: Times out

The Mac is overloaded or the audit log is huge. Check `/monitor/health/detailed`. If `disk_free_gb < 5`, retention rotation hasn't run — see `docs/sop/maintenance.md` "Log retention rotation".

## Things that go wrong

| Symptom | Cause | Fix |
|---|---|---|
| HMAC `MISMATCH` on a freshly downloaded file | Wrong `LOCALLYAI_AUDIT_HMAC_KEY` in your local `.env` | Source the firm's `.env`, not your dev `.env` |
| Page renders but `version` is `unknown` | `release_manifest.json` missing or unreadable | Re-run `update.sh` to restore the manifest |
| Telemetry `Active allowlist` shows fields the firm didn't agree to | Field-set drift since the firm last consented | Check `docs/sop/data-isolation.md` "Field-set change log"; resend the disclosure template if needed |
| Audit chain `TAMPERED` | Multiple causes — see runbook | `audit-chain-broken.md` |

## When to escalate

- Audit chain `TAMPERED` after one full re-verification cycle → founder, within 1 hour
- HMAC `MISMATCH` on a fresh download from a fresh login → founder, within 1 hour (possible key compromise)
- Snapshot 500-errors and `audit-chain-broken.md` doesn't help → founder, same business day
- DPO disputes a fact in the snapshot (e.g. claims an erasure was filed that doesn't appear) → founder + read the audit log directly

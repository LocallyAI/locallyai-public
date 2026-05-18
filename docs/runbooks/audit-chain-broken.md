# Runbook: Audit chain TAMPERED

**When**: `GET /admin/audit-verify` returns `{"status": "TAMPERED", ...}`. Or the compliance snapshot shows the audit chain status as TAMPERED. Or the breach detector emits an `AUDIT_CHAIN_BROKEN` event.

**Time budget**: 30 minutes triage. If you can't classify the cause within 30 minutes, **escalate**.

**Risk if you stop midway**: The firm's audit chain stays in a broken state. Their HMAC tamper-evidence is meaningless until the chain is recovered or explicitly reset. **Do not** delete or truncate the audit log in the meantime — every entry must be preserved for the 6-year retention obligation.

**Prerequisites**:
- Manager UI admin key OR shell access to the office Mac
- The current `LOCALLYAI_AUDIT_HMAC_KEY` (in the firm's `.env`)
- Read access to historical audit archives (`logs/audit-*.log.gz`)

## Decision tree

The `TAMPERED` response includes a `source` and a `reason` or `broken_at_line`. Use those to branch:

| `source` | `reason` | Likely cause | Procedure |
|---|---|---|---|
| `audit.log` | `broken_at_line: 1` | Salt rotation crossed an archive boundary | Step A |
| `audit.log` | `broken_at_line: N > 1` (mid-file) | Manual edit OR partial write OR clock skew | Step B |
| `audit.log` | `tail truncated: chain head does not match .audit_chain` | `.audit_chain` desync (often a backup-restore artefact) | Step C |
| `audit-YYYY-MM-DD.log.gz` (any archive) | `unreadable archive` | Archive corrupted on disk | Step D |
| `audit-YYYY-MM-DD.log.gz` | `broken_at_line: N` | Archive tampered after rotation (rare) | Step E |

## Step A — Salt rotation boundary

When `manage_users.py rotate-salt` runs, it writes a `salt_era_boundary` entry to audit.log that DOES chain (it carries `_chain_hmac`). The chain across the boundary should still verify. If `broken_at_line: 1` of the live log post-rotation, either:

- The rotation completed but `.audit_chain` wasn't atomically updated (older code path; Round-1 finding 2.2 fixed this).
- The boundary entry was lost (e.g. partial fsync on a power loss right at rotation).

Recovery:

```bash
cd ~/locallyai
# Inspect the most recent audit entries
.venv/bin/python -c "
import json
from pathlib import Path
for line in Path('logs/audit.log').read_text().splitlines()[:3]:
    print(json.dumps(json.loads(line), indent=2))
"
```

Look for `event: salt_era_boundary` in the first 1-3 lines.

- If present and validating: the chain head in `.audit_chain` is stale. **Escalate** — recovering the head correctly requires verifying it matches the boundary entry's `_chain_hmac`.
- If absent: the boundary entry was lost. The chain CAN be re-rooted by appending a synthetic recovery entry (`event: chain_recovery_after_loss`) — **escalate**, do not improvise.

## Step B — Mid-file break (manual edit OR clock skew OR partial write)

### B.1 Read the broken entry + the one before it

```bash
cd ~/locallyai
N=$(curl -k -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/audit-verify | python3 -c "import sys,json; print(json.load(sys.stdin)['broken_at_line'])")
sed -n "$((N-1)),$N p" logs/audit.log | python3 -c "import sys,json; [print(json.dumps(json.loads(l), indent=2)) for l in sys.stdin]"
```

### B.2 Classify

| What you see | Cause | Next |
|---|---|---|
| Both entries look normal; timestamps within seconds of each other | Likely a partial write that corrupted `_chain_hmac` | B.3 — verify entry integrity |
| Entry N has a timestamp BEFORE entry N-1 | Clock skew on the Mac (NTP problem) | B.4 |
| Entry N has fields you don't recognise OR missing required fields | Manual edit (suspicious) | **Escalate immediately** — possible insider edit |
| Entry N is malformed (truncated JSON) | Crashed mid-write OR disk full at the time | B.5 |

### B.3 Partial-write recovery

The corrupted entry's `_chain_hmac` field is the only thing wrong. The entry IS recoverable: re-derive `_chain_hmac` from the rest of the entry + the previous entry's `_chain_hmac`. **DO NOT** edit the live log directly. Append a `chain_recovery` audit entry, and document the procedure used. **Escalate** for the actual edit — chain-recovery actions are founder-level for the first 5 occurrences.

### B.4 Clock skew

NTP fell out of sync. Check:
```bash
sntp -sS time.apple.com
```

If the offset > 5 seconds, force resync. Then the chain itself may still be intact (timestamps don't participate in the HMAC payload directly — but if a writer is interleaved with a sentinel write, the order matters). **Escalate** to confirm what action is safe.

### B.5 Truncated entry

The disk likely filled at the moment of the write. Check `df -h ~/locallyai`. If currently full, fix disk first (`api-down.md` Step C.2). Then the truncated last line: it has no valid `_chain_hmac`, so `_verify_lines` should have skipped it (chain head unchanged). If it didn't skip — the entry is partially-valid JSON — the file needs hand-repair. **Escalate**.

## Step C — `.audit_chain` desync

The live log validates internally, but the final head doesn't match the `.audit_chain` file. Causes:

| Cause | Fix |
|---|---|
| Backup-restore where `.audit_chain` was restored from a snapshot taken AT a different time than `audit.log` | Re-derive the head from the live log: `.venv/bin/python -c "from api import _verify_lines; ..."` — **escalate**, the procedure is delicate |
| `.audit_chain` was deleted (sees as `"0"*64`) | The chain detects this as TAMPERED because audit.log starts with real chained entries. **Escalate** — do not zero the chain to "fix" |
| Crash between `audit.log` write and `.audit_chain` write | Round-2 A4 fixed this for new code (atomic rename + long-lived fd). Pre-fix deployments may exhibit it once after a crash. **Escalate** for the first occurrence |

## Step D — Archive unreadable

```bash
gzip -t ~/locallyai/logs/audit-YYYY-MM-DD.log.gz
```

If this fails: the gzip is corrupt. Check if the firm has a backup. If not, the audit history before that archive is unrecoverable AS A CHAIN, but the individual entries may still be readable via `zcat | head -1`. Document the gap in the firm's record + **escalate** — this is a 6-year-retention compliance issue.

## Step E — Archive tampered

This is the most serious case. A rotated archive should never change after rotation. If a verifier finds `broken_at_line: N` inside `audit-YYYY-MM-DD.log.gz`, someone with file-level access edited the archive.

**STOP**. Do not touch the archive. Preserve the file as-is, with its timestamps. **Escalate immediately** — this is potentially a notifiable incident under GDPR Art. 33 / PDPL Art. 31 if it indicates unauthorised access.

## Things that go wrong

| Symptom | Cause | Fix |
|---|---|---|
| `audit-verify` returns 200 OK but the chain "feels" wrong | The endpoint is right; trust it. If you're suspicious, run with a fresh checkout of the verifier code | — |
| Fix attempted from Step B succeeds, but next snapshot still TAMPERED | The fix didn't propagate to `.audit_chain` | `.audit_chain` must contain the head after the recovery entry, not before |
| `audit-verify` is slow (>30s) | Audit log is huge | This is Round-2 B4 (already fixed in the verifier itself); if it's slow, the firm's audit.log is multi-GB — schedule retention rotation per `docs/sop/maintenance.md` |

## When to escalate

**Always**, for any of these:
- Any case in Step B, C, D, E above marked **escalate** (almost all of them)
- Suspected insider edit (entry contents look wrong, not just `_chain_hmac`)
- Cannot determine the cause within 30 minutes
- Multiple distinct firms break their chain in the same week (could indicate a code regression we shipped)

The audit chain is the cornerstone of LocallyAI's tamper-evidence claim to regulators. Any recovery action that isn't a clean re-verification gets founder approval first.

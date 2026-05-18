# Syncthing setup for the 2-node Mac fleet

This is the manual half of the LocallyAI HA bring-up. The
`scripts/syncthing_setup.sh` script does the install + initial config on
each Mac; this doc covers the pairing step and the operational checks
afterwards.

## Why Syncthing

LocallyAI's 2-node Mac edition uses Syncthing to replicate three files
between the two Macs:

- `users.json` — API keys
- `erasure.log` — GDPR Article-17 tombstones
- `fleet.json` — node membership registry

Documents (`data/`) are also synced when the firm uploads from the
worker app. Per-node files (`audit.log`, `.audit_chain`, `billing.log`,
`logs/security.log`) **must NOT** be synced — chains are per-node by
design and Syncthing would create conflicts on every write.

## One-time bring-up

1. **On each Mac**, in the LocallyAI repo directory:

   ```bash
   bash scripts/syncthing_setup.sh
   ```

   The script installs Syncthing via Homebrew, generates a Syncthing
   identity, registers a launchd job (`com.locallyai.syncthing`), and
   prints the local **Device ID** plus the web GUI URL
   (`http://127.0.0.1:8384`).

2. **Open the Syncthing web GUI on each Mac**. They run on
   `127.0.0.1:8384` — accessible only from the local machine.

3. **Pair the two devices.** On Mac-A's GUI:
   - Click **Add Remote Device**.
   - Paste Mac-B's Device ID (printed by the script on Mac-B).
   - Tick the `locallyai-shared` folder.
   - Click **Save**.

   Repeat on Mac-B with Mac-A's Device ID.

4. **Accept the folder share** when each side prompts you. Set both
   sides to point at the same path: `<repo>/shared` (or whatever
   `LOCALLYAI_SHARED_DIR` you set).

5. **Wait until both folders report "Up to Date"** in each Syncthing
   GUI. On a LAN this is under 30 seconds the first time.

6. **Set the env var on both nodes**:

   ```bash
   echo "LOCALLYAI_SHARED_DIR=$(pwd)/shared" >> .env
   ```

   Then restart the LocallyAI service:

   ```bash
   launchctl kickstart -k gui/$(id -u)/com.locallyai.server
   ```

7. **Verify the fleet** — from either Mac:

   ```bash
   ADMIN="$(grep LOCALLYAI_ADMIN_KEY .env | cut -d= -f2)"
   curl -sk -H "Authorization: Bearer $ADMIN" \
        https://localhost:8000/admin/fleet/audit-verify
   ```

   You should see both nodes listed with `status: "ok"`.

## What "synced" means in practice

| File | Sync interval | Effect of lag |
|---|---|---|
| `users.json` | ~10 seconds | A key rotated on Mac-A may not authenticate on Mac-B for up to 10s. |
| `erasure.log` | ~10 seconds | An erasure on Mac-A may allow up to 10s of further audit writes on Mac-B before the tombstone replicates (LocallyAI's `is_erased` cache catches it within 1s of the file landing). |
| `fleet.json` | ~10 seconds | A failing node disappears from the fleet endpoint within 90s (sentinel TTL) regardless of sync. |
| `data/<files>` | Variable; depends on file size and LAN speed. | A document uploaded to Mac-A is not searchable on Mac-B until Mac-B re-indexes (typically 10–30s for a small PDF). |

## Conflict handling

Syncthing's last-writer-wins. When both Macs write the same file in the
same window (rare but possible during a network partition), the loser
gets renamed with a `.sync-conflict-...` suffix.

The LocallyAI sentinel scans the shared dir on every tick. Any conflict
file is:

1. Moved into `SHARED_DIR/conflicts/` so the live tree stays clean.
2. Recorded as a `sync_conflict` event in `logs/security.log`.
3. Surfaced as a critical alert via `/admin/monitor`.

**Never delete conflict files without reviewing them.** They may contain
legitimate operator changes that lost the race. Resolve via the fleet
dashboard.

## Operational health checks

- **Syncthing daemon up?**

  ```bash
  launchctl list | grep com.locallyai.syncthing
  curl -fs http://127.0.0.1:8384/rest/system/ping -H "X-API-Key: <key>"
  ```

- **Folder up to date?**

  Visit the web GUI; the `locallyai-shared` folder should show **"Up to
  Date"** in green. Anything else (Scanning, Syncing, Out of Sync) means
  Mac-A and Mac-B disagree.

- **Conflicts pending?**

  ```bash
  ls $LOCALLYAI_SHARED_DIR/conflicts/ 2>/dev/null
  ```

  Empty = clean. Any contents need operator review.

## Removing the sync layer (rollback)

If you decide to revert to single-node:

1. `unset LOCALLYAI_SHARED_DIR` in `.env` on both nodes.
2. Move `users.json` back into the repo root (it's already there if
   `SHARED_DIR == BASE_DIR` was the default).
3. `launchctl bootout gui/$(id -u)/com.locallyai.syncthing` (or just
   leave Syncthing running idle).
4. Restart the LocallyAI service on each node.

The audit chain is unaffected — chains have always been per-node.

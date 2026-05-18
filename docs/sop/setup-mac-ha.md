# Setup — Mac 2-node HA

End state: two Macs (Mac-A, Mac-B) on the same office LAN. Either Mac
can fail; the firm keeps working. Worker app health-checks both nodes
every 5s, retries failed requests on the survivor.

Time required: 30–60 min, assuming both Macs already have the
single-node install completed.

**Prerequisites:** [setup-mac-single.md](setup-mac-single.md) completed
on **both** Macs separately, each box passing `pass=14 warn≤1 fail=0`.

---

## 0. Pre-flight (5 min)

### 0.0 macOS version pin — BOTH Macs

**Two-Mac HA gives hardware redundancy, NOT software redundancy.**
A single macOS auto-update applied silently overnight can reboot
both Macs into an untested OS version at the same time and take the
fleet down. Before doing anything else, lock both Macs to their
current macOS version.

On **each** Mac (Mac-A and Mac-B):

1. **System Settings → General → Software Update → ⓘ next to *Automatic Updates***.
2. Set:
   - Download new updates when available: **OFF**
   - Install macOS updates: **OFF**
   - Install application updates from the App Store: **OFF**
   - Install Security Responses and system files: ON (CVE patches only)
3. **Both Macs must run the same macOS major version + build.** Run
   `sw_vers` on each; if they differ, upgrade/downgrade the lagging
   one to match BEFORE pairing them. HA is a same-version assumption.
4. Record the version + build in vendor-records under both Macs'
   `firm-profile.md` entries.

Vendor approves new macOS versions for the fleet after testing in
their own environment — see [maintenance.md §macos-version-policy](maintenance.md#macos-version-policy).

### 0.1 Naming

Pick names you will use throughout. Conventionally:

- **Mac-A** — the first Mac, becomes the bootstrap node.
- **Mac-B** — the second Mac, joins Mac-A's cluster.

### 0.2 Find each Mac's LAN IP

On each Mac in turn:

```bash
ipconfig getifaddr en0
```

(Try `en1` if `en0` is empty — that's Wi-Fi vs Ethernet.)

Write down both IPs. We'll use these placeholders in this doc:

- **Mac-A IP**: `10.0.0.11` (yours will differ)
- **Mac-B IP**: `10.0.0.12` (yours will differ)

Whenever you see those IPs below, substitute your actual values.

### 0.3 Both Macs reachable from each other

On Mac-A:

```bash
ping -c 3 10.0.0.12
```

On Mac-B:

```bash
ping -c 3 10.0.0.11
```

Both should get replies. If either fails, fix the network first
(switch / VLAN / firewall). HA can't work without bidirectional LAN
connectivity.

### 0.4 Both Macs run the same model

Compare on each Mac:

```bash
grep '^OLLAMA_MODEL=\|^MLX_MODEL=' .env
```

If they differ, edit `.env` on the slower Mac to match the faster one,
then `ollama pull <model>` to pre-download. **HA refuses mixed
backends** — same OS, same model, same backend.

### 0.5 Both Macs' clocks synchronised

```bash
date -u
```

Run on each Mac within 5 seconds of each other. The output should
agree to within a second. If not: System Settings → General → Date &
Time, force "Set automatically" on, wait 30s, retry.

---

## 1. Set up the shared store (Syncthing) — 10 min per Mac

### 1.1 On Mac-A: run the Syncthing setup script

```bash
cd ~/locallyai
bash scripts/syncthing_setup.sh
```

The script:

1. Installs Syncthing via Homebrew (1–2 min).
2. Creates `~/locallyai/shared/` with chmod 700.
3. Generates Mac-A's Syncthing identity (one-time).
4. Registers `com.locallyai.syncthing` as a launchd job.
5. Configures the `locallyai-shared` folder pointing at
   `~/locallyai/shared`.
6. Prints **Mac-A's Device ID** — a long string like
   `7CFNRIH-OBKC4FE-...-2BWYMUQ`.

**Copy Mac-A's Device ID** into a temporary text file. You'll paste it
on Mac-B in step 1.4.

### 1.2 On Mac-B: same script

```bash
cd ~/locallyai
bash scripts/syncthing_setup.sh
```

**Copy Mac-B's Device ID.**

### 1.3 On Mac-A: open the Syncthing GUI

In Mac-A's web browser, visit `http://127.0.0.1:8384`.

A Syncthing dashboard appears. (Don't share this URL outside the Mac
— Syncthing's GUI is bound to localhost only.)

If the browser warns about an HTTP-only page, accept it; the GUI is
local-only and not in scope for the LocallyAI TLS rules.

### 1.4 On Mac-A: add Mac-B as a remote device

1. Click **Add Remote Device** (bottom-right).
2. **Device ID**: paste **Mac-B's Device ID** (the one you copied in 1.2).
3. **Device Name**: `mac-b` (any label).
4. Click the **Sharing** tab in the dialog.
5. Tick the box next to `locallyai-shared`.
6. Click **Save**.

### 1.5 On Mac-B: open its Syncthing GUI

In Mac-B's web browser, `http://127.0.0.1:8384`.

A blue notification at the top reads:

> Device "mac-a" (Mac-A's name) wants to connect.

Click **Add Device** in the notification. In the dialog, click the
**Sharing** tab → tick `locallyai-shared` → **Save**.

### 1.6 On Mac-B: accept Mac-A's folder share

A second blue notification reads:

> "mac-a" wants to share folder "locallyai-shared".

Click **Add**. In the dialog:

- **Folder Path**: change to `/Users/<your-username>/locallyai/shared`
  (the same physical path you set on Mac-A).
- Click **Save**.

### 1.7 Wait for "Up to Date"

On both Mac-A's and Mac-B's GUI, the `locallyai-shared` folder card
should turn **green** with the label **"Up to Date"** within 30
seconds on a LAN. While syncing it's blue ("Syncing").

If it stays orange ("Out of Sync") for more than 2 minutes:
[incidents-software.md § "Sync conflict"](incidents-software.md#sync-conflict)
or [incidents-physical.md § "Network partition"](incidents-physical.md#network-partition).

### 1.8 Verify sync

On Mac-A:

```bash
echo "test from A at $(date)" > ~/locallyai/shared/_sync_test.txt
```

Within 10 seconds, on Mac-B:

```bash
cat ~/locallyai/shared/_sync_test.txt
```

Should print "test from A at …". If not: not synced. Wait 30 more
seconds; if still no, check the GUI for an error banner.

Clean up the test file:

```bash
# On Mac-A:
rm ~/locallyai/shared/_sync_test.txt
```

(It will disappear from Mac-B too.)

---

## 2. Move shared state into the synced folder — 3 min, do once on Mac-A only

The single-node install put `users.json` and `fleet.json` in the repo
root. Move them into `shared/` so both nodes see one copy.

**ONLY ON MAC-A:**

```bash
cd ~/locallyai
mv users.json shared/users.json
# fleet.json hasn't been written yet (HA mode not enabled), so nothing to move.
```

Within 30 seconds, Mac-B's `shared/users.json` will appear with the
identical content. **Do not move it on Mac-B too** — that would create
a Syncthing conflict.

---

## 3. Stand up the 2-node Qdrant cluster — 10 min

### 3.1 Pick a shared API key

Generate a random hex string:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output. It will be the `QDRANT_API_KEY` on **both** Macs.
Store it in your password vault under `LocallyAI / Qdrant API key`.

### 3.2 Verify Docker Desktop is running

On both Macs, the Docker icon should be in the menu bar (top right).
If not, open the **Docker** app from Applications and wait for it to
say "Docker Desktop is running."

If Docker isn't installed:

```bash
brew install --cask docker
open -a Docker
```

…and wait until the menu-bar icon settles to "running."

### 3.3 On Mac-A: bootstrap the cluster

Substitute Mac-A's IP for `10.0.0.11` and your shared key for
`<shared-secret>`:

```bash
QDRANT_NODE_BIND_IP=10.0.0.11 \
QDRANT_API_KEY=<shared-secret> \
bash scripts/qdrant_cluster_setup.sh
```

Wait 30 s. The script prints "Qdrant ready" then a JSON block with one
peer. Copy the printed `Qdrant cluster member started on 10.0.0.11`
line — confirms the bootstrap.

### 3.4 On Mac-B: join the cluster

Substitute Mac-B's IP for `10.0.0.12`:

```bash
QDRANT_NODE_BIND_IP=10.0.0.12 \
QDRANT_BOOTSTRAP_PEER=http://10.0.0.11:6335 \
QDRANT_API_KEY=<shared-secret> \
bash scripts/qdrant_cluster_setup.sh
```

Wait 30 s. The cluster JSON should now show **two** peers.

### 3.5 Verify cluster health

On either Mac:

```bash
curl -sk -H "api-key: <shared-secret>" http://10.0.0.11:6333/cluster | python3 -m json.tool
```

The `peers` object should have **two** entries (Mac-A and Mac-B), each
with a `uri` field. If only one: 3.4 didn't take. Read
[qdrant-ha.md § "Re-adding a wiped node"](../qdrant-ha.md#re-adding-a-wiped-node).

---

## 4. Wire HA into `.env` on both Macs — 5 min

Edit `~/locallyai/.env` on **both** Macs. Append (don't replace
anything that's there):

```
# === HA fleet ===
LOCALLYAI_SHARED_DIR=/Users/<your-username>/locallyai/shared
LOCALLYAI_NODE_ID=mac-a                    # use 'mac-b' on the other Mac
QDRANT_URLS=http://10.0.0.11:6333,http://10.0.0.12:6333
QDRANT_API_KEY=<shared-secret>
LOCALLYAI_HA=1
```

**On Mac-B, change `LOCALLYAI_NODE_ID=mac-b`.** Other lines are identical.

Save with the editor of your choice. If you used `nano`: `Ctrl+O`,
`Enter`, `Ctrl+X`.

### 4.1 Confirm permissions stayed at 0600

```bash
ls -la .env
# Expected: -rw-------
```

If it says `-rw-r--r--`: the editor reset perms. Fix:

```bash
chmod 600 .env
```

---

## 5. Restart and verify — 5 min

### 5.1 Restart the LocallyAI service on **both** Macs

On each Mac:

```bash
launchctl kickstart -k gui/$(id -u)/com.locallyai.server
```

Wait for `/healthz`:

```bash
until curl -skf -o /dev/null --max-time 2 https://localhost:8000/healthz; do sleep 4; done
echo READY
```

### 5.2 Check the fleet from Mac-A

```bash
ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' .env | cut -d= -f2)
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/admin/fleet/nodes | python3 -m json.tool
```

Expected: a `nodes` list with **2 entries**, each `alive: true`,
node_ids `mac-a` and `mac-b`.

If only `mac-a` is there: Mac-B's startup hasn't registered yet. Wait
60s and retry. If still missing:
[incidents-software.md § "Fleet desync"](incidents-software.md#fleet-desync).

### 5.3 Verify the fleet-wide audit chain

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/admin/fleet/audit-verify | python3 -m json.tool
```

Expected: `fleet_status: "ok"`, both nodes listed with their per-node
chain status.

### 5.4 Verify Qdrant cluster from the API's perspective

```bash
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/admin/fleet/qdrant-health | python3 -m json.tool
```

Expected: `mode: "cluster"`, `peer_count: 2`.

---

## 6. Configure the worker-ui smart client — 3 min

Tell the worker app about both nodes so it knows where to fail over.

### 6.1 Edit the worker-ui env

```bash
cd apps/worker-ui
echo 'VITE_API_BASE_URLS=https://10.0.0.11:8000,https://10.0.0.12:8000' >> .env.local
```

Substitute your real Mac IPs.

### 6.2 Rebuild

```bash
npm run build
```

(Or `bun run build` if you use bun. Takes about 60 s.)

### 6.3 Distribute

Copy `apps/worker-ui/dist/` to wherever it's served from (the install
script puts it where the launcher script expects). When users open
worker-ui, they'll now see the **fleet status indicator** showing both
nodes; it turns yellow if one drops.

---

## 7. Stand up the fleet dashboard — 3 min

The fleet-ui is an admin dashboard with per-node health, audit chain,
sync conflicts, gate utilisation, alerts.

### 7.1 Build it once

```bash
cd ~/locallyai/apps/fleet-ui
npm install
npm run build
```

### 7.2 Run it

```bash
npm run preview
```

Output:

```
  ➜  Local:   http://127.0.0.1:5175/
```

Open that URL in a browser on the Mac. Sign in with your `ADMIN_KEY`.
You should see five panels with live data.

For long-term running, `nohup npm run preview &` — or set up a separate
launchd job (template at the end of this file).

---

## 8. Smoke-test failover — 5 min

This is the test that proves HA actually works. Do it once.

### 8.1 From a third device on the LAN, send a chat

(If you don't have a third device, use the same Mac and time the
result.)

```bash
USER_KEY=<a real user key>
time curl -sk -X POST -H "Authorization: Bearer $USER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"failover test"}],"max_tokens":20}' \
  https://10.0.0.11:8000/v1/chat/completions
```

Note the `node_id` field in the response — should be `mac-a` (since you
hit Mac-A directly).

### 8.2 Stop Mac-A's API service

On Mac-A:

```bash
launchctl bootout gui/$(id -u)/com.locallyai.server
```

### 8.3 Re-send the same chat (worker-ui style)

The worker-ui retries on the next healthy node. To simulate, hit
Mac-B directly:

```bash
curl -sk -X POST -H "Authorization: Bearer $USER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"failover test 2"}],"max_tokens":20}' \
  https://10.0.0.12:8000/v1/chat/completions
```

`node_id` should now be `mac-b`. The chat works.

### 8.4 Bring Mac-A back

On Mac-A:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.locallyai.server.plist
```

Wait for `/healthz`. Within 60s, the fleet dashboard should show both
nodes alive again.

### 8.5 If the failover test FAILED

Read these in order:

1. [incidents-software.md § "Fleet desync"](incidents-software.md#fleet-desync)
2. [incidents-physical.md § "Network partition"](incidents-physical.md#network-partition-between-macs)
3. [qdrant-ha.md](../qdrant-ha.md)

Do not declare HA "live" to the firm until §8 passes cleanly.

---

## 9. Tell the firm — 1 min

What changes for users:

- The worker-ui app now shows **two green dots** (one per Mac) at the
  top. If one turns red, IT is on it; users can keep working.
- A chat occasionally takes 2–5 seconds longer than usual — that's a
  retry on the surviving node. **It is not an error.**
- Mid-stream regeneration ("[regenerating on another node…]") happens
  rarely and means a node died while the model was typing. Just wait.

What changes for IT:

- Both Macs must stay powered on and on the LAN. Either being
  unplugged > 90s shows up in the fleet dashboard.
- Updates / restarts: do them ONE Mac at a time, never both at once
  (otherwise the firm sees a real outage). See
  [maintenance.md § "Rolling updates"](maintenance.md#rolling-updates).

---

## 10. Update the credential register

Make sure these new entries are in your password vault:

- [ ] `LocallyAI / Qdrant API key`
- [ ] `LocallyAI / Mac-A Syncthing Device ID`
- [ ] `LocallyAI / Mac-B Syncthing Device ID`

The admin key, FileVault recovery, and per-user keys you set up in the
single-node phase still apply.

---

## 11. You are done

HA setup is complete. From here on:

- All daily tasks: [daily.md](daily.md).
- Anything weird (sync conflict, fleet desync, audit-verify reports
  TAMPERED): the relevant chapter under [SOP.md](../SOP.md).
- When a Mac dies: [incidents-physical.md § "One Mac dies"](incidents-physical.md#one-mac-dies).
- When you need to update software: [maintenance.md § "Rolling updates"](maintenance.md#rolling-updates).

---

## Appendix: long-term fleet-ui as a launchd job

If you want fleet-ui running in the background without manual `npm run
preview`, on the Mac you want to host it on:

```bash
cat > ~/Library/LaunchAgents/com.locallyai.fleet-ui.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.locallyai.fleet-ui</string>
  <key>WorkingDirectory</key><string>$HOME/locallyai/apps/fleet-ui</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/npm</string>
    <string>run</string>
    <string>preview</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/locallyai/logs/fleet-ui.log</string>
  <key>StandardErrorPath</key><string>$HOME/locallyai/logs/fleet-ui.log</string>
</dict>
</plist>
EOF
launchctl load ~/Library/LaunchAgents/com.locallyai.fleet-ui.plist
```

Then `http://127.0.0.1:5175/` is always up after login.

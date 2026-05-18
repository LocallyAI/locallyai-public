# Incident playbooks — physical / environment

What to do when the box dies, the building loses power, the network
breaks, or someone steals the hardware. Each section: **trigger →
immediate action → recovery → after-action**.

---

## Power outage — short (< 30 min)

**Trigger:** UPS alarm, building lights off, you can hear UPS battery
beeping.

### Immediate action

Within the UPS runtime budget:

1. Tell users in firm chat: "AI assistant is offline due to power
   issue. Check back in 30 min."
2. **Gracefully shut down** the LocallyAI service to flush
   in-flight writes:
   ```bash
   launchctl bootout gui/$(id -u)/com.locallyai.server   # Mac
   Stop-Service LocallyAIServer                           # Windows
   ```
3. Shut down the Macs/PCs cleanly: Apple menu → Shut Down (Mac), or
   `shutdown /s /t 0` (Win). Don't leave them on UPS to die at random
   battery levels.

### Recovery (when power returns)

1. Power on the boxes.
2. Wait for them to fully boot.
3. The LocallyAI service auto-starts via launchd / Windows Service.
4. Run the [Quick health sweep](daily.md#quick-health-sweep-do-this-every-morning).
5. Check `audit-verify` — should be `ok`. (Sudden power loss can in
   theory corrupt the last byte; if you see TAMPERED, see
   [incidents-software.md § "Audit chain TAMPERED"](incidents-software.md#audit-chain-tampered).)
6. HA: confirm both peers are alive. The fleet endpoint will show one
   as offline if it boots slower than the other; wait 90s.

### After-action

- File the incident in your ops register.
- If the chain came back TAMPERED, treat as Art. 33-eligible until
  proven otherwise. Power-loss-induced corruption is the most likely
  cause; document the root cause so the regulator can rule it
  benign.

---

## Power outage — extended (> 30 min, building down)

**Trigger:** as above but power isn't coming back today.

### Immediate action

1. Same shutdown as the short case.
2. Tell users: "Service offline until power returns. Estimated <X>
   hours."
3. If the firm has critical workflows that need AI access during the
   outage and you have laptop-form-factor hardware: bring up a
   secondary single-node deployment on the office's mobile-data
   hotspot. The single-node install is fast (~15 min if model is
   already pulled). User keys still work.

### Recovery

Same as the short case.

### After-action

- If you brought up a secondary box, **decommission it cleanly** when
  the primary is back: `bash uninstall.sh` (Mac) or the Windows
  equivalent, plus a secure wipe per [decommission.md](decommission.md).
- Audit logs from the secondary need to be retained per your
  retention policy. They're a separate per-node chain; an auditor can
  verify them independently.

---

## One Mac dies (HA)

**Trigger:** fleet dashboard shows Mac-A `alive: false` for >2 minutes
AND `ping` from Mac-B to Mac-A's IP fails.

### Immediate action

1. Tell users: "Mac-A is offline; service continues from Mac-B. Some
   in-flight requests may have failed once and retried."
2. **Verify the surviving node is healthy**:
   ```bash
   # On Mac-B:
   curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/audit-verify
   curl -sk -H "Authorization: Bearer $ADMIN_KEY" https://localhost:8000/admin/fleet/qdrant-health
   ```
3. **Decide on Qdrant write consistency** — see
   [../qdrant-ha.md § "Operating with one node down"](../qdrant-ha.md#operating-with-one-node-down).
   If document uploads need to keep working, temporarily relax
   `write_consistency_factor` to 1. **Document this as a partial-
   availability event in your ops register.**

### Recovery (Mac-A returns)

If you didn't change Qdrant consistency: nothing else to do. The peer
auto-reconverges within seconds.

If you did temporarily set `write_consistency_factor=1`:

```bash
curl -X PATCH "http://10.0.0.12:6333/collections/locallyai_legal_poc" \
     -H "api-key: $QDRANT_API_KEY" \
     -H 'Content-Type: application/json' \
     -d '{"params":{"write_consistency_factor":2}}'
```

If Mac-A came back from a fresh disk (e.g. you reinstalled): see
[../qdrant-ha.md § "Re-adding a wiped node"](../qdrant-ha.md#re-adding-a-wiped-node).

### After-action

- File the outage duration, the cause (if known), and the recovery
  steps in the ops register.
- Re-run the failover test (per
  [setup-mac-ha.md § 8](setup-mac-ha.md#8-smoke-test-failover-5-min))
  to confirm HA is healthy again.
- Did the firm notice? If yes (worker-app errors, regenerating-stream
  marker), document; that's the user-facing failover SLA being
  exercised. If no, HA is doing its job.

---

## Both Macs die (HA) / single Mac dies (single-node)

**Trigger:** the firm is fully down.

### Immediate action

1. Tell users: "AI assistant is fully offline. Estimated <X> minutes."
2. Identify the cause:
   - Power outage? → [Power outage](#power-outage--short--30-min)
   - Hardware failure? → [Hardware replacement](#hardware-replacement)
   - Network failure? → [Network partition](#network-partition-between-macs)
   - Network *between users and Macs*? Fix the LAN/Wi-Fi.

### Recovery

Depends on cause. Three common scenarios:

**A. Both still boot, just service crashed.** Restart the service on
each. Run the audit. Likely cause was a coordinated push of bad
config or a network-wide outage that took out shared dependencies
(Docker Hub for Qdrant pulls, etc.). Document.

**B. One can boot, the other can't.** Bring up the survivor as a
single-node deployment temporarily:
```bash
# On the surviving Mac, edit .env:
# LOCALLYAI_HA=1   →   LOCALLYAI_HA=0
# leave LOCALLYAI_SHARED_DIR set so users.json keeps working
launchctl kickstart -k gui/$(id -u)/com.locallyai.server
```
Then begin [Hardware replacement](#hardware-replacement) on the dead
Mac.

**C. Neither boots.** Disaster. See [recovery.md § "Restore from
scratch"](recovery.md#restore-from-scratch).

### After-action

A "both nodes down" event is rare and worth a deeper post-mortem.
Schedule one with the DPO + IT-ops within a week.

---

## Network partition between Macs

**Trigger:** both Macs are up; both `/healthz` ok; but the fleet
dashboard shows each one seeing only itself, AND `ping` between them
fails.

### Diagnose

```bash
# On Mac-A:
ping -c 3 <Mac-B IP>            # fails
arp -a | grep <Mac-B IP>        # is Mac-B in ARP at all?
```

Common causes:

1. **One Mac's network cable unplugged.** Trivial; plug it.
2. **Wi-Fi flaky.** Move to Ethernet; Wi-Fi is not appropriate for HA.
3. **VLAN / switch ACL change.** Talk to the network admin.
4. **One Mac changed IP** (DHCP lease churn). Check
   `ipconfig getifaddr en0` on each — if Mac-A's IP changed, the
   other Mac's `.env` still has the old IP. Update.

### Immediate action

1. Tell users: "AI assistant may have intermittent failures while we
   investigate."
2. The smart client should already be handling this — the firm's UX is
   "occasional retry stutter" rather than full outage.
3. **Do not write any users / erase any users** during a partition.
   You'll create a Syncthing conflict. Wait until partition heals.

### Recovery

1. Fix the network.
2. Within 60s, fleet endpoints re-converge.
3. Qdrant Raft re-converges within seconds — the partition triggered
   read-only on writes (because `write_consistency_factor=2`); after
   reconvergence writes resume. Document any failed write attempts
   from the partition window.
4. Inspect `$LOCALLYAI_SHARED_DIR/conflicts/` — Syncthing may have
   produced conflict files if any user-management op happened during
   the window. Reconcile per
   [incidents-software.md § "Sync conflict"](incidents-software.md#sync-conflict).

### After-action

- File the duration of the partition.
- If the partition coincided with operator writes, audit those writes
  for divergence between nodes.

---

## NAS failure (future, when you have one)

LocallyAI's 2-node Mac edition uses Syncthing today, not a NAS. When
you upgrade to a NAS-backed deployment, this section will be updated
with the appropriate procedures (TrueNAS controller failover,
NFS lock manager hangs, etc.). For now, see [Network partition](#network-partition-between-macs)
above — Syncthing-managed shared dirs behave similarly to a NAS at the
"shared store unreachable" failure mode.

---

## Upstream-internet failure during install

**Trigger:** during `install.sh` or `install.ps1`, internet drops.
Common at: `brew install`, `winget install`, `ollama pull`, `git
clone`.

### Action

The installers are mostly idempotent. Wait for internet to return,
re-run the installer:

```bash
bash install.sh
# or
PowerShell -ExecutionPolicy Bypass -File .\install.ps1
```

It detects already-installed components and skips them.

If the model pull was interrupted partway:

```bash
ollama pull <model>      # resumes from where it left off
```

### Verification

[setup-mac-single.md § 4](setup-mac-single.md#4-verify-the-install)
once it completes.

---

## Time-sync drift

**Trigger:** audit timestamps appear out of order, or HMAC chain
verifies but `last_seen` semantics misbehave (a node briefly shows
offline because its clock jumped).

### Diagnose

```bash
# On each box:
date -u
ntpdate -q time.apple.com   # Mac
w32tm /query /status        # Win
```

If the offset is >1s: time sync isn't working.

### Fix

Mac:

```bash
sudo sntp -sS time.apple.com
sudo systemsetup -setusingnetworktime On
```

Windows:

```powershell
w32tm /resync /force
```

If that doesn't stick (NTP blocked at the firewall): change to your
firm's domain-controller NTP source via
`System Settings → General → Date & Time → Source` (Mac) or
`w32tm /config /manualpeerlist:<dc>` (Win).

### Verify

```bash
date -u
# Should match online "what is the UTC time" within ~1s.
```

---

## Theft of a Mac

**Trigger:** a Mac is missing. Could be opportunistic theft, could be
"left at the café," could be an inside actor.

### Immediate action — the next 60 minutes matter

1. **Treat as a personal-data breach.** Start the Art. 33 72-hour
   clock now.
2. **Tell the DPO immediately.**
3. **Determine what was on the box** — see "Scope" below.
4. **Apple ID Find My (Mac):**
   - Go to https://www.icloud.com/find on another machine.
   - If the Mac is online: **Erase Mac** remotely. This is the
     fastest path to destroying the salt + key material.
   - If offline: mark for erase; it'll erase the next time it
     connects.
5. **MDM (if your firm uses Jamf, Kandji, Mosyle, Microsoft Intune
   for Mac, etc.):** push a remote wipe. Faster than Find My if you
   have it.
6. **Windows:** if BitLocker is on AND you don't have the recovery
   key written on the box, the disk is unreadable. Still:
   - Microsoft account → "Find my device" → mark + lock + erase.
   - Or your MDM (Intune / SCCM) — push wipe.
7. **Rotate everything that was on the box:**
   ```bash
   # On the surviving node (HA) or a fresh box (single-node):
   python manage_users.py rotate-audit-salt --keep-eras 0
   # Generate a new HMAC chain key and a new admin key.
   # All user keys must be rotated, since the file was on the box.
   for u in $(python manage_users.py list | tail -n +3 | awk '{print $1}'); do
     python manage_users.py rotate "$u"
     # And TELL EACH USER their new key.
   done
   ```

### Scope (what was on the box?)

- `users.json` — every user's API key. **All compromised.** Rotate
  every key.
- `.env` — admin key, audit salt, audit HMAC key. **All
  compromised.** Rotate.
- `tls/key.pem` — the deployment's TLS private key. **Compromised.**
  Generate new cert; redistribute trust.
- `logs/audit.log` and gz archives — pseudonymised, but with the
  `.env` salt the attacker can re-identify every entry. **Treat as
  if real names were exposed.**
- `logs/billing.log` — real names + activity. **Real names compromised.**
- `data/` — the firm's source documents. **Confidential client
  documents compromised** if the disk wasn't encrypted, OR if the
  attacker has the FileVault/BitLocker recovery key.

### What FileVault/BitLocker actually defends against

- Theft of the box without the recovery key: drive contents are
  computationally unrecoverable.
- Theft of the box with the recovery key: same as no encryption.

If your firm's vault that holds the recovery key was compromised at
the same time, treat as plaintext exposure.

### Recovery

1. **Replace the hardware.** Order a new Mac/PC.
2. **Fresh install** on the new box per
   [setup-mac-single.md](setup-mac-single.md) or
   [setup-windows.md](setup-windows.md).
3. **Re-issue every user key.** Use the rotation output from the
   immediate-action step.
4. **Re-distribute the new TLS cert** to every user's trusted-cert
   keychain.
5. **Distribute the new worker-app config** with the new node
   URL/IP if it changed.

### After-action

- File the regulator notification (Art. 33). The DPO writes; the IT
  evidence pack from
  [compliance.md § "Article 33"](compliance.md#article-33--personal-data-breach-notification)
  is what they cite.
- Notify users (Art. 34) — phishing risk is high since their real names
  + activity may be in attacker hands.
- Document the lesson: was the recovery key in the same drawer? Was
  FileVault actually on? Were there ACL gaps?

---

## Fire / water at the office

**Trigger:** the building's ops team tells you to evacuate; or you
find water damage on return.

### Immediate action

1. Treat both Macs as compromised even if they look fine — water
   ingress, smoke ingress, soot can lead to electrical shorts hours or
   days later.
2. **Power down the boxes** before evacuating if it's safe to do so
   (1-second `launchctl bootout` then long-press the power button).
   If unsafe, leave.
3. After evacuation: insurance claim. Don't power on damaged
   hardware until a hardware tech inspects.

### Recovery

Treated like [Theft of a Mac](#theft-of-a-mac) for credentials —
assume the hardware will be in unknown hands during repair.

If insurance replaces the hardware: fresh install on new boxes.

If the existing hardware is recoverable: only after a hardware tech
confirms the disk is uncorrupted, attempt to read the data. The HMAC
chain will tell you if the audit log corrupted (TAMPERED) — if it
did, start a new chain era.

---

## Heat / cooling failure (server room AC dies)

**Trigger:** the room housing the deployment Mac(s) goes hot —
ambient >30°C, or you can hear the Mac's fans at maximum.

### Why this matters

Apple Silicon throttles aggressively when too hot. You'll see:

- Latency rising minute by minute.
- Sentinel may or may not catch it (depends if you've wired a
  temperature sensor; LocallyAI doesn't ship one).
- `pmset -g thermlog | tail -50` shows thermal events.

Sustained high temperature shortens the Mac's life and can corrupt
in-flight writes if it triggers an emergency shutdown.

### Action

1. **Tell users:** "AI assistant may be slower; we're investigating
   a cooling issue."
2. **Reduce load** to give the Mac thermal headroom:
   - Lower `LOCALLYAI_MAX_CONCURRENT_INFERENCE` to 2.
   - Restart, accept the brief unavailability.
3. **Fix the cooling.** Restart the AC; open windows; bring a fan;
   move the Mac to a cooler room temporarily. Out of LocallyAI
   scope but in scope for the firm's facilities team.
4. **Monitor recovery.** Once temperature is back to 22-26°C, raise
   the gate back, restart.

### After-action

Log it. If recurrent, the firm needs a proper server-room cooling
plan — the deployment Mac shouldn't share an office with people in
summer.

---

## Voltage spike / brownout

**Trigger:** building electrical fault; UPS chirps; you smell
something burning; the power flickers.

### Action

1. **Power down everything immediately and cleanly** if you have
   warning:
   ```bash
   launchctl bootout gui/$(id -u)/com.locallyai.server
   sudo shutdown -h now
   ```
   (Don't trust the UPS to last for a graceful shutdown if you can
   do it now.)
2. **Once power is stable**, power on. Run the
   [Quick health sweep](daily.md#quick-health-sweep-do-this-every-morning).
3. **Inspect for damage.** A surge can fry power supplies, disks,
   GPU components silently. Run Apple Diagnostics
   (boot holding D); check `Disk Utility → First Aid`.
4. **Audit-verify.** A power loss mid-write can corrupt the audit
   log; if TAMPERED, see
   [incidents-software.md § "Audit chain TAMPERED"](incidents-software.md#audit-chain-tampered).

### Prevention

- UPS sized to last >5 minutes of full load = enough for a clean
  shutdown.
- Surge protector at the UPS input.
- For high-uptime deployments: dual power supplies, dual UPSes.

---

## Building access denied (lockdown / pandemic / strike)

**Trigger:** the firm's office is closed; nobody can physically
reach the deployment Mac.

### What still works

The deployment continues running unattended. Users connect from
home (if VPN is available) or wait for office reopen.

### What breaks

- Maintenance you'd planned (model swap, salt rotation) is on hold
  unless someone has remote-screen-share access to the Mac.
- Hardware incidents (Mac dies, disk fails) are unrecoverable until
  someone gets in.
- **Backup tapes** that lived in the same building are now
  inaccessible; off-site backups become critical.

### Pre-conditions (set up BEFORE lockdown is possible)

- **Remote management** for the deployment Mac via:
  - Apple Remote Desktop / Screen Sharing through a firm VPN.
  - Jamf / Kandji MDM if the firm has it.
  - Tailscale / WireGuard mesh so an admin's home Mac can reach the
    deployment.
- **Off-site backup** that's not in the same building.
- **Documented escalation contact** with physical access (e.g. a
  building-services contractor with a key) for true emergencies.

### During the lockdown

- Daily health sweep via remote.
- Defer all non-critical maintenance.
- If a hardware incident: degrade gracefully — single-node could
  fall over to the firm's emergency mobile-data deployment per
  [Power outage — extended](#power-outage--extended--30-min-building-down)
  pattern.

---

## Construction next door / vibration / dust

**Trigger:** building construction work near the deployment room;
vibration, dust ingress.

### Concerns

- Dust ingress kills fans and overheats components.
- Vibration can damage spinning disks (Macs use SSDs, but external
  Time Machine drives may be HDDs — vulnerable).
- Cable-cuts during construction.

### Action

- **Move the deployment temporarily** if practical. A locked office
  closet, an unused conference room, anywhere away from the work.
- **If can't move:** dust covers (computer dust covers exist), fan
  filters, more frequent cleaning.
- **Document** as an environmental risk in the firm's risk
  register.

---

## Hardware replacement

**Trigger:** a Mac/PC has a failing component (battery swelling, fan
seized, GPU artefacts, disk SMART warning, kernel panics). Or the
firm wants to upgrade.

### Procedure (HA — easier)

1. **On the failing node**, gracefully shut down the service:
   ```bash
   launchctl bootout gui/$(id -u)/com.locallyai.server
   ```
2. Confirm the surviving node is doing all the work — fleet dashboard
   shows the failing node `alive: false`.
3. Order replacement hardware.
4. While waiting: the firm runs on one node. **Tell users**: "Service
   is on a single node temporarily; brief blips possible if anything
   else fails."
5. When replacement arrives: fresh install per the relevant
   single-node guide on the new box.
6. Pair into HA per the relevant HA guide. Expect to copy the salt +
   ERA values from the surviving node's `.env` to the new node's
   `.env` per
   [maintenance.md § "HA: rotate from ONE node"](maintenance.md#ha-rotate-from-one-node-restart-both)
   (similar pattern for any cross-node `.env` field).
7. Re-pair Syncthing. Re-bootstrap Qdrant cluster (one new peer
   joins; old peer-id may need force-remove via
   [../qdrant-ha.md § "Re-adding a wiped node"](../qdrant-ha.md#re-adding-a-wiped-node)).
8. Verify failover.

### Procedure (single-node — harder, planned downtime)

1. Tell users: "AI assistant down for hardware replacement, ETA <X>."
2. Take a backup of `data/`, `storage/`, `users.json`, `.env`, `logs/`
   (cold storage destination).
3. Power down the failing box.
4. Set up the new box per the install guide.
5. Restore from backup — see [recovery.md § "Restore from cold
   backup"](recovery.md#restore-from-cold-backup).
6. Verify, tell users.

### After-action

- The old box: secure-wipe the disk before disposal (`diskutil
  secureErase` / `cipher /w:`). FileVault/BitLocker doesn't substitute
  for an explicit wipe at end-of-life — the encryption keys are still
  in the secure enclave / TPM and recovering them is non-trivial but
  not impossible for a determined attacker.
- File the asset disposal in the firm's ITAM register.

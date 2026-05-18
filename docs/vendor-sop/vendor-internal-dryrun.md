# Internal dry-run / dogfood onboarding

> Run the entire onboarding pipeline on a vendor-controlled Mac (a
> cofounder's MacBook, a partner's office device, a spare lab Mac)
> *as if* it were a real firm's office Mac. Discover friction before
> a real firm does.
>
> This is the **single most valuable test** before a new
> region/model/install-pathway hits a paying firm. Every onboarding
> playbook gap, every "obvious" assumption, every step the operator
> remembers but never wrote down — surfaces here.

---

## When to run a dry-run

| Trigger | Cadence | Why |
|---|---|---|
| Before the first paying firm onboards | Once | Catches every "we forgot to document" gap |
| Before the first KSA firm | Once | Validates the Arabic / RTL / Hijri / PDPL paths end-to-end |
| Before the first Windows firm | Once | Validates setup-windows.md against a real Mac-free environment |
| Before the first 2-node HA fleet | Once | Validates failover under a real LAN |
| After any structural change to install.sh | Per change | E.g., new prompt added, new region, new backend |
| After any structural change to the intake form | Per change | E.g., new field, hash-computation change |
| After any change to the vendor registration scripts | Per change | E.g., onboard_firm.sh, telemetry token format |
| After any release that changes the first-run UX | Per change | E.g., new launcher, new login gate flow |
| Annually as a regression check | Once a year | Catches drift even when no specific change triggered it |

**Default rule of thumb**: if the change affects what happens between
"firm IT receives the intake URL" and "firm DPO completes the audit
walkthrough", a dry-run is justified.

---

## What a dry-run validates (and what it doesn't)

### Validates well

- Intake form UX from a fresh perspective (cofounder fills it cold)
- Hash verification flow with a self-chosen legal name
- Vendor-side scripts on a real-world filename and inbox flow
- `install.sh` against a Mac with no LocallyAI prior state
- Telemetry round-trip across a real internet path (cofounder's home
  / office network → CF Worker → your dashboard)
- Worker / Manager Tauri apps connecting from a separate laptop over
  the LAN
- The 2-hour handover call format
- launchd KeepAlive across a real overnight cycle
- Update-poll cadence + first-update apply against a real cohort of one
- The 30-day soak watch (let it run and observe)

### Doesn't validate

- The DPA legal cycle (no real legal review)
- Corpus quality at scale (you'll use a small synthetic corpus)
- Real partner / lawyer feedback on UX (cofounder is not a lawyer)
- True 4-hour SLA pressure (no real incident motivation)
- Cross-firm interactions (there's only one test firm)
- Long-tail incidents (chaos suite covers most; dry-run catches the
  rest only by luck)

### Falsely reassuring if not careful

- Network familiarity — your cofounder's network is not representative
  of every law firm. Don't conclude "mDNS works" from a successful
  dogfood; conclude "mDNS works on this network".
- Hardware identicality — if the cofounder's Mac matches your dev Mac
  exactly, you'll miss hardware-tier failure modes. Pick a Mac that
  differs in at least RAM size or macOS version.
- Patience — the cofounder will be patient with friction you'd lose a
  paying firm over. Take notes on every "small" issue and treat them
  seriously.

---

## Setup

### Two devices, distinct roles

| Device | Role | Runs |
|---|---|---|
| Cofounder's MacBook | "Office Mac" | `install.sh`, becomes the API host (`https://office-mac.local:8000`) |
| Your laptop | Vendor laptop | Holds `~/.locallyai/vendor/firms-registry.json`, GPG key, vendor-records clone; runs `scripts/onboard_firm.sh` |
| Your laptop OR a spare device | "Staff laptop" | Runs Tauri Worker app pointed at cofounder's Mac over LAN |

The vendor laptop and the staff laptop can be the same physical
device, but if they are: complete the vendor-side work first, then
install the Tauri Worker app and switch context. Don't blur the
roles — they exist in separate columns of the firm-side / vendor-side
operational model.

### Pick a Mac that differs from your dev box

If your dev box is a 64 GB Mac Studio M2 Max, pick a 16 GB MacBook Air
M2 (or vice versa). Different RAM = different model picker outcome =
different code path tested.

If your dev box is on macOS 15.x, pick a 14.x or 13.x Mac. Different
OS version = catches macOS-version-specific quirks.

### Block enough time

| Compressed (fastest viable) | Realistic (recommended) |
|---|---|
| 4 hours single sitting | Spread over 3–5 calendar days |

Compressed misses the realistic friction of "form returns 2 days
later", "DPA waits a week", "30-day soak". Realistic catches more.

---

## The fake firm identity

### Legal name

Use a name that is **immediately identifiable** as a test firm and
that **could not collide** with a real firm name:

```
LocallyAI Internal Test — Cofounder Mac (YYYY-MM)
```

The date suffix prevents collision across multiple dry-runs (each
test gets a distinct firm_id hash because the date is different).

### firm_id hash (predictable; fine for testing)

```sh
FN="LocallyAI Internal Test — Cofounder Mac (2026-05)" \
  python3 -c "import os, hashlib; print(hashlib.sha256(f'locallyai-firm:{os.environ[\"FN\"]}'.encode()).hexdigest()[:16])"
```

Note this value down. You'll see it appear on the monitor dashboard
in the green-dot card and you want to be able to recognise it
instantly.

### Slug

```
locallyai-internal-test-cofounder-mac-2026-05
```

Filenames will be long but unambiguous.

### Other intake values (recommended defaults for the test)

| Field | Value | Why |
|---|---|---|
| Primary IT | Cofounder's name | Real person to take the call |
| Secondary IT | Your name | Real fallback |
| DPO | Your name (mark "TEST") | Will not be used |
| Time zone | Cofounder's actual TZ | Realistic |
| Office hours | Their actual hours | Realistic |
| Mac model / RAM / etc. | Cofounder's actual Mac | Realistic for that model class |
| Office subnet | Cofounder's actual home/office subnet | Realistic for their network |
| Data region | Match the test scenario (UK or KSA) | Drives DPA template branch + Arabic UI |
| Telemetry opt-in | YES | The whole point of the test is the round-trip |
| Update channel | `dev` for dry-run | So you catch issues before stable |

---

## Walking through the phases

### Phase 0 — Pre-engagement

**Skip the sales phase**. You don't need a discovery call with your
cofounder. Just record in your notes:

- "Skipped phase 0 for dry-run — fake commercial decision."
- Pretend to send the pre-engagement pack (you won't actually).

### Phase 1 — Intake

**Send the URL for real**. Email your cofounder the intake template:

> Hi [name], we're dogfooding the onboarding flow. Please fill out
> [https://locallyai-monitor.<your-cf-account>.workers.dev/onboarding.html]
> as if you were a law firm's IT person. Use this legal name exactly:
>
>   `LocallyAI Internal Test — Cofounder Mac (2026-05)`
>
> Email both downloaded files back. Time it for me — I want to know
> how long the form actually takes a stranger.

Note from cofounder: how long did the form take? What was confusing?
What fields did they hesitate on? **These are the most valuable
findings of the dry-run.**

### Phase 2 — Vendor processing (do for real)

Run the entire phase 2 against the returned files. Treat each step as
binding:

- [ ] Hash verification — does it match? If not, why?
- [ ] File the profile to vendor-records (commit + push for real — the
      test firm's record lives in the same private repo as real firms,
      tagged by the date suffix)
- [ ] Run `scripts/onboard_firm.sh` for real — it will register the
      test firm in production FIRM_TOKENS
- [ ] Skip DPA send (or send a dummy with subject "DRY-RUN — IGNORE")
- [ ] Share the telemetry token with cofounder via 1Password

### Phase 3 — On-site install

**Travel to the cofounder's Mac** (or video-call them with screen
share if remote). **Do every step as if you were on a real firm
visit**:

- [ ] Bring the laptop kit (per
      [vendor-onboarding.md §vendor-laptop-kit](vendor-onboarding.md#vendor-laptop-kit-pack-the-night-before))
- [ ] Pre-arrival call (yes, even if it's your cofounder)
- [ ] Pre-stage the .env from the USB
- [ ] Run `install.sh` step by step
- [ ] Run `audit_install.sh` — must pass
- [ ] Run `audit_egress.sh` — must PASS (install LuLu if not, even
      though cofounder may grumble about firewall warnings)
- [ ] Verify telemetry round-trip
- [ ] Stay until the firm appears on your monitor dashboard with a
      green dot

If anything required you to "remember" something not in the playbook
— **stop, write it into [vendor-onboarding.md](vendor-onboarding.md)
or the relevant install chapter, then continue**. The dry-run's value
is exactly these gaps.

### Phase 4 — Client app distribution

If cofounder has a second device (phone doesn't count — needs a real
laptop): install the Tauri Worker app on it, point at the cofounder's
office Mac over the LAN. Tests the LAN bind + CORS + mDNS path that
single-Mac dogfood would miss.

If cofounder has only one device: install the Worker app on the same
Mac, point at `https://localhost:8000`. Misses the LAN test but
validates the rest.

### Phase 5 — User provisioning

Walk cofounder through `manage_users.py add` for both an admin tier
and a worker tier user. Do **not** create users for them — same rule
as a real firm.

Cofounder records both keys in their own password manager. They are
the test admin + test worker.

### Phase 6 — Initial corpus ingestion

Pre-prepare a synthetic corpus for the test:

- 5–10 short documents in the appropriate language(s)
- Public-domain content (no real firm material, no NDAs)
- Mix of doc types: at least one PDF, one .docx, one .md
- For KSA test: include at least 2 Arabic documents

Cofounder ingests via the manager UI. Watch the ticker. Verify
queries return sources after ingestion completes.

### Phase 7 — Handover &amp; training

**Run the full 2-hour handover call** with cofounder + you (you play
both vendor + DPO + lawyer roles). Record it (with consent) — the
recording is the most useful artefact for refining the handover
script for the next real firm.

The point isn't to teach the cofounder (they already know how the
system works). The point is to **time-box the call honestly** and
discover that "30 minutes for daily ops walkthrough" actually takes
50, or that the DPO walkthrough has dead air at minute 22.

### Phase 8 — Steady state (let it run)

**Do not decommission immediately.** Let the test firm sit on the
dashboard for at least:

- 7 days minimum (catches launchd KeepAlive overnight cycles)
- 30 days ideal (catches the first auto-update, the first weekly
  retention prune, the first quarterly token rotation candidate)
- 90 days if scheduling permits (catches a real release cycle from
  dev → stable)

During the soak: treat the test firm as a real firm on the dashboard.
If a real alert fires for the test firm, respond per the SLA. The
test firm gets 4-hour treatment; if you let it slip you've corrupted
the test conditions for everything you'd learn from steady state.

---

## Cleanup checklist

When the test is complete (after the soak ends):

### On the cofounder's Mac

- [ ] Stop the launchd agents:
      ```sh
      launchctl unload ~/Library/LaunchAgents/com.locallyai.*.plist
      ```
- [ ] Verify nothing's still running on port 8000:
      ```sh
      lsof -iTCP:8000 -sTCP:LISTEN
      ```
- [ ] Remove the install dir (optional — cofounder may want to keep
      it as a sandbox):
      ```sh
      rm -rf /path/to/locallyai
      ```
- [ ] Remove the launchd plist files:
      ```sh
      rm ~/Library/LaunchAgents/com.locallyai.*.plist
      ```
- [ ] Uninstall LuLu if cofounder doesn't want a personal firewall
      (System Settings → uninstall, or `brew uninstall --cask lulu`)
- [ ] Remove the Tauri Worker / Manager apps from /Applications

### On the vendor side

- [ ] **Decommission the test firm in FIRM_TOKENS**:
      Edit `~/.locallyai/vendor/firms-registry.json` to remove the
      test firm's entry, then push the new merged JSON to the Worker:
      ```sh
      cd /path/to/locallyai
      WRANGLER_JSON="$(REG=~/.locallyai/vendor/firms-registry.json python3 -c 'import json, os; d=json.load(open(os.environ["REG"])); print(json.dumps({k: v["token"] for k, v in d.items()}))')"
      cd docs/monitor/cloudflare-worker
      printf '%s' "$WRANGLER_JSON" | npx wrangler secret put FIRM_TOKENS
      ```
      Within ~5 min the test firm's heartbeats start returning 401 →
      the dashboard marks it stale → eventually drops it.
- [ ] **Append to `vendor-records/firms-issued.log`** the
      decommission event:
      ```
      ISO_NOW | <firm_id> | <firm_name> | <operator> | decommissioned
      ```
- [ ] **Move the firm record** from active to archived:
      ```sh
      cd ~/locallyai-vendor-records
      mkdir -p firms/archived
      git mv firms/locallyai-internal-test-cofounder-mac-2026-05.md \
              firms/archived/
      git commit -m "archive test firm 2026-05 (dry-run complete)"
      git push
      ```
- [ ] Remove the cofounder's 1Password share (it's likely already
      expired; verify in 1Password admin)

### Optional (deeper cleanup)

- [ ] If you set up a fresh test environment for the run (e.g., a
      separate KV namespace for testing), revert it
- [ ] Clear monitor Worker `FIRM_STATE` and `ALERTS` KV entries for
      the test firm (KV TTL handles this within 7-30 days, but you
      can force-delete via wrangler if you want a clean dashboard)

---

## Lessons-learned write-up

Within 1 week of cleanup, file:

```
vendor-records/dryruns/<YYYY-MM>-<scenario>.md
```

Format:

```markdown
# Dry-run: <scenario>

**Test firm**:    <legal name>
**firm_id**:      <hash>
**Cofounder**:    <name>
**Test Mac**:     <model + RAM + macOS version>
**Test network**: <home / office / mobile hotspot>
**Started**:      YYYY-MM-DD
**Soak ended**:   YYYY-MM-DD
**Decommissioned**: YYYY-MM-DD

## What we were testing
One paragraph — why this dry-run, what specific path or change we wanted to validate.

## What we found

### Phase 1 — Intake
- <observation>
- <gap surfaced>

### Phase 2 — Vendor processing
- <observation>

### Phase 3 — On-site install
- <observation>

### ... (per phase)

## SOP updates filed during the run
- [commit-sha] — <what changed>
- [commit-sha] — <what changed>

## Open items
- [ ] <thing we didn't fix during the run that needs follow-up>

## Recommendation
Should the next dry-run for this scenario class repeat the same setup,
or have we learned enough to graduate to "first real firm"?
```

The cumulative dry-run log is **more valuable than any individual
run** — it shows whether the playbook is converging (each run
surfaces fewer findings) or diverging (each run uncovers new gaps,
suggesting an underlying instability).

---

## Common pitfalls (from prior dry-runs)

> When you run your first dry-run, append findings here so future
> runs benefit. Below are illustrative entries; replace with real
> ones once they exist.

- **Cofounder's network had no working mDNS.** Tauri Worker app couldn't
  resolve `office-mac.local`. Fixed by setting the office Mac IP
  explicitly in the Tauri config. **Action**: client-install.md should
  default to "ask firm IT to test mDNS resolution from a staff laptop
  before assuming it works".

- **install.sh prompted for `LOCALLYAI_DATA_REGION` even though the
  pre-staged .env had it.** Root cause: the .env was placed in the
  wrong directory (`/path/to/locallyai/.env.local` instead of
  `/path/to/locallyai/.env`). **Action**: install.sh should check both
  locations or print an error.

- **Telemetry token wouldn't authenticate (HTTP 401).** Cofounder's
  paste from 1Password included a trailing whitespace. **Action**:
  telemetry.py should `.strip()` the token before use.

- **Cofounder didn't know which "model" to pick during install.** The
  installer picker was technically correct but unhelpful. **Action**:
  add a one-line description per model option.

- **30-day soak ended at "everything green" but we never tested an
  update apply.** **Action**: dry-run procedure should explicitly
  cut a `dev` release during the soak so the test firm receives it.

---

## Anti-patterns to avoid

- **Don't run the dry-run from the same Mac as your dev box.** The
  whole point is a fresh environment — using your dev box validates
  nothing.
- **Don't have the cofounder's Mac on your office Wi-Fi.** Their
  home / mobile / unfamiliar network exposes more.
- **Don't skip the audit scripts because "it's just a test".** The
  audit scripts catch real-world deviations even on a test firm.
- **Don't decommission the moment install completes.** The soak is
  most of the test value.
- **Don't treat dry-run findings as "non-issues for real firms".**
  Every gap a cofounder hits, a real firm IT contact will hit harder.
- **Don't run dry-runs without writing them up.** An undocumented
  dry-run is a dry-run with zero institutional value.

---

## Graduating to "first real firm"

After a successful dry-run, the criterion for moving on:

- [ ] All SOP updates filed during the run are merged
- [ ] Lessons-learned write-up is filed
- [ ] No "open items" of severity blocker remain
- [ ] If the dry-run was triggered by a specific change (e.g., a new
      install pathway), that change has shipped to `stable` and the
      change's own pre-release checklist is green
- [ ] At least 7 days of soak data on the dashboard with no
      unresolved alerts

When all of these are checked: the change is "production-ready" for
a real firm.

If any are not: do not onboard the first real firm yet. Document the
remaining work and schedule.

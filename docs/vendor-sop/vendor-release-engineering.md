# Release engineering (vendor side)

> Vendor-internal procedures for cutting and shipping a release. The
> firm-side perspective on what happens when a release lands is in
> [docs/sop/updates.md](../sop/updates.md). **Read that first** if
> you have not already — this chapter assumes familiarity with the
> two-channel pipeline (dev → 24h soak → stable), GPG signing, and
> the kill switch.

---

## Release cadence

| Type | Target cadence | Channel | Tier composition |
|---|---|---|---|
| Patch / bug-fix | as needed | `dev`, then promote `stable` after 24h soak | Tier A only |
| Minor feature | every 2 weeks | `dev`, then `stable` after 24h | Tier A + B |
| Major feature | monthly | `dev`, then `stable` after 24h, with optional firm-by-firm rollout | A + B + (rarely C) |
| Tier C (config / model swap / breaking) | quarterly with explicit firm coordination | `stable` only after 7-day notice | C |

**Tier A** = safe, auto-applied (deploy.py atomic with healthz auto-rollback).
**Tier B** = needs the firm's maintenance window (manager applies via UI).
**Tier C** = breaking; vendor coordinates per firm.

Tier classification is set in `release_manifest.json` per artefact.

---

## Pre-release checklist

Run from the founder's daily-driver Mac in the LocallyAI/locallyai
repo, on a clean working tree, on `main` synced to remote.

- [ ] `git status` — clean
- [ ] `git pull --rebase` — up to date
- [ ] `bash scripts/audit_install.sh` — pass=14 warn≤1 fail=0
- [ ] `.venv/bin/python tests/ha_chaos.py` — pass=13 fail=0
- [ ] `.venv/bin/python tests/test_api.py` — all green (or however your test runner is invoked)
- [ ] Manually exercise the worker UI for 5 min — ingest a doc, ask a query, rename a conversation, delete a doc
- [ ] Manually exercise the manager UI for 5 min — view audit log, create user, view updates page, view downloads page, view documents page
- [ ] Clock check: not Friday after 14:00 local
- [ ] Kill switch state: `{"status":"go"}` — verified via `bash scripts/kill_switch_emergency.sh status`
- [ ] No critical alerts on the monitor dashboard
- [ ] Release notes drafted in `docs/release-notes-<version>.md` (consumer-facing — what changed for the firm)

If any check fails, fix or reschedule. **Do not** "skip just this once" —
the soak and the chaos suite catch real regressions in our integration
points (audit chain HMAC, pseudonymisation salt rotation, sync conflict
resolution).

---

## Cutting a `dev` release

```sh
cd /path/to/locallyai
bash scripts/release_server.sh dev <VERSION> <TIER> "<one-line summary>"
# Example:
bash scripts/release_server.sh dev 0.5.2 A "fix BM25 tokenizer Arabic edge case"
```

The script:

1. Builds the SHA-256 manifest of all release artefacts → `release_manifest.json`.
2. Commits the manifest with a release-notes-aware commit message.
3. Tags the commit `v<VERSION>-dev` (signed: GPG passphrase prompt).
4. Pushes the tag + commit to `origin main`.

Firms with `LOCALLYAI_UPDATE_CHANNEL=dev` (only the vendor's own dev
boxes — never a firm) start auto-applying within their next poll
window (5 min default).

Pinentry-mac will prompt for the GPG passphrase; type carefully (it's
the high-value secret).

### If pinentry-mac fails

If you see "Inappropriate ioctl for device":

```sh
export GPG_TTY=$(tty)
gpgconf --kill gpg-agent       # next gpg call respawns it
```

If pinentry-mac is missing entirely:

```sh
brew install pinentry-mac
```

(install.sh installs this on fresh Macs; founder-laptop reinstalls
sometimes miss it.)

---

## 24-hour soak watch

Once the dev release is tagged, the soak begins. The soak is a
**conscious vendor activity**, not just a passive wait.

For 24 hours:

- Every 2 hours during waking hours: glance at the monitor dashboard.
  Any new alerts since the dev release? Any firms reporting regressions
  in heartbeat metadata?
- Run `.venv/bin/python tests/test_smoke.py` (if you have one) at
  hours 1, 6, and 18 against your own dev box.
- Read your shift notes inbox for any reports.

If anything goes wrong during the soak: **do not promote to stable**.
Roll back the dev tag (delete and re-tag a known-good version), publish
release notes explaining the rollback, and re-cut after fixing the
issue.

```sh
git tag -d v0.5.2-dev
git push --delete origin v0.5.2-dev
# Fix issue, re-cut from main
bash scripts/release_server.sh dev 0.5.3 A "..."
```

---

## Promoting to `stable`

After 24h soak with no regressions:

```sh
bash scripts/release_server.sh promote <VERSION>
# Example:
bash scripts/release_server.sh promote 0.5.2
```

The script:

1. Verifies the dev tag exists and was signed >=24h ago (refuses if too soon).
2. Re-tags the same commit as `v<VERSION>` (no `-dev` suffix).
3. Pushes the new tag.

Firms with `LOCALLYAI_UPDATE_CHANNEL=stable` (the default) start picking
it up on their next poll.

### Tier B / C handling

- Tier A artefacts auto-apply via deploy.py with healthz auto-rollback.
- Tier B artefacts wait for the firm's manager to click Apply in the
  manager UI's `/updates` route. The dashboard banner shows what's
  pending.
- Tier C artefacts require explicit vendor coordination (phone call to
  firm IT first, scheduled cutover, vendor on standby during apply).

---

## Post-release watch

Once promoted to stable: 24-hour intensive watch.

- Every 30 min for the first 4h: check monitor dashboard for any firm
  showing red after the update lands.
- Every 2h for the next 20h.
- Any auto-rollback fired by deploy.py = treat as a Tier-1 bad-release
  signal and consider invoking the kill switch:
  ```sh
  bash scripts/kill_switch_emergency.sh require-version <PRIOR_KNOWN_GOOD_VERSION> "auto-rollback fired on <new>"
  ```

Append release notes to `vendor-records/release-history.log` with:

```
2026-05-12 | v0.5.2 | A | "fix BM25 tokenizer Arabic edge case" | promoted 2026-05-13 | post-release: clean
```

---

## Bad-release rollback

If you discover a bad release after promotion:

1. **Kill switch — pin everyone to known-good** (immediate):
   ```sh
   bash scripts/kill_switch_emergency.sh require-version <KNOWN_GOOD> "rollback in progress"
   ```
2. **Investigate** — what's the actual scope? Use the monitor
   dashboard's metadata to find which firms have the bad version.
3. **Comms** — if any firm pulled the bad version, phone call within
   1 hour. Walk them through manual rollback if auto-rollback didn't
   kick in.
4. **Fix forward** — cut a new dev release with the fix; soak; promote.
5. **Lift kill switch** once the new stable supersedes the bad one.
6. **Post-incident review** per [V5 appendix](vendor-incidents-own-infra.md#appendix-post-incident-review-template).

The bad-release retro is the most valuable retro you'll write — every
one improves the pre-release checklist for next time.

---

## GPG release-signing key safety

The single most important secret in vendor inventory.

- **Lives in macOS Keychain** via pinentry-mac (set up by `install.sh`)
- **Backup**: encrypted USB in fireproof off-site safe
- **Passphrase**: memorised + 1Password Founder vault + sealed envelope
- **Revocation cert**: pre-generated at key creation, in the sealed
  envelope (so a successor can revoke even without the private key)
- **Never** stored in a file readable by the daily-driver shell
- **Never** copied to another machine without a documented reason and a
  cleanup plan
- **Never** used to sign anything other than LocallyAI release tags

If you suspect leak: [V5 §gpg-release-signing-key-leak](vendor-incidents-own-infra.md#gpg-release-signing-key-leak).

---

## Releasing client apps (Tauri Worker / Manager)

Different pipeline from the server release — uses GitHub Actions:

```sh
bash scripts/release_clients.sh
# auto-bumps patch, updates Tauri configs, commits, tags vX.Y.Z-clients, pushes
```

GitHub Actions builds the .dmg / .msi matrix on macOS + Windows runners
and attaches the artefacts to the release page. Office Macs pull these
into their local installer mirror via the `client_installers.py` /
sentinel daily refresh.

The `-clients` tag is **not** GPG-signed today (signed code-signing
certificates are a future spend — Apple Developer + Microsoft
Authenticode). Until then, end-user installers carry the right-click →
Open / "More info → Run anyway" friction. Document this expectation in
the firm's onboarding email.

---

## Release notes hygiene

Every release tag must have a matching consumer-facing notes file at
`docs/release-notes-<version>.md`. Format:

```markdown
# v0.5.2 — 2026-05-12

## Tier A (auto-applied)

- Fixed BM25 tokenizer edge case where mixed Arabic/English queries
  returned 0 sources (#142). Affects KSA firms only.

## Tier B (manager applies)

- (none this release)

## Tier C (vendor coordinates)

- (none this release)

## What firm IT needs to do

Nothing. This is a Tier A release that applies automatically.

## What firm DPO should know

No changes to data handling, retention, or audit chain in this release.
```

Firms read these. Be honest about what changed, especially anything
that affects retention, audit, or pseudonymisation.

---

## Coordination with `vendor-records/release-history.log`

Each release adds one row:

```
ISO_DATE | VERSION | TIER | "summary" | promoted DATE | post-release: clean / regression / rollback
```

This log is a counterpart to the firm-side `firms-issued.log`. It gives
us "what releases happened when" without grepping git tags. Useful for
post-incident timeline reconstruction ("did the issue start before or
after v0.5.2?").

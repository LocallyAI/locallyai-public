# Incident playbooks — operator error

The category of "I broke it" — typo in `.env`, accidental `rm`, lost
admin key. These are the most common incidents. Most are recoverable;
some aren't.

---

## Forgot admin key

**Trigger:** you didn't save the key from `install.sh` output and now
need it for an admin endpoint.

### The blunt truth

The admin key isn't stored — it's generated, written to `.env`, and
shown once. If you didn't save it, you need to read it from `.env`:

```bash
grep '^LOCALLYAI_ADMIN_KEY=' ~/locallyai/.env | cut -d= -f2
```

If `.env` is intact, the key is right there. **Save it now.**

If `.env` has been deleted or corrupted, see "[Lost .env]" below.

---

## Lost admin key AND `.env`

**Trigger:** `.env` was deleted or unreadable; you have no copy.

### Recover

The admin key, audit salt, and audit HMAC key are dead. You can't
recover them; you can only generate new ones and accept that:

- New audit entries can't be HMAC-chained against old ones (different
  key). Old entries become a self-contained verifiable era; new
  entries start a new era.
- All historical pseudonyms are unrecoverable for subject-access
  (different salt). Document the loss for the DPO.

```bash
# Stop service
launchctl bootout gui/$(id -u)/com.locallyai.server

# Backup whatever's left for forensics
mkdir -p ~/incident-<date>
cp -p logs/audit.log ~/incident-<date>/  2>/dev/null
cp -rp logs/audit-*.log.gz ~/incident-<date>/  2>/dev/null

# Generate fresh secrets (32 bytes each)
ADMIN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
SALT=$(python3 -c "import secrets; print(secrets.token_hex(32))")
HMAC=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Write a new .env (preserve any other env vars you remember)
cat > .env <<EOF
LOCALLYAI_ADMIN_KEY=$ADMIN
LOCALLYAI_AUDIT_SALT=$SALT
LOCALLYAI_AUDIT_HMAC_KEY=$HMAC
LOCALLYAI_BACKEND=ollama
LLM_BASE_URL=http://localhost:11434
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:14b
PORT=8000
LOCALLYAI_API_BASE=https://localhost:8000
LOCALLYAI_DEPLOYMENT_ID=locallyai-prod
EOF
chmod 600 .env

# Reset the chain so the verifier doesn't choke on a head from the old key
rm logs/.audit_chain

# Restart
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.locallyai.server.plist
```

Save the new admin key in the password vault NOW.

### After-action

- File the loss as an Art. 32 control failure — the firm couldn't
  protect its own keys.
- Tell the DPO. The audit log historically before the recovery point
  is no longer chain-verifiable against the live service; it's
  evidence frozen in time. That's still defensible to a regulator —
  the data wasn't tampered with, the verification key was lost — but
  document it.

---

## Wrong env edit broke service

**Trigger:** you edited `.env`, restarted, now `/healthz` won't
respond.

### Diagnose

```bash
tail -50 logs/launchd_error.log
```

Common errors:
- `KeyError: '...'` — you deleted a required env var. The startup
  config-loading complains.
- `int(...) ValueError` — you put a non-numeric value in a numeric
  env var (e.g. `LOCALLYAI_AUDIT_RETENTION_DAYS=`).
- `OSError: ... could not parse` — you broke `.env` syntax (missing
  `=`, stray quotes).

### Fix

If you have a backup `.env.bak`:

```bash
cp .env.bak .env
chmod 600 .env
launchctl kickstart -k gui/$(id -u)/com.locallyai.server
```

Otherwise, open `.env` in a text editor; fix the line you edited
(read the error message in the log carefully — it usually names the
field). Save with chmod 600.

```bash
chmod 600 .env
launchctl kickstart -k gui/$(id -u)/com.locallyai.server
```

### Prevention

Always copy `.env` before editing:

```bash
cp -p .env .env.bak
# now edit .env
```

If the edit breaks: `mv .env.bak .env` and restart.

---

## Accidentally rm'd `audit.log`

**Trigger:** `rm logs/audit.log`, possibly with `> logs/audit.log` or
similar truncation.

### What happens

`audit-verify` returns `TAMPERED — tail truncated`. Per
[incidents-software.md § "Audit chain TAMPERED"](incidents-software.md#audit-chain-tampered),
you don't restore the log — you preserve evidence and start a new
chain era.

### Procedure

Same as the software-incident chapter for tampered chain. Document the
operator action that caused it; this is a self-induced Art. 33
candidate (the audit log of business activity is now incomplete) but
NOT a security incident as such.

### Prevention

Move the `rm` and `>` interactive shell habits away from `logs/`. The
supervisor re-tightens permissions on every boot; consider patching
your local install to also chattr / chmod 0440 to prevent accidental
truncation.

---

## Accidentally rm'd `users.json`

**Trigger:** `rm users.json` — every user's API key is now gone.

### Recover

`users.json` is on the same disk as the install. Single-node:

- Check if a backup or recent Time Machine snapshot has it:
  ```bash
  tmutil listbackups | head -3
  # Pick the latest, find the file:
  ls /Volumes/Backup\ of\ <Mac>/Latest/<your home>/locallyai/users.json
  ```
- HA mode: it's also in `$LOCALLYAI_SHARED_DIR/users.json` and on the
  peer node:
  ```bash
  ls $LOCALLYAI_SHARED_DIR/users.json
  scp <peer-ip>:~/locallyai/shared/users.json ./shared/users.json
  ```
- Worst case: regenerate user keys for everyone:
  ```bash
  python manage_users.py add "First Last"   # for each user
  ```
  Print each new key to its user.

### After-action

If you were forced to regenerate keys: document the user-impact event.
Users had a brief window where their old keys 401'd before the new
ones reached them.

---

## Accidentally pushed secrets to git

**Trigger:** `git status` shows `.env`, `users.json`, `tls/key.pem`
are tracked. OR you already pushed.

### If not yet pushed

```bash
git rm --cached .env users.json tls/key.pem
echo ".env" >> .gitignore
echo "users.json" >> .gitignore
echo "tls/key.pem" >> .gitignore
git add .gitignore
git commit -m "remove accidentally committed secrets"
```

That removes them from the index and future commits, but they're
still in the prior commit (locally). If you haven't pushed: `git
filter-repo` or `git rebase -i` to scrub them out of history.

### If already pushed

**Treat the keys as leaked.** Anyone who sees the GitHub repo (public
or private — assume the key has been read) has them.

1. **Within minutes**: rotate every secret. Salt, HMAC key, admin key,
   every user key. Per
   [incidents-security.md § "Salt leak"](incidents-security.md#salt-leak),
   [§ "HMAC chain key leak"](incidents-security.md#hmac-chain-key-leak),
   [§ "Admin key leak"](incidents-security.md#admin-key-leak),
   [§ "User key leak"](incidents-security.md#user-key-leak).
2. **Within 24h**: scrub the git history with `git filter-repo`:
   ```bash
   pip install git-filter-repo
   git filter-repo --path .env --path users.json --path tls/key.pem --invert-paths
   git push --force-with-lease origin main
   ```
3. **Notify your git host** (GitHub, GitLab) — they have processes
   for cached / cloned copies of the leaked content.
4. **Treat as Art. 33-eligible breach**.

### Prevention

`.gitignore` already blocks `.env`, `users.json`, `tls/`. The failure
mode is a `git add -f` or a manual file copy that bypasses it. Pre-
commit hook to refuse adding any matching path is the durable fix.

---

## Accidentally ingested wrong document

**Trigger:** you `cp`'d a doc into `data/` that shouldn't be there
(client A's NDA into client B's deployment, draft from an unrelated
matter, internal HR doc, etc.). Worse: it's already been ingested
and answered against.

### Action

1. Remove the file:
   ```bash
   rm data/wrong_document.pdf
   ```
2. Force re-index to drop it from Qdrant:
   ```bash
   python ingest.py --force
   ```
3. The Qdrant collection now no longer includes that document's
   chunks. **Verify** by sending a chat about the wrong doc:
   ```bash
   curl -sk -X POST -H "Authorization: Bearer $USER_KEY" \
     -H 'Content-Type: application/json' \
     -d '{"messages":[{"role":"user","content":"<distinctive query about that doc>"}]}' \
     https://localhost:8000/v1/chat/completions
   ```
   `usage.sources_retrieved` should now be 0 (or the response cites
   different documents).

### After-action

The audit log retains a record of every chat the wrong doc answered.
Those entries' `sources_retrieved > 0` and `query_hash` are evidence —
which user asked what (by pseudonym) and how many sources came back.
Decide whether to disclose to the affected user(s) per your firm's
policy.

If the document was high-sensitivity and was answered against during
its presence in `data/`, this may be a personal-data or
confidentiality breach. Talk to the DPO.

---

## Accidental erasure of wrong user

**Trigger:** you ran `manage_users.py erase Alice` when you meant
`Bob`.

### What happens

- Alice's API key is dead. She can no longer chat.
- Alice's lines in `billing.log` are redacted to `(erased)`.
- A tombstone for Alice's pseudonym(s) is in `erasure.log` —
  syncs to peers within 10s.
- Alice can no longer be added back with the same name AND have
  audit-write rights, because `validate_key` checks `is_erased` on the
  pseudonym, not the name. To re-enable, you have to:

### Recover (partial)

You can re-add Alice as a user (`manage_users.py add Alice`) — that
gets her a fresh API key. BUT new chats from her will still be
**refused** at audit-write time, because her pseudonym is in the
erasure ledger.

To restore Alice's ability to write audit entries, you must remove
her tombstone(s) from the erasure log. **This is a deliberate
break of the GDPR Art. 17 control** — only do it after documenting
that the erasure was an operator error and was retracted by the
data subject (Alice didn't actually request erasure).

```bash
# Identify Alice's pseudonyms across all eras:
.venv/bin/python -c "
from dotenv import load_dotenv; load_dotenv('.env')
from config import pseudonymise_user, known_salt_eras
for era in known_salt_eras():
    print(era, pseudonymise_user('Alice', era=era))
"

# Edit erasure.log: open it, find the lines whose pseudonym matches
# any of Alice's eras, delete them. (Use a text editor or sed -i.)
# Example (CHECK CAREFULLY before running):
sed -i.bak '/<Alice-pseudonym>/d' $LOCALLYAI_SHARED_DIR/erasure.log

# Force a refresh on every node:
ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' .env | cut -d= -f2)
curl -sk -X POST -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/admin/fleet/refresh
```

### Recover billing.log redaction

`billing.log` has Alice's old rows rewritten to `(erased)`. There is
no automatic restore. If Alice's billing history matters (it usually
does — that's how you invoice her time):

1. From your billing-database backup or a Time Machine snapshot,
   extract the pre-erasure `billing.log`.
2. Manually merge the legitimate-Alice rows back. Document the
   manual edit with timestamps and operator name in the firm's
   ITAM/billing-correction register.

### Prevention

`manage_users.py erase` should require a DPO sign-off, not a casual
operator decision. Workflow: only the DPO has the runbook line that
includes `erase`; IT-ops uses `remove` for routine off-boarding.

[daily.md § "User management"](daily.md#user-management) covers the
`remove` vs `erase` distinction.

### After-action

- Document the operator-error incident.
- File a self-corrected entry — Alice was never validly erased, the
  control was misfired, the data was restored within X minutes.

---

## Homebrew install failed

**Trigger:** `bash install.sh` fails at the brew install step.

### Common causes + fixes

**Network:** retry once your connection is stable.

**Permissions:** Homebrew on macOS sometimes wants `sudo` for the
first install. The Homebrew installer asks for the password; type
your Mac login password.

**Already partially installed:** `brew doctor` to diagnose.

**Apple Silicon vs Intel binary:** make sure the install command
matches your Mac. Apple Silicon goes to `/opt/homebrew/`, not
`/usr/local/`.

After fixing, re-run `bash install.sh` — it picks up where it left
off.

---

## "Permission denied" on a file you own

**Trigger:** `cat .env` returns "Permission denied" even though you
own it.

### Cause

The supervisor re-applied chmod 0o600 (`-rw-------`). Only the owner
can read. **You should be the owner** if you ran the install — but
sometimes shell context changed (`sudo` ran an install step).

### Fix

```bash
ls -la .env       # confirms permissions and owner
sudo chown $(whoami) .env       # only if owner is root or another user
chmod 600 .env
```

For routine reads:

```bash
sudo cat .env
```

(the supervisor runs as your user, so it can read its own files.)

---

## "Address already in use"

**Trigger:** supervisor refuses to start because port 8000 is held.

### Action

See [incidents-software.md § "API not responding"](incidents-software.md#api-not-responding)
Branch C. Common: AirPlay Receiver on macOS Sonoma+.

---

## Sudo'd a destructive command

**Trigger:** you ran `sudo rm -rf /something/critical`, `sudo dd
if=... of=/dev/disk0`, or similar in the deployment's directory.
The shell history shows the command but you can't undo it.

### Triage

1. **Stop typing.** Don't try to "fix" by running more commands —
   you may overwrite the disk's freed blocks and prevent recovery.
2. **What's the impact?** Read your shell history (`history | tail
   -20`) and identify exactly what was destroyed.
3. **Is it backed up?** If yes, restore per
   [recovery.md](recovery.md). If no, accept the loss; document
   what was lost; brief the DPO if it was log/audit data.

### Common cases

- `sudo rm -rf .venv/` → harmless. Re-create:
  `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
- `sudo rm -rf storage/` → re-ingest:
  `python ingest.py --force`. Slow but recoverable from `data/`.
- `sudo rm -rf logs/` → audit history gone. New chain era starts;
  document the loss for the DPO; archive if you have a backup.
- `sudo rm -rf data/` → CRITICAL. Restore from the firm's DMS or
  backup; without backup, the corpus is gone and all citations are
  hallucinated until re-ingest from a fresh source.
- `sudo dd ... of=/dev/disk*` → you may have overwritten the boot
  disk. Stop everything; boot from external media; assess.

### Prevention

- **Never `sudo rm -rf`** in this directory tree. The supervisor
  runs as your user; you don't need root for any LocallyAI op.
- Use `mv to_delete /tmp/` instead — gives you 24h to realise the
  mistake before macOS / Win cleans /tmp.
- Aliases: `alias rm='rm -i'` makes `rm` confirm. Annoying for
  long ops; install if you've ever done this once.

---

## Accidental git reset --hard / git clean -fd

**Trigger:** you wanted to discard one file's local change; you
ran `git reset --hard HEAD` and lost ALL uncommitted work.
Or `git clean -fd` removed untracked files including a draft
`.env.bak` you needed.

### Recover

`git reset --hard` only affects tracked files. Untracked files (like
`.env`, `users.json`, `tls/`, `logs/` — all gitignored) survive.

If you lost an UNCOMMITTED CHANGE to a tracked file:

```bash
git reflog | head -20
# Find a SHA before the reset.
git checkout <sha> -- <file>
```

If you `git clean -fd`'d an untracked file: it's gone. macOS
Time Machine may have a copy if you'd let it back up before the
clean.

### Prevention

- `git status` before any reset/clean — see exactly what would be
  destroyed.
- `git stash` instead of `reset --hard` for "undo my changes" —
  preserves them in the stash for later inspection.

---

## Accidentally exported the wrong evidence pack to the wrong recipient

**Trigger:** building an evidence pack for an Art. 33 disclosure;
sent the pack to the wrong email / cloud share. The recipient now
has data they shouldn't.

### Action — minutes matter

1. **Recall** through whatever channel sent it:
   - Outlook: Recall message (works only if recipient hasn't read).
   - Cloud share: revoke the share link immediately, deny access.
   - Slack/Teams: delete the message; message the recipient asking
     them to delete their copy.
2. **Tell the DPO immediately.** This is itself a (small) breach —
   regulated data went to an unintended party.
3. **Assess scope** with the DPO: what was in the pack? Was it
   redacted (good)? Did it include the salt or the unredacted
   `users.json` (bad)?
4. If salt or HMAC key was in the pack: full
   [incidents-security.md § "Salt leak"](incidents-security.md#salt-leak)
   procedure.

### Prevention

Build evidence packs into a **review-required** workflow:

```bash
mkdir -p ~/locallyai-evidence-<date>
# ... build pack ...
# Confirm contents and recipients in writing with the DPO before
# any send.
ls ~/locallyai-evidence-<date>/
```

---

## Time-zone confusion on audit timestamps

**Trigger:** you're reading audit.log; the timestamps don't seem
to match when the user said the event happened.

### Why

Audit timestamps are UTC (`%Y-%m-%dT%H:%M:%SZ` — note the `Z`).
The user is in a different timezone (e.g. UAE = UTC+4, BST = UTC+1
or +0).

### Convert

Mac:

```bash
# UTC to local:
date -j -f "%Y-%m-%dT%H:%M:%SZ" "2026-05-06T15:00:00Z" "+%Y-%m-%d %H:%M:%S %Z"

# Local to UTC for searching:
date -j -u -f "%Y-%m-%d %H:%M:%S" "2026-05-06 16:00:00" "+%Y-%m-%dT%H:%M:%SZ"
```

Linux/Win (with `python3`):

```bash
python3 -c "
from datetime import datetime, timezone
import zoneinfo
ts = '2026-05-06T15:00:00Z'
utc = datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
print('UTC :', utc)
print('Riyadh:', utc.astimezone(zoneinfo.ZoneInfo('Asia/Riyadh')))
print('Dubai :', utc.astimezone(zoneinfo.ZoneInfo('Asia/Dubai')))
print('London:', utc.astimezone(zoneinfo.ZoneInfo('Europe/London')))
"
```

### Why audit logs are UTC (and never change to local)

- DST transitions would create ambiguous timestamps (e.g. 01:30
  happens twice in autumn). UTC is monotonic.
- HA: nodes can be in different timezones. UTC is the only way to
  have a consistent fleet view.
- Regulators expect UTC.

Don't "fix" this by switching to local time. Document the rule in
the firm's evidence-handling notes and convert when reading.

---

## Worker app shows wrong nodes after HA setup

**Trigger:** users still see the old single-node URL, or only one
node, after you set up HA.

### Cause

Worker-ui is a built bundle. It bakes `VITE_API_BASE_URLS` at build
time. If you rebuilt only on the deployment Mac, users with old
bundles still have the old URLs.

### Fix

Rebuild and redistribute:

```bash
cd apps/worker-ui
echo 'VITE_API_BASE_URLS=https://10.0.0.11:8000,https://10.0.0.12:8000' > .env.local
npm run build
# then the launcher distributes dist/ to where users open it from
```

Have users hard-refresh their worker-app window (Cmd+Shift+R on Mac,
Ctrl+Shift+R on Win).

# Repository access — per-client SSH deploy keys

This chapter is vendor-side: the procedure LocallyAI's vendor engineer
follows when preparing a new client deployment. Each client gets their
**own** read-only SSH deploy key on the GitHub repo, separate from the
vendor's personal credentials, separate from every other client.

> **TL;DR — for new firms, the bootstrap script does this for you.**
> The install bootstrap (`onboarding/intake?t=<token>`) generates a
> per-firm SSH keypair on the office Mac, then POSTs the public key to
> the vendor monitor's `/onboarding/deploy-key` endpoint, which calls
> the GitHub API and registers the key on `LocallyAI/vendor-records`
> in under a second. **No manual paste, no email round-trip, no
> vendor-laptop step.** The rest of this chapter is the **fallback
> manual procedure** for when the auto endpoint isn't available
> (offline install, GitHub API outage, custom deploy-key repo per
> firm, etc.) and the **rotation/revocation procedure** for after
> the firm is live.

> **Read this BEFORE running `git clone` in any setup chapter.** If
> you've already cloned with HTTPS or with your personal SSH key, this
> chapter explains how to convert.

---

## Auto deploy-key flow (default for new firms)

The vendor monitor worker exposes `POST /onboarding/deploy-key`
(deployed since 2026-05-15). Flow:

1. Firm IT runs the install bootstrap on the office Mac:
   ```bash
   curl -fsSL "https://locallyai-monitor.your-cf-account.workers.dev/onboarding/intake?t=<install-token>" | bash
   ```
2. The bootstrap script:
   - Generates an `ed25519` keypair at
     `~/.ssh/locallyai_deploy_<firm-slug>` (no passphrase; chmod 600)
   - POSTs `{install_token, pubkey, firm_label}` to
     `/onboarding/deploy-key` on the vendor monitor worker
   - On `200 ok`, writes the SSH config alias (per §3 below) and
     proceeds to `git clone`
   - On `manual_required: true` (worker not configured) OR any
     non-2xx, prints the public key + GitHub UI URL and prompts the
     operator to paste it manually (the manual procedure in §1-§2
     below)
3. Vendor side: nothing to do. The deploy key appears in
   `https://github.com/LocallyAI/vendor-records/settings/keys`
   labelled `<firm-label> (auto-created YYYY-MM-DD)`.

### Vendor configuration that makes this work

Two Cloudflare Worker secrets must be set on the
`locallyai-monitor` worker:

| Secret | Value | Purpose |
|---|---|---|
| `GITHUB_DEPLOY_KEY_REPO` | `LocallyAI/vendor-records` | Owner/repo where deploy keys land |
| `GITHUB_DEPLOY_KEYS_PAT` | fine-grained PAT, repo=`vendor-records`, **Administration: Read and write**, 90+ days | The PAT the worker uses to call the GitHub Deploy Keys API |

To set them (one-time, per worker deploy):

```bash
cd ~/locallyai/docs/monitor/cloudflare-worker
printf '%s' 'LocallyAI/vendor-records' | npx wrangler secret put GITHUB_DEPLOY_KEY_REPO
# Copy fresh PAT to clipboard first, then:
pbpaste | tr -d '\n\r ' | npx wrangler secret put GITHUB_DEPLOY_KEYS_PAT
```

### PAT rotation

Quarterly OR on suspected compromise:

1. Generate fresh PAT at
   https://github.com/settings/personal-access-tokens (resource
   owner = `LocallyAI`, repo = `vendor-records`, **Administration:
   Read and write**, 90 days).
2. Copy to clipboard.
3. Run the `pbpaste | wrangler secret put` line above. Wrangler
   replaces the old secret atomically — zero downtime, the next
   `/onboarding/deploy-key` call uses the new PAT.
4. Revoke the old PAT in the same GitHub UI.
5. Verify with the smoke test: mint a throwaway install token via
   `/onboarding/mint-token`, POST a throwaway public key to
   `/onboarding/deploy-key`, confirm `200 ok` with a `key_id`,
   `DELETE /repos/LocallyAI/vendor-records/keys/<key_id>` to clean
   up.

### Verifying the auto flow is live

```bash
curl -s https://locallyai-monitor.your-cf-account.workers.dev/onboarding/deploy-key \
  -X POST -H "Content-Type: application/json" -d '{}' \
  | python3 -m json.tool
```

Expected: `{"ok": false, "error": "missing or malformed install_token"}`
(the endpoint exists and validation kicks in). If the endpoint
returns 404, the worker hasn't been deployed since the deploy-key
handler was added — `cd docs/monitor/cloudflare-worker && npx
wrangler deploy`.

### When the auto flow fails — what bootstrap shows the operator

The bootstrap script handles three failure modes gracefully:

- `manual_required: true` → worker secrets aren't set; operator gets
  the public key + a link to `https://github.com/LocallyAI/vendor-records/settings/keys/new`
  with the title pre-suggested. They paste, save, return to the
  install.
- `key already exists on the repo` (GitHub 422) → safe to ignore;
  install proceeds.
- Any other 4xx/5xx → operator falls back to the manual procedure
  in §1-§2 below.

---

**Why this matters:**

- **Per-client revocability** — if a client offboards, their key is
  removed from the GitHub repo's deploy-key list and that client's
  Mac can no longer pull updates. No need to rotate anyone else's
  credentials.
- **Audit trail** — GitHub records when each deploy key last fetched.
  You can see "Client X pulled the latest update on 2026-05-04";
  invaluable for support and compliance evidence.
- **No token in shell history** — HTTPS clones leave `git clone
  https://...:GHP_TOKEN@github.com/...` in `~/.bash_history` /
  `~/.zsh_history`. Deploy keys never appear in shell history.
- **Survives the leaver** — if your firm's lead engineer leaves
  LocallyAI's vendor, you don't have to re-clone every client's box;
  the per-client deploy key is still valid.
- **Read-only** — the deploy key has NO push access. A compromised
  client Mac cannot push to `main` and overwrite other clients'
  pulls. (HTTPS personal-access tokens almost always carry write.)

> Deploy keys are a **per-repository** GitHub feature. They do NOT
> grant access to other repos in your org. One deploy key = one repo.
> If your firm has separate `locallyai-app`, `locallyai-models`, and
> `locallyai-docs` repos, each needs its own deploy key per client.

---

## 0. Naming convention

Adopt this convention so the GitHub deploy-key list stays legible
when you have 20+ clients:

| Field | Value |
|---|---|
| **Key file** | `~/.ssh/locallyai_deploy_<clientslug>` (e.g. `locallyai_deploy_acme_law`) |
| **Key comment** | `locallyai-<clientslug>-<role>` (e.g. `locallyai-acme_law-mac-studio-primary`) |
| **GitHub deploy-key title** | `Client <name> — <role>` (e.g. `Acme Law — Mac Studio Primary`) |

`<role>` is one of: `mac-studio-primary`, `mac-studio-secondary`,
`win-primary`, `win-secondary`, `dev-laptop`, etc.

For 2-node HA, **issue separate keys per node** (so revoking one node
doesn't break the other). Naming: `locallyai_deploy_acme_law_a` and
`...b`; titles `Acme Law — Mac Studio A` / `... — Mac Studio B`.

---

## Manual procedure (fallback only)

The sections below are the **manual** procedure that the bootstrap
script performs automatically. Use these when:

- The vendor monitor worker is unreachable (rare; usually a
  Cloudflare incident or the worker hasn't been deployed yet).
- The deploy-key endpoint returned `manual_required: true` because
  the vendor PAT secrets aren't configured.
- You're issuing a deploy key to a non-firm install (engineer's
  dev laptop, vendor's own QA mac, etc.) where there's no install
  token to validate.
- You're rotating an existing key (per §7) where the procedure has
  to happen out-of-band of the bootstrap.

Otherwise — for new firm installs — let the bootstrap do this. The
auto path is faster, less error-prone, and self-documents in
`vendor-records` with the firm label and creation date.

## 1. Generate the key — Mac

On the client's Mac (or on the vendor laptop while preparing it
before delivery):

```bash
# Replace <clientslug> with the client's identifier (e.g. acme_law)
CLIENT="acme_law"
ROLE="mac-studio-primary"

ssh-keygen -t ed25519 \
  -C "locallyai-${CLIENT}-${ROLE}" \
  -f ~/.ssh/locallyai_deploy_${CLIENT}
```

When prompted for a passphrase: **leave blank** (the deploy key is
already restricted to one repo, read-only, on a controlled machine —
adding a passphrase means every `git pull` on the deployment Mac
would prompt, which is an operational nightmare for the firm's IT-ops
person and breaks `update.sh` automation).

`ssh-keygen` produces two files:

- `~/.ssh/locallyai_deploy_acme_law` — **private key**. Lives only
  on this Mac. Never copied off.
- `~/.ssh/locallyai_deploy_acme_law.pub` — **public key**. Goes to
  GitHub.

```bash
chmod 600 ~/.ssh/locallyai_deploy_${CLIENT}
chmod 644 ~/.ssh/locallyai_deploy_${CLIENT}.pub
```

---

## 1-Win. Generate the key — Windows

In an elevated PowerShell on the client's Windows box (or on the
vendor's prep box):

```powershell
$client = "acme_law"
$role   = "win-primary"

ssh-keygen -t ed25519 `
  -C "locallyai-$client-$role" `
  -f "$env:USERPROFILE\.ssh\locallyai_deploy_$client"
```

Same rules: empty passphrase; chmod-equivalent via icacls:

```powershell
$keyPath = "$env:USERPROFILE\.ssh\locallyai_deploy_$client"
icacls $keyPath /inheritance:r /grant "$($env:USERNAME):(R)"
```

Windows 10+ ships OpenSSH; you don't need PuTTY / Pageant. If
`ssh-keygen` fails with "command not found": Windows Settings →
Apps → Optional features → Add → "OpenSSH Client".

---

## 2. Register the public key on GitHub

1. Open the GitHub repository in a browser
   (`https://github.com/<vendor-or-firm>/locallyai`).
2. Click **Settings** (top of the repo page; you need admin rights
   on the repo).
3. Left sidebar → **Deploy keys**.
4. Click **Add deploy key**.
5. Fill in:
   - **Title:** `Client <name> — <role>` per the convention above
     (e.g. `Acme Law — Mac Studio Primary`).
   - **Key:** paste the **public** key. From the prep machine:
     ```bash
     cat ~/.ssh/locallyai_deploy_${CLIENT}.pub
     # macOS shortcut:
     pbcopy < ~/.ssh/locallyai_deploy_${CLIENT}.pub
     # Windows shortcut (PowerShell):
     Get-Content "$env:USERPROFILE\.ssh\locallyai_deploy_${client}.pub" | Set-Clipboard
     ```
     (Copy the output — single line, starts with `ssh-ed25519 ...`.)
   - **Allow write access:** ⚠️ **DO NOT TICK.** Leave it OFF.
     Read-only is the whole point.
6. Click **Add key**.

If you can't see the **Settings** tab: you don't have admin rights on
the repo. Get them from whoever does, or have them run this step.

### Verify

After adding, the deploy-key list shows the new entry with a "Last
used: never" stamp. After your first clone (§4), GitHub updates that
to a timestamp.

---

## 3. Configure SSH on the client machine

The client machine needs to know which key to use when talking to
github.com. The cleanest way: an SSH config alias so `git clone
git@github-locallyai:...` automatically uses the right key.

### Mac (and Linux, and Git Bash on Windows)

Edit `~/.ssh/config` (create if missing):

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
nano ~/.ssh/config
```

Append this stanza (replace `${CLIENT}` with the actual slug — SSH
config does NOT do shell expansion):

```
# LocallyAI deployment key for <client name>
Host github-locallyai
  HostName github.com
  User git
  IdentityFile ~/.ssh/locallyai_deploy_acme_law
  IdentitiesOnly yes
```

Save with `Ctrl+O`, `Enter`, `Ctrl+X`.

Set permissions on `~/.ssh/config` if newly created:
```bash
chmod 600 ~/.ssh/config
```

### Windows native OpenSSH (PowerShell)

Edit `%USERPROFILE%\.ssh\config` (the same file pattern works):

```powershell
notepad "$env:USERPROFILE\.ssh\config"
```

Same stanza, with the key path Windows-style:

```
Host github-locallyai
  HostName github.com
  User git
  IdentityFile C:\Users\<your-username>\.ssh\locallyai_deploy_acme_law
  IdentitiesOnly yes
```

ACL the config:

```powershell
icacls "$env:USERPROFILE\.ssh\config" /inheritance:r /grant "$($env:USERNAME):(R,W)"
```

### Why `IdentitiesOnly yes`

Without this line, ssh-agent will offer EVERY key it has loaded, and
GitHub will accept the first matching one — likely the vendor's
personal key, which is wrong (it grants the wrong access scope and
muddles the audit trail). `IdentitiesOnly yes` forces SSH to use ONLY
the listed `IdentityFile`. Always include it.

---

## 4. Clone using the alias

```bash
git clone git@github-locallyai:<vendor-or-firm>/locallyai.git
```

Note: `git@github-locallyai:` — `github-locallyai` is the alias from
your `~/.ssh/config`. Not `git@github.com:` (which would skip the
alias and use whatever default key you have).

On first connection, SSH will ask:

```
The authenticity of host 'github.com (140.82.x.x)' can't be established.
ED25519 key fingerprint is SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU.
Are you sure you want to continue connecting (yes/no)?
```

Type `yes` and press Enter. (Verify against
`https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints`
if paranoid.)

Then `cd locallyai` and run the rest of the setup chapter
(install.sh / install.ps1).

### Verify the deploy key was used

After the clone:
1. Open the GitHub deploy-key list (Settings → Deploy keys).
2. The entry you created should show "Last used: a few seconds ago".

If it still says "never" after a clone: the SSH config didn't take
effect. Re-check the alias name (`github-locallyai`, not
`github.com`); re-check the `IdentityFile` path; verify with:

```bash
ssh -Tv git@github-locallyai 2>&1 | grep -E "Offering|Authenticated|debug1: Identity"
```

That should show your `locallyai_deploy_<client>` key being offered
and accepted.

---

## 5. Pulling updates

`git pull` from the cloned directory uses the same alias
automatically — `git remote -v` shows
`origin git@github-locallyai:...`. No further config needed.

The `update.sh` (Mac) and `update.ps1`-equivalent (Windows) flows in
[maintenance.md § "Software updates"](maintenance.md#software-updates)
do `git pull` as their first step. They work unchanged.

---

## 6. HA — second node

For a 2-node HA fleet, **issue a separate deploy key per node**.
Steps 1–4 again, with role `mac-studio-secondary` (or `win-secondary`).
GitHub deploy-key list will have two entries; you can see each node's
last-pull timestamp independently — useful for catching "Mac-B has
been on holiday for a month" before the operator does.

Why not share one key across nodes? Two reasons:

1. **Revocation granularity.** If Mac-B is stolen, you revoke ONLY
   Mac-B's key; Mac-A keeps pulling.
2. **Audit independence.** When Mac-A pulls and Mac-B doesn't (HA
   rolling-update gone wrong), each node's deploy-key timestamp tells
   you which fell behind.

The clone target file path on the second node should match the first
(typically `~/locallyai`). The two clones are **independent**; they
don't share git remotes or hooks. Both pull from the same upstream
`main`.

---

## 7. Rotation

Rotate the deploy key:

- **Annually**, as a documented control (matches the firm's other
  credential-rotation cadence).
- **Immediately** if the client's Mac is suspected compromised, the
  vendor engineer who set it up has left, or the public key was
  exposed somewhere it shouldn't have been.

### Procedure

On the client's Mac:

```bash
CLIENT="acme_law"
# 1. Move the old key out of the way (don't delete yet — fallback).
mv ~/.ssh/locallyai_deploy_${CLIENT}     ~/.ssh/locallyai_deploy_${CLIENT}.old
mv ~/.ssh/locallyai_deploy_${CLIENT}.pub ~/.ssh/locallyai_deploy_${CLIENT}.pub.old

# 2. Generate a new key with the same filename pattern.
ssh-keygen -t ed25519 \
  -C "locallyai-${CLIENT}-mac-studio-primary-rotated-$(date +%Y%m%d)" \
  -f ~/.ssh/locallyai_deploy_${CLIENT}

# 3. Add the NEW public key to GitHub deploy keys (per §2 above).
#    Title: "Client <name> — Mac Studio Primary (rotated YYYY-MM-DD)"

# 4. Test that the new key works:
ssh -Tv git@github-locallyai 2>&1 | tail -5
git pull       # in the cloned directory; should succeed

# 5. Once confirmed, remove the OLD deploy key from GitHub
#    (Settings → Deploy keys → red bin icon).

# 6. Delete the local .old files:
rm ~/.ssh/locallyai_deploy_${CLIENT}.old ~/.ssh/locallyai_deploy_${CLIENT}.pub.old
```

Keep the SSH config alias unchanged (it points at the same path).

---

## 8. Revocation — when a client offboards

Procedure when the client ends their LocallyAI engagement:

1. **GitHub Settings → Deploy keys → delete every key with the
   client's slug.** Effective immediately — the client's Mac can no
   longer pull updates.
2. **Update the credential register.** Mark the client's deploy key
   entry "REVOKED" with the date.
3. **Inform the client's IT-ops person** that future pulls will fail.
   They keep the deployment running on the version they have; they
   just won't get updates.

The client's `.git/config` still has the SSH alias pointing at the
revoked key. `git pull` produces:

```
ERROR: The key you are authenticating with has been marked as read only.
```

…or, if you also removed it from the deploy-key list on GitHub:

```
ERROR: Repository not found.
fatal: Could not read from remote repository.
```

That's the right behaviour. The client's IT-ops person doesn't need
to do anything; pulls just stop working.

If the client wants a "fresh" deployment elsewhere (different vendor,
fork the repo): the source is open and they can clone via HTTPS or
their own deploy key.

---

## 9. Vendor-side credential register entries

Per-client entries in your **vendor** credential vault (NOT the
firm's vault — these are your operational secrets):

| Item | Where stored |
|---|---|
| `locallyai_deploy_<client>` private key | On the client's Mac at `~/.ssh/locallyai_deploy_<client>` (chmod 600). Never copied to vendor cloud storage. |
| `locallyai_deploy_<client>.pub` public key | Vendor's git of "client deploy keys" (a separate, internal repo or a 1Password vault entry). Audit trail. |
| GitHub deploy-key title (e.g. "Acme Law — Mac Studio A") | Vendor's CRM / client-onboarding tracker. Cross-references the client. |
| Date issued | Same. Required for the annual-rotation policy. |
| Date last rotated | Same. |
| Date revoked (if applicable) | Same; entries persist as audit trail. |

On the **client** side: the client doesn't need to know any of this.
Their IT-ops person clones from the alias, pulls when told to, and
otherwise treats LocallyAI like any normal piece of installed
software.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Permission denied (publickey)` on clone | Public key wasn't added to deploy-key list, OR added under the wrong repo | Verify in GitHub Settings → Deploy keys; re-add if missing |
| `Permission denied (publickey)` after rotation | New private key isn't on disk yet, or path in `~/.ssh/config` is wrong | `ls -la ~/.ssh/locallyai_deploy_*`; `cat ~/.ssh/config` |
| Wrong key being offered | `IdentitiesOnly yes` missing; ssh-agent offers personal keys first | Add `IdentitiesOnly yes` to the SSH config stanza |
| `git pull` works on Mac-A but fails on Mac-B | Each node has its own key; Mac-B's key was revoked or never added | Add a separate deploy key for Mac-B per §6 |
| GitHub shows "Last used: never" after first clone | SSH didn't actually use the alias | `git remote -v` should show `git@github-locallyai:...`; if it shows `git@github.com:...` re-clone with the alias |
| Key works at clone but `git pull` later fails | Deploy key was revoked from GitHub mid-deployment | Issue + register a new key per §7; re-clone or update remote URL if needed |

---

## 11. Why not personal access tokens?

PATs work — `git clone https://x:TOKEN@github.com/firm/repo` —
but for client deployments they're worse than deploy keys:

- **Token is in shell history**, in `.git/config`, and in any error
  message that prints the remote URL. Easy to leak.
- **Token grants whatever scope the user who minted it has.** Almost
  always more than just one repo.
- **Tokens are user-scoped.** When the user leaves the vendor, every
  client deployment using their token breaks. Deploy keys are
  repo-scoped and survive personnel changes.
- **No per-client revocability.** Revoking a token revokes it
  everywhere. Deploy keys revoke per-client.

Stick with deploy keys.

---

## 12. Why not GitHub App / fine-grained PAT?

GitHub Apps and fine-grained PATs are the modern equivalent of
deploy keys with more flexibility. They're fine for vendor-side
tooling. For per-client deployment access on a single client's Mac,
deploy keys are simpler and the operational story is the same. Stick
with deploy keys unless you have a specific reason to upgrade.

---

## Continue to setup

Now that the client's Mac (or Windows box) can pull from your repo,
return to the relevant setup chapter:

- [setup-mac-single.md](setup-mac-single.md) — the `git clone` step
  there now uses the alias.
- [setup-windows.md](setup-windows.md) — same.
- [setup-mac-ha.md](setup-mac-ha.md) — pair a second Mac with its own
  separate deploy key per §6.
- [setup-saudi.md](setup-saudi.md) — region overrides on top.

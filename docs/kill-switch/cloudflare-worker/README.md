# LocallyAI kill-switch — Cloudflare Worker (KV-backed)

TOTP-gated emergency stop for system updates. Self-contained on
Cloudflare: the JSON lives in CF KV (Cloudflare's free key-value
store), the Worker fronts it with TOTP auth, office Macs poll the
Worker URL. No GitHub repo, no second GitHub account, no PAT.

## Threat model

After deployment, an attacker needs **all of these** to flip the switch:
- The Worker URL (low secrecy).
- AND a valid 6-digit TOTP from the operator's phone.
- OR one of 10 single-use recovery codes (sealed envelope + password manager).

A LocallyAI GitHub compromise alone reaches NOTHING here. The
Cloudflare account is the only credential boundary, and it uses
different creds (different email, different 2FA) from your
LocallyAI workflow.

## Cost

Cloudflare Workers free tier: 100 000 requests/day. KV free tier:
1 000 reads/day, 1 000 writes/day, 1 GB storage. Office Macs poll
every 60 s = 1 440 polls/firm/day. The Worker caches the payload
for 60 s server-side via Cache-Control, so every-other-poll skips
KV entirely. **Comfortably free** up to ~50 firms; $5/mo Workers
Paid plan covers thousands.

---

## One-time setup (~25 minutes)

### 1. Create your Cloudflare account (~3 min)

1. https://dash.cloudflare.com/sign-up
2. Email: any address that ISN'T tied to your LocallyAI GitHub. If
   you don't have one handy, create a free Outlook / ProtonMail /
   iCloud account first — takes 2 min, no Gmail required.
3. Verify the email (CF sends a confirmation link).
4. **Enable 2FA**: dashboard → top-right → My Profile → Authentication →
   2FA. Use a different authenticator app entry than your LocallyAI
   GitHub one (you can use the same physical device — it's the entry
   that needs to be distinct).

### 2. Generate your TOTP secret + recovery codes (~2 min)

```bash
# From the LocallyAI repo root, on a trusted machine:
bash scripts/kill_switch_totp_setup.sh
```

This prints:
- An `otpauth://` URI — scan into Google Authenticator / 1Password /
  Authy / iOS Passwords / Bitwarden / etc. (`brew install qrencode`
  first to also get an inline ASCII QR.)
- 10 single-use recovery codes — save in a password manager **and** a
  printed sealed envelope.

The script never persists the secret to disk; clear scrollback (cmd-K)
when done.

If you already generated the secret previously and just need a QR:
```bash
bash scripts/kill_switch_totp_qr.sh '<your-otpauth-uri OR bare-secret>'
```

### 3. Install wrangler + login (~5 min)

```bash
cd docs/kill-switch/cloudflare-worker
npm install                                    # installs wrangler locally
npx wrangler login
# Browser opens. Authorize as the Cloudflare account from step 1
# (NOT a personal CF account you also use for unrelated projects).
```

### 4. Create the KV namespace (~1 min)

```bash
npx wrangler kv namespace create killswitch_state
```
Output looks like:
```
🌀 Creating namespace with title "locallyai-killswitch-killswitch_state"
✨ Success!
Add the following to your configuration file in your kv_namespaces array:
[[kv_namespaces]]
binding = "KILLSWITCH"
id = "abc123def456..."
```
**Copy the `id` value** (the long hex string) and paste it into
`wrangler.toml`, replacing `REPLACE_WITH_KV_NAMESPACE_ID`.

### 5. Set the two secrets (~3 min)

The setup-script output from step 2 has the exact one-liners. Roughly:

```bash
echo '<TOTP_SECRET_BASE32 from step 2>' \
  | npx wrangler secret put TOTP_SECRET_BASE32

echo '<JSON-array-of-hashed-codes from step 2>' \
  | npx wrangler secret put RECOVERY_CODES_HASHED
```

If you'd rather paste interactively (no shell history of the secrets):
```bash
npx wrangler secret put TOTP_SECRET_BASE32        # paste when prompted
npx wrangler secret put RECOVERY_CODES_HASHED     # paste when prompted
```

### 6. Deploy (~30 s)

```bash
npx wrangler deploy
```
Output:
```
✨ Success! Uploaded to <your-account>.workers.dev
   https://locallyai-killswitch.<your-account>.workers.dev
```
**Save that URL** — it's your kill-switch endpoint.

### 7. Wire up the operator CLI (~30 s)

Add to your shell rc (`~/.zshrc` or `~/.bashrc`):
```bash
export LOCALLYAI_KILL_SWITCH_API_URL=https://locallyai-killswitch.<your-account>.workers.dev/
```
Reload: `source ~/.zshrc`

### 8. Tell every firm's office Mac about the URL (~30 s per firm)

In the firm's `.env`:
```
LOCALLYAI_KILL_SWITCH_URL=https://locallyai-killswitch.<your-account>.workers.dev/
```
Then restart their API:
```bash
launchctl kickstart -k "gui/$(id -u)/app.locallyai.api"
```

(install.sh now ships this URL by default; existing deployments need
the manual edit.)

### 9. Verify end-to-end (~1 min)

```bash
# Public read (no auth):
curl -s "$LOCALLYAI_KILL_SWITCH_API_URL"
# Should print the default JSON payload (kill_switch_active:false, etc.)

# Authenticated mutate — block a fake tag, then unblock:
bash scripts/kill_switch_emergency.sh blocklist v0.0.0-fake-test
# Enter your TOTP code when prompted.
bash scripts/kill_switch_emergency.sh unblocklist v0.0.0-fake-test
# Same.
```

If both succeed, the system is live.

---

## During an incident

```bash
bash scripts/kill_switch_emergency.sh stop "v1.2.0-stable causing healthz failures"
bash scripts/kill_switch_emergency.sh blocklist v1.2.0-stable
bash scripts/kill_switch_emergency.sh require-version 1.2.1
bash scripts/kill_switch_emergency.sh resume
```

Each prompts for your TOTP code (read silently). Worker verifies +
writes KV. Firms react within ≤60 s.

## Recovery — phone lost

1. Run any kill-switch action; enter a **recovery code** instead of
   the 6-digit TOTP. Worker accepts (you'll see `used_recovery: true`).
2. Within 24 h, regenerate by re-running `kill_switch_totp_setup.sh`
   and re-uploading the new `TOTP_SECRET_BASE32` +
   `RECOVERY_CODES_HASHED` via `wrangler secret put`. Old codes stop
   working.
3. Re-scan the new `otpauth://` URI into your replacement phone.

If you lose phone AND all recovery codes: sign into the CF dashboard,
rotate the secrets via the Worker → Settings → Variables UI,
redeploy. Have your CF dashboard creds in your password manager.

## Migration from the GitHub-backed version

If you previously deployed the GitHub-backed Worker (the version
with `GITHUB_PAT`, `GITHUB_REPO`, `REPO_FILE` secrets):

1. `npx wrangler kv namespace create killswitch_state` — create the new namespace
2. Update `wrangler.toml` with the new namespace `id`
3. `npx wrangler deploy` — deploys the new code
4. Optionally remove the now-unused secrets:
   ```bash
   npx wrangler secret delete GITHUB_PAT
   npx wrangler secret delete GITHUB_REPO
   npx wrangler secret delete REPO_FILE
   ```
5. The Worker URL is unchanged; office Macs keep working without
   .env edits.
6. Optionally archive the `locallyai-status` GitHub account/repo if
   you created one — no longer needed.

## What this Worker does NOT do

- Sign the payload (clients trust the response from the Worker URL).
  Future enhancement: Ed25519 signature so office Macs verify the
  payload regardless of who served it (defends against Worker
  takeover).
- Rate-limit auth attempts. Add via CF dashboard → Security → WAF if
  you see brute-force attempts.
- Persist used recovery codes (Worker is stateless across requests).
  Recovery codes are single-use **by procedure**: regenerate the
  pool after a recovery use.

# Runbook: Vendor monitor dashboard locked out

**When**: You can't sign in to `https://locallyai-monitor.your-cf-account.workers.dev` even with a correct TOTP. Or you keep getting redirected back to the login screen. Or you see 401 / 429 errors in the browser console.

**Time budget**: 5 minutes. If this takes longer, the dashboard itself is broken — **escalate**.

**Risk if you stop midway**: You can't monitor firms. The kill switch is unaffected (separate Worker). Heartbeats from firms continue to land in KV; you just can't see them.

**Prerequisites**:
- Wrangler installed (`npm i -g wrangler` or `npx wrangler`)
- Cloudflare account authenticated (`wrangler login` once per machine)
- This repo cloned (for the `verify_compliance_snapshot.py` + KV commands)
- One unused recovery code from the sealed envelope (last-resort path)

## Decision tree

| Symptom | Procedure |
|---|---|
| TOTP rejected; was working an hour ago; you typed several codes | Step A (rate limit + replay state) |
| TOTP rejected; you've never logged in from this machine | Step B (clock skew on your phone) |
| Login screen accepted but every poll returns 401 | Step C (stale sessionStorage) |
| `429 Too Many Requests` in browser console | Step A |
| `503` / `502` on the worker URL | Step D (worker deploy / Cloudflare incident) |

## Step A — Rate-limit lockout

Round-2 of the red-team added a per-IP rate limit on the admin auth path. 10 failed attempts within an hour locks the IP for the rest of that hour.

### A.1 Confirm

```bash
cd docs/monitor/cloudflare-worker
# What's your current IP?
curl -s ifconfig.me
# Then check if there's a record for it:
npx wrangler kv key list --binding RATE_LIMITS --remote | grep "ratelimit:admin:<your-ip>"
```

If a row exists:
```bash
npx wrangler kv key get "ratelimit:admin:<your-ip>" --binding RATE_LIMITS --remote
```

You'll see `{"first_seen": ..., "count": 10}` (or close to 10).

### A.2 Clear

```bash
npx wrangler kv key delete "ratelimit:admin:<your-ip>" --binding RATE_LIMITS --remote
```

Expected: `Deleting the key ... Success.`

### A.3 Also clear any stale TOTP-replay records

After Round-2 we cache hashed TOTP codes for 120 seconds to defeat replay. If you typed a valid code earlier, it's blocked from re-use for 120s.

```bash
npx wrangler kv key list --binding RATE_LIMITS --remote --prefix "totp:"
```

If rows exist and you don't want to wait 120s for them to expire:
```bash
for k in $(npx wrangler kv key list --binding RATE_LIMITS --remote --prefix "totp:" | python3 -c "import sys,json; [print(r['name']) for r in json.load(sys.stdin)]"); do
  npx wrangler kv key delete "$k" --binding RATE_LIMITS --remote
done
```

### A.4 Hard-refresh + try once

In the browser: Cmd-Shift-R. Type a **fresh** TOTP code from your authenticator. Should succeed.

If it fails, move to Step B (clock issue) or Step C (stale session).

## Step B — Clock skew on your phone

TOTP windows are 30 seconds. The worker accepts t-1, t, t+1 — so a 90-second tolerance. If your phone's clock is off by more than 90s, no TOTP will ever match.

### B.1 Check your authenticator app

Most apps have a "Time correction" or "Sync" button in settings. Hit it.

(Google Authenticator: Settings → Time correction for codes → Sync now.)

### B.2 Check the worker's clock isn't drifting

The worker runs on Cloudflare's edge — its clock is correct. Skew is always your phone, not the worker.

### B.3 Try a fresh code

After sync, fetch a code from the authenticator and type it within 5 seconds of seeing it (don't let it tick over).

## Step C — Stale sessionStorage

The dashboard (post-Round-2 fix) stores a session token in `sessionStorage` after first successful login. If that token is corrupted or from a prior worker version:

### C.1 Clear sessionStorage manually

Open DevTools → Application → Storage → sessionStorage → `https://locallyai-monitor.your-cf-account.workers.dev`. Right-click → Clear.

OR run in DevTools console:
```js
sessionStorage.clear()
location.reload()
```

### C.2 Sign in again

You should be presented with the TOTP prompt fresh.

## Step D — Worker deploy / Cloudflare incident

### D.1 Confirm the worker is up

```bash
curl -I https://locallyai-monitor.your-cf-account.workers.dev/
```

Expected: `200 OK` (or `307` redirecting to `/index.html`).

If `5xx`: check `https://www.cloudflarestatus.com/`. If CF has an outage, wait. If only your worker is down:

```bash
cd docs/monitor/cloudflare-worker
npx wrangler deployments list | head -5
```

If the most recent deploy is broken, roll back:
```bash
npx wrangler rollback
```

This puts the previous version back live. **Escalate** to debug the broken version separately.

## Last-resort: recovery code

If TOTP is genuinely lost (phone destroyed, account deleted, can't sync):

### Recovery.1 — Grab a fresh code from the sealed envelope

The recovery codes were generated at worker setup and stored in `vendor-records/operator-recovery-codes.gpg`. Decrypt with your vendor PGP key.

### Recovery.2 — Use ONE code at the login screen

Type a recovery code in the same TOTP field. The worker handles them as fallback.

**The code is single-use** (Round-2 B1 fix). It's consumed from KV on first successful use. Cross it off your sealed envelope list.

### Recovery.3 — Re-provision TOTP

After logging in via recovery, reset TOTP on your authenticator. The TOTP secret in the worker (`ADMIN_TOTP_SECRET_BASE32`) didn't change — the recovery code consumed itself, not the TOTP seed. You need to re-import the seed into your new authenticator. **See `docs/sop/vendor-monitoring.md` "Vendor TOTP setup".**

### Recovery.4 — Replenish the recovery pool

After you're in, you have N-1 recovery codes left. If N-1 ≤ 3, re-mint:
```bash
cd docs/monitor/cloudflare-worker
npx wrangler kv key delete "recovery:hashed" --binding RATE_LIMITS --remote
# Then re-generate via scripts/onboard_vendor.sh (re-seeds from ADMIN_RECOVERY_HASHED env)
```

Update your sealed envelope with the new code list. **Escalate** if this is the third pool-refresh in 12 months — it suggests an operational problem (phones being lost too often).

## Things that go wrong

| Symptom | Cause | Fix |
|---|---|---|
| KV delete returns "Resource not found" | Different IP than you thought (VPN/cellular vs home) | Re-check `curl ifconfig.me`; you might have multiple records |
| Cleared everything but still locked out | Browser is sending an old SESSION header from cache | Cmd-Shift-R; if still failing, try an incognito window |
| Recovery code rejected | Already used OR wrong code | Try the next one in the envelope. If multiple fail, **escalate** — could indicate KV state corruption |
| `wrangler login` opens browser but never returns | CF auth flow timed out | `wrangler logout && wrangler login` |

## When to escalate

- All 3 recovery codes you tried get rejected → founder (KV corruption or wrong envelope)
- Rate-limit record won't delete (`wrangler kv key delete` reports success but the record persists) → founder (CF KV API issue)
- The worker URL `5xx`s for >5 minutes after a rollback → founder (worker code itself is broken)
- You can't access wrangler because your CF account is locked → founder + CF support ticket

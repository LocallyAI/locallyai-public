# Runbook: rotate the GitHub deploy-keys PAT

**When**: Quarterly (calendar reminder, vendor-side) OR on suspected
compromise (token in chat, screenshot, screen recording, etc.) OR
when the current PAT is within 7 days of expiry (GitHub emails the
owner ~7 days before).

**Time budget**: 5 minutes. If you're past 10 minutes, the auto
deploy-key endpoint is degraded — new firm installs fall back to the
manual procedure (`docs/sop/repo-access.md` §1-§2) which works but
slows onboarding by ~10 min per firm.

**Risk if you stop midway**: Two PATs are temporarily valid (the old
one wasn't revoked yet). Both work. Worst case: a malicious actor
who has the old PAT can still create deploy keys until you complete
step 5. Complete the procedure within the time budget.

**Prerequisites**:
- Browser logged into the LocallyAI GitHub organisation owner account
- This repo cloned locally (for `wrangler` access)
- Tailscale OR direct internet so `wrangler` can reach Cloudflare

## Procedure

### 1. Generate a fresh PAT

1. Open https://github.com/settings/personal-access-tokens
2. Click **Generate new token (fine-grained)**
3. Settings:
   - **Resource owner**: `LocallyAI` (NOT your personal account)
   - **Repository access**: "Only select repositories" → `vendor-records`
   - **Repository permissions** → **Administration**: Read and write
   - **Expiration**: 90 days
4. Click **Generate token**, copy the `github_pat_…` string to clipboard

Do NOT click off the page until step 3 is complete — once you leave,
GitHub never shows the token again.

### 2. Confirm the token is in clipboard, not visible anywhere

```bash
pbpaste | head -c 11   # should print "github_pat_"
pbpaste | wc -c        # should be ~93 chars + a trailing newline
```

If `pbpaste` returns something else, copy again.

### 3. Upload to Cloudflare

```bash
cd ~/locallyai/docs/monitor/cloudflare-worker
pbpaste | tr -d '\n\r ' | npx wrangler secret put GITHUB_DEPLOY_KEYS_PAT
```

Expected: `✨ Success! Uploaded secret GITHUB_DEPLOY_KEYS_PAT`. The
worker now uses the new PAT for the next request — wrangler swaps
the secret atomically; no deploy needed.

### 4. Smoke-test the new PAT works

```bash
# Mint a throwaway install token
INTAKE=$(printf 'firm_name=PostRotateTest\n' | base64)
PROFILE=$(printf '# rotation smoke test\n' | base64)
MINT=$(curl -s -X POST https://locallyai-monitor.your-cf-account.workers.dev/onboarding/mint-token \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "import json; print(json.dumps({'intake_blob':'$INTAKE','profile_md':'$PROFILE'}))")")
TOKEN=$(echo "$MINT" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# Generate a throwaway SSH key + post it to the deploy-key endpoint
TMP=$(mktemp -d)
ssh-keygen -t ed25519 -N "" -f "$TMP/id_ed25519" -q -C "rotate-smoke-$(date +%s)"
RESP=$(curl -s -X POST https://locallyai-monitor.your-cf-account.workers.dev/onboarding/deploy-key \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "import json; print(json.dumps({'install_token':'$TOKEN','pubkey':'$(cat $TMP/id_ed25519.pub)','firm_label':'rotate-smoke'}))")")
echo "$RESP" | python3 -m json.tool

# Cleanup the test key
KEY_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['key_id'])")
pbpaste | tr -d '\n\r ' | xargs -I{} curl -s -X DELETE \
  -H "Authorization: Bearer {}" -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/LocallyAI/vendor-records/keys/$KEY_ID"
rm -rf "$TMP"
```

Expected: `{"ok": true, "repo": "LocallyAI/vendor-records", "key_id": <int>, "key_title": "rotate-smoke (auto-created YYYY-MM-DD)"}` and the cleanup DELETE returns 204.

If you get `{"ok": false, "github_status": 401}` — wrangler stored
the wrong value (clipboard had something other than the PAT). Repeat
step 2 + 3.

### 5. Revoke the old PAT

1. Back to https://github.com/settings/personal-access-tokens
2. Find the OLD token (you'll see two; the older one is the
   compromised/expired one)
3. Click it → **Revoke** at the bottom
4. Confirm

Verify revocation took effect: any cached PAT value the worker
might have had is gone next call. The next deploy-key request uses
the new PAT only (Cloudflare always reads from the secret store, not
from in-memory cache).

### 6. Clear the new PAT from your clipboard

```bash
pbcopy < /dev/null
```

Otherwise the PAT sits in clipboard history (and macOS Universal
Clipboard syncs it across iCloud-paired Apple devices).

### 7. File the rotation

Manager UI → Compliance tab → Backup Test Attestations card → Record
test:

- `test_type` = "deploy-key PAT rotation"
- `result` = "passed" (or "failed" if step 4 didn't succeed)
- `operator` = your name
- `notes` = "Q? YYYY rotation. New PAT expires YYYY-MM-DD."

This makes the rotation visible in the firm-facing compliance
snapshot's Backup Attestations section, which feeds into the DPO's
quarterly review.

## Things that go wrong

| Symptom | Cause | Fix |
|---|---|---|
| `wrangler secret put` returns "Not authenticated" | Wrangler login expired | `npx wrangler login`, retry |
| Smoke test returns `github_status: 401` | New PAT lacks Administration:write OR wrong resource owner | Regenerate PAT with correct settings; rerun step 3 + 4 |
| Smoke test returns `github_status: 404` | `LocallyAI/vendor-records` repo doesn't exist or PAT scoped to a different repo | Check `GITHUB_DEPLOY_KEY_REPO` secret; check PAT's "Only select repositories" includes vendor-records |
| Smoke test succeeds but the cleanup DELETE returns 403 | New PAT has read-only on Administration | Regenerate with Read **and** write |
| `pbpaste` returns the OLD PAT | You copied something else after generating, or generated without copying | Generate again; this time keep clipboard untouched until step 3 done |

## When to escalate

- Two consecutive rotation attempts fail at step 4 → founder
  (could indicate org-wide PAT policy change, GitHub outage, or worker config drift)
- `wrangler secret list` doesn't show `GITHUB_DEPLOY_KEYS_PAT` after step 3 returns success → founder (Cloudflare account / wrangler binding mismatch)
- An old PAT shows recent activity in the GitHub audit log AFTER you revoked it → security incident, founder + DPO immediately

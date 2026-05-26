# Air-gap mode (`LOCALLYAI_AIR_GAP=1`)

Set on the firm's office Mac when the deployment should never reach
the vendor's infrastructure for any reason — typically because the
firm has stricter than usual policies about outbound connections, or
because the network it runs on is genuinely isolated.

## What gets disabled

| Subsystem | Air-gap behaviour | Code path |
|---|---|---|
| Kill-switch poll | Skipped; cache returns `(None, "air-gap mode")` | `kill_switch._fetch()` |
| Vendor release fetch | `gh release list` never runs; available-list is empty | `system_updates._list_remote_tags()` |
| Model auto-download | `select(model_id)` refuses if the model isn't already cached | `llm_models.select()` |

Anything else that touches the public internet is **not** under
air-gap mode's control. The operator is still responsible for
firewalling the Mac at the network layer if they need true network
isolation.

## What does NOT get disabled

These are firm-controlled paths the env-var doesn't touch — by
design, because they're either firm-owned infra or local-only:

- Inbound API access (the firm's lawyers still reach `:8000` over the LAN)
- Syncthing peer-to-peer file sync (firm-controlled; runs over the firm LAN)
- Local Qdrant + MLX inference (no internet contact)
- Audit-log writing, billing-log writing, compliance snapshot
- The firm's own HuggingFace cache (if a model is already downloaded,
  it loads from `~/.cache/huggingface/hub/` without network)

## What the firm gives up

Air-gap mode is a trade. The benefits are real (no vendor backdoor,
no forged kill-switch flip, no surprise tag fetch on an audit-day)
but the costs are also real:

- **No automatic security patches.** A CVE in the upstream code
  won't reach the firm unless the firm's own IT pulls it manually
  from a trusted mirror. We strongly recommend the firm clone the
  public repo locally + `git pull` on a documented cadence (weekly
  minimum).
- **No emergency stop.** If the vendor discovers a critical bug in
  release `v1.2.0` that the firm has already deployed, the
  kill-switch can't centrally freeze it — the firm's IT discovers
  the issue from its own monitoring + rolls back manually.
- **No model auto-pull.** Switching from Qwen 7B to Qwen 14B in the
  Manager UI's Models tab will fail with the "side-load required"
  message. The firm needs to rsync the model files into
  `~/.cache/huggingface/hub/models--mlx-community--Qwen2.5-14B-Instruct-4bit/`
  from a vetted mirror before the select succeeds.
- **Reduced operational signal.** Vendor-side telemetry (if enabled
  separately) also stops. The vendor won't proactively notice if the
  firm's install starts misbehaving.

## How to enable

Add to `.env` on the office Mac:

```
LOCALLYAI_AIR_GAP=1
```

Restart the API:

```
launchctl kickstart -k gui/$UID/app.locallyai.api
```

Verify:

```
curl -k -H "Authorization: Bearer $LOCALLYAI_ADMIN_KEY" \
  https://localhost:8000/admin/updates
# → returns an empty available list when air-gap is on
```

## Side-loading models (air-gap-friendly)

On a vetted mirror Mac (or staging machine the firm trusts):

```bash
# 1. Use Manager UI or huggingface-cli to download to the staging Mac.
huggingface-cli download mlx-community/Qwen2.5-14B-Instruct-4bit

# 2. The model lands at:
#    ~/.cache/huggingface/hub/models--mlx-community--Qwen2.5-14B-Instruct-4bit/

# 3. rsync (or USB) the entire directory to the office Mac's HF cache:
rsync -av \
  ~/.cache/huggingface/hub/models--mlx-community--Qwen2.5-14B-Instruct-4bit/ \
  office-mac:~/.cache/huggingface/hub/models--mlx-community--Qwen2.5-14B-Instruct-4bit/

# 4. On the office Mac, Manager UI → Models → select that model. The
#    download_status check finds it locally and select succeeds without
#    a network hit.
```

The same pattern works for the embedding model + reranker model.

## When to turn it off

Air-gap mode is operational, not regulatory — flip it back to `0`
anytime, restart the API, and the vendor channels come back up.
Common reasons to disable temporarily:

- Vendor publishes a CVE patch the firm wants to apply
- Firm wants to test a new model from the Manager UI dropdown
- The firm's procurement team approves the original concern that
  motivated the air-gap setting

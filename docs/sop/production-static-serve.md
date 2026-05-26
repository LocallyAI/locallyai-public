# Production static serve for worker-UI + manager-UI

The repository's two TanStack Start apps (`apps/worker-ui/`,
`apps/manager-ui/`) build to a Cloudflare Workers SSR bundle. The
launchd plists currently run `npm run dev` (Vite dev server) for
operator convenience — HMR, live edit, no rebuild loop. That mode
is appropriate for the maintainer's dogfood install but is not
appropriate for a production deployment at a firm: dev mode has
larger source maps in responses, no asset hashing for cache busting,
no minification of bundles, no CSP-friendly nonce handling, and
ships its own websocket loop the firm's IT doesn't want on a
production network.

This SOP describes the production-equivalent serve and the launchd
plist change required to switch over.

## Status today

| Asset | Build command | Output | Serve command (today) | Serve command (production) |
|---|---|---|---|---|
| worker-ui | `npm run build` | `apps/worker-ui/dist/` (server + client) | `npm run dev --port 5174` | `npx wrangler dev --port 5174 --local` against `dist/server/wrangler.json` |
| manager-ui | `npm run build` | `apps/manager-ui/dist/` (server + client) | `npm run dev --port 5173` | `npx wrangler dev --port 5173 --local` against `dist/server/wrangler.json` |

Both builds verified on this dogfood install on 2026-05-26. The
`dist/` trees are present and current. Operator can switch over by
editing the launchd plists.

## Switching the launchd plists (operator runbook)

This is a SCHEDULED maintenance task — not run during business
hours, because the UIs flip mode and any in-flight chat conversation
loses HMR/websocket continuity.

### Step 1 — Build both apps (cold cache: ~2 minutes)

```bash
cd ~/locallyai/apps/worker-ui  && npm run build
cd ~/locallyai/apps/manager-ui && npm run build
```

Builds are idempotent. Re-run after every `git pull` of either app
directory.

### Step 2 — Edit each launchd plist's ProgramArguments

Replace the `npm run dev …` invocation with `npx wrangler dev
--local …` pointing at the built `dist/server/wrangler.json`. Both
plists live at:

- `~/Library/LaunchAgents/app.locallyai.worker-ui.plist`
- `~/Library/LaunchAgents/app.locallyai.manager-ui.plist`

Worker-UI ProgramArguments (production):

```xml
<array>
  <string>/opt/homebrew/bin/npx</string>
  <string>wrangler</string><string>dev</string>
  <string>--local</string>
  <string>--port</string><string>5174</string>
  <string>--ip</string><string>0.0.0.0</string>
  <string>dist/server/index.js</string>
</array>
```

Manager-UI ProgramArguments (production): identical except `--port 5173`.

The `WorkingDirectory` stays at `apps/worker-ui` (or `manager-ui`).
The `EnvironmentVariables` block can drop `NODE_ENV=development` (now
NODE_ENV is unset, which makes wrangler default to production).

### Step 3 — Reload the launchd jobs

```bash
launchctl unload ~/Library/LaunchAgents/app.locallyai.worker-ui.plist
launchctl load   ~/Library/LaunchAgents/app.locallyai.worker-ui.plist
launchctl unload ~/Library/LaunchAgents/app.locallyai.manager-ui.plist
launchctl load   ~/Library/LaunchAgents/app.locallyai.manager-ui.plist
sleep 5
```

### Step 4 — Smoke-test

```bash
curl -sI http://localhost:5174  # → HTTP/1.1 200, worker-ui
curl -sI http://localhost:5173  # → HTTP/1.1 200, manager-ui
```

Open both apps via Launchpad (`LocallyAI Workspace.app` +
`LocallyAI Manager.app`). Verify:

- Workspace: send a chat message, plugin picker dropdown works
- Manager: Plugins tab shows the 3 plugins; Audit tab "Verify chain"
  button returns green; toggling a plugin works

### Rollback (under 60 seconds)

Revert the plists to `npm run dev` + reload both. The `dist/` trees
on disk are harmless — they'll just sit unused. The dev-mode launchd
process picks up source-tree changes again.

## CI gating

A Week 1 follow-up (per the red-team remediation plan) wires
`npm run build` into the GitHub Actions CI job so a broken build
fails the PR rather than being discovered at launch time. Until that
lands, the operator runs the build manually before the plist switch.

## Why we ship `wrangler dev --local` and not a static-file server

The TanStack Start build is an SSR Cloudflare Worker — there is no
`index.html` that a static server can hand out. `wrangler dev
--local` runs the same Worker entry that Cloudflare's edge would
run, but against `localhost`, with no internet egress. It's the
truest local equivalent of production behaviour.

Future: a vendor option to deploy the built worker to the firm's own
Cloudflare account (so the manager UI lives at
`https://manager.firm.example`) is on the longer-term roadmap. For
now, all UI traffic stays on the office Mac's loopback.

## Why we didn't switch tonight

The dogfood install runs the dev plists today. Switching during the
demo-prep window risks a regression that's harder to roll back under
time pressure than the original critique was painful to leave open.
The static-build artifacts exist, the SOP exists, the operator can
switch any business day in Week 1.

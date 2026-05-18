# LocallyAI Workspace — desktop wrapper

A minimal macOS WKWebView wrapper around the firm's lawyer-facing chat
workspace (the worker-ui). Replaces "open the URL in Safari" with a
real app icon a non-technical user can double-click.

This is the **lawyer's app** — same delivery shape as the Manager
desktop wrapper (`apps/manager-desktop`) but pointed at the workspace
service rather than the admin Manager UI.

## Why this exists

The worker-ui (chat + retrieval) lives at `http://<office-mac>:5174`
(Vite dev server). The Manager UI lives at `https://<office-mac>:8000`.
Both are served from the firm's office Mac but on different ports. The
admin/DPO uses Manager; lawyers use Workspace. They are separate apps
because they have different audiences and different auth boundaries —
lawyers shouldn't see admin endpoints.

For a lawyer the URL UX is identical to the Manager — they don't type
anything; they double-click the dock icon.

## Building

Requires Xcode Command Line Tools (`xcode-select --install`) for `swiftc`.

```bash
# Default URL: http://office-mac.local:5174
./build.sh

# Per-firm build
WORKSPACE_URL=http://office.acme-law.local:5174 ./build.sh
```

Output:

- `dist/LocallyAI Workspace.app` — bundle to drag to `/Applications`
- `dist/LocallyAI Workspace.zip` — for distribution

## Distribution

Same two paths as the Manager desktop wrapper — manual (AirDrop / email
of the .zip) or via the `/admin/installers/` endpoint. See
`apps/manager-desktop/README.md` for the full procedure; substitute
"Workspace" for "Manager" throughout.

The relevant runbook is `docs/runbooks/add-new-firm.md` — in
particular the staff-laptop distribution step.

## Configuring the URL after install

Same precedence as Manager:

1. `LOCALLYAI_WORKSPACE_URL` environment variable (debug override)
2. `UserDefaults` key `WorkspaceURL` (set via "Set Workspace URL…" menu)
3. Compile-time default (baked at build time via `WORKSPACE_URL=...`)

## HTTP vs HTTPS

Worker-ui currently runs as a Vite dev server on plain HTTP. The
deployment is on the office LAN (and Tailscale), so traffic isn't
internet-exposed. That's an acceptable posture for the firm-internal
service, but ISO 27001 A.8.24 / GDPR Art. 32 would prefer TLS
end-to-end on the wire. Two follow-ups:

- Switch to `vite preview` against a built production bundle served
  via the FastAPI backend on `:8000` (TLS for free, same cert as
  Manager UI).
- Or run a tiny TLS reverse proxy in front of `:5174`.

Both are out of scope for the desktop wrapper itself — the wrapper
just renders whatever the URL gives it.

## File structure

```
apps/worker-desktop/
├── LocallyAIWorkspace.swift   # The whole app (~200 lines)
├── Info.plist.tmpl            # Bundle metadata
├── Resources/                 # Drop AppIcon.icns here for an icon
├── build.sh                   # Build + bundle + ad-hoc sign + zip
└── README.md                  # This file
```

## Future work

- Same as `apps/manager-desktop/README.md` "Future work" — Developer
  ID sign + notarisation via the `locallyai-clients` release pipeline,
  app icon, Windows / Linux builds.
- Single combined "LocallyAI" launcher that shows BOTH Workspace and
  Manager icons. Probably overkill — lawyers and DPOs usually only
  need one of the two.

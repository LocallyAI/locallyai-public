# 0004 — Native Swift wrappers (Mac) + Tauri (Windows) for desktop clients

- **Status:** accepted
- **Date:** 2026-05-08
- **Deciders:** single-author
- **Tags:** ui, packaging, performance

## Context

LocallyAI's two end-user UIs (Manager for admins/DPO, Workspace for lawyers) are SPAs built with TanStack Start (Vite + React + TypeScript). They need to be distributable to staff laptops as **apps**, not browser bookmarks, because:

1. **Lawyers expect a dock icon.** "Open Safari, paste in office-mac.local:8000, accept the cert warning" is friction that ruins adoption. The reference user is a 55-year-old partner; the bar is "double-click an icon, sign in once."
2. **Self-signed cert handling.** The office Mac uses a self-signed TLS cert (no public DNS, no Let's Encrypt). A web browser shows a scary warning; a wrapped app can pin the cert at install time and never warn the user again.
3. **Per-firm baked URL.** Each staff bundle should know which office Mac to talk to without the user typing the hostname. The build embeds `https://<firm>.local:8000` as the default URL.
4. **Cross-platform** (Mac + Windows). Most firms are pure Mac; some are mixed; one or two are pure Windows.
5. **No JavaScript runtime should ship.** Bundling Chromium adds ~150 MB per app and a long-tail security surface that the small-firm IT person cannot patch.

The question: how to wrap the SPAs in a desktop shell that meets all five requirements without inheriting Electron's bloat.

## Decision

Two parallel implementations sharing the same SPA build:

- **macOS — native Swift + WKWebView wrappers.** `apps/manager-desktop/` and `apps/worker-desktop/` are minimal Swift apps (~250 LOC each) that load the SPA URL in a WKWebView, handle `window.open()` for new-tab links, handle downloads (e.g. compliance-snapshot HTML), and persist the server URL in `UserDefaults`. Compiled with `swiftc`, no Xcode project required. Output: a 130 KB `.app` bundle.
- **Windows — Tauri (Rust + WebView2).** `apps/clients/manager-tauri/` and `apps/clients/worker-tauri/` use Tauri's Rust core + Edge WebView2 (system-provided on Windows 10+). Output: a ~5 MB `.msi`. Same SPA build, same URL conventions, same cert-trust expectations.

The build flow runs at install time on the office Mac (`scripts/build_staff_apps.sh`) so every firm gets bundles with **that firm's hostname baked in**. A "Refresh" + "Rebuild" pair of buttons in the manager UI re-runs the build without IT needing shell access (see `client_installers.py` + `/admin/installers/rebuild`).

## Alternatives considered

- **Electron** for both platforms. The default choice — huge ecosystem, well-documented. Rejected primarily on **footprint** (~150 MB per app, bundled Chromium) and **maintenance burden** (the Electron security advisories alone are a part-time job). Also: a single binary that includes a full browser is a much larger attack surface for the long-tail security stance LocallyAI takes.
- **Pure browser** (no wrapper). The user types `office-mac.local:8000` in Safari. Rejected on UX grounds: the cert-warning flow, the no-dock-icon experience, and the missing per-firm URL baking are each disqualifying.
- **Tauri on macOS too** (instead of Swift). Tempting for unification — one codebase, two platforms. Rejected because (a) Tauri on macOS uses WKWebView under the hood anyway, so the platform isn't different, just an extra Rust toolchain dependency on every office Mac that wants to rebuild bundles, and (b) Tauri's installer for a Mac `.app` is ~3 MB vs Swift's 130 KB. The Swift path is materially lighter and the Swift code is short enough that maintenance cost is negligible.
- **PWA installation** (Add to Home Screen on iOS / Install as App in Chrome). Rejected because (a) self-signed certs and PWA install don't play well — Service Worker requires a valid TLS cert in most browsers, and (b) PWA "apps" still look like the browser they came from; partners experience them as browser tabs.
- **Capacitor / Cordova-style wrapper.** Rejected for the same Electron-class footprint argument; nothing they offer beats a 250-LOC Swift file.
- **Bundle a browser like Brave / Firefox in single-site mode.** Rejected as functionally equivalent to Electron in footprint with worse customisation.

## Consequences

### Positive

- **130 KB Mac app bundle.** Distributable by AirDrop, email, or any internal share mechanism. The whole `LocallyAI Manager.app` zip is ~30 KB compressed.
- **Per-firm baking.** Every firm's staff bundles point at that firm's hostname out of the box. Zero first-launch URL prompt.
- **No JavaScript runtime to maintain.** Swift bins are statically linked against macOS system frameworks; Tauri uses the system WebView2 on Windows. No security advisories from a bundled Chromium.
- **Same SPA, two shells.** The Manager and Workspace SPAs are identical across Mac and Windows; only the shell changes. UI work doesn't fork.
- **Operator can rebuild bundles in-place** via the manager UI's `/admin/installers/rebuild` endpoint after a `git pull` or hostname change (see `client_installers.py:rebuild_async`).

### Negative

- **Two codebases for the shell** (Swift + Rust). Bug fixes that affect both platforms need to land twice. Mitigated by keeping each shell minimal — the surface is ~250 LOC Swift and ~300 LOC Rust, both close to "WebView with handlers for download / window.open / URL config".
- **Apple Developer ID not yet provisioned.** Bundles are ad-hoc signed; first launch triggers the Gatekeeper warning that the user dismisses once with right-click → Open. Documented in `docs/sop/client-install.md`. The fix is a paid Apple Developer Program subscription ($99/year) — not blocking the engineering story, just a procurement task.
- **Windows code-signing also unprovisioned.** Same shape — SmartScreen warns once, user clicks through. Fix is a $100–500/year code-signing cert.
- **Two build scripts** — `apps/manager-desktop/build.sh` (Swift) and the Tauri build in `apps/clients/*/src-tauri/`. Driven from one place (`scripts/build_staff_apps.sh`).

### Neutral

- The Swift wrappers are intentionally tiny — adding features (e.g. a settings menu) means writing more Swift. The decision was to keep them as thin shells and put all real UI in the SPA.
- WKWebView and WebView2 both have quirks around `window.open` and file downloads that needed explicit delegate methods. Documented in the wrapper sources.

## References

- `apps/manager-desktop/LocallyAIManager.swift` — Mac shell (Manager)
- `apps/worker-desktop/LocallyAIWorkspace.swift` — Mac shell (Workspace)
- `apps/manager-desktop/build.sh`, `apps/worker-desktop/build.sh` — build scripts
- `apps/clients/manager-tauri/`, `apps/clients/worker-tauri/` — Tauri shells (Windows)
- `scripts/build_staff_apps.sh` — orchestrates the per-firm rebuild
- `client_installers.py` — manager-UI-exposed refresh + rebuild endpoints
- `docs/sop/client-install.md` — operator install + distribution workflow

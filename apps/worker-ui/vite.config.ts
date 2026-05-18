// @lovable.dev/vite-tanstack-config already includes the following — do NOT add them manually
// or the app will break with duplicate plugins:
//   - tanstackStart, viteReact, tailwindcss, tsConfigPaths, cloudflare (build-only),
//     componentTagger (dev-only), VITE_* env injection, @ path alias, React/TanStack dedupe,
//     error logger plugins, and sandbox detection (port/host/strictPort).
// You can pass additional config via defineConfig({ vite: { ... } }) if needed.
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

// Vite's default dev-server allowedHosts rejects unknown Host headers
// (DNS-rebinding defence). When the dev server is reachable from staff
// laptops via the office mDNS hostname or LAN IP, those Host values
// have to be allowlisted — otherwise the staff laptop's browser gets a
// 403 from Vite. The desktop app wrapper hits the dev server with the
// hostname baked into its build, so the list below has to track every
// hostname a firm's lawyer might connect by.
//
// `.local` covers any mDNS hostname (office-mac.local, etc.).
// `true` would disable the check entirely — we don't, because Vite's
// allowlist is a real defence against DNS-rebinding attacks from a
// page the lawyer happens to be visiting in the same browser session.
export default defineConfig({
  vite: {
    server: {
      allowedHosts: [
        "localhost",
        ".local",            // mDNS — e.g. office-mac.local
        ".ts.net",           // Tailscale magic DNS
        "192.168.0.0/16",    // LAN
        "100.64.0.0/10",     // Tailscale CGNAT
      ],
    },
  },
});

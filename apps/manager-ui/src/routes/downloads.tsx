// IT downloads page — staff-laptop client installers, served from this
// office Mac (not GitHub). Pulls happen automatically on a 24h cadence
// (sentinel) but IT can force-refresh via the button up top.
//
// Auth: this whole route is admin-only (the manager UI's bearer is the
// LOCALLYAI_ADMIN_KEY). The download flow uses authedFetch + blob so the
// admin key never appears in a URL string.

import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/TopBar";
import {
  Download, RefreshCw, Apple, MonitorSmartphone, AlertTriangle,
  CheckCircle2, Loader2, Github, FileDown, Hammer,
} from "lucide-react";
import {
  listInstallers, refreshInstallers, rebuildInstallers, downloadInstaller,
  type InstallerFile, type InstallersListResponse,
} from "@/lib/api";

export const Route = createFileRoute("/downloads")({
  head: () => ({ meta: [{ title: "Client Apps — LocallyAI" }] }),
  component: DownloadsPage,
});

function formatBytes(n: number): string {
  if (n < 1024)        return `${n} B`;
  if (n < 1024 ** 2)   return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3)   return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}
function relTime(iso: string | null): string {
  if (!iso) return "never";
  try {
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 60_000)         return "just now";
    if (ms < 3_600_000)      return `${Math.floor(ms / 60_000)} min ago`;
    if (ms < 86_400_000)     return `${Math.floor(ms / 3_600_000)} hr ago`;
    return `${Math.floor(ms / 86_400_000)} d ago`;
  } catch { return iso; }
}

function DownloadsPage() {
  const [data, setData] = useState<InstallersListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [refreshing, setRefreshing] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);

  const load = async () => {
    try { setData(await listInstallers()); setError(null); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed to load"); }
  };

  useEffect(() => {
    void load();
    // Poll while a refresh OR rebuild is in flight so the UI flips to "ready" without a manual reload.
    const t = setInterval(() => {
      if (data?.refresh_in_flight || data?.rebuild_in_flight || refreshing || rebuilding) void load();
    }, 2500);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.refresh_in_flight, data?.rebuild_in_flight, refreshing, rebuilding]);

  const onRefresh = async () => {
    setRefreshing(true); setError(null);
    try { await refreshInstallers(); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "Refresh failed"); }
    finally { setRefreshing(false); }
  };

  const onRebuild = async () => {
    setRebuilding(true); setError(null);
    try { await rebuildInstallers(); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "Rebuild failed"); }
    finally { setRebuilding(false); }
  };

  const onDownload = async (file: InstallerFile) => {
    setBusy((b) => ({ ...b, [file.name]: true }));
    try {
      const blob = await downloadInstaller(file.name);
      // Synthesise a download link so the browser saves the blob with the
      // correct filename. URL.revokeObjectURL releases the blob memory once
      // the click has triggered the download.
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = file.name; a.style.display = "none";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed");
    } finally {
      setBusy((b) => ({ ...b, [file.name]: false }));
    }
  };

  const groups = (data?.files ?? []).reduce<Record<string, InstallerFile[]>>((acc, f) => {
    const k = `${f.app}|${f.platform}`;
    (acc[k] ||= []).push(f);
    return acc;
  }, {});
  const order = ["Worker|macOS", "Worker|Windows", "Manager|macOS", "Manager|Windows"];

  return (
    <>
      <TopBar
        title="Client App Downloads"
        description="Distribute the Worker + Manager apps to staff laptops — no GitHub access required."
      />
      <main className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-5xl space-y-6">
          {/* Status strip */}
          <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3 text-xs">
              <Github className="h-4 w-4 text-muted-foreground" />
              <div>
                <div className="font-medium">
                  Source: <span className="terminal-font">{data?.status.github_repo ?? "—"}</span>
                </div>
                <div className="text-muted-foreground">
                  Last pulled {relTime(data?.status.last_pulled_iso ?? null)}
                  {data?.status.last_tag && (
                    <span className="ms-2">· tag <span className="terminal-font">{data.status.last_tag}</span></span>
                  )}
                  {data?.status.last_status && (
                    <span className="ms-2">· {data.status.last_status}</span>
                  )}
                </div>
                <div className="mt-0.5 text-muted-foreground">
                  Last local rebuild {relTime(data?.status.last_rebuilt_iso ?? null)}
                  {data?.status.last_rebuild_status && (
                    <span className="ms-2">· {data.status.last_rebuild_status}</span>
                  )}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {data?.refresh_in_flight && (
                <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" /> Pulling…
                </span>
              )}
              {data?.rebuild_in_flight && (
                <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" /> Rebuilding…
                </span>
              )}
              <button
                onClick={onRefresh}
                disabled={refreshing || data?.refresh_in_flight}
                className="inline-flex items-center gap-1.5 rounded-md border border-border bg-secondary px-3 py-1.5 text-xs font-medium hover:bg-accent disabled:opacity-40"
                title="Pull pre-built generic-URL bundles from GitHub Releases."
              >
                <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
                Check for updates
              </button>
              <button
                onClick={onRebuild}
                disabled={rebuilding || data?.rebuild_in_flight || data?.status.swiftc_available === false}
                className="inline-flex items-center gap-1.5 rounded-md border border-border bg-secondary px-3 py-1.5 text-xs font-medium hover:bg-accent disabled:opacity-40"
                title="Rebuild the per-firm staff apps in-place — runs scripts/build_staff_apps.sh, bakes this firm's hostname into Manager.app + Workspace.app. Use after a git pull or hostname change."
              >
                <Hammer className={`h-3.5 w-3.5 ${rebuilding ? "animate-pulse" : ""}`} />
                Rebuild per-firm apps
              </button>
            </div>
          </div>

          {/* Rebuild failure detail */}
          {data?.status.last_rebuild_status?.startsWith("failed") && data.status.last_rebuild_detail && (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs">
              <div className="font-semibold text-destructive">Last rebuild failed</div>
              <pre className="terminal-font mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-[11px] text-destructive/90">
                {data.status.last_rebuild_detail}
              </pre>
            </div>
          )}

          {/* swiftc-not-installed warning */}
          {data && data.status.swiftc_available === false && (
            <div className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 p-3 text-xs text-warning">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <div className="font-semibold">Xcode Command Line Tools not installed.</div>
                <div className="mt-1 opacity-90">
                  In-place rebuild needs <code>swiftc</code>. Install with{" "}
                  <code>xcode-select --install</code>. (You can still pull
                  pre-built generic bundles via "Check for updates".)
                </div>
              </div>
            </div>
          )}

          {/* gh-not-installed warning */}
          {data && data.status.gh_cli_available === false && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <div className="font-semibold">GitHub CLI not installed on this Mac.</div>
                <div className="mt-1 opacity-90">
                  Auto-pull and "Check for updates" both need <code>gh</code>.
                  Install with <code>brew install gh</code> then run{" "}
                  <code>gh auth login</code> as a user with read access to{" "}
                  <span className="terminal-font">{data.status.github_repo}</span>.
                </div>
              </div>
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* Empty state — pre-first-pull */}
          {data && data.files.length === 0 && (
            <div className="rounded-lg border border-dashed border-border bg-card/40 p-10 text-center">
              <FileDown className="mx-auto h-8 w-8 text-muted-foreground/60" />
              <h3 className="mt-3 text-sm font-semibold">No installers cached yet</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                Click <strong>Check for updates</strong> above to pull the latest
                LocallyAI Worker + Manager builds from GitHub. Auto-pulls happen
                daily once configured.
              </p>
            </div>
          )}

          {/* Installer grid — one card per (app, platform) */}
          {data && data.files.length > 0 && (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {order.map((key) => {
                const files = groups[key];
                if (!files || files.length === 0) return null;
                const [app, platform] = key.split("|");
                const Icon = platform === "macOS" ? Apple : MonitorSmartphone;
                return (
                  <div key={key} className="rounded-lg border border-border bg-card overflow-hidden">
                    <div className="flex items-center gap-2 border-b border-border px-4 py-3">
                      <Icon className="h-4 w-4 text-muted-foreground" />
                      <div className="text-sm font-semibold">
                        LocallyAI {app}{" "}
                        <span className="text-xs font-normal text-muted-foreground">· {platform}</span>
                      </div>
                    </div>
                    <ul className="divide-y divide-border">
                      {files.map((f) => (
                        <li key={f.name} className="flex items-center gap-3 px-4 py-3">
                          <div className="min-w-0 flex-1">
                            <div className="terminal-font truncate text-xs">{f.name}</div>
                            <div className="mt-0.5 text-[10.5px] text-muted-foreground">
                              {formatBytes(f.size_bytes)} · {relTime(f.mtime_iso)}
                            </div>
                          </div>
                          <button
                            onClick={() => onDownload(f)}
                            disabled={busy[f.name]}
                            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
                          >
                            {busy[f.name]
                              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              : <Download className="h-3.5 w-3.5" />}
                            Download
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}
            </div>
          )}

          {/* IT install hints */}
          <div className="rounded-lg border border-border bg-card/40 p-4 text-xs text-muted-foreground">
            <div className="flex items-center gap-2 text-foreground">
              <CheckCircle2 className="h-3.5 w-3.5 text-success" />
              <span className="text-sm font-semibold">Distribution paths</span>
            </div>
            <ul className="mt-3 list-disc space-y-1.5 ps-5">
              <li>
                <strong className="text-foreground">Manual:</strong> staff laptop user
                visits this page (admin key required), downloads the file for their OS,
                drag-to-Applications (Mac) or runs the .msi (Windows).
              </li>
              <li>
                <strong className="text-foreground">MDM bulk push:</strong> IT
                downloads here once, uploads the .dmg / .msi to Jamf / Munki / Intune,
                pushes to the device fleet. Pre-stage the office server URL via the
                config-script in <span className="terminal-font">docs/sop/client-install.md</span>.
              </li>
              <li>
                <strong className="text-foreground">Internal share:</strong> drop the
                downloaded files into the firm's SharePoint / network share with
                install instructions for staff to self-serve.
              </li>
            </ul>
          </div>
        </div>
      </main>
    </>
  );
}

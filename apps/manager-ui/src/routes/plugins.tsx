// Plugins page — toggle each installed plugin on/off for the entire firm.
// Plugins live under <install>/plugins/<name>/ as git-cloned bundles; this
// page reads the marketplace endpoint to discover them and POSTs to the
// enable/disable endpoints to flip the per-firm state file. A disabled
// plugin's skills become invisible to chat without uninstalling anything.

import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/TopBar";
import {
  Puzzle,
  AlertTriangle,
  Loader2,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
} from "lucide-react";
import { Switch } from "@/components/ui/switch";
import {
  getMarketplace,
  setPluginEnabled,
  type MarketplaceResponse,
  type MarketplacePlugin,
} from "@/lib/api";

export const Route = createFileRoute("/plugins")({
  head: () => ({ meta: [{ title: "Plugins — LocallyAI" }] }),
  component: PluginsPage,
});

function PluginsPage() {
  const [data, setData] = useState<MarketplaceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Tracks which plugin name is mid-toggle so we can show a spinner on the
  // right row and disable double-clicks. A free-form string keeps the
  // component immune to renames mid-flight.
  const [pendingName, setPendingName] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const load = async () => {
    setLoading(true);
    try {
      const r = await getMarketplace();
      setData(r);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load marketplace");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const onToggle = async (p: MarketplacePlugin, next: boolean) => {
    setPendingName(p.name);
    setError(null);
    try {
      await setPluginEnabled(p.name, next);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : `Toggle failed for ${p.name}`);
    } finally {
      setPendingName(null);
    }
  };

  const toggleExpand = (name: string) => {
    setExpanded((prev) => ({ ...prev, [name]: !prev[name] }));
  };

  const plugins = data?.plugins ?? [];

  return (
    <>
      <TopBar
        title="Plugins"
        description="Activate or disable the plugins available to all firm users. Disabled plugins are invisible to chat."
      />
      <main className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-5xl space-y-4">
          <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-card p-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Puzzle className="h-3.5 w-3.5" />
              <span>
                {data
                  ? `${plugins.length} plugin${plugins.length === 1 ? "" : "s"} installed · ${
                      plugins.filter((p) => p.enabled).length
                    } enabled`
                  : "—"}
              </span>
            </div>
            <button
              onClick={() => void load()}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-secondary px-3 py-1.5 text-xs font-medium hover:bg-accent disabled:opacity-40"
            >
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              Refresh
            </button>
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {data && plugins.length === 0 && (
            <div className="rounded-lg border border-dashed border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
              <Puzzle className="mx-auto mb-3 h-8 w-8 opacity-50" />
              <div className="text-foreground">No plugins installed.</div>
              <div className="mt-2 text-xs">
                Run{" "}
                <code className="rounded bg-secondary px-1.5 py-0.5 terminal-font text-[11px]">
                  git clone https://github.com/LocallyAI/locallyai-plugins-uk-public.git plugins
                </code>{" "}
                in the install directory.
              </div>
            </div>
          )}

          {plugins.map((p) => {
            const isPending = pendingName === p.name;
            const isOpen = !!expanded[p.name];
            return (
              <div
                key={p.name}
                className={`rounded-lg border ${
                  p.enabled ? "border-primary/30 bg-primary/5" : "border-border bg-card"
                }`}
              >
                <div className="flex items-start justify-between gap-3 p-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-base font-semibold">{p.name}</h3>
                      <span className="rounded bg-secondary px-1.5 py-0.5 text-[10px] terminal-font uppercase text-muted-foreground">
                        v{p.version}
                      </span>
                      {p.enabled && (
                        <span className="inline-flex items-center gap-1 rounded bg-primary/15 px-2 py-0.5 text-[10px] text-primary">
                          <CheckCircle2 className="h-2.5 w-2.5" /> enabled
                        </span>
                      )}
                    </div>
                    {p.description && (
                      <p className="mt-1 text-xs text-muted-foreground">{p.description}</p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {isPending && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
                    <Switch
                      checked={p.enabled}
                      disabled={isPending}
                      onCheckedChange={(v: boolean) => void onToggle(p, v)}
                      aria-label={`${p.enabled ? "Disable" : "Enable"} plugin ${p.name}`}
                    />
                  </div>
                </div>

                <div className="border-t border-border/60 px-4 py-2.5">
                  <button
                    onClick={() => toggleExpand(p.name)}
                    className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground"
                  >
                    {isOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                    Skills ({p.skills.length})
                  </button>
                  {isOpen && (
                    <div className="mt-2 space-y-1.5">
                      {p.skills.length === 0 ? (
                        <div className="text-[11px] text-muted-foreground">No skills declared.</div>
                      ) : (
                        p.skills.map((s) => (
                          <div
                            key={s.name}
                            className="rounded border border-border/60 bg-background/40 px-2.5 py-1.5"
                          >
                            <div className="terminal-font text-[11px] font-semibold">{s.name}</div>
                            {s.description && (
                              <div className="mt-0.5 text-[11px] text-muted-foreground">{s.description}</div>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </div>

                {p.mcp_servers.length > 0 && (
                  <div className="border-t border-border/60 px-4 py-2.5">
                    <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      Declared MCP servers
                    </div>
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {p.mcp_servers.map((m) => (
                        <span
                          key={m}
                          className="rounded bg-secondary px-2 py-0.5 text-[10.5px] terminal-font text-muted-foreground"
                        >
                          {m}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {data && (
            <div className="rounded-lg border border-border bg-card/40 p-3 text-[11px] text-muted-foreground">
              State persisted to{" "}
              <code className="terminal-font">{data.state_file}</code>.
            </div>
          )}
        </div>
      </main>
    </>
  );
}

// MCP Servers page — toggle the in-process tool servers that plugins call.
// Disabling a server removes its tools from every plugin's toolbox without
// uninstalling the plugin itself, so a firm can keep e.g. the document
// search plugin loaded but turn off its citation-formatter MCP if they
// want to wire in a custom one.

import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/TopBar";
import {
  Server,
  AlertTriangle,
  Loader2,
  RefreshCw,
  CheckCircle2,
  Wrench,
} from "lucide-react";
import { Switch } from "@/components/ui/switch";
import {
  getMarketplace,
  setMcpServerEnabled,
  type MarketplaceResponse,
  type MarketplaceMcpServer,
} from "@/lib/api";

export const Route = createFileRoute("/mcp")({
  head: () => ({ meta: [{ title: "MCP Servers — LocallyAI" }] }),
  component: McpPage,
});

function McpPage() {
  const [data, setData] = useState<MarketplaceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [pendingName, setPendingName] = useState<string | null>(null);

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

  const onToggle = async (s: MarketplaceMcpServer, next: boolean) => {
    setPendingName(s.name);
    setError(null);
    try {
      await setMcpServerEnabled(s.name, next);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : `Toggle failed for ${s.name}`);
    } finally {
      setPendingName(null);
    }
  };

  const servers = data?.mcp_servers ?? [];

  return (
    <>
      <TopBar
        title="MCP Servers"
        description="In-process tool servers that plugins call. Disabling one removes its tools from every plugin's toolbox without uninstalling."
      />
      <main className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-5xl space-y-4">
          <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-card p-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Server className="h-3.5 w-3.5" />
              <span>
                {data
                  ? `${servers.length} server${servers.length === 1 ? "" : "s"} discovered · ${
                      servers.filter((s) => s.enabled).length
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

          {data && servers.length === 0 && (
            <div className="rounded-lg border border-dashed border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
              <Server className="mx-auto mb-3 h-8 w-8 opacity-50" />
              <div className="text-foreground">No MCP servers discovered.</div>
              <div className="mt-2 text-xs">
                Servers are loaded from <code>mcp_servers/</code> at startup. Restart the API after dropping one in.
              </div>
            </div>
          )}

          {servers.map((s) => {
            const isPending = pendingName === s.name;
            return (
              <div
                key={s.name}
                className={`rounded-lg border ${
                  s.enabled ? "border-primary/30 bg-primary/5" : "border-border bg-card"
                }`}
              >
                <div className="flex items-start justify-between gap-3 p-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-base font-semibold terminal-font">{s.name}</h3>
                      <span className="inline-flex items-center gap-1 rounded bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        <Wrench className="h-2.5 w-2.5" />
                        {s.tool_count} {s.tool_count === 1 ? "tool" : "tools"}
                      </span>
                      {s.enabled && (
                        <span className="inline-flex items-center gap-1 rounded bg-primary/15 px-2 py-0.5 text-[10px] text-primary">
                          <CheckCircle2 className="h-2.5 w-2.5" /> enabled
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {s.enabled
                        ? "Tools exposed to every plugin that declares this server as a dependency."
                        : "Tools hidden from chat. Plugins that depend on this server will report missing tools at call time."}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {isPending && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
                    <Switch
                      checked={s.enabled}
                      disabled={isPending}
                      onCheckedChange={(v: boolean) => void onToggle(s, v)}
                      aria-label={`${s.enabled ? "Disable" : "Enable"} MCP server ${s.name}`}
                    />
                  </div>
                </div>
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

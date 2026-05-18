import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/TopBar";
import { RotateCw, HardDrive, Activity, AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import {
  getDetailedHealth,
  getAlerts,
  getDiagnosticianHistory,
  type DetailedHealth,
  type AlertsResponse,
  type DiagnosticianHistoryResponse,
} from "@/lib/api";

export const Route = createFileRoute("/system")({
  head: () => ({ meta: [{ title: "System — LocallyAI" }] }),
  component: SystemPage,
});

function SystemPage() {
  const [health, setHealth] = useState<DetailedHealth | null>(null);
  const [alerts, setAlerts] = useState<AlertsResponse | null>(null);
  const [history, setHistory] = useState<DiagnosticianHistoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, a, d] = await Promise.all([
        getDetailedHealth(),
        getAlerts(),
        getDiagnosticianHistory(50),
      ]);
      setHealth(h);
      setAlerts(a);
      setHistory(d);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load system data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    const interval = window.setInterval(refresh, 15_000);
    return () => window.clearInterval(interval);
  }, []);

  const backendOk = health?.backend.reachable ?? false;
  const diskFree = health?.disk_free_gb ?? null;
  const auditEntries = health?.audit_log.line_count ?? 0;
  const auditSize = health?.audit_log.size_bytes ?? 0;

  return (
    <>
      <TopBar title="System & Monitoring" description="Backend health, alerts, and diagnostician history" />
      <main className="flex-1 space-y-6 p-6">
        <div className="flex items-center justify-end gap-2">
          <button
            onClick={refresh}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-md border border-border bg-secondary px-3 py-1.5 text-xs font-medium hover:bg-accent disabled:opacity-40"
          >
            <RotateCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>

        {error && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <ServiceCard
            label="Inference backend"
            ok={backendOk}
            value={backendOk ? "Reachable" : "Unreachable"}
            sub={backendOk ? "Inference backend online" : "Check inference backend (MLX / Ollama / LM Studio)"}
          />
          <ServiceCard
            label="Disk free"
            ok={diskFree !== null && diskFree > 10}
            value={diskFree !== null ? `${diskFree} GB` : "—"}
            sub={diskFree !== null && diskFree < 10 ? "Low disk space" : "Storage volume"}
            icon={<HardDrive className="h-4 w-4" />}
          />
          <ServiceCard
            label="Audit log"
            ok={auditEntries > 0}
            value={auditEntries.toLocaleString()}
            sub={`${(auditSize / 1024).toFixed(1)} KB on disk`}
            icon={<Activity className="h-4 w-4" />}
          />
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="rounded-lg border border-border bg-card lg:col-span-2">
            <div className="flex items-center justify-between border-b border-border p-4">
              <div>
                <h2 className="text-sm font-semibold">Active alerts</h2>
                <p className="text-xs text-muted-foreground">
                  Overall status:{" "}
                  <span
                    className={
                      alerts?.status === "ok"
                        ? "text-success"
                        : alerts?.status === "degraded"
                          ? "text-warning"
                          : alerts?.status === "critical"
                            ? "text-destructive"
                            : "text-muted-foreground"
                    }
                  >
                    {alerts?.status ?? "unknown"}
                  </span>
                </p>
              </div>
            </div>
            {alerts && alerts.alerts.length === 0 ? (
              <div className="px-4 py-8 text-center text-xs text-muted-foreground">
                All clear — no alerts.
              </div>
            ) : (
              <div className="divide-y divide-border">
                {alerts?.alerts.map((a, i) => (
                  <div key={i} className="flex items-start gap-3 px-4 py-3 text-sm">
                    <AlertTriangle
                      className={`mt-0.5 h-4 w-4 shrink-0 ${
                        a.level === "critical"
                          ? "text-destructive"
                          : a.level === "warning"
                            ? "text-warning"
                            : "text-muted-foreground"
                      }`}
                    />
                    <div className="flex-1">
                      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{a.level}</div>
                      <div className="text-sm">{a.message}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="rounded-lg border border-border bg-card">
            <div className="border-b border-border p-4">
              <h2 className="text-sm font-semibold">Watchdog</h2>
              <p className="text-xs text-muted-foreground">Sentinel agent state</p>
            </div>
            <div className="p-4 text-xs">
              {health?.watchdog ? (
                <pre className="terminal-font max-h-48 overflow-auto whitespace-pre-wrap break-words text-muted-foreground">
                  {JSON.stringify(health.watchdog, null, 2)}
                </pre>
              ) : (
                <div className="text-muted-foreground">Watchdog state unavailable.</div>
              )}
            </div>
          </div>
        </div>

        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border p-4">
            <div className="flex items-center gap-2">
              <Activity className="h-4 w-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold">Diagnostician history</h2>
            </div>
            <span className="text-xs text-muted-foreground">
              {history?.entries.length ?? 0} most recent events
            </span>
          </div>
          <div className="max-h-96 overflow-auto bg-background/60 p-4">
            {history && history.entries.length > 0 ? (
              <pre className="terminal-font text-[11px] leading-relaxed text-muted-foreground">
                {history.entries.map((entry, i) => {
                  if (entry.raw) return <div key={i}>{entry.raw}</div>;
                  const isWarn = (entry.event ?? "").includes("fail") || (entry.event ?? "").includes("lock");
                  return (
                    <div key={i} className={isWarn ? "text-warning" : ""}>
                      {(entry.timestamp ?? "—") + "  " + (entry.event ?? "—") + "  " + (entry.detail ?? "")}
                    </div>
                  );
                })}
              </pre>
            ) : (
              <div className="text-center text-xs text-muted-foreground">No diagnostician events recorded.</div>
            )}
          </div>
        </div>
      </main>
    </>
  );
}

function ServiceCard({
  label,
  value,
  sub,
  ok,
  icon,
}: {
  label: string;
  value: string;
  sub: string;
  ok: boolean;
  icon?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span className="uppercase tracking-wider">{label}</span>
        {icon ?? (ok ? <CheckCircle2 className="h-4 w-4 text-success" /> : <XCircle className="h-4 w-4 text-destructive" />)}
      </div>
      <div className="mt-3 text-2xl font-semibold tracking-tight">{value}</div>
      <div className="mt-2 text-xs text-muted-foreground">{sub}</div>
    </div>
  );
}

import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/TopBar";
import { StatCard } from "@/components/StatCard";
import { Cpu, MemoryStick, Users, Zap, AlertTriangle, FileText, Activity } from "lucide-react";
import {
  getDetailedHealth,
  getAlerts,
  listUsers,
  listModels,
  type DetailedHealth,
  type AlertsResponse,
  type AuditEntry,
} from "@/lib/api";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [{ title: "Dashboard — LocallyAI" }],
  }),
  component: DashboardPage,
});

function formatTimeAgo(iso?: string): string {
  if (!iso) return "—";
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return iso;
  const diffMs = Date.now() - ts;
  const sec = Math.floor(diffMs / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} hr ago`;
  return `${Math.floor(hr / 24)} d ago`;
}

function DashboardPage() {
  const [health, setHealth] = useState<DetailedHealth | null>(null);
  const [alerts, setAlerts] = useState<AlertsResponse | null>(null);
  const [userCount, setUserCount] = useState<number | null>(null);
  const [modelCount, setModelCount] = useState<number | null>(null);
  const [primaryModel, setPrimaryModel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const [h, a, users, models] = await Promise.all([
          getDetailedHealth(),
          getAlerts(),
          listUsers(),
          listModels(),
        ]);
        if (cancelled) return;
        setHealth(h);
        setAlerts(a);
        setUserCount(users.length);
        setModelCount(models.length);
        setPrimaryModel(models[0]?.id ?? null);
        setError(null);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load dashboard");
      }
    };
    refresh();
    const interval = window.setInterval(refresh, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  const backendOk = health?.backend.reachable ?? false;
  const overallStatus = alerts?.status ?? "unknown";
  const last5: AuditEntry[] = health?.audit_log.last_5 ?? [];
  const totalQueries = health?.audit_log.line_count ?? 0;
  const diskFree = health?.disk_free_gb ?? null;

  return (
    <>
      <TopBar title="Dashboard" description="Overview of system status and recent activity" />
      <main className="flex-1 space-y-6 p-6">
        {error && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="System Status"
            value={
              <span className="flex items-center gap-2">
                <span
                  className={`status-dot ${
                    overallStatus === "ok"
                      ? "bg-success"
                      : overallStatus === "degraded"
                        ? "bg-warning"
                        : overallStatus === "critical"
                          ? "bg-destructive"
                          : "bg-muted-foreground"
                  }`}
                />
                {overallStatus === "ok"
                  ? "Healthy"
                  : overallStatus === "degraded"
                    ? "Degraded"
                    : overallStatus === "critical"
                      ? "Critical"
                      : "Loading…"}
              </span>
            }
            hint={backendOk ? "Inference backend reachable" : "Inference backend offline"}
            accent={overallStatus === "ok" ? "success" : undefined}
          />
          <StatCard
            label="Provisioned Users"
            value={userCount === null ? "—" : String(userCount)}
            hint="Includes the synthetic admin"
            icon={<Users className="h-4 w-4" />}
          />
          <StatCard
            label="Total Queries"
            value={totalQueries.toLocaleString()}
            hint="Audit log entries"
            icon={<Zap className="h-4 w-4" />}
          />
          <StatCard
            label="Disk Free"
            value={diskFree !== null ? `${diskFree} GB` : "—"}
            hint={modelCount !== null ? `${modelCount} model${modelCount === 1 ? "" : "s"} installed` : "Storage volume"}
            icon={<FileText className="h-4 w-4" />}
          />
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="rounded-lg border border-border bg-card p-5 lg:col-span-2">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold">Local Model Status</h2>
                <p className="text-xs text-muted-foreground">
                  {primaryModel ?? "No models loaded"}
                </p>
              </div>
              <span
                className={`rounded-md border px-2 py-0.5 text-xs ${
                  backendOk
                    ? "border-success/30 bg-success/10 text-success"
                    : "border-destructive/30 bg-destructive/10 text-destructive"
                }`}
              >
                {backendOk ? "Loaded" : "Offline"}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-4">
              <ModelMetric
                label="Disk Free"
                value={diskFree !== null ? `${diskFree} GB` : "—"}
                sub="Local storage"
                icon={<MemoryStick className="h-4 w-4" />}
              />
              <ModelMetric
                label="Backend"
                value={health ? (backendOk ? "Online" : "Offline") : "…"}
                sub="Ollama / MLX"
                icon={<Cpu className="h-4 w-4" />}
              />
              <ModelMetric
                label="Audit Log Size"
                value={
                  health?.audit_log.size_bytes
                    ? `${(health.audit_log.size_bytes / 1024).toFixed(1)} KB`
                    : "—"
                }
                sub={`${totalQueries} entries`}
                icon={<Activity className="h-4 w-4" />}
              />
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card p-5">
            <h2 className="mb-4 text-sm font-semibold">Alerts</h2>
            <div className="space-y-3">
              {alerts && alerts.alerts.length === 0 && (
                <div className="rounded-md border border-border p-3 text-xs text-muted-foreground">
                  No active alerts. Last health check just now.
                </div>
              )}
              {alerts?.alerts.map((a, i) => (
                <div
                  key={i}
                  className={`flex gap-3 rounded-md border p-3 ${
                    a.level === "critical"
                      ? "border-destructive/30 bg-destructive/5"
                      : a.level === "warning"
                        ? "border-warning/30 bg-warning/5"
                        : "border-border"
                  }`}
                >
                  <AlertTriangle
                    className={`h-4 w-4 shrink-0 ${
                      a.level === "critical"
                        ? "text-destructive"
                        : a.level === "warning"
                          ? "text-warning"
                          : "text-muted-foreground"
                    }`}
                  />
                  <div className="text-xs">
                    <div className="font-medium uppercase tracking-wider text-foreground">
                      {a.level}
                    </div>
                    <div className="mt-0.5 text-muted-foreground">{a.message}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border p-5">
            <h2 className="text-sm font-semibold">Recent Activity</h2>
            <a href="/audit" className="text-xs text-primary hover:underline">
              View audit log →
            </a>
          </div>
          {last5.length === 0 ? (
            <div className="px-5 py-8 text-center text-xs text-muted-foreground">
              No queries recorded yet.
            </div>
          ) : (
            <div className="divide-y divide-border">
              {[...last5].reverse().map((entry, i) => (
                <div key={i} className="grid grid-cols-12 gap-4 px-5 py-3 text-sm hover:bg-accent/30">
                  <div className="col-span-3 flex items-center gap-2 text-xs text-muted-foreground">
                    <div className="flex h-6 w-6 items-center justify-center rounded-full bg-secondary text-[10px] font-medium uppercase text-foreground">
                      {(entry.user_hash ?? "??").slice(0, 2)}
                    </div>
                    <span className="terminal-font">{(entry.user_hash ?? "—").slice(0, 8)}</span>
                  </div>
                  <div className="col-span-5 truncate text-foreground">
                    {entry.model ?? "—"}{" "}
                    <span className="terminal-font text-muted-foreground">
                      · {entry.query_hash ? entry.query_hash.slice(0, 10) : "—"}
                    </span>
                  </div>
                  <div className="col-span-2 truncate text-xs text-muted-foreground">
                    {entry.sources ?? 0} src · {Math.round(entry.latency_ms ?? 0)} ms
                  </div>
                  <div className="col-span-2 text-end text-xs text-muted-foreground">
                    {formatTimeAgo(entry.timestamp)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </main>
    </>
  );
}

function ModelMetric({
  label,
  value,
  sub,
  icon,
}: {
  label: string;
  value: string;
  sub: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="rounded-md border border-border bg-background/40 p-3">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        {icon}
      </div>
      <div className="mt-2 text-lg font-semibold tracking-tight">{value}</div>
      <div className="text-xs text-muted-foreground">{sub}</div>
    </div>
  );
}

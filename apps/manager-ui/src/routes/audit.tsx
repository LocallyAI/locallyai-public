import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/TopBar";
import { Calendar, Download, AlertTriangle, Loader2 } from "lucide-react";
import {
  getAuditSummary,
  downloadAuditCsv,
  getDetailedHealth,
  type AuditSummary,
  type AuditEntry,
} from "@/lib/api";

export const Route = createFileRoute("/audit")({
  head: () => ({ meta: [{ title: "Audit Log — LocallyAI" }] }),
  component: AuditPage,
});

function isoToday(offsetDays = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return d.toISOString().slice(0, 10);
}

function AuditPage() {
  const [fromDate, setFromDate] = useState(() => isoToday(-7));
  const [toDate, setToDate] = useState(() => isoToday(0));
  const [summary, setSummary] = useState<AuditSummary | null>(null);
  const [recent, setRecent] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, h] = await Promise.all([getAuditSummary(fromDate, toDate), getDetailedHealth()]);
      setSummary(s);
      setRecent(h.audit_log.last_5 ?? []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load audit data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const exportCsv = async () => {
    setDownloading(true);
    setError(null);
    try {
      const blob = await downloadAuditCsv(fromDate, toDate);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit_${fromDate}_${toDate}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "CSV export failed");
    } finally {
      setDownloading(false);
    }
  };

  const totalSources = summary
    ? Object.values(summary.by_user).reduce((acc, u) => acc + u.total_sources, 0)
    : 0;
  const avgLatencyAcrossUsers = summary
    ? (() => {
        const entries = Object.values(summary.by_user);
        if (entries.length === 0) return 0;
        return entries.reduce((acc, u) => acc + u.avg_latency_ms * u.queries, 0) / Math.max(1, summary.total_queries);
      })()
    : 0;

  return (
    <>
      <TopBar title="Audit Log" description="Tamper-evident record of all queries and document events" />
      <main className="flex-1 space-y-4 p-6">
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1.5">
            <Calendar className="h-3.5 w-3.5 text-muted-foreground" />
            <input
              type="date"
              value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
              className="bg-transparent text-xs outline-none"
            />
            <span className="text-xs text-muted-foreground">→</span>
            <input
              type="date"
              value={toDate}
              onChange={(e) => setToDate(e.target.value)}
              className="bg-transparent text-xs outline-none"
            />
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="rounded-md border border-border bg-secondary px-3 py-1.5 text-xs font-medium hover:bg-accent disabled:opacity-40"
          >
            {loading ? "Loading…" : "Refresh"}
          </button>
          <div className="ml-auto flex items-center gap-2">
            <span className="text-xs text-muted-foreground">
              {summary ? `${summary.total_queries} queries in range` : "—"}
            </span>
            <button
              onClick={exportCsv}
              disabled={downloading || loading}
              className="flex items-center gap-1.5 rounded-md border border-border bg-secondary px-3 py-1.5 text-xs font-medium hover:bg-accent disabled:opacity-40"
            >
              {downloading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
              Export CSV
            </button>
          </div>
        </div>

        {error && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <SummaryStat label="Queries in range" value={summary ? summary.total_queries.toLocaleString() : "—"} />
          <SummaryStat label="Total sources retrieved" value={summary ? totalSources.toLocaleString() : "—"} />
          <SummaryStat label="Avg latency (ms)" value={summary ? Math.round(avgLatencyAcrossUsers).toLocaleString() : "—"} />
        </div>

        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border p-4">
            <h2 className="text-sm font-semibold">By user (pseudonymised)</h2>
            <span className="text-xs text-muted-foreground">
              Hashes can be re-identified only with the audit salt
            </span>
          </div>
          {summary && Object.keys(summary.by_user).length > 0 ? (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
                  <th className="px-4 py-2 text-start font-medium">User hash</th>
                  <th className="px-4 py-2 text-end font-medium">Queries</th>
                  <th className="px-4 py-2 text-end font-medium">Total sources</th>
                  <th className="px-4 py-2 text-end font-medium">Avg latency (ms)</th>
                  <th className="px-4 py-2 text-start font-medium">Matter codes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {Object.entries(summary.by_user).map(([userHash, stats]) => (
                  <tr key={userHash} className="hover:bg-accent/30">
                    <td className="px-4 py-3 terminal-font text-xs">{userHash}</td>
                    <td className="px-4 py-3 text-end text-xs">{stats.queries.toLocaleString()}</td>
                    <td className="px-4 py-3 text-end text-xs">{stats.total_sources.toLocaleString()}</td>
                    <td className="px-4 py-3 text-end text-xs">{Math.round(stats.avg_latency_ms).toLocaleString()}</td>
                    <td className="px-4 py-3 text-xs text-muted-foreground">
                      {stats.matter_codes.length > 0 ? stats.matter_codes.join(", ") : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="px-4 py-10 text-center text-xs text-muted-foreground">
              {loading ? "Loading summary…" : "No queries recorded in this date range."}
            </div>
          )}
        </div>

        <div className="rounded-lg border border-border bg-card">
          <div className="border-b border-border p-4">
            <h2 className="text-sm font-semibold">Recent log entries</h2>
            <p className="text-xs text-muted-foreground">Last 5 entries from the live audit log</p>
          </div>
          {recent.length === 0 ? (
            <div className="px-4 py-10 text-center text-xs text-muted-foreground">No entries.</div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
                  <th className="px-4 py-2 text-start font-medium">Timestamp</th>
                  <th className="px-4 py-2 text-start font-medium">User hash</th>
                  <th className="px-4 py-2 text-start font-medium">Model</th>
                  <th className="px-4 py-2 text-end font-medium">Sources</th>
                  <th className="px-4 py-2 text-end font-medium">Latency (ms)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {[...recent].reverse().map((entry, i) => (
                  <tr key={i} className="hover:bg-accent/30">
                    <td className="px-4 py-2.5 terminal-font text-xs text-muted-foreground">
                      {entry.timestamp ?? "—"}
                    </td>
                    <td className="px-4 py-2.5 terminal-font text-xs">{entry.user_hash ?? "—"}</td>
                    <td className="px-4 py-2.5 text-xs">{entry.model ?? "—"}</td>
                    <td className="px-4 py-2.5 text-end text-xs terminal-font">{entry.sources ?? "—"}</td>
                    <td className="px-4 py-2.5 text-end text-xs terminal-font">
                      {entry.latency_ms !== undefined ? Math.round(entry.latency_ms) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </>
  );
}

function SummaryStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <div className="text-xs uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold tracking-tight">{value}</div>
    </div>
  );
}

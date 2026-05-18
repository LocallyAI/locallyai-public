import { useEffect, useState, useCallback } from "react";
import {
  getAdminKey,
  setAdminKey,
  clearAdminKey,
  getFleetNodes,
  getFleetAudit,
  getQdrantHealth,
  getSyncConflicts,
  getFleetAlerts,
  getFleetGate,
  type FleetNodesResp,
  type FleetAuditResp,
  type QdrantHealthResp,
  type SyncConflictsResp,
  type AlertsResp,
  type GateResp,
} from "./api";

const REFRESH_MS = 5000;

export function App() {
  const [authed, setAuthed] = useState<boolean>(!!getAdminKey());

  if (!authed) return <Login onAuthed={() => setAuthed(true)} />;
  return <Dashboard onSignOut={() => { clearAdminKey(); setAuthed(false); }} />;
}

function Login({ onAuthed }: { onAuthed: () => void }) {
  const [val, setVal] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const submit = async () => {
    setErr(null);
    if (val.length < 32) { setErr("Admin key must be at least 32 characters."); return; }
    setAdminKey(val);
    // probe one endpoint to validate
    try {
      await getFleetNodes();
      onAuthed();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <div style={{ maxWidth: 460, margin: "10vh auto", padding: 24,
                  background: "var(--panel)", border: "1px solid var(--border)",
                  borderRadius: 12 }}>
      <h1 style={{ marginTop: 0, fontSize: 22 }}>LocallyAI Fleet</h1>
      <p style={{ color: "var(--muted)" }}>
        Sign in with the <code>LOCALLYAI_ADMIN_KEY</code> from <code>.env</code>.
      </p>
      <input
        type="password"
        autoFocus
        placeholder="admin key"
        value={val}
        onChange={(e) => setVal(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && void submit()}
        style={{
          width: "100%", padding: "10px 12px",
          background: "#0b0d10", border: "1px solid var(--border)",
          borderRadius: 8, color: "var(--text)", marginBottom: 12,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        }}
      />
      <button
        onClick={() => void submit()}
        style={{
          width: "100%", padding: "10px", background: "var(--link)",
          color: "#0b0d10", border: 0, borderRadius: 8, fontWeight: 600,
          cursor: "pointer",
        }}
      >Sign in</button>
      {err && <p style={{ color: "var(--err)", marginTop: 12 }}>{err}</p>}
    </div>
  );
}

function Dashboard({ onSignOut }: { onSignOut: () => void }) {
  const [nodes, setNodes] = useState<FleetNodesResp | null>(null);
  const [audit, setAudit] = useState<FleetAuditResp | null>(null);
  const [qdrant, setQdrant] = useState<QdrantHealthResp | null>(null);
  const [conflicts, setConflicts] = useState<SyncConflictsResp | null>(null);
  const [alerts, setAlerts] = useState<AlertsResp | null>(null);
  const [gate, setGate] = useState<GateResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<number>(0);

  const refresh = useCallback(async () => {
    setErr(null);
    try {
      const [n, a, q, c, al, g] = await Promise.all([
        getFleetNodes(), getFleetAudit(), getQdrantHealth(),
        getSyncConflicts(), getFleetAlerts(), getFleetGate(),
      ]);
      setNodes(n); setAudit(a); setQdrant(q); setConflicts(c); setAlerts(al); setGate(g);
      setLastRefresh(Date.now());
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), REFRESH_MS);
    return () => clearInterval(t);
  }, [refresh]);

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "24px 20px 80px" }}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22 }}>LocallyAI · Fleet</h1>
          <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 2 }}>
            Auto-refresh every {REFRESH_MS / 1000}s
            {lastRefresh > 0 && <> · last {Math.round((Date.now() - lastRefresh) / 1000)}s ago</>}
          </div>
        </div>
        <button
          onClick={onSignOut}
          style={{
            padding: "6px 12px", background: "transparent",
            color: "var(--muted)", border: "1px solid var(--border)",
            borderRadius: 8, cursor: "pointer", fontSize: 12,
          }}
        >Sign out</button>
      </header>

      {err && (
        <div style={{ background: "#3a1212", color: "var(--err)",
                      padding: "10px 14px", borderRadius: 8, marginBottom: 16 }}>
          {err}
        </div>
      )}

      <Section title={`Nodes ${nodes ? `(${nodes.active_count}/${nodes.nodes.length} alive)` : ""}`}>
        {nodes && (
          <Table
            headers={["Node", "Hostname", "Backend", "Started", "Last seen", "Status"]}
            rows={nodes.nodes.map(n => [
              n.node_id + (n.node_id === nodes.this_node ? "  ·  this" : ""),
              n.hostname, n.backend, n.started_at, n.last_seen,
              n.alive ? <Badge tone="ok" key={n.node_id}>alive</Badge>
                      : <Badge tone="err" key={n.node_id}>offline</Badge>,
            ])}
          />
        )}
      </Section>

      <Section title={`Audit chain  ${audit ? `(${audit.fleet_status})` : ""}`}>
        {audit && (
          <Table
            headers={["Node", "Status", "Entries", "Detail"]}
            rows={audit.nodes.map(n => [
              n.node_id,
              <Badge tone={n.status === "ok" ? "ok" : "err"} key={n.node_id}>{n.status}</Badge>,
              n.entries ?? "—",
              n.reason || (n.broken_at_line ? `broken @ ${n.source}:${n.broken_at_line}` : ""),
            ])}
          />
        )}
      </Section>

      <Section title={`Qdrant  (${qdrant?.mode || "?"})`}>
        {qdrant && (
          <KV
            rows={[
              ["Mode", qdrant.mode],
              ["Raft state", qdrant.raft_state || "—"],
              ["Peer count", String(qdrant.peer_count ?? 0)],
              ["Peers", qdrant.peers?.length
                ? qdrant.peers.map(p => `${p.id}: ${p.uri}`).join("  ·  ")
                : "—"],
              ...(qdrant.reason ? [["Note", qdrant.reason]] : []),
            ]}
          />
        )}
      </Section>

      <Section title={`Sync conflicts  (${conflicts?.conflicts.length || 0})`}>
        {conflicts && (
          conflicts.conflicts.length === 0
            ? <Empty>None — shared store is clean.</Empty>
            : <Table
                headers={["File", "Size", "Quarantined at"]}
                rows={conflicts.conflicts.map(c => [c.name, fmtBytes(c.size), c.mtime])}
              />
        )}
      </Section>

      <Section title="Inference gate (concurrency)">
        {gate && (
          <Table
            headers={["Node", "In-flight / max", "Queued / max", "Peak queue", "Admitted", "Rejected (busy)"]}
            rows={gate.nodes.map(n => {
              const g = n.gate || {};
              const tone = (g.queued ?? 0) >= ((g.max_queue ?? 1) * 0.75)
                ? "warn"
                : (g.in_flight ?? 0) >= (g.max_inflight ?? 1)
                  ? "warn"
                  : "ok";
              return [
                n.node_id,
                n.unreachable ? <Badge tone="err" key={n.node_id}>unreachable</Badge>
                              : <Badge tone={tone} key={n.node_id}>{g.in_flight ?? 0} / {g.max_inflight ?? "?"}</Badge>,
                `${g.queued ?? 0} / ${g.max_queue ?? "?"}`,
                String(g.peak_queue ?? 0),
                String(g.total_admitted ?? 0),
                <span key={`r-${n.node_id}`} style={{
                  color: (g.total_rejected ?? 0) > 0 ? "var(--err)" : "var(--text)"
                }}>{g.total_rejected ?? 0}</span>,
              ];
            })}
          />
        )}
      </Section>

      <Section title="Alerts">
        {alerts && (
          alerts.nodes.every(n => Array.isArray((n.alerts as { alerts?: unknown[] })?.alerts)
            ? ((n.alerts as { alerts: unknown[] }).alerts.length === 0)
            : false)
            ? <Empty>All clear across the fleet.</Empty>
            : <Table
                headers={["Node", "Alerts"]}
                rows={alerts.nodes.map(n => [
                  n.node_id,
                  n.unreachable
                    ? <Badge tone="err" key={`${n.node_id}-x`}>unreachable: {n.unreachable.slice(0, 80)}</Badge>
                    : <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 11 }}>
                        {JSON.stringify(n.alerts, null, 2)}
                      </pre>,
                ])}
              />
        )}
      </Section>

      <footer style={{ color: "var(--muted)", fontSize: 11, marginTop: 24, textAlign: "center" }}>
        Fleet dashboard · this view is admin-only · per-node audit chains by design
      </footer>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ background: "var(--panel)", border: "1px solid var(--border)",
                      borderRadius: 12, padding: 16, marginBottom: 16 }}>
      <h2 style={{ margin: "0 0 12px", fontSize: 14, color: "var(--muted)",
                   textTransform: "uppercase", letterSpacing: 0.5 }}>{title}</h2>
      {children}
    </section>
  );
}

function Table({ headers, rows }: { headers: string[]; rows: React.ReactNode[][] }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr>{headers.map(h => (
            <th key={h} style={{ textAlign: "start", padding: "6px 10px",
                                 color: "var(--muted)", fontWeight: 500,
                                 borderBottom: "1px solid var(--border)" }}>{h}</th>
          ))}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((cell, j) => (
              <td key={j} style={{ padding: "8px 10px",
                                   borderBottom: "1px solid #1a2028" }}>{cell}</td>
            ))}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function KV({ rows }: { rows: (string | React.ReactNode)[][] }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "180px 1fr", rowGap: 6 }}>
      {rows.map((r, i) => (
        <>
          <div key={`k-${i}`} style={{ color: "var(--muted)" }}>{r[0]}</div>
          <div key={`v-${i}`}>{r[1]}</div>
        </>
      ))}
    </div>
  );
}

function Badge({ tone, children }: { tone: "ok" | "warn" | "err"; children: React.ReactNode }) {
  const colour = tone === "ok" ? "var(--ok)" : tone === "warn" ? "var(--warn)" : "var(--err)";
  return (
    <span style={{ color: colour, background: "rgba(255,255,255,0.04)",
                   padding: "2px 8px", borderRadius: 6, fontSize: 12,
                   border: `1px solid ${colour}33` }}>{children}</span>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div style={{ color: "var(--muted)", fontStyle: "italic" }}>{children}</div>;
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

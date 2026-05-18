import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { TopBar } from "@/components/TopBar";
import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  Plus,
  Search,
  ShieldAlert,
  Trash2,
  XCircle,
} from "lucide-react";
import {
  listRecentConflictChecks,
  runConflictCheck,
  type ConflictCheckResult,
  type ConflictLogEntry,
  type ConflictParty,
  type ConflictRole,
  type ConflictStatus,
} from "@/lib/api";

export const Route = createFileRoute("/conflicts")({
  head: () => ({ meta: [{ title: "Conflicts — LocallyAI" }] }),
  component: ConflictsPage,
});

const ROLES: ConflictRole[] = ["client", "opposing", "interested"];

function statusPill(status: ConflictStatus) {
  if (status === "conflict") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-destructive/15 px-2 py-0.5 text-[11px] font-semibold text-destructive">
        <XCircle className="h-3 w-3" /> Conflict
      </span>
    );
  }
  if (status === "review") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-warning/15 px-2 py-0.5 text-[11px] font-semibold text-warning">
        <ShieldAlert className="h-3 w-3" /> Review
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-success/15 px-2 py-0.5 text-[11px] font-semibold text-success">
      <CheckCircle2 className="h-3 w-3" /> Clear
    </span>
  );
}

function ConflictsPage() {
  const [parties, setParties] = useState<ConflictParty[]>([
    { role: "client", name: "" },
    { role: "opposing", name: "" },
  ]);
  const [description, setDescription] = useState("");
  const [opposingCounsel, setOpposingCounsel] = useState("");
  const [matterId, setMatterId] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ConflictCheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<ConflictLogEntry[]>([]);
  const [recentLoading, setRecentLoading] = useState(true);

  const refresh = async () => {
    setRecentLoading(true);
    try {
      const data = await listRecentConflictChecks(50);
      setRecent(data.checks || []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load recent checks");
    } finally {
      setRecentLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const updateParty = (idx: number, patch: Partial<ConflictParty>) => {
    setParties((ps) => ps.map((p, i) => (i === idx ? { ...p, ...patch } : p)));
  };

  const addParty = () =>
    setParties((ps) => [...ps, { role: "interested", name: "" }]);

  const removeParty = (idx: number) =>
    setParties((ps) => (ps.length > 1 ? ps.filter((_, i) => i !== idx) : ps));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const validParties = parties
      .map((p) => ({ ...p, name: p.name.trim() }))
      .filter((p) => p.name);
    if (validParties.length === 0) {
      setError("At least one party name is required.");
      return;
    }
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await runConflictCheck({
        parties: validParties,
        description: description.trim(),
        opposing_counsel: opposingCounsel
          .split(/[,\n]/)
          .map((s) => s.trim())
          .filter(Boolean),
        matter_id: matterId.trim() || undefined,
      });
      setResult(r);
      // Refresh recent log so the new entry appears
      refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Conflict check failed");
    } finally {
      setBusy(false);
    }
  };

  const sortedHits = useMemo(
    () => (result ? [...result.hits].sort((a, b) => b.score - a.score) : []),
    [result],
  );

  return (
    <>
      <TopBar
        title="Conflict checks"
        description="Run a first-pass conflict-of-interest check before opening a new matter"
      />
      <main className="flex-1 space-y-6 p-6">
        {error && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* ── Run a check ── */}
          <section className="rounded-lg border border-border bg-card">
            <header className="border-b border-border p-4">
              <h2 className="text-sm font-semibold">Run a check</h2>
              <p className="text-xs text-muted-foreground">
                Names are matched against the firm's matter corpus, then an
                LLM pass classifies the relationship. Advisory only — the
                partner's review is authoritative.
              </p>
            </header>
            <form onSubmit={submit} className="space-y-4 p-4">
              <div>
                <label className="block text-xs font-medium">Matter ID (optional)</label>
                <input
                  value={matterId}
                  onChange={(e) => setMatterId(e.target.value)}
                  placeholder="e.g. 2026-046"
                  className="mt-1 h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
                />
              </div>

              <div>
                <div className="flex items-center justify-between">
                  <label className="text-xs font-medium">Parties</label>
                  <button
                    type="button"
                    onClick={addParty}
                    className="flex items-center gap-1 text-[11px] text-primary hover:underline"
                  >
                    <Plus className="h-3 w-3" /> Add party
                  </button>
                </div>
                <div className="mt-2 space-y-2">
                  {parties.map((p, idx) => (
                    <div key={idx} className="flex gap-2">
                      <select
                        value={p.role}
                        onChange={(e) =>
                          updateParty(idx, { role: e.target.value as ConflictRole })
                        }
                        className="h-9 rounded-md border border-border bg-background px-2 text-xs outline-none focus:border-primary"
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                      <input
                        value={p.name}
                        onChange={(e) => updateParty(idx, { name: e.target.value })}
                        placeholder="Party name (e.g. Acme Ltd)"
                        className="h-9 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
                      />
                      {parties.length > 1 && (
                        <button
                          type="button"
                          onClick={() => removeParty(idx)}
                          className="rounded-md border border-border px-2 text-muted-foreground hover:bg-accent"
                          aria-label="Remove party"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium">Matter description</label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Short description of the proposed engagement"
                  rows={3}
                  className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
                />
              </div>

              <div>
                <label className="block text-xs font-medium">
                  Opposing counsel (comma-separated, optional)
                </label>
                <input
                  value={opposingCounsel}
                  onChange={(e) => setOpposingCounsel(e.target.value)}
                  placeholder="e.g. Smith &amp; Co LLP, Brown Lawyers"
                  className="mt-1 h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
                />
              </div>

              <button
                type="submit"
                disabled={busy}
                className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
              >
                <Search className="h-4 w-4" />
                {busy ? "Checking…" : "Run conflict check"}
              </button>
            </form>
          </section>

          {/* ── Result panel ── */}
          <section className="rounded-lg border border-border bg-card">
            <header className="border-b border-border p-4">
              <h2 className="text-sm font-semibold">Result</h2>
              <p className="text-xs text-muted-foreground">
                {result
                  ? `Checked at ${new Date(result.checked_at).toLocaleString()} · ${result.elapsed_ms} ms`
                  : "Submit a check to see the result here."}
              </p>
            </header>
            <div className="space-y-4 p-4">
              {!result && (
                <div className="rounded-md border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
                  No check run yet.
                </div>
              )}
              {result && (
                <>
                  <div className="flex items-start gap-3">
                    {statusPill(result.status)}
                    <div className="text-xs text-muted-foreground">
                      {result.summary}
                    </div>
                  </div>
                  {result.key_concerns.length > 0 && (
                    <div>
                      <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                        Key concerns
                      </div>
                      <ul className="mt-1 list-inside list-disc space-y-1 text-xs">
                        {result.key_concerns.map((k, i) => (
                          <li key={i}>{k}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {result.recommended_action && (
                    <div className="rounded-md border border-border bg-secondary/30 p-3 text-xs">
                      <div className="font-medium">Recommended action</div>
                      <div className="mt-1 text-muted-foreground">
                        {result.recommended_action}
                      </div>
                    </div>
                  )}
                  <div>
                    <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                      Hits ({sortedHits.length})
                    </div>
                    {sortedHits.length === 0 ? (
                      <div className="mt-1 text-xs text-muted-foreground">
                        No related-matter hits found.
                      </div>
                    ) : (
                      <ul className="mt-2 space-y-2">
                        {sortedHits.map((h, i) => (
                          <li
                            key={i}
                            className="rounded-md border border-border p-3 text-xs"
                          >
                            <div className="flex items-start justify-between gap-2">
                              <div className="flex items-center gap-2">
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                                    h.bucket === "strong"
                                      ? "bg-warning/20 text-warning"
                                      : "bg-secondary text-muted-foreground"
                                  }`}
                                >
                                  {h.bucket}
                                </span>
                                <code className="terminal-font text-[11px]">
                                  {h.source}
                                </code>
                              </div>
                              <span className="text-[10px] text-muted-foreground">
                                score {h.score.toFixed(3)}
                              </span>
                            </div>
                            {h.matter_code && (
                              <div className="mt-1 text-[11px] text-muted-foreground">
                                matter <code>{h.matter_code}</code>
                              </div>
                            )}
                            <div className="mt-2 line-clamp-3 text-muted-foreground">
                              {h.snippet}
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </>
              )}
            </div>
          </section>
        </div>

        {/* ── Recent log ── */}
        <section className="rounded-lg border border-border bg-card">
          <header className="flex items-center justify-between border-b border-border p-4">
            <div>
              <h2 className="text-sm font-semibold">Recent checks</h2>
              <p className="text-xs text-muted-foreground">
                Party names are pseudonymised at rest; only the operator who
                ran the check sees the originals.
              </p>
            </div>
            <button
              onClick={refresh}
              className="rounded-md border border-border bg-secondary px-2 py-1 text-xs hover:bg-accent"
            >
              Refresh
            </button>
          </header>
          {recentLoading ? (
            <div className="p-6 text-center text-xs text-muted-foreground">Loading…</div>
          ) : recent.length === 0 ? (
            <div className="p-6 text-center text-xs text-muted-foreground">
              No checks recorded yet.
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
                  <th className="px-4 py-2 text-start font-medium">When</th>
                  <th className="px-4 py-2 text-start font-medium">Status</th>
                  <th className="px-4 py-2 text-start font-medium">Matter</th>
                  <th className="px-4 py-2 text-start font-medium">Parties</th>
                  <th className="px-4 py-2 text-end font-medium">Strong / Weak</th>
                  <th className="px-4 py-2 text-start font-medium">Decision</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {recent.map((e, i) => (
                  <tr key={i} className="hover:bg-accent/30">
                    <td className="px-4 py-2 text-xs text-muted-foreground">
                      {new Date(e.timestamp).toLocaleString()}
                    </td>
                    <td className="px-4 py-2">{statusPill(e.status)}</td>
                    <td className="px-4 py-2">
                      {e.matter_id ? (
                        <code className="terminal-font text-[11px]">{e.matter_id}</code>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex items-center gap-1 text-xs text-muted-foreground">
                        <Eye className="h-3 w-3" />
                        {e.parties_hashed.length} pseudonymised
                      </div>
                    </td>
                    <td className="px-4 py-2 text-end text-xs">
                      <span className="text-warning">{e.hit_count_strong}</span>
                      <span className="text-muted-foreground">
                        {" / "}
                        {e.hit_count_weak}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-xs text-muted-foreground">
                      {e.decision === "pending" ? "Pending" : e.decision}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </main>
    </>
  );
}

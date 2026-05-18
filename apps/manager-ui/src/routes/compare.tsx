import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { TopBar } from "@/components/TopBar";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ClipboardPaste,
  FileText,
  GitCompareArrows,
} from "lucide-react";
import {
  compareDocuments,
  listDocuments,
  type CompareResult,
  type CompareSection,
  type CompareSignificance,
  type DocumentInfo,
} from "@/lib/api";

export const Route = createFileRoute("/compare")({
  head: () => ({ meta: [{ title: "Compare documents — LocallyAI" }] }),
  component: ComparePage,
});

function significancePill(s: CompareSignificance | undefined) {
  if (s === "high") {
    return (
      <span className="rounded-full bg-destructive/15 px-2 py-0.5 text-[10px] font-semibold text-destructive">
        HIGH
      </span>
    );
  }
  if (s === "medium") {
    return (
      <span className="rounded-full bg-warning/15 px-2 py-0.5 text-[10px] font-semibold text-warning">
        MEDIUM
      </span>
    );
  }
  if (s === "low") {
    return (
      <span className="rounded-full bg-success/15 px-2 py-0.5 text-[10px] font-semibold text-success">
        LOW
      </span>
    );
  }
  return null;
}

function changeTypeBadge(t: CompareSection["change_type"]) {
  const map: Record<CompareSection["change_type"], { label: string; cls: string }> = {
    added:             { label: "added",      cls: "bg-success/15 text-success" },
    removed:           { label: "removed",    cls: "bg-destructive/15 text-destructive" },
    rewritten:         { label: "rewritten",  cls: "bg-warning/15 text-warning" },
    "whitespace-only": { label: "whitespace", cls: "bg-secondary text-muted-foreground" },
  };
  const { label, cls } = map[t];
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase ${cls}`}>{label}</span>
  );
}

function ComparePage() {
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [docsLoading, setDocsLoading] = useState(true);
  const [mode, setMode] = useState<"docs" | "paste">("docs");
  const [docA, setDocA] = useState("");
  const [docB, setDocB] = useState("");
  const [textA, setTextA] = useState("");
  const [textB, setTextB] = useState("");
  const [labelA, setLabelA] = useState("Version A");
  const [labelB, setLabelB] = useState("Version B");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CompareResult | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  useEffect(() => {
    (async () => {
      try {
        const r = await listDocuments();
        setDocs(r.data || []);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Failed to load documents");
      } finally {
        setDocsLoading(false);
      }
    })();
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setResult(null);
    if (mode === "docs") {
      if (!docA || !docB) {
        setError("Pick two documents to compare.");
        return;
      }
      if (docA === docB) {
        setError("Pick two different documents.");
        return;
      }
    } else {
      if (!textA.trim() || !textB.trim()) {
        setError("Both text bodies are required.");
        return;
      }
    }
    setBusy(true);
    try {
      const r = await compareDocuments(
        mode === "docs"
          ? { doc_a: docA, doc_b: docB }
          : { text_a: textA, text_b: textB, label_a: labelA, label_b: labelB },
      );
      setResult(r);
      setExpanded(new Set());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Compare failed");
    } finally {
      setBusy(false);
    }
  };

  const toggleSection = (idx: number) => {
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const headlineCounts = useMemo(() => {
    if (!result) return null;
    const counts = { high: 0, medium: 0, low: 0 };
    for (const s of result.sections) {
      const sig = s.commentary?.significance;
      if (sig) counts[sig] += 1;
    }
    return counts;
  }, [result]);

  return (
    <>
      <TopBar
        title="Compare documents"
        description="Diff two drafts and get per-clause significance commentary"
      />
      <main className="flex-1 space-y-6 p-6">
        {error && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <section className="rounded-lg border border-border bg-card">
          <header className="border-b border-border p-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold">Set up a comparison</h2>
                <p className="text-xs text-muted-foreground">
                  Pick two ingested documents, or paste two text bodies for an
                  external-draft compare.
                </p>
              </div>
              <div className="flex rounded-md border border-border bg-secondary p-0.5 text-xs">
                <button
                  onClick={() => setMode("docs")}
                  className={`flex items-center gap-1 rounded px-2 py-1 ${
                    mode === "docs" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"
                  }`}
                >
                  <FileText className="h-3 w-3" />
                  Ingested docs
                </button>
                <button
                  onClick={() => setMode("paste")}
                  className={`flex items-center gap-1 rounded px-2 py-1 ${
                    mode === "paste" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"
                  }`}
                >
                  <ClipboardPaste className="h-3 w-3" />
                  Paste-compare
                </button>
              </div>
            </div>
          </header>

          <form onSubmit={submit} className="space-y-4 p-4">
            {mode === "docs" ? (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <label className="block text-xs">
                  <span className="font-medium">Document A</span>
                  <select
                    value={docA}
                    onChange={(e) => setDocA(e.target.value)}
                    className="mt-1 h-9 w-full rounded-md border border-border bg-background px-2 text-sm outline-none focus:border-primary"
                    disabled={docsLoading}
                  >
                    <option value="">— select —</option>
                    {docs.map((d) => (
                      <option key={d.name} value={d.name}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block text-xs">
                  <span className="font-medium">Document B</span>
                  <select
                    value={docB}
                    onChange={(e) => setDocB(e.target.value)}
                    className="mt-1 h-9 w-full rounded-md border border-border bg-background px-2 text-sm outline-none focus:border-primary"
                    disabled={docsLoading}
                  >
                    <option value="">— select —</option>
                    {docs.map((d) => (
                      <option key={d.name} value={d.name}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <div className="space-y-2">
                  <input
                    value={labelA}
                    onChange={(e) => setLabelA(e.target.value)}
                    placeholder="Label A"
                    className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
                  />
                  <textarea
                    value={textA}
                    onChange={(e) => setTextA(e.target.value)}
                    placeholder="Paste version A here"
                    rows={10}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
                  />
                </div>
                <div className="space-y-2">
                  <input
                    value={labelB}
                    onChange={(e) => setLabelB(e.target.value)}
                    placeholder="Label B"
                    className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
                  />
                  <textarea
                    value={textB}
                    onChange={(e) => setTextB(e.target.value)}
                    placeholder="Paste version B here"
                    rows={10}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
                  />
                </div>
              </div>
            )}

            <button
              type="submit"
              disabled={busy}
              className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
            >
              <GitCompareArrows className="h-4 w-4" />
              {busy ? "Comparing…" : "Run comparison"}
            </button>
          </form>
        </section>

        {result && (
          <section className="rounded-lg border border-border bg-card">
            <header className="space-y-2 border-b border-border p-4">
              <div className="flex items-center gap-2">
                <span
                  className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                    result.verdict === "material-changes"
                      ? "bg-destructive/15 text-destructive"
                      : result.verdict === "minor-changes"
                        ? "bg-warning/15 text-warning"
                        : "bg-success/15 text-success"
                  }`}
                >
                  {result.verdict.replace("-", " ")}
                </span>
                <span className="text-sm font-semibold">
                  {result.label_a} vs {result.label_b}
                </span>
              </div>
              <p className="text-xs text-muted-foreground">{result.summary}</p>
              <div className="flex gap-4 text-[11px] text-muted-foreground">
                <span>{result.sections.length} changed section(s)</span>
                <span>
                  Significance: {headlineCounts?.high ?? 0}H ·{" "}
                  {headlineCounts?.medium ?? 0}M · {headlineCounts?.low ?? 0}L
                </span>
                <span>{result.elapsed_ms} ms</span>
              </div>
            </header>

            {result.sections.length === 0 ? (
              <div className="p-6 text-center text-sm text-muted-foreground">
                No textual differences between the two versions.
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {result.sections.map((s, i) => {
                  const open = expanded.has(i);
                  const heading = s.heading_b || s.heading_a || `(section ${i + 1})`;
                  return (
                    <li key={i} className="p-4">
                      <button
                        onClick={() => toggleSection(i)}
                        className="flex w-full items-start gap-3 text-left"
                      >
                        {open ? (
                          <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                        ) : (
                          <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                        )}
                        <div className="flex-1 min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-medium">{heading}</span>
                            {changeTypeBadge(s.change_type)}
                            {significancePill(s.commentary?.significance)}
                          </div>
                          {s.commentary?.summary && (
                            <p className="mt-1 text-xs text-muted-foreground">
                              {s.commentary.summary}
                            </p>
                          )}
                        </div>
                      </button>
                      {open && (
                        <div className="mt-3 space-y-3 pl-7">
                          {s.commentary?.why_matters && (
                            <div className="rounded-md border border-border bg-secondary/30 p-3 text-xs">
                              <div className="font-medium">Why it matters</div>
                              <div className="mt-1 text-muted-foreground">
                                {s.commentary.why_matters}
                              </div>
                              {s.commentary.watch_for?.length > 0 && (
                                <ul className="mt-2 list-inside list-disc space-y-1">
                                  {s.commentary.watch_for.map((w, j) => (
                                    <li key={j} className="text-muted-foreground">{w}</li>
                                  ))}
                                </ul>
                              )}
                            </div>
                          )}
                          {s.diff && (
                            <pre className="terminal-font max-h-80 overflow-auto rounded-md border border-border bg-background p-3 text-[11px] leading-snug">
                              {s.diff}
                            </pre>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        )}
      </main>
    </>
  );
}

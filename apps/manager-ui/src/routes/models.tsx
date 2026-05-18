// Models page — pick the LLM the firm runs. Curated list (vendor-vetted)
// to prevent an admin from accidentally selecting a model that ships
// custom inference code with trust_remote_code=True.

import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/TopBar";
import {
  Cpu, CheckCircle2, Loader2, Download, AlertTriangle, RefreshCw,
} from "lucide-react";
import { listLlmModels, selectLlmModel, type ModelsResponse } from "@/lib/api";

export const Route = createFileRoute("/models")({
  head: () => ({ meta: [{ title: "Models — LocallyAI" }] }),
  component: ModelsPage,
});

function ModelsPage() {
  const [data, setData] = useState<ModelsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const load = async () => {
    try { setData(await listLlmModels()); setError(null); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed to load"); }
  };
  useEffect(() => {
    void load();
    // Poll while a download is in flight so the UI flips to "active" without manual refresh.
    const t = setInterval(() => { if (data?.download.in_flight) void load(); }, 3000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.download.in_flight]);

  const onSelect = async (id: string) => {
    setError(null);
    try {
      const r = await selectLlmModel(id);
      if (!r.accepted) setError(r.detail);
      else { setConfirmId(null); await load(); }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Select failed");
    }
  };

  return (
    <>
      <TopBar
        title="LLM Models"
        description="Pick the language model this firm runs. Switching downloads the new weights and restarts the API."
      />
      <main className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-5xl space-y-6">
          {/* Status header */}
          {data && (
            <div className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-center gap-3">
                <Cpu className="h-5 w-5 text-primary" />
                <div className="min-w-0 flex-1">
                  <div className="text-xs text-muted-foreground">Currently active</div>
                  <div className="terminal-font text-sm font-semibold truncate">{data.current || "(none)"}</div>
                </div>
                <button
                  onClick={() => void load()}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border bg-secondary px-3 py-1.5 text-xs font-medium hover:bg-accent"
                >
                  <RefreshCw className="h-3.5 w-3.5" /> Refresh
                </button>
              </div>
              {data.download.in_flight && (
                <div className="mt-3 rounded-md border border-primary/30 bg-primary/5 p-3 text-xs">
                  <div className="flex items-center gap-2 text-primary">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    <span className="font-semibold">Downloading {data.download.in_flight}</span>
                  </div>
                  <div className="mt-2 max-h-40 overflow-y-auto terminal-font text-[10.5px] text-muted-foreground">
                    {data.download.log_tail.map((l, i) => <div key={i}>{l}</div>)}
                  </div>
                </div>
              )}
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* Curated grid */}
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {(data?.models ?? []).map((m) => (
              <div key={m.id} className={`rounded-lg border p-4 ${m.active ? "border-primary/40 bg-primary/5" : "border-border bg-card"}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-sm font-semibold">{m.label}</div>
                    <div className="terminal-font mt-1 text-[10.5px] text-muted-foreground truncate">{m.id}</div>
                  </div>
                  {m.active && (
                    <span className="inline-flex items-center gap-1 rounded bg-primary/15 px-2 py-0.5 text-[10px] text-primary">
                      <CheckCircle2 className="h-2.5 w-2.5" /> active
                    </span>
                  )}
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
                  <div><span className="text-muted-foreground">Disk: </span>{m.approx_disk_gb} GB</div>
                  <div><span className="text-muted-foreground">RAM: </span>{m.approx_ram_gb} GB</div>
                </div>
                <div className="mt-2 flex flex-wrap gap-1">
                  {m.languages.map((l) => (
                    <span key={l} className="rounded bg-secondary px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground">{l}</span>
                  ))}
                </div>
                {m.notes && <div className="mt-2 text-[11px] text-muted-foreground">{m.notes}</div>}
                <div className="mt-3 flex items-center justify-between gap-2">
                  <span className="text-[10.5px] text-muted-foreground">
                    {m.downloaded ? "downloaded" : "will download on select"}
                  </span>
                  {m.active ? (
                    <span className="text-[10.5px] text-success">in use</span>
                  ) : confirmId === m.id ? (
                    <div className="flex items-center gap-1.5">
                      <button onClick={() => onSelect(m.id)}
                        disabled={!!data?.download.in_flight}
                        className="rounded-md border border-destructive/40 bg-destructive/10 px-2 py-0.5 text-[11px] text-destructive disabled:opacity-40">
                        Confirm switch
                      </button>
                      <button onClick={() => setConfirmId(null)}
                        className="rounded-md border border-border bg-secondary px-2 py-0.5 text-[11px]">
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setConfirmId(m.id)}
                      disabled={!!data?.download.in_flight}
                      className="inline-flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
                    >
                      <Download className="h-3 w-3" />
                      {m.downloaded ? "Activate" : "Download + activate"}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>

          <div className="rounded-lg border border-border bg-card/40 p-4 text-xs text-muted-foreground">
            <div className="flex items-center gap-2 text-foreground">
              <CheckCircle2 className="h-3.5 w-3.5 text-success" />
              <span className="text-sm font-semibold">How model switching works</span>
            </div>
            <ol className="mt-3 list-decimal space-y-1.5 ps-5">
              <li>Click <strong>Download + activate</strong>; the model is downloaded via huggingface-hub (resumable, cached at <code>~/.cache/huggingface</code>).</li>
              <li><code>MLX_MODEL</code> is rewritten in <code>.env</code> in place (comments preserved, perms 0600).</li>
              <li>The API is restarted via <code>launchctl kickstart</code>; the new weights load on first inference.</li>
              <li>The previous model stays cached on disk — switching back is instant.</li>
            </ol>
            <p className="mt-3">
              For an off-list model, edit <code>MLX_MODEL</code> in <code>.env</code> directly. We curate this list to
              prevent accidentally selecting a model that ships custom inference code via
              <code> trust_remote_code=True</code>.
            </p>
          </div>
        </div>
      </main>
    </>
  );
}

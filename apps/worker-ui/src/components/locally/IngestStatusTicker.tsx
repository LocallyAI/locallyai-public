// Live "Indexing N of M" indicator. Polls /v1/ingest/status every 2s
// while there's anything in flight or pending BM25; backs off to silent
// (renders nothing) when the queue is idle.
//
// Single-source-of-truth for indexing visibility — the upload chip shows
// upload progress, this component shows server-side indexing progress.
// They overlap briefly during the "completing" → "queued for indexing"
// transition.

import { useEffect, useState } from "react";
import { getIngestStatus, flushIngest, type IngestStatusResponse } from "@/lib/api";
import { Loader2, CheckCircle2, ListChecks } from "lucide-react";
import { t } from "@/lib/i18n";

const POLL_MS_BUSY = 2000;
const POLL_MS_IDLE = 10_000;

export function IngestStatusTicker() {
  const [status, setStatus] = useState<IngestStatusResponse | null>(null);
  const [flushing, setFlushing] = useState(false);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      try {
        const s = await getIngestStatus();
        if (!alive) return;
        setStatus(s);
        const busy = s.in_flight > 0 || s.queued > 0 || s.bm25_pending;
        timer = setTimeout(tick, busy ? POLL_MS_BUSY : POLL_MS_IDLE);
      } catch {
        // Backend down / not auth'd — back off, don't spam.
        timer = setTimeout(tick, POLL_MS_IDLE * 3);
      }
    };
    void tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, []);

  if (!status) return null;
  const busy = status.in_flight > 0 || status.queued > 0 || status.bm25_pending;
  if (!busy) return null;

  const totalPending = status.in_flight + status.queued;
  const stage = status.in_flight > 0
    ? t("ingest.indexing", "Indexing")
    : status.bm25_pending
      ? t("ingest.search_index", "Updating search index")
      : t("ingest.queued", "Queued");

  return (
    <div className="mx-auto w-full max-w-3xl px-6 pb-1">
      <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-surface/50 px-3 py-1.5 text-[11.5px] text-muted-foreground animate-in fade-in duration-200">
        <div className="flex items-center gap-2">
          {status.in_flight > 0 ? (
            <Loader2 className="h-3 w-3 animate-spin text-primary" />
          ) : status.bm25_pending ? (
            <ListChecks className="h-3 w-3 text-primary" />
          ) : (
            <CheckCircle2 className="h-3 w-3 text-primary" />
          )}
          <span>
            {stage}
            {totalPending > 0 && (
              <span className="ms-1 tabular-nums text-foreground">
                {totalPending} {totalPending === 1 ? t("ingest.doc_one", "doc") : t("ingest.doc_many", "docs")}
              </span>
            )}
            {status.completed_total > 0 && (
              <span className="ms-2 text-muted-foreground/70">
                · {status.completed_total} {t("ingest.done_total", "done")}
              </span>
            )}
            {status.failed_total > 0 && (
              <span className="ms-2 text-destructive/80">
                · {status.failed_total} {t("ingest.failed_total", "failed")}
              </span>
            )}
          </span>
        </div>
        {status.bm25_pending && status.in_flight === 0 && status.queued === 0 && (
          <button
            onClick={async () => {
              setFlushing(true);
              try { await flushIngest(); } finally { setFlushing(false); }
            }}
            disabled={flushing}
            className="rounded px-2 py-0.5 text-[11px] font-medium text-primary transition-colors hover:bg-primary/10 disabled:opacity-50"
          >
            {flushing ? t("ingest.rebuilding", "Rebuilding…") : t("ingest.rebuild_now", "Rebuild now")}
          </button>
        )}
      </div>
    </div>
  );
}

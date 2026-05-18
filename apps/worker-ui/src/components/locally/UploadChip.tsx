// Inline chip(s) shown above the composer for in-flight uploads. One chip
// per file. Shows filename, file-type icon, byte progress, percentage, and
// pause/resume/cancel controls. Auto-dismisses 4s after done, 8s after error,
// 1.5s after cancel.

import {
  useUploads, dismissUpload, pauseUpload, resumeUpload, cancelUpload,
  type UploadItem,
} from "@/hooks/use-uploads";
import { FileTypeIcon } from "./FileTypeIcon";
import { Check, X, AlertCircle, Loader2, Pause, Play, Hash } from "lucide-react";

export function UploadChips() {
  const items = useUploads();
  if (items.length === 0) return null;
  return (
    <div className="mx-auto w-full max-w-3xl px-6 pb-2">
      <div className="flex flex-col gap-1.5">
        {items.map((item) => (
          <UploadChip key={item.id} item={item} />
        ))}
      </div>
    </div>
  );
}

function UploadChip({ item }: { item: UploadItem }) {
  const pct = item.sizeBytes > 0 ? Math.min(100, Math.round((item.loadedBytes / item.sizeBytes) * 100)) : 0;
  const ext = item.name.split(".").pop()?.toLowerCase() ?? "";
  const isDone      = item.status === "done";
  const isError     = item.status === "error";
  const isCancelled = item.status === "cancelled";
  const isPaused    = item.status === "paused";
  const isHashing   = item.status === "hashing";
  const isCompleting = item.status === "completing";
  const isActive    = !isDone && !isError && !isCancelled;
  const isUploading = item.status === "uploading";

  return (
    <div
      className={[
        "group relative flex items-center gap-3 overflow-hidden rounded-lg border bg-surface/80 px-3 py-2",
        "transition-colors",
        isError ? "border-destructive/40" :
        isDone ? "border-primary/40" :
        isPaused ? "border-yellow-500/40" :
        "border-border",
        "animate-in slide-in-from-bottom-1 fade-in duration-200",
      ].join(" ")}
    >
      <FileTypeIcon ext={ext} className="h-4 w-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-[12px] font-medium text-foreground">{item.name}</span>
          <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
            {isError ? "" :
             isHashing ? "hashing…" :
             isCompleting ? "verifying…" :
             `${formatBytes(item.loadedBytes)} / ${formatBytes(item.sizeBytes)} · ${pct}%`}
          </span>
        </div>
        <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-accent/40">
          <div
            className={[
              "h-full transition-all",
              isError ? "bg-destructive" :
              isDone ? "bg-primary" :
              isPaused ? "bg-yellow-500" :
              isHashing ? "bg-muted-foreground/60 animate-pulse" :
              "bg-foreground/60",
            ].join(" ")}
            style={{ width: `${isDone || isError ? 100 : pct}%` }}
          />
        </div>
        {isError && (
          <div className="mt-1 truncate text-[11px] text-destructive">{item.error}</div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        {isHashing  && <Hash       className="h-3.5 w-3.5 animate-pulse text-muted-foreground" />}
        {isUploading && <Loader2   className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        {isCompleting && <Loader2  className="h-3.5 w-3.5 animate-spin text-primary" />}
        {isDone     && <Check      className="h-3.5 w-3.5 text-primary" />}
        {isError    && <AlertCircle className="h-3.5 w-3.5 text-destructive" />}

        {/* Pause / resume only for active transfers (not hashing/completing). */}
        {(isUploading || isPaused) && (
          <button
            onClick={() => (isPaused ? resumeUpload(item.id) : pauseUpload(item.id))}
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            aria-label={isPaused ? "Resume" : "Pause"}
            title={isPaused ? "Resume" : "Pause"}
          >
            {isPaused ? <Play className="h-3 w-3" /> : <Pause className="h-3 w-3" />}
          </button>
        )}
        {/* Cancel for anything still in-flight; dismiss for terminal states. */}
        {isActive ? (
          <button
            onClick={() => cancelUpload(item.id)}
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            aria-label="Cancel"
            title="Cancel"
          >
            <X className="h-3 w-3" />
          </button>
        ) : (
          <button
            onClick={() => dismissUpload(item.id)}
            className="rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-accent hover:text-foreground group-hover:opacity-100"
            aria-label="Dismiss"
          >
            <X className="h-3 w-3" />
          </button>
        )}
      </div>
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024)             return `${n} B`;
  if (n < 1024 * 1024)      return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3)        return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

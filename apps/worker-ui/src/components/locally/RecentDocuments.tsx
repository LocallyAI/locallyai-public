// Popover triggered by Composer's "Recent documents" button. Lists the
// firm's ingested corpus newest-first with file-type icons + indexing
// badge. Refreshes when opened. No infinite scroll — for >50 docs the
// firm's IT-ops navigates directly via the manager UI's documents
// page; this popover is a quick-glance.

import { useEffect, useState } from "react";
import { listDocuments, type DocumentInfo } from "@/lib/api";
import { FileTypeIcon } from "./FileTypeIcon";
import { Loader2, Inbox } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { t } from "@/lib/i18n";

interface Props {
  trigger: React.ReactNode;
}

export function RecentDocuments({ trigger }: Props) {
  const [open, setOpen] = useState(false);
  const [docs, setDocs] = useState<DocumentInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setDocs(null);
    setError(null);
    listDocuments()
      .then((r) => { if (alive) setDocs(r.data); })
      .catch((e: Error) => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [open]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent
        align="start"
        sideOffset={8}
        className="w-[360px] max-h-[420px] overflow-hidden p-0"
      >
        <div className="border-b border-border px-3 py-2.5">
          <div className="text-[13px] font-semibold">
            {t("docs.title", "Documents in this workspace")}
          </div>
          <div className="text-[11px] text-muted-foreground">
            {docs ? `${docs.length} ${docs.length === 1 ? "document" : "documents"}` : ""}
          </div>
        </div>

        <div className="max-h-[360px] overflow-y-auto p-1">
          {/* Loading state */}
          {!docs && !error && (
            <div className="flex items-center justify-center gap-2 py-12 text-[12px] text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {t("docs.loading", "Loading documents…")}
            </div>
          )}

          {/* Error state */}
          {error && (
            <div className="px-3 py-8 text-center text-[12px] text-destructive">
              {error}
            </div>
          )}

          {/* Empty state */}
          {docs && docs.length === 0 && (
            <div className="flex flex-col items-center justify-center gap-3 px-4 py-12 text-center">
              <Inbox className="h-8 w-8 text-muted-foreground/60" />
              <div>
                <div className="text-[13px] font-medium text-foreground">
                  {t("docs.empty_title", "No documents yet")}
                </div>
                <div className="mt-1 text-[11px] text-muted-foreground">
                  {t("docs.empty_hint", "Drag a PDF, DOCX, TXT, or MD into the window to ingest it.")}
                </div>
              </div>
            </div>
          )}

          {/* List */}
          {docs && docs.length > 0 && (
            <ul className="divide-y divide-border/40">
              {docs.map((d) => (
                <li key={d.name} className="flex items-start gap-2.5 px-3 py-2">
                  <FileTypeIcon ext={d.suffix} className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[12.5px] font-medium text-foreground">{d.name}</div>
                    <div className="mt-0.5 flex items-center gap-2 text-[10.5px] text-muted-foreground">
                      <span>{formatBytes(d.size_bytes)}</span>
                      <span>·</span>
                      <span>{relativeTime(d.ingested_at)}</span>
                      {d.indexed ? (
                        <span className="ms-auto rounded bg-primary/15 px-1.5 py-0.5 text-[9.5px] font-medium text-primary">
                          indexed
                        </span>
                      ) : (
                        <span className="ms-auto rounded bg-muted px-1.5 py-0.5 text-[9.5px] font-medium text-muted-foreground">
                          pending
                        </span>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function relativeTime(iso: string): string {
  try {
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 60_000)        return "just now";
    if (ms < 3_600_000)     return `${Math.floor(ms / 60_000)} min ago`;
    if (ms < 86_400_000)    return `${Math.floor(ms / 3_600_000)} hr ago`;
    if (ms < 7 * 86_400_000) return `${Math.floor(ms / 86_400_000)} d ago`;
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}

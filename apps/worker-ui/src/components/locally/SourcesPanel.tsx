import { ExternalLink, X } from "lucide-react";
import { t } from "@/lib/i18n";
import { FileTypeIcon } from "./FileTypeIcon";
import { openCitedDocument } from "@/lib/api";

async function openSource(s: Source) {
  try {
    await openCitedDocument(s.title, s.pageNum ?? null);
  } catch (e) {
    // Surface a minimal alert; full error handling stays in the parent
    // (the worker-ui's toast system can be wired later).
    alert(`Could not open document: ${e instanceof Error ? e.message : "unknown error"}`);
  }
}

// SECURITY: snippet contains attacker-controlled document content (anything
// that lands in data/ becomes a citation). NEVER render snippet, title, or
// type via dangerouslySetInnerHTML or a Markdown renderer that allows raw
// HTML. React text-node escaping is the only thing keeping prompt-injection
// in a document from becoming XSS in the UI. Adding a Markdown renderer
// here later? Use one with HTML disabled (e.g. react-markdown with
// `disallowedElements={["script","iframe","object","embed"]}` and
// `allowedAttributes={...}`) and run snippets through DOMPurify first.
export type Source = {
  id: string;
  /** File name shown in the "title" row + the `filename` query
   *  parameter for the Open-document URL. */
  title: string;
  snippet: string;
  /** Display string under the title — e.g. "PDF · p.12 · Article 17". */
  page: string;
  type: string;
  /** Section header (used in `page` display + as `#section=` fragment). */
  section?: string;
  /** 1-based page number (PDF). Null when the chunker couldn't
   *  infer a page. The Open-document URL appends `#page=N` for PDFs. */
  pageNum?: number | null;
};

interface Props {
  sources: Source[];
  onClose?: () => void;
}

export function SourcesPanel({ sources, onClose }: Props) {
  return (
    // border-s = inline-start border; in RTL the panel is on the left,
    // border on its right, which matches user expectation.
    <aside className="hidden h-screen w-[340px] shrink-0 flex-col border-s border-border bg-sidebar/60 lg:flex">
      <div className="flex items-center justify-between border-b border-border px-5 py-4">
        <div>
          <div className="text-[13px] font-semibold text-foreground">{t("sources.title")}</div>
          <div className="text-[11px] text-muted-foreground">
            {sources.length === 1
              ? t("sources.count_one").replace("{n}", String(sources.length))
              : t("sources.count_many").replace("{n}", String(sources.length))}
          </div>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {sources.map((s, i) => {
          const ext = (s.title.split(".").pop() || s.type || "").toLowerCase();
          return (
            <div
              key={s.id}
              style={{ animationDelay: `${i * 40}ms` }}
              className="group rounded-lg border border-border bg-surface p-4 transition-all hover:border-primary/40 hover:shadow-sm animate-in fade-in slide-in-from-bottom-1 duration-200 fill-mode-both"
            >
              <div className="flex items-start gap-3">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-accent text-[11px] font-semibold text-primary">
                  {i + 1}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <FileTypeIcon ext={ext} className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="truncate text-[12px] font-medium text-foreground">
                      {s.title}
                    </span>
                  </div>
                  <div className="mt-0.5 text-[10.5px] uppercase tracking-wider text-muted-foreground">
                    {s.type} · {s.page}
                  </div>
                </div>
              </div>
              <p className="mt-3 border-s-2 border-border ps-3 text-[12.5px] leading-relaxed text-muted-foreground">
                "{s.snippet}"
              </p>
              <button
                onClick={() => openSource(s)}
                className="mt-3 inline-flex items-center gap-1 text-[11.5px] font-medium text-primary transition-opacity hover:underline hover:opacity-80"
              >
                {t("sources.open", "Open document")} <ExternalLink className="h-3 w-3" />
              </button>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

import {
  FileText, ChevronDown, ChevronUp, Copy, ThumbsUp, ThumbsDown,
  AlertTriangle, BadgeCheck, BadgeAlert, BadgeX, Loader2,
} from "lucide-react";
import { useState } from "react";
import type { Source } from "./SourcesPanel";
import { t } from "@/lib/i18n";
import { verifyCitations, type CitationVerifyResult } from "@/lib/api";

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  time: string;
  sources?: Source[];
  /** True while the assistant message is still being streamed from the
   *  server. MessageBubble renders a pulsing cursor at the end of the
   *  partial content + a "generating" pill near the LocallyAI label so
   *  the user knows the model is still working — important when there
   *  are slow gaps between tokens (long cold-load on big models, KV
   *  pressure under concurrency, etc.). Cleared on onFinish. */
  isStreaming?: boolean;
  /** Plugin/skill the user had selected when this turn was sent — shown
   *  as a small provenance pill so reviewers can tell what practice
   *  profile and skill body shaped the answer. Only meaningful on
   *  assistant messages; persisted alongside `sources` in history. */
  plugin?: string;
  skill?: string;
};

export function MessageBubble({ msg }: { msg: ChatMessage }) {
  const [open, setOpen] = useState(false);
  const [citeBusy, setCiteBusy] = useState(false);
  const [citeError, setCiteError] = useState<string | null>(null);
  const [citeResult, setCiteResult] = useState<CitationVerifyResult | null>(null);

  const runVerifyCitations = async () => {
    setCiteBusy(true);
    setCiteError(null);
    try {
      const r = await verifyCitations(msg.content);
      setCiteResult(r);
    } catch (e: unknown) {
      setCiteError(e instanceof Error ? e.message : "Verification failed");
    } finally {
      setCiteBusy(false);
    }
  };

  if (msg.role === "user") {
    return (
      // justify-end on flex naturally puts the bubble at the inline-end
      // edge in both LTR and RTL. animate-in handles the subtle reveal
      // when a new bubble lands.
      <div className="flex justify-end animate-in fade-in slide-in-from-bottom-1 duration-200">
        <div className="max-w-[85%] rounded-2xl rounded-se-md bg-accent px-4 py-3 text-[14px] leading-relaxed text-foreground">
          {msg.content}
          <div className="mt-1 text-end text-[10.5px] text-muted-foreground">{msg.time}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-3 animate-in fade-in slide-in-from-bottom-1 duration-200">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-foreground/95">
        <div className="h-2 w-2 rounded-sm bg-background" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-[12.5px] font-semibold text-foreground">LocallyAI</span>
          <span className="text-[10.5px] text-muted-foreground">{msg.time}</span>
          {msg.isStreaming && (
            <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
              </span>
              {t("message.generating", "Generating…")}
            </span>
          )}
          {/* Plugin/skill provenance — visible only when the user had a
              plugin active for this turn. Makes the demo obvious + lets
              reviewers see what practice profile shaped the answer. */}
          {msg.plugin && (
            <span
              className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/5 px-2 py-0.5 text-[10px] font-medium text-primary"
              title={`Plugin: ${msg.plugin}${msg.skill ? ` · Skill: ${msg.skill}` : ""}`}
            >
              {msg.plugin}
              {msg.skill ? ` · ${msg.skill}` : ""}
            </span>
          )}
          {/*
            AI-output disclaimer required by DPA §9.4.4 (UK) / §4a.4 (KSA).
            Persistent, visible label on every assistant response. The DPA
            text says vendor "will display, in the user interface of the
            Services, a visible, persistent disclaimer alongside every
            AI-generated response indicating that the response is
            AI-generated and must be verified before use." This is that.
          */}
          <span
            className="ms-auto inline-flex items-center gap-1 rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-300"
            title={t("message.ai_disclaimer_tooltip", "AI-generated content. Always verify against the cited sources before relying on it for any client-facing or filed work product.")}
          >
            <AlertTriangle className="h-2.5 w-2.5" />
            {t("message.ai_disclaimer", "AI-generated — verify before use")}
          </span>
        </div>
        <div className="mt-2 whitespace-pre-wrap text-[14px] leading-[1.7] text-foreground/95">
          {msg.content.split(/(\[\d+\])/g).map((part, i) =>
            /^\[\d+\]$/.test(part) ? (
              <span
                key={i}
                className="mx-0.5 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-[5px] bg-accent px-1 align-[1px] text-[10.5px] font-semibold text-primary animate-in fade-in zoom-in-90 duration-200"
              >
                {part.replace(/[\[\]]/g, "")}
              </span>
            ) : (
              <span key={i}>{part}</span>
            )
          )}
          {msg.isStreaming && (
            // Pulsing cursor at the end of the partial content. Shows the
            // model is still emitting tokens — important during slow gaps.
            <span
              className="ms-0.5 inline-block h-[1.1em] w-[7px] translate-y-[2px] animate-pulse bg-foreground/70 align-baseline"
              aria-label="generating"
            />
          )}
        </div>

        {msg.sources && msg.sources.length > 0 && (
          <div className="mt-4">
            <button
              onClick={() => setOpen((o) => !o)}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[11.5px] font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              <FileText className="h-3 w-3" />
              {msg.sources.length} sources
              {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            </button>

            {open && (
              <div className="mt-2 space-y-2">
                {msg.sources.map((s, i) => (
                  <div
                    key={s.id}
                    className="rounded-md border border-border bg-surface/60 p-3"
                  >
                    <div className="flex items-center gap-2">
                      <span className="flex h-5 min-w-[20px] items-center justify-center rounded bg-accent px-1 text-[10.5px] font-semibold text-primary">
                        {i + 1}
                      </span>
                      <span className="text-[12px] font-medium text-foreground">{s.title}</span>
                      <span className="text-[10.5px] text-muted-foreground">· {s.page}</span>
                    </div>
                    <p className="mt-1.5 ps-7 text-[12px] leading-relaxed text-muted-foreground">
                      "{s.snippet}"
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="mt-3 flex items-center gap-1 text-muted-foreground">
          <button className="rounded p-1.5 hover:bg-accent hover:text-foreground" aria-label={t("message.copy")}>
            <Copy className="h-3.5 w-3.5" />
          </button>
          <button className="rounded p-1.5 hover:bg-accent hover:text-foreground" aria-label={t("message.helpful")}>
            <ThumbsUp className="h-3.5 w-3.5" />
          </button>
          <button className="rounded p-1.5 hover:bg-accent hover:text-foreground" aria-label={t("message.not_helpful")}>
            <ThumbsDown className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={runVerifyCitations}
            disabled={citeBusy || !msg.content || msg.isStreaming}
            className="ms-2 inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] font-medium hover:bg-accent hover:text-foreground disabled:opacity-40"
            aria-label={t("message.verify_citations", "Verify citations")}
            title={t(
              "message.verify_citations_tooltip",
              "Extract case-law and statute citations from this response and verify each one against the firm corpus and BAILII (UK).",
            )}
          >
            {citeBusy ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <BadgeCheck className="h-3 w-3" />
            )}
            {t("message.verify_citations", "Verify citations")}
          </button>
        </div>

        {citeError && (
          <div className="mt-3 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1.5 text-[11px] text-destructive">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
            <span>{citeError}</span>
          </div>
        )}

        {citeResult && <CitationsPanel result={citeResult} />}
      </div>
    </div>
  );
}

function CitationsPanel({ result }: { result: CitationVerifyResult }) {
  if (result.count === 0) {
    return (
      <div className="mt-3 rounded-md border border-border bg-surface/60 p-2 text-[11.5px] text-muted-foreground">
        No citations detected in this response.
      </div>
    );
  }
  return (
    <div className="mt-3 space-y-2">
      <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        Citations ({result.count}) · {result.elapsed_ms} ms
      </div>
      {result.citations.map((c, i) => {
        const corpusFound = c.found_in_corpus.found;
        const externalFound = c.found_external.found;
        const onPoint = c.on_point.on_point;
        let badgeIcon = <BadgeAlert className="h-3 w-3" />;
        let badgeCls = "bg-warning/15 text-warning";
        let badgeText = "Found in corpus only";
        if (!c.verified) {
          badgeIcon = <BadgeX className="h-3 w-3" />;
          badgeCls = "bg-destructive/15 text-destructive";
          badgeText = "Not found";
        } else if (externalFound && onPoint === true) {
          badgeIcon = <BadgeCheck className="h-3 w-3" />;
          badgeCls = "bg-success/15 text-success";
          badgeText = "Verified";
        } else if (externalFound && onPoint === false) {
          badgeIcon = <BadgeX className="h-3 w-3" />;
          badgeCls = "bg-destructive/15 text-destructive";
          badgeText = "Real citation, possibly inapposite";
        } else if (externalFound) {
          badgeIcon = <BadgeCheck className="h-3 w-3" />;
          badgeCls = "bg-success/15 text-success";
          badgeText = "Found externally";
        } else if (corpusFound) {
          badgeText = "Found in corpus only";
        }
        return (
          <div key={i} className="rounded-md border border-border bg-surface/60 p-3 text-[12px]">
            <div className="flex items-start justify-between gap-2">
              <code className="font-mono text-[11.5px] font-semibold">{c.cite}</code>
              <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold ${badgeCls}`}>
                {badgeIcon} {badgeText}
              </span>
            </div>
            <div className="mt-1 text-[10.5px] text-muted-foreground">
              {c.jurisdiction} · {c.kind}
              {c.year ? ` · ${c.year}` : ""}
            </div>
            {c.found_external.url && (
              <a
                href={c.found_external.url}
                target="_blank"
                rel="noreferrer noopener"
                className="mt-1 inline-block text-[11px] text-primary hover:underline"
              >
                Open in BAILII →
              </a>
            )}
            {c.on_point.reasoning && (
              <p className="mt-2 text-[11.5px] text-muted-foreground">
                <span className="font-semibold">On-point: </span>
                {c.on_point.reasoning}
              </p>
            )}
            {c.on_point.suggestion && (
              <p className="mt-1 text-[11px] text-muted-foreground italic">
                {c.on_point.suggestion}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function TypingMessage() {
  return (
    <div className="flex gap-3">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-foreground/95">
        <div className="h-2 w-2 rounded-sm bg-background" />
      </div>
      <div className="flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-[12.5px] font-semibold text-foreground">LocallyAI</span>
          <span className="text-[10.5px] text-muted-foreground">{t("message.searching")}</span>
        </div>
        <div className="mt-3 space-y-2">
          <div className="h-2.5 w-3/4 animate-pulse rounded bg-accent" />
          <div className="h-2.5 w-2/3 animate-pulse rounded bg-accent [animation-delay:120ms]" />
          <div className="h-2.5 w-1/2 animate-pulse rounded bg-accent [animation-delay:240ms]" />
        </div>
      </div>
    </div>
  );
}

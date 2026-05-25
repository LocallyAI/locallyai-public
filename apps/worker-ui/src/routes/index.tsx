import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { Shield, AlertTriangle, FileSearch, Sparkles, ChevronDown, Pencil, Building2 } from "lucide-react";
import { Sidebar, type Conversation } from "@/components/locally/Sidebar";
import { SourcesPanel, type Source } from "@/components/locally/SourcesPanel";
import { Composer } from "@/components/locally/Composer";
import { MessageBubble, TypingMessage, type ChatMessage } from "@/components/locally/Message";
import { UploadDropZone } from "@/components/locally/UploadDropZone";
import { UploadChips } from "@/components/locally/UploadChip";
import { IngestStatusTicker } from "@/components/locally/IngestStatusTicker";
import { PluginPicker } from "@/components/locally/PluginPicker";
import { Toaster } from "@/components/ui/sonner";
import {
  streamChatCompletion,
  getMe,
  getBranding,
  listModels,
  type ChatMessage as ApiChatMessage,
  type SourceCitation,
  type BrandingResponse,
  type ModelInfo,
  ApiError,
} from "@/lib/api";
import { clearUserKey } from "@/lib/auth";

export const Route = createFileRoute("/")({
  component: Workspace,
});

type ViewState = "empty" | "active" | "loading" | "error";

interface StoredConversation {
  id: string;
  title: string;
  date: string;
  messages: ChatMessage[];
}

const HISTORY_KEY = "locallyai_worker_conversations";

function loadHistory(): StoredConversation[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as StoredConversation[];
    // Re-sanitize titles on load — guards against tampered storage and
    // against legacy entries written before the sanitizer existed.
    return parsed.map((c) => ({ ...c, title: sanitizeTitle(c.title) || "Untitled" }));
  } catch {
    return [];
  }
}

function saveHistory(items: StoredConversation[]): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, 50)));
}

function nowTime(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Conversation titles round-trip through localStorage and render in the
// sidebar + header. React escapes text, so no XSS surface — but a malicious
// or careless paste can still smuggle in things that break the UI or cause
// confusion: control chars (NUL, BEL, line breaks), zero-width characters,
// bidi overrides (RTL spoofing of innocuous-looking names), and runaway
// whitespace. Strip them at the single choke point.
const TITLE_MAX_LENGTH = 120;
function sanitizeTitle(raw: string): string {
  if (typeof raw !== "string") return "";
  // 1. Strip C0 (\u0000-\u001F) and C1 (\u007F-\u009F) control chars.
  // 2. Strip zero-width chars + BOM (\u200B-\u200D, \uFEFF).
  // 3. Strip bidi formatting (\u202A-\u202E overrides, \u2066-\u2069 isolates) —
  //    these can flip "report.pdf" into "fdp.troper" visually in the sidebar.
  // 4. Collapse internal whitespace runs to a single space.
  // 5. Trim, then enforce max length.
  // eslint-disable-next-line no-control-regex
  const stripped = raw
    .replace(/[\u0000-\u001F\u007F-\u009F]/g, "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/[\u202A-\u202E\u2066-\u2069]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return stripped.slice(0, TITLE_MAX_LENGTH);
}

function nowDateLabel(): string {
  const d = new Date();
  const today = new Date();
  if (d.toDateString() === today.toDateString()) {
    return `Today, ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  }
  return d.toLocaleDateString();
}

function mapBackendSources(citations: SourceCitation[] | undefined): Source[] {
  if (!citations || citations.length === 0) return [];
  return citations.map((c, i) => {
    const filename = (c.source || "").split(/[\\/]/).pop() || `Chunk ${i + 1}`;
    const ext = filename.includes(".") ? filename.split(".").pop()!.toUpperCase() : "Excerpt";
    // "page" display string built from structural fields the server
    // now surfaces. Examples: "p.12 · Article 17", "§3.4", or
    // "score 0.873" as a fallback when neither is available.
    const parts: string[] = [];
    if (c.page) parts.push(`p.${c.page}`);
    if (c.section) parts.push(`§${c.section}`);
    const display = parts.length > 0 ? parts.join(" · ")
                  : (c.score ? `score ${c.score.toFixed(3)}` : "—");
    return {
      id: c.chunk_id || `s${i + 1}`,
      title: filename,
      page: display,
      type: ext,
      snippet: c.snippet || "Retrieved from your private corpus.",
      section: c.section || undefined,
      pageNum: c.page ?? null,
    } satisfies Source;
  });
}

function Workspace() {
  const [view, setView] = useState<ViewState>("empty");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversations, setConversations] = useState<StoredConversation[]>(() => loadHistory());
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [user, setUser] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);
  // Full ModelInfo[] so the plugin picker can read the active model's
  // `tool_calling` capability and gate itself accordingly. The header
  // dropdown still renders just the id list, derived from this.
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [branding, setBranding] = useState<BrandingResponse | null>(null);

  // Plugin/skill picker state — forwarded to the chat payload when set.
  const [selectedPlugin, setSelectedPlugin] = useState<string | null>(null);
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await getMe();
        if (!cancelled) setUser(me.user);
      } catch {
        // gate component already handled this; nothing to do here.
      }
      try {
        const m = await listModels();
        if (!cancelled) {
          setModels(m);
          if (m.length > 0) setModel((current) => current ?? m[0].id);
        }
      } catch {
        // backend may be unreachable; the chat call will surface a clearer error.
      }
      try {
        const b = await getBranding();
        if (!cancelled) setBranding(b);
      } catch { /* legacy / unreachable — header degrades gracefully */ }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const conversationListItems = useMemo<Conversation[]>(
    () => conversations.map((c) => ({ id: c.id, title: c.title, date: c.date })),
    [conversations],
  );

  // The currently active model record — used by the picker to decide
  // whether to enable itself / show the "unverified for tools" warning.
  // Falls back to the first model if the id pointer is stale.
  const activeModel = useMemo<ModelInfo | null>(() => {
    if (models.length === 0) return null;
    return models.find((m) => m.id === model) ?? models[0];
  }, [models, model]);

  const persist = (id: string, title: string, msgs: ChatMessage[]) => {
    setConversations((prev) => {
      const others = prev.filter((c) => c.id !== id);
      const next = [{ id, title, date: nowDateLabel(), messages: msgs }, ...others];
      saveHistory(next);
      return next;
    });
  };

  const handleSelect = (id: string | null) => {
    if (!id) return;
    const found = conversations.find((c) => c.id === id);
    if (!found) return;
    setActiveId(id);
    setMessages(found.messages);
    setView(found.messages.length === 0 ? "empty" : "active");
    setErrorMessage(null);
  };

  const handleNew = () => {
    setView("empty");
    setActiveId(null);
    setMessages([]);
    setErrorMessage(null);
  };

  const handleRename = (id: string, title: string) => {
    const next = sanitizeTitle(title);
    if (next.length === 0) return;
    setConversations((prev) => {
      const updated = prev.map((c) => (c.id === id ? { ...c, title: next } : c));
      saveHistory(updated);
      return updated;
    });
  };

  const handleSend = async (text: string) => {
    setErrorMessage(null);
    const conversationId = activeId ?? crypto.randomUUID();
    const isNewConversation = !activeId;

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      time: nowTime(),
    };
    const nextMessagesForRequest = [...messages, userMsg];
    setMessages(nextMessagesForRequest);
    setView("loading");
    setActiveId(conversationId);

    const apiMessages: ApiChatMessage[] = nextMessagesForRequest.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    // Optimistically insert an empty assistant message that we'll fill in
    // as tokens stream from the server. The user sees the answer typing
    // out live, and a mid-stream node failover shows up as a regen marker
    // (handled inside streamChatCompletion).
    //
    // Loading-animation strategy:
    //   1. view stays "loading" (TypingMessage skeleton visible) UNTIL the
    //      first token arrives — covers the cold-load gap (2-5s on 70B
    //      MLX) where the user would otherwise see an empty bubble.
    //   2. Once tokens flow, the assistant message is marked
    //      isStreaming=true so MessageBubble renders a pulsing cursor
    //      after the partial content. Cleared on onFinish so the cursor
    //      disappears when generation completes.
    // Snapshot the picker at send-time so the badge on the assistant
    // bubble reflects what was active when the turn went out, even if
    // the user changes the picker before the response finishes.
    const turnPlugin = selectedPlugin ?? undefined;
    const turnSkill = selectedSkill ?? undefined;

    const aiMsgId = crypto.randomUUID();
    const aiMsg: ChatMessage = {
      id: aiMsgId,
      role: "assistant",
      content: "",
      time: nowTime(),
      sources: [],
      isStreaming: true,
      plugin: turnPlugin,
      skill: turnSkill,
    };
    // Don't insert the empty assistant bubble yet — TypingMessage will
    // sit in its place until the first token. This avoids the
    // "blank bubble for 3 seconds" UX during cold-load.

    let acc = "";
    let firstTokenSeen = false;
    let finalSources: Source[] = [];
    await new Promise<void>((resolve) => {
      streamChatCompletion(
        {
          messages: apiMessages,
          model: model ?? undefined,
          // Conditional spread keeps undefined fields out of the JSON.
          // Backend tolerates either, but this keeps the wire format clean.
          ...(turnPlugin ? { plugin: turnPlugin } : {}),
          ...(turnSkill ? { skill: turnSkill } : {}),
        },
        {
          onToken: (delta) => {
            acc += delta;
            if (!firstTokenSeen) {
              firstTokenSeen = true;
              // First token landed — swap the typing skeleton for a real
              // (still-streaming) bubble.
              setMessages([...nextMessagesForRequest, { ...aiMsg, content: acc }]);
              setView("active");
            } else {
              setMessages((prev) =>
                prev.map((m) => (m.id === aiMsgId ? { ...m, content: acc } : m))
              );
            }
          },
          onFinish: (final) => {
            const fSources = mapBackendSources(
              (final as { sources?: SourceCitation[] }).sources
            );
            finalSources = fSources;
            // Always materialise the bubble on finish — covers the case
            // where the model streams nothing (empty response) and we
            // still need to render the citations.
            if (!firstTokenSeen) {
              setMessages([...nextMessagesForRequest, {
                ...aiMsg, content: acc, sources: fSources, isStreaming: false,
              }]);
              setView("active");
            } else {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === aiMsgId ? { ...m, content: acc, sources: fSources, isStreaming: false } : m
                )
              );
            }
            const finalMessages = [
              ...nextMessagesForRequest,
              { ...aiMsg, content: acc, sources: fSources },
            ];
            const autoTitle = sanitizeTitle(text).slice(0, 64);
            const title = isNewConversation
              ? autoTitle + (text.length > 64 ? "…" : "")
              : conversations.find((c) => c.id === conversationId)?.title ?? autoTitle;
            persist(conversationId, title, finalMessages);
            resolve();
          },
          onError: (err) => {
            const detail =
              err instanceof ApiError
                ? `${err.message} (HTTP ${err.status})`
                : err.message;
            setErrorMessage(detail);
            setView("error");
            resolve();
          },
        }
      );
    });
    void finalSources;
  };

  const activeSources: Source[] = useMemo(() => {
    const last = [...messages].reverse().find((m) => m.role === "assistant" && m.sources);
    return last?.sources ?? [];
  }, [messages]);

  const handleSignOut = () => {
    clearUserKey();
    window.location.reload();
  };

  return (
    <UploadDropZone>
    <div className="flex h-screen w-full overflow-hidden bg-background text-foreground">
      <Toaster />
      <Sidebar
        conversations={conversationListItems}
        activeId={activeId}
        onSelect={handleSelect}
        onNew={handleNew}
        onRename={handleRename}
        userName={user}
        onSignOut={handleSignOut}
      />

      <main className="flex h-screen min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b border-border px-6">
          <div className="flex items-center gap-3">
            {activeId ? (
              <HeaderTitle
                key={activeId}
                value={conversations.find((c) => c.id === activeId)?.title ?? "Conversation"}
                onCommit={(t) => handleRename(activeId, t)}
              />
            ) : (
              <button className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[13px] font-medium text-foreground hover:bg-accent">
                New conversation
                <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
              </button>
            )}
          </div>

          <div className="flex min-w-0 items-center gap-2">
            {branding && (
              <div
                className="hidden shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-primary/30 bg-primary/5 px-2.5 py-1 text-[11px] font-medium text-foreground sm:inline-flex"
                title={`Connected to ${branding.firm_name}'s LocallyAI deployment at ${branding.office_host || branding.deployment_id}. ${branding.isolation_statement}`}
              >
                <Building2 className="h-3 w-3 shrink-0 text-primary" />
                <span className="shrink-0">Firm:</span>
                <span className="block max-w-[160px] truncate text-primary">{branding.firm_name}</span>
              </div>
            )}
            <div
              className="hidden shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-border bg-surface/60 px-2.5 py-1 text-[11px] text-muted-foreground lg:inline-flex"
              title="Running locally on this firm's hardware — no data leaves the network."
            >
              <span className="relative flex h-1.5 w-1.5 shrink-0">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/60 opacity-60" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
              </span>
              On-premise
            </div>
            <div
              className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-border bg-surface/60 px-2.5 py-1 text-[11px] text-muted-foreground"
              title="HTTPS · audit-chained · pseudonymised users"
            >
              <Shield className="h-3 w-3 shrink-0 text-primary" />
              Secure
            </div>
            {models.length > 1 && (
              <select
                value={model ?? ""}
                onChange={(e) => setModel(e.target.value)}
                className="rounded-md border border-border bg-surface/60 px-2 py-1 text-[11px] text-foreground"
              >
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.id}
                  </option>
                ))}
              </select>
            )}
          </div>
        </header>

        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex-1 overflow-y-auto">
            {view === "empty" && <EmptyState user={user} onPick={handleSend} />}

            {(view === "active" || view === "loading") && (
              <div className="mx-auto w-full max-w-3xl space-y-8 px-6 py-10">
                {messages.map((m) => (
                  <MessageBubble key={m.id} msg={m} />
                ))}
                {view === "loading" && <TypingMessage />}
              </div>
            )}

            {view === "error" && <ErrorState message={errorMessage} onRetry={handleNew} />}
          </div>

          <IngestStatusTicker />
          <UploadChips />
          <PluginPicker
            selectedPlugin={selectedPlugin}
            selectedSkill={selectedSkill}
            onPluginChange={setSelectedPlugin}
            onSkillChange={setSelectedSkill}
            toolCalling={activeModel?.tool_calling}
          />
          <Composer onSend={handleSend} disabled={view === "loading"} />
        </div>
      </main>

      {view === "active" && activeSources.length > 0 && <SourcesPanel sources={activeSources} />}
    </div>
    </UploadDropZone>
  );
}

function EmptyState({ user, onPick }: { user: string | null; onPick: (q: string) => void }) {
  const suggestions = [
    "Summarise the key obligations in the most recent contract I uploaded",
    "What are the standard termination rights in our vendor agreements?",
    "Compare confidentiality clauses across our top NDAs",
    "Flag any non-standard liability caps in recent contracts",
  ];

  const greeting = user && user !== "admin" ? `Hello, ${user}.` : "Hello.";

  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="mx-auto w-full max-w-2xl text-center">
        <div className="mx-auto mb-6 flex h-12 w-12 items-center justify-center rounded-xl bg-surface">
          <Sparkles className="h-5 w-5 text-primary" />
        </div>
        <h1 className="text-[26px] font-semibold tracking-tight text-foreground">{greeting}</h1>
        <p className="mt-2 text-[14px] text-muted-foreground">
          Ask a question about your firm's documents. Answers are generated locally and include citations.
        </p>

        <div className="mt-10 grid grid-cols-1 gap-2 text-start sm:grid-cols-2">
          {suggestions.map((s) => (
            <button
              key={s}
              onClick={() => onPick(s)}
              className="group rounded-lg border border-border bg-surface/60 px-4 py-3 text-[13px] leading-snug text-foreground transition-colors hover:border-primary/40 hover:bg-surface"
            >
              <span className="block text-[10.5px] font-medium uppercase tracking-wider text-muted-foreground">
                Suggested
              </span>
              <span className="mt-1 block">{s}</span>
            </button>
          ))}
        </div>

        <div className="mx-auto mt-10 flex max-w-md items-center justify-center gap-2 rounded-md border border-border bg-surface/40 px-4 py-2.5 text-[11.5px] text-muted-foreground">
          <Shield className="h-3.5 w-3.5 text-primary" />
          All processing happens on your firm's system. No data leaves this environment.
        </div>
      </div>
    </div>
  );
}

function HeaderTitle({ value, onCommit }: { value: string; onCommit: (next: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing]);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const commit = () => {
    const next = draft.trim();
    if (next.length > 0 && next !== value) onCommit(next);
    setEditing(false);
  };

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            setDraft(value);
            setEditing(false);
          }
        }}
        maxLength={120}
        className="rounded-md border border-border bg-surface px-2 py-1 text-[13px] font-medium text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
      />
    );
  }

  return (
    <button
      type="button"
      onClick={() => setEditing(true)}
      className="group flex items-center gap-1.5 rounded-md px-2 py-1 text-[13px] font-medium text-foreground hover:bg-accent"
      title="Click to rename"
    >
      <span className="max-w-[40ch] truncate">{value}</span>
      <Pencil className="h-3 w-3 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
    </button>
  );
}

function ErrorState({ message, onRetry }: { message: string | null; onRetry: () => void }) {
  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="max-w-md text-center">
        <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
          <AlertTriangle className="h-5 w-5 text-destructive" />
        </div>
        <h2 className="text-[18px] font-semibold text-foreground">Request failed</h2>
        <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">
          {message ?? "The local model is not responding. Please try again or contact your administrator."}
        </p>
        <div className="mt-5 flex items-center justify-center gap-2">
          <button
            onClick={onRetry}
            className="rounded-md bg-foreground px-3.5 py-2 text-[12.5px] font-medium text-background"
          >
            Start a new conversation
          </button>
        </div>
        <div className="mt-8 inline-flex items-center gap-2 rounded-md border border-border bg-surface/60 px-3 py-2 text-[11.5px] text-muted-foreground">
          <FileSearch className="h-3.5 w-3.5" />
          You can still browse previous conversations from the sidebar.
        </div>
      </div>
    </div>
  );
}

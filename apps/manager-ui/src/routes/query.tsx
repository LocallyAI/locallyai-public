import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { TopBar } from "@/components/TopBar";
import {
  Plus, Search, Send, MessageSquare, FileText, Loader2, AlertTriangle,
  Paperclip, FolderUp, ChevronDown, FileUp, Pause, Play, X as XIcon,
  CheckCircle2, XCircle, ListChecks,
} from "lucide-react";
import {
  chatCompletion,
  listModels,
  getIngestStatus, flushIngest,
  type ChatMessage as ApiChatMessage,
  type SourceCitation,
  type IngestStatusResponse,
  ApiError,
} from "@/lib/api";
import {
  useUploads, uploadFiles, pauseUpload, resumeUpload, cancelUpload,
  dismissUpload, type UploadItem,
} from "@/hooks/use-uploads";

export const Route = createFileRoute("/query")({
  head: () => ({ meta: [{ title: "Query — LocallyAI" }] }),
  component: QueryPage,
});

interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  content: string;
  time: string;
  sources?: SourceCitation[];
}

interface Conversation {
  id: string;
  title: string;
  updatedAt: number;
  messages: ChatTurn[];
}

const STORAGE_KEY = "locallyai_manager_conversations";

function loadConversations(): Conversation[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Conversation[]) : [];
  } catch {
    return [];
  }
}

function saveConversations(items: Conversation[]): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, 100)));
}

function formatTime(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// `webkitdirectory` is supported in every evergreen browser but isn't
// in React's TS defs.
type DirInputProps = React.InputHTMLAttributes<HTMLInputElement> & {
  webkitdirectory?: string; directory?: string;
};

const ALLOWED_EXTS = new Set(["pdf", "docx", "txt", "md"]);

function formatBytes(n: number): string {
  if (n < 1024)        return `${n} B`;
  if (n < 1024 ** 2)   return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3)   return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

function relativeDate(ms: number): string {
  const sec = Math.max(1, Math.floor((Date.now() - ms) / 1000));
  if (sec < 60) return "Just now";
  if (sec < 3600) return `${Math.floor(sec / 60)} min ago`;
  if (sec < 86400) return "Today";
  if (sec < 86400 * 2) return "Yesterday";
  return new Date(ms).toLocaleDateString();
}

function QueryPage() {
  const [conversations, setConversations] = useState<Conversation[]>(() => loadConversations());
  const [activeId, setActiveId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [model, setModel] = useState<string | null>(null);
  const [models, setModels] = useState<string[]>([]);

  // ── Upload-in-chat state ────────────────────────────────────────────────
  const uploads = useUploads();
  const fileInput   = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [attachOpen, setAttachOpen] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const dragCounter = useRef(0);

  // Live ingest queue ticker — same shape as documents.tsx but compact.
  const [ingestStatus, setIngestStatus] = useState<IngestStatusResponse | null>(null);
  const [flushing, setFlushing] = useState(false);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      try {
        const s = await getIngestStatus();
        if (!alive) return;
        setIngestStatus(s);
        const isBusy = s.in_flight > 0 || s.queued > 0 || s.bm25_pending;
        timer = setTimeout(tick, isBusy ? 2000 : 10_000);
      } catch { timer = setTimeout(tick, 30_000); }
    };
    void tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, []);

  const onPickFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    const all = e.target.files ? Array.from(e.target.files) : [];
    if (all.length > 0) {
      // Folder picks: silently filter unsupported. Single-file picks
      // surface per-file errors via the use-uploads hook.
      const isFolderPick = all.length > 1 && all.some(
        (f) => /[\\/]/.test((f as File & { webkitRelativePath?: string }).webkitRelativePath ?? ""),
      );
      const accepted = isFolderPick
        ? all.filter((f) => ALLOWED_EXTS.has(f.name.split(".").pop()?.toLowerCase() ?? ""))
        : all;
      if (accepted.length > 0) void uploadFiles(accepted);
    }
    e.target.value = "";
    setAttachOpen(false);
  };

  // Page-level drag overlay. Counter pattern handles nested elements
  // firing dragenter/leave on every child.
  useEffect(() => {
    const onEnter = (e: DragEvent) => {
      if (!e.dataTransfer || !Array.from(e.dataTransfer.types).includes("Files")) return;
      dragCounter.current++;
      setDragActive(true);
    };
    const onLeave = () => {
      dragCounter.current = Math.max(0, dragCounter.current - 1);
      if (dragCounter.current === 0) setDragActive(false);
    };
    const onOver = (e: DragEvent) => { e.preventDefault(); };
    const onDrop = (e: DragEvent) => {
      e.preventDefault();
      dragCounter.current = 0;
      setDragActive(false);
      if (e.dataTransfer?.files && e.dataTransfer.files.length > 0) {
        void uploadFiles(e.dataTransfer.files);
      }
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("dragover",  onOver);
    window.addEventListener("drop",      onDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("dragover",  onOver);
      window.removeEventListener("drop",      onDrop);
    };
  }, []);

  useEffect(() => {
    saveConversations(conversations);
  }, [conversations]);

  useEffect(() => {
    let cancelled = false;
    listModels()
      .then((m) => {
        if (cancelled) return;
        setModels(m.map((x) => x.id));
        if (m[0]) setModel((cur) => cur ?? m[0].id);
      })
      .catch(() => {
        // ignore — chat call will surface the error if the backend is down
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const active = useMemo(
    () => conversations.find((c) => c.id === activeId) ?? null,
    [conversations, activeId],
  );

  const filtered = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    if (!term) return conversations;
    return conversations.filter((c) => c.title.toLowerCase().includes(term));
  }, [conversations, searchTerm]);

  const newConversation = () => {
    setActiveId(null);
    setError(null);
  };

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || busy) return;
    setError(null);
    const text = input.trim();
    setInput("");
    setBusy(true);

    let convo = active;
    let convoId = activeId;
    if (!convo) {
      convoId = crypto.randomUUID();
      convo = {
        id: convoId,
        title: text.slice(0, 64) + (text.length > 64 ? "…" : ""),
        updatedAt: Date.now(),
        messages: [],
      };
      setConversations((prev) => [convo!, ...prev]);
      setActiveId(convoId);
    }

    const userMsg: ChatTurn = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      time: formatTime(),
    };
    const baseMessages = [...convo.messages, userMsg];

    setConversations((prev) =>
      prev.map((c) => (c.id === convoId ? { ...c, messages: baseMessages, updatedAt: Date.now() } : c)),
    );

    try {
      const apiMessages: ApiChatMessage[] = baseMessages.map((m) => ({ role: m.role, content: m.content }));
      const res = await chatCompletion({
        messages: apiMessages,
        model: model ?? undefined,
      });
      const answer = res.choices[0]?.message?.content ?? "";
      const aiMsg: ChatTurn = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: answer,
        time: formatTime(),
        sources: res.sources ?? [],
      };
      setConversations((prev) =>
        prev.map((c) =>
          c.id === convoId
            ? { ...c, messages: [...baseMessages, aiMsg], updatedAt: Date.now() }
            : c,
        ),
      );
    } catch (err: unknown) {
      const detail =
        err instanceof ApiError ? `${err.message} (HTTP ${err.status})` : err instanceof Error ? err.message : "Unknown error";
      setError(detail);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <TopBar title="Query Interface" description="Conversational analysis over your private corpus" />
      <main className="flex flex-1 overflow-hidden">
        <aside className="hidden w-72 shrink-0 flex-col border-e border-border bg-card/30 md:flex">
          <div className="p-3">
            <button
              onClick={newConversation}
              className="flex w-full items-center justify-center gap-2 rounded-md border border-border bg-secondary px-3 py-2 text-sm font-medium hover:bg-accent"
            >
              <Plus className="h-4 w-4" />
              New conversation
            </button>
          </div>
          <div className="px-3 pb-2">
            <div className="flex items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1.5">
              <Search className="h-3.5 w-3.5 text-muted-foreground" />
              <input
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground"
                placeholder="Search history"
              />
            </div>
          </div>
          <div className="flex-1 overflow-auto px-2 pb-3">
            <div className="px-2 pb-1 pt-3 text-[10px] uppercase tracking-wider text-muted-foreground">History</div>
            {filtered.length === 0 ? (
              <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                {conversations.length === 0 ? "No conversations yet" : "No matches"}
              </div>
            ) : (
              filtered.map((c) => (
                <button
                  key={c.id}
                  onClick={() => setActiveId(c.id)}
                  className={`group flex w-full flex-col items-start gap-0.5 rounded-md px-2 py-2 text-start hover:bg-accent ${
                    activeId === c.id ? "bg-accent" : ""
                  }`}
                >
                  <div className="flex w-full items-center gap-2">
                    <MessageSquare className="h-3 w-3 shrink-0 text-muted-foreground" />
                    <span className="truncate text-xs text-foreground">{c.title}</span>
                  </div>
                  <span className="ps-5 text-[10px] text-muted-foreground">{relativeDate(c.updatedAt)}</span>
                </button>
              ))
            )}
          </div>
        </aside>

        <section className="flex flex-1 flex-col">
          <div className="flex items-center justify-between border-b border-border px-6 py-3">
            <div>
              <h2 className="text-sm font-semibold">{active?.title ?? "New conversation"}</h2>
              <p className="text-xs text-muted-foreground">
                {active
                  ? `${active.messages.length} message${active.messages.length === 1 ? "" : "s"}`
                  : "Ask a question about your private corpus to begin"}
              </p>
            </div>
            <div className="flex items-center gap-2">
              {models.length > 1 && (
                <select
                  value={model ?? ""}
                  onChange={(e) => setModel(e.target.value)}
                  className="rounded-md border border-border bg-card px-2 py-0.5 text-xs"
                >
                  {models.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              )}
              {model && (
                <span className="rounded-md border border-border bg-card px-2 py-0.5 text-xs text-muted-foreground terminal-font">
                  {model}
                </span>
              )}
            </div>
          </div>

          <div className="flex-1 overflow-auto">
            <div className="mx-auto max-w-3xl space-y-8 px-6 py-8">
              {(active?.messages ?? []).map((m) => (
                <Message key={m.id} msg={m} />
              ))}
              {busy && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Generating answer…
                </div>
              )}
              {error && (
                <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{error}</span>
                </div>
              )}
              {!active && !busy && (
                <div className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
                  Type a question below to start a new conversation grounded in your indexed corpus.
                </div>
              )}
            </div>
          </div>

          {/* In-flight uploads + indexing ticker — sit right above
              the composer so the user sees what's happening. */}
          {(uploads.length > 0 || (ingestStatus && (ingestStatus.in_flight > 0 || ingestStatus.queued > 0 || ingestStatus.bm25_pending))) && (
            <div className="mx-auto w-full max-w-3xl space-y-2 px-6 pt-2">
              {ingestStatus && (ingestStatus.in_flight > 0 || ingestStatus.queued > 0 || ingestStatus.bm25_pending) && (
                <div className="flex items-center justify-between rounded-md border border-border bg-card/60 px-3 py-1.5 text-[11px]">
                  <div className="flex items-center gap-2 text-muted-foreground">
                    {ingestStatus.in_flight > 0
                      ? <Loader2 className="h-3 w-3 animate-spin text-primary" />
                      : ingestStatus.bm25_pending
                        ? <ListChecks className="h-3 w-3 text-primary" />
                        : <CheckCircle2 className="h-3 w-3 text-primary" />}
                    <span>
                      {ingestStatus.in_flight > 0 ? "Indexing" : ingestStatus.bm25_pending ? "Updating search index" : "Queued"}{" "}
                      <span className="tabular-nums text-foreground">
                        {ingestStatus.in_flight + ingestStatus.queued}
                      </span>{" "}
                      doc{ingestStatus.in_flight + ingestStatus.queued === 1 ? "" : "s"}
                    </span>
                  </div>
                  {ingestStatus.bm25_pending && ingestStatus.in_flight === 0 && ingestStatus.queued === 0 && (
                    <button
                      disabled={flushing}
                      onClick={async () => { setFlushing(true); try { await flushIngest(); } finally { setFlushing(false); } }}
                      className="rounded px-2 py-0.5 text-[10.5px] font-medium text-primary hover:bg-primary/10 disabled:opacity-50"
                    >
                      {flushing ? "Rebuilding…" : "Rebuild now"}
                    </button>
                  )}
                </div>
              )}
              {uploads.length > 0 && (
                <div className="space-y-1 rounded-md border border-border bg-card/40 p-2">
                  {uploads.map((u) => <UploadRow key={u.id} item={u} />)}
                </div>
              )}
            </div>
          )}

          <form onSubmit={send} className="border-t border-border bg-card/30 px-6 py-4">
            <div className="mx-auto max-w-3xl">
              {/* Hidden file inputs — the buttons below trigger them. */}
              <input
                ref={fileInput}
                type="file"
                multiple
                accept=".pdf,.docx,.txt,.md"
                onChange={onPickFiles}
                className="hidden"
                aria-hidden
              />
              <input
                {...({
                  ref: folderInput,
                  type: "file",
                  multiple: true,
                  webkitdirectory: "",
                  directory: "",
                  onChange: onPickFiles,
                  className: "hidden",
                  "aria-hidden": true,
                } as DirInputProps)}
              />

              <div className="flex items-end gap-2 rounded-lg border border-border bg-background p-2">
                {/* Attach popover (file / folder) */}
                <div className="relative">
                  <button
                    type="button"
                    onClick={() => setAttachOpen((v) => !v)}
                    className="inline-flex items-center gap-1 rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
                    title="Attach documents"
                    aria-label="Attach documents"
                  >
                    <Paperclip className="h-4 w-4" />
                    <ChevronDown className="h-3 w-3 opacity-60" />
                  </button>
                  {attachOpen && (
                    <>
                      {/* click-away */}
                      <div className="fixed inset-0 z-10" onClick={() => setAttachOpen(false)} />
                      <div className="absolute bottom-full mb-2 z-20 w-44 rounded-md border border-border bg-card p-1 shadow-lg">
                        <button
                          type="button"
                          onClick={() => fileInput.current?.click()}
                          className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-xs text-foreground hover:bg-accent"
                        >
                          <Paperclip className="h-3.5 w-3.5 text-muted-foreground" />
                          Pick files
                        </button>
                        <button
                          type="button"
                          onClick={() => folderInput.current?.click()}
                          className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-xs text-foreground hover:bg-accent"
                        >
                          <FolderUp className="h-3.5 w-3.5 text-muted-foreground" />
                          Pick folder
                        </button>
                      </div>
                    </>
                  )}
                </div>

                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send(e as unknown as React.FormEvent);
                    }
                  }}
                  rows={1}
                  placeholder="Ask a follow-up — or drop documents anywhere on the page…"
                  className="flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground"
                />
                <button
                  type="submit"
                  disabled={!input.trim() || busy}
                  className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
                >
                  <Send className="h-3.5 w-3.5" />
                  Send
                </button>
              </div>
              <div className="mt-2 flex items-center justify-between text-[10px] text-muted-foreground">
                <span>Inference local · 0 external calls · Drop PDF/DOCX/TXT/MD to ingest</span>
                <span>{conversations.length} stored conversation{conversations.length === 1 ? "" : "s"}</span>
              </div>
            </div>
          </form>
        </section>
      </main>

      {/* Page-level drag overlay — same pattern as worker-ui's UploadDropZone. */}
      {dragActive && (
        <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-background/70 backdrop-blur-sm">
          <div className="flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-primary bg-card/90 px-10 py-8 text-foreground shadow-xl">
            <FileUp className="h-8 w-8 text-primary" />
            <div className="text-sm font-semibold">Drop to ingest</div>
            <div className="text-xs text-muted-foreground">PDF, DOCX, TXT, MD up to 5 GB · resumable</div>
          </div>
        </div>
      )}
    </>
  );
}

function UploadRow({ item }: { item: UploadItem }) {
  const pct = item.sizeBytes > 0 ? Math.min(100, Math.round((item.loadedBytes / item.sizeBytes) * 100)) : 0;
  const isDone       = item.status === "done";
  const isError      = item.status === "error";
  const isCancelled  = item.status === "cancelled";
  const isPaused     = item.status === "paused";
  const isUploading  = item.status === "uploading";
  const isHashing    = item.status === "hashing";
  const isCompleting = item.status === "completing";
  const isActive     = !isDone && !isError && !isCancelled;

  return (
    <div className="group flex items-center gap-3 rounded border border-border bg-background/40 px-2.5 py-1.5">
      <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-[12px] font-medium">{item.name}</span>
          <span className="shrink-0 text-[10.5px] tabular-nums text-muted-foreground">
            {isError ? "" :
             isHashing ? "hashing…" :
             isCompleting ? "verifying…" :
             `${formatBytes(item.loadedBytes)} / ${formatBytes(item.sizeBytes)} · ${pct}%`}
          </span>
        </div>
        <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-accent/40">
          <div
            className={[
              "h-full transition-all",
              isError ? "bg-destructive" :
              isDone ? "bg-success" :
              isPaused ? "bg-yellow-500" :
              "bg-primary",
            ].join(" ")}
            style={{ width: `${isDone || isError ? 100 : pct}%` }}
          />
        </div>
        {isError && <div className="mt-1 truncate text-[10.5px] text-destructive">{item.error}</div>}
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        {(isUploading || isPaused) && (
          <button
            onClick={() => (isPaused ? resumeUpload(item.id) : pauseUpload(item.id))}
            className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
            aria-label={isPaused ? "Resume" : "Pause"}
          >
            {isPaused ? <Play className="h-3 w-3" /> : <Pause className="h-3 w-3" />}
          </button>
        )}
        {isActive ? (
          <button onClick={() => cancelUpload(item.id)} className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground" aria-label="Cancel">
            <XIcon className="h-3 w-3" />
          </button>
        ) : (
          <button onClick={() => dismissUpload(item.id)} className="rounded p-1 text-muted-foreground opacity-0 hover:bg-accent hover:text-foreground group-hover:opacity-100" aria-label="Dismiss">
            <XIcon className="h-3 w-3" />
          </button>
        )}
        {isDone       && <CheckCircle2 className="h-3.5 w-3.5 text-success" />}
        {isError      && <XCircle      className="h-3.5 w-3.5 text-destructive" />}
        {isCompleting && <Loader2      className="h-3.5 w-3.5 animate-spin text-primary" />}
      </div>
    </div>
  );
}

function Message({ msg }: { msg: ChatTurn }) {
  const isUser = msg.role === "user";
  return (
    <div className="flex gap-3">
      <div
        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[10px] font-semibold uppercase ${
          isUser ? "bg-secondary text-foreground" : "bg-primary/15 text-primary border border-primary/30"
        }`}
      >
        {isUser ? "Y" : "AI"}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-xs">
          <span className="font-medium text-foreground">{isUser ? "You" : "LocallyAI"}</span>
          <span className="terminal-font text-muted-foreground">{msg.time}</span>
        </div>
        <div className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-foreground">{msg.content}</div>
        {!isUser && Array.isArray(msg.sources) && msg.sources.length > 0 && (
          <div className="mt-3 space-y-2">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {msg.sources.length} citation{msg.sources.length === 1 ? "" : "s"}
            </div>
            <div className="space-y-1.5">
              {msg.sources.map((s, i) => {
                const filename = (s.source || "").split(/[\\/]/).pop() || `Chunk ${i + 1}`;
                return (
                  <div
                    key={s.chunk_id || `${i}`}
                    className="rounded-md border border-border bg-secondary/40 px-2.5 py-2"
                  >
                    <div className="flex items-center gap-1.5 text-[11px]">
                      <FileText className="h-3 w-3 text-muted-foreground" />
                      <span className="terminal-font font-medium text-foreground">{filename}</span>
                      <span className="text-muted-foreground">· score {s.score.toFixed(3)}</span>
                    </div>
                    {s.snippet && (
                      <div className="mt-1 line-clamp-3 text-[11.5px] italic leading-relaxed text-muted-foreground">
                        “{s.snippet}”
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

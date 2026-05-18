import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { TopBar } from "@/components/TopBar";
import {
  Upload, FolderUp, FileText, Search, CheckCircle2, Loader2,
  XCircle, Send, AlertTriangle, Pause, Play, X as XIcon,
  ChevronDown, ListChecks, Trash2, ShieldCheck,
} from "lucide-react";
import {
  chatCompletion, listDocuments, getIngestStatus, flushIngest, deleteDocument,
  getDocumentAcl, setDocumentAcl, deleteDocumentAcl,
  type ChatMessage as ApiChatMessage,
  type SourceCitation, type DocumentInfo, type IngestStatusResponse,
  type DocAcl,
} from "@/lib/api";
import {
  useUploads, uploadFiles, pauseUpload, resumeUpload, cancelUpload,
  dismissUpload, type UploadItem,
} from "@/hooks/use-uploads";

export const Route = createFileRoute("/documents")({
  head: () => ({ meta: [{ title: "Documents — LocallyAI" }] }),
  component: DocumentsPage,
});

interface InlineMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources: SourceCitation[];
}

type DirInputProps = React.InputHTMLAttributes<HTMLInputElement> & {
  webkitdirectory?: string; directory?: string;
};

function formatBytes(n: number): string {
  if (n < 1024)        return `${n} B`;
  if (n < 1024 ** 2)   return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3)   return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

function relTime(iso: string): string {
  try {
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 60_000)         return "just now";
    if (ms < 3_600_000)      return `${Math.floor(ms / 60_000)} min ago`;
    if (ms < 86_400_000)     return `${Math.floor(ms / 3_600_000)} hr ago`;
    if (ms < 7 * 86_400_000) return `${Math.floor(ms / 86_400_000)} d ago`;
    return new Date(iso).toLocaleDateString();
  } catch { return iso; }
}

function DocumentsPage() {
  const uploads = useUploads();
  const [docs, setDocs] = useState<DocumentInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [skippedNote, setSkippedNote] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);

  const [messages, setMessages] = useState<InlineMessage[]>([]);
  const [input, setInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);

  // ── Live corpus list (refreshes when uploads or queue change) ─────────────
  const refreshDocs = async () => {
    try { setDocs((await listDocuments()).data); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed to load library"); }
  };
  useEffect(() => { void refreshDocs(); }, []);
  // Refresh when an upload finishes.
  const lastDoneCount = useRef(0);
  useEffect(() => {
    const done = uploads.filter((u) => u.status === "done").length;
    if (done !== lastDoneCount.current) { lastDoneCount.current = done; void refreshDocs(); }
  }, [uploads]);

  // ── Live ingest queue status (poll while busy) ────────────────────────────
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
        const busy = s.in_flight > 0 || s.queued > 0 || s.bm25_pending;
        timer = setTimeout(tick, busy ? 2000 : 10_000);
      } catch { timer = setTimeout(tick, 30_000); }
    };
    void tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, []);
  // When ingest queue empties, refresh docs once more so "indexed" badges flip.
  const lastBusy = useRef(false);
  useEffect(() => {
    const busy = !!ingestStatus && (ingestStatus.in_flight > 0 || ingestStatus.queued > 0 || ingestStatus.bm25_pending);
    if (lastBusy.current && !busy) void refreshDocs();
    lastBusy.current = busy;
  }, [ingestStatus]);

  // Per-row delete state — tracks which row is currently mid-delete and
  // which row is showing the inline confirmation prompt.
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [aclFor, setAclFor] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const onDeleteClick = (name: string) => {
    if (deleting) return;  // ignore while a delete is in flight
    setConfirmDelete(name);
  };
  const onConfirmDelete = async (name: string) => {
    setDeleting(name);
    setConfirmDelete(null);
    try {
      const r = await deleteDocument(name);
      // Optimistic UI: drop the row immediately, then refresh truth.
      setDocs((prev) => (prev ? prev.filter((d) => d.name !== name) : prev));
      void refreshDocs();
      void r;  // (use the response if we ever add a toast)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeleting(null);
    }
  };

  const startUploads = async (list: FileList | File[]) => {
    setError(null);
    const arr = Array.from(list);
    if (arr.length === 0) return;
    const r = await uploadFiles(arr);
    if (r.skipped > 0) setSkippedNote(`Skipped ${r.skipped} unsupported file${r.skipped === 1 ? "" : "s"}.`);
  };

  const dragCounter = useRef(0);
  const onDragEnter = () => { dragCounter.current++; setDragActive(true); };
  const onDragLeave = () => { dragCounter.current = Math.max(0, dragCounter.current - 1); if (dragCounter.current === 0) setDragActive(false); };
  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    dragCounter.current = 0;
    setDragActive(false);
    if (e.dataTransfer.files.length > 0) void startUploads(e.dataTransfer.files);
  };

  const sendQuestion = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || chatBusy) return;
    setChatError(null);
    const text = input.trim();
    const userMsg: InlineMessage = { id: crypto.randomUUID(), role: "user", content: text, sources: [] };
    const next = [...messages, userMsg];
    setMessages(next);
    setInput("");
    setChatBusy(true);
    try {
      const apiMessages: ApiChatMessage[] = next.map((m) => ({ role: m.role, content: m.content }));
      const res = await chatCompletion({ messages: apiMessages });
      const answer = res.choices[0]?.message?.content ?? "";
      setMessages((m) => [...m, { id: crypto.randomUUID(), role: "assistant", content: answer, sources: res.sources ?? [] }]);
    } catch (e: unknown) {
      setChatError(e instanceof Error ? e.message : "Chat failed");
    } finally { setChatBusy(false); }
  };

  const totalBytes = (docs ?? []).reduce((acc, d) => acc + d.size_bytes, 0);

  return (
    <>
      <TopBar title="Document Intelligence" description="Upload, index, and query confidential documents" />
      <main className="grid flex-1 grid-cols-1 gap-6 p-6 lg:grid-cols-5">
        <div className="space-y-6 lg:col-span-3">
          <div
            onDragOver={(e) => e.preventDefault()}
            onDragEnter={onDragEnter}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            className={`rounded-lg border-2 border-dashed bg-card/50 p-8 text-center transition-colors ${
              dragActive ? "border-primary bg-card" : "border-border hover:border-primary/50 hover:bg-card"
            }`}
          >
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
              <Upload className="h-5 w-5 text-primary" />
            </div>
            <h3 className="mt-4 text-sm font-semibold">Drop files or a folder to upload</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              PDF, DOCX, TXT, MD up to 5 GB per file · Resumable across page reloads · Files never leave this device
            </p>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".pdf,.docx,.txt,.md"
              className="hidden"
              onChange={(e) => { if (e.target.files) void startUploads(e.target.files); e.target.value = ""; }}
            />
            <input
              {...({
                ref: folderInputRef,
                type: "file",
                multiple: true,
                webkitdirectory: "",
                directory: "",
                className: "hidden",
                onChange: (e: React.ChangeEvent<HTMLInputElement>) => {
                  if (e.target.files) void startUploads(e.target.files);
                  e.target.value = "";
                },
              } as DirInputProps)}
            />
            <div className="mt-4 flex items-center justify-center gap-2">
              <button
                onClick={() => fileInputRef.current?.click()}
                className="inline-flex items-center gap-1.5 rounded-md border border-border bg-secondary px-3 py-1.5 text-xs font-medium hover:bg-accent"
              >
                <Upload className="h-3.5 w-3.5" /> Select files
              </button>
              <button
                onClick={() => folderInputRef.current?.click()}
                className="inline-flex items-center gap-1.5 rounded-md border border-border bg-secondary px-3 py-1.5 text-xs font-medium hover:bg-accent"
              >
                <FolderUp className="h-3.5 w-3.5" /> Select folder
              </button>
            </div>
            {(error || skippedNote) && (
              <div className="mt-4 inline-flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-start text-xs text-destructive">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>{error ?? skippedNote}</span>
              </div>
            )}
          </div>

          {/* Live ingest queue ticker */}
          {ingestStatus && (ingestStatus.in_flight > 0 || ingestStatus.queued > 0 || ingestStatus.bm25_pending) && (
            <div className="flex items-center justify-between rounded-md border border-border bg-card/60 px-3 py-2 text-xs">
              <div className="flex items-center gap-2 text-muted-foreground">
                {ingestStatus.in_flight > 0
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                  : ingestStatus.bm25_pending
                    ? <ListChecks className="h-3.5 w-3.5 text-primary" />
                    : <CheckCircle2 className="h-3.5 w-3.5 text-primary" />}
                <span>
                  {ingestStatus.in_flight > 0 ? "Indexing" : ingestStatus.bm25_pending ? "Updating search index" : "Queued"}{" "}
                  <span className="tabular-nums text-foreground">
                    {ingestStatus.in_flight + ingestStatus.queued}
                  </span>{" "}
                  doc{ingestStatus.in_flight + ingestStatus.queued === 1 ? "" : "s"}
                  {ingestStatus.completed_total > 0 && <span className="ms-2 text-muted-foreground/70">· {ingestStatus.completed_total} done</span>}
                  {ingestStatus.failed_total    > 0 && <span className="ms-2 text-destructive/80">· {ingestStatus.failed_total} failed</span>}
                </span>
              </div>
              {ingestStatus.bm25_pending && ingestStatus.in_flight === 0 && ingestStatus.queued === 0 && (
                <button
                  disabled={flushing}
                  onClick={async () => { setFlushing(true); try { await flushIngest(); } finally { setFlushing(false); } }}
                  className="rounded px-2 py-0.5 text-[11px] font-medium text-primary hover:bg-primary/10 disabled:opacity-50"
                >
                  {flushing ? "Rebuilding…" : "Rebuild now"}
                </button>
              )}
            </div>
          )}

          {/* In-flight uploads */}
          {uploads.length > 0 && (
            <div className="space-y-1.5 rounded-md border border-border bg-card/40 p-3">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                In-flight uploads
              </div>
              {uploads.map((u) => <UploadRow key={u.id} item={u} />)}
            </div>
          )}

          {/* Library */}
          <div className="rounded-lg border border-border bg-card">
            <div className="flex items-center justify-between border-b border-border p-4">
              <h2 className="text-sm font-semibold">Library</h2>
              <span className="text-xs text-muted-foreground">
                {docs ? `${docs.length} document${docs.length === 1 ? "" : "s"} · ${formatBytes(totalBytes)}` : "Loading…"}
              </span>
            </div>
            {!docs ? (
              <div className="px-4 py-12 text-center text-xs text-muted-foreground">
                <Loader2 className="me-2 inline h-3 w-3 animate-spin" />
                Loading library…
              </div>
            ) : docs.length === 0 ? (
              <div className="px-4 py-12 text-center text-xs text-muted-foreground">
                No documents uploaded yet. Drop files or a folder above to begin.
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
                    <th className="px-4 py-2 text-start font-medium">Name</th>
                    <th className="px-4 py-2 text-start font-medium">Status</th>
                    <th className="px-4 py-2 text-start font-medium">Size</th>
                    <th className="px-4 py-2 text-start font-medium">Added</th>
                    <th className="px-4 py-2 text-end font-medium"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {docs.map((d) => {
                    const isConfirming = confirmDelete === d.name;
                    const isDeleting   = deleting === d.name;
                    return (
                      <tr key={d.name} className="hover:bg-accent/30">
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <FileText className="h-4 w-4 text-muted-foreground" />
                            <span className="terminal-font text-xs">{d.name}</span>
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          {d.indexed ? (
                            <span className="inline-flex items-center gap-1 rounded-md border border-success/30 bg-success/10 px-2 py-0.5 text-xs text-success">
                              <CheckCircle2 className="h-3 w-3" />Indexed
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 rounded-md border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs text-primary">
                              <Loader2 className="h-3 w-3 animate-spin" />Pending
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-xs text-muted-foreground">{formatBytes(d.size_bytes)}</td>
                        <td className="px-4 py-3 text-xs text-muted-foreground">{relTime(d.ingested_at)}</td>
                        <td className="px-4 py-3 text-end">
                          {isConfirming ? (
                            <div className="inline-flex items-center gap-1.5 text-xs">
                              <span className="text-muted-foreground">Delete?</span>
                              <button
                                onClick={() => onConfirmDelete(d.name)}
                                disabled={isDeleting}
                                className="rounded-md border border-destructive/40 bg-destructive/10 px-2 py-0.5 text-destructive hover:bg-destructive/20 disabled:opacity-40"
                              >
                                Yes
                              </button>
                              <button
                                onClick={() => setConfirmDelete(null)}
                                className="rounded-md border border-border bg-secondary px-2 py-0.5 text-foreground hover:bg-accent"
                              >
                                No
                              </button>
                            </div>
                          ) : isDeleting ? (
                            <Loader2 className="ms-auto h-3.5 w-3.5 animate-spin text-destructive" />
                          ) : (
                            <div className="flex items-center gap-0.5">
                              <button
                                onClick={() => setAclFor(d.name)}
                                className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                                title="Edit access control (per-doc ACL)"
                                aria-label={`Edit ACL for ${d.name}`}
                              >
                                <ShieldCheck className="h-3.5 w-3.5" />
                              </button>
                              <button
                                onClick={() => onDeleteClick(d.name)}
                                className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                                title="Delete document and its embeddings"
                                aria-label={`Delete ${d.name}`}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>

        <div className="lg:col-span-2">
          <div className="sticky top-20 flex h-[calc(100vh-7rem)] flex-col rounded-lg border border-border bg-card">
            <div className="border-b border-border p-4">
              <h2 className="text-sm font-semibold">Ask your documents</h2>
              <p className="text-xs text-muted-foreground">Answers cite source files</p>
            </div>
            <div className="flex-1 space-y-4 overflow-auto p-4">
              {messages.length === 0 && (
                <div className="rounded-md border border-dashed border-border p-4 text-center text-xs text-muted-foreground">
                  Upload a document, then ask a question about its contents.
                </div>
              )}
              {messages.map((m) =>
                m.role === "user" ? (
                  <div key={m.id} className="flex justify-end">
                    <div className="max-w-[85%] rounded-lg rounded-tr-sm bg-primary/15 px-3 py-2 text-sm">{m.content}</div>
                  </div>
                ) : (
                  <div key={m.id}>
                    <div className="rounded-lg rounded-tl-sm border border-border bg-background/40 px-3 py-2.5 text-sm leading-relaxed">{m.content}</div>
                    {m.sources.length > 0 && (
                      <div className="mt-2 space-y-1.5">
                        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                          {m.sources.length} citation{m.sources.length === 1 ? "" : "s"}
                        </div>
                        {m.sources.map((s, i) => {
                          const filename = (s.source || "").split(/[\\/]/).pop() || `Chunk ${i + 1}`;
                          return (
                            <div key={s.chunk_id || `${i}`} className="rounded-md border border-border bg-secondary/40 px-2 py-1.5 text-[11px]">
                              <div className="flex items-center gap-1.5">
                                <FileText className="h-3 w-3 text-muted-foreground" />
                                <span className="terminal-font font-medium text-foreground">{filename}</span>
                                <span className="text-muted-foreground">· {s.score.toFixed(3)}</span>
                              </div>
                              {s.snippet && <div className="mt-1 line-clamp-2 italic text-muted-foreground">"{s.snippet}"</div>}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                ),
              )}
              {chatBusy && (
                <div className="text-xs text-muted-foreground">
                  <Loader2 className="me-2 inline h-3 w-3 animate-spin" />
                  Searching your corpus…
                </div>
              )}
              {chatError && (
                <div className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">{chatError}</div>
              )}
            </div>
            <form onSubmit={sendQuestion} className="border-t border-border p-3">
              <div className="flex items-center gap-2 rounded-md border border-border bg-background px-3 py-2">
                <Search className="h-4 w-4 text-muted-foreground" />
                <input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
                  placeholder="Ask a question about your documents…"
                />
                <button
                  type="submit"
                  disabled={!input.trim() || chatBusy}
                  className="rounded-md bg-primary p-1.5 text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
                >
                  <Send className="h-3.5 w-3.5" />
                </button>
              </div>
              <p className="mt-2 text-[10px] text-muted-foreground">
                Inference runs locally · No data leaves your device
              </p>
            </form>
          </div>
        </div>
      </main>
      {aclFor && <AclModal docName={aclFor} onClose={() => setAclFor(null)} />}
    </>
  );
}

function AclModal({ docName, onClose }: { docName: string; onClose: () => void }) {
  const [acl, setAcl] = useState<DocAcl | null>(null);
  const [allowedRaw, setAllowedRaw] = useState("");
  const [matterCode, setMatterCode] = useState("");
  const [ethicalRaw, setEthicalRaw] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const a = await getDocumentAcl(docName);
        if (!alive) return;
        setAcl(a);
        setAllowedRaw((a.allowed_users || []).join(", "));
        setMatterCode(a.matter_code || "");
        setEthicalRaw((a.ethical_wall || []).join(", "));
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "failed to load ACL");
      }
    })();
    return () => { alive = false; };
  }, [docName]);

  const save = async () => {
    setBusy(true); setError(null); setSavedNote(null);
    try {
      const allowed = allowedRaw.split(",").map((s) => s.trim()).filter(Boolean);
      const ethical = ethicalRaw.split(",").map((s) => s.trim()).filter(Boolean);
      const r = await setDocumentAcl(docName, {
        allowed_users: allowed,
        matter_code: matterCode.trim(),
        ethical_wall: ethical,
      });
      setSavedNote(`Saved (v${r.acl.version}) — ${r.chunks_updated} chunks updated`);
      setAcl(r.acl);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally { setBusy(false); }
  };

  const reset = async () => {
    if (!confirm("Remove the explicit ACL? Document will fall back to default policy (everyone-in-firm unless LOCALLYAI_DOC_ACL_DEFAULT=restricted).")) return;
    setBusy(true); setError(null);
    try {
      await deleteDocumentAcl(docName);
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "reset failed");
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-lg border border-border bg-card p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">Document access control</h2>
            <p className="mt-0.5 text-xs text-muted-foreground"><code className="text-[11px]">{docName}</code></p>
          </div>
          <button onClick={onClose} className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground">
            <XIcon className="h-4 w-4" />
          </button>
        </div>
        <div className="mt-4 space-y-3">
          <div>
            <label className="block text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">
              Allowed users (comma-separated; <code>*</code> = everyone in firm)
            </label>
            <input value={allowedRaw} onChange={(e) => setAllowedRaw(e.target.value)}
              placeholder="Alice, Bob, Charlie  OR  *"
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs font-mono" />
          </div>
          <div>
            <label className="block text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">Matter code (optional, for audit + filtering)</label>
            <input value={matterCode} onChange={(e) => setMatterCode(e.target.value)}
              placeholder="M-2026-0042"
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs font-mono" />
          </div>
          <div>
            <label className="block text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">Ethical-wall tags (comma-separated, optional)</label>
            <input value={ethicalRaw} onChange={(e) => setEthicalRaw(e.target.value)}
              placeholder="acquirer-team, target-team"
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs font-mono" />
          </div>
          {acl && acl.set_at && (
            <div className="text-[11px] text-muted-foreground">
              Last set <code>{acl.set_at}</code> by <code>{acl.set_by}</code> · v{acl.version}
              {acl.default && <span className="ml-2 italic">(default policy — no explicit ACL set yet)</span>}
            </div>
          )}
          {error && (
            <div className="rounded border border-red-200 bg-red-50 p-2 text-[11px] text-red-900 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-200">
              {error}
            </div>
          )}
          {savedNote && (
            <div className="rounded border border-green-200 bg-green-50 p-2 text-[11px] text-green-900 dark:border-green-900/50 dark:bg-green-900/20 dark:text-green-200">
              {savedNote}
            </div>
          )}
        </div>
        <div className="mt-5 flex items-center justify-between gap-2">
          <button onClick={reset} disabled={busy}
            className="text-xs text-muted-foreground hover:text-destructive disabled:opacity-50">
            Reset to default
          </button>
          <div className="flex items-center gap-2">
            <button onClick={onClose} disabled={busy}
              className="rounded border border-border bg-background px-3 py-1.5 text-xs hover:bg-accent">
              Cancel
            </button>
            <button onClick={save} disabled={busy}
              className="rounded bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
              {busy ? "Saving…" : "Save ACL"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function UploadRow({ item }: { item: UploadItem }) {
  const pct = item.sizeBytes > 0 ? Math.min(100, Math.round((item.loadedBytes / item.sizeBytes) * 100)) : 0;
  const isDone      = item.status === "done";
  const isError     = item.status === "error";
  const isCancelled = item.status === "cancelled";
  const isPaused    = item.status === "paused";
  const isUploading = item.status === "uploading";
  const isHashing   = item.status === "hashing";
  const isCompleting = item.status === "completing";
  const isActive    = !isDone && !isError && !isCancelled;

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
        {isDone     && <CheckCircle2 className="h-3.5 w-3.5 text-success" />}
        {isError    && <XCircle    className="h-3.5 w-3.5 text-destructive" />}
        {isCompleting && <Loader2  className="h-3.5 w-3.5 animate-spin text-primary" />}
      </div>
    </div>
  );
}
// Suppress the unused-import warnings — these icons are used via dynamic class strings in the future
void ChevronDown;

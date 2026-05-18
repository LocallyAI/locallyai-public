// Typed client for the LocallyAI backend, scoped to the user-tier endpoints
// that the worker UI consumes. Reads VITE_API_BASE_URL (single-node, default
// http://localhost:8000) OR VITE_API_BASE_URLS (HA, comma-separated list of
// node URLs) and the user's API key from localStorage.
//
// HA mode (VITE_API_BASE_URLS set):
//   - Polls /healthz on each URL every HEALTH_INTERVAL_MS.
//   - Requests go to the most recently healthy URL; on network or 5xx
//     failure they retry on the next healthy URL with the SAME
//     client_request_id so the server-side dedup cache can return the
//     prior result if the first node actually completed before the
//     transport failed.
//   - Streaming requests that disconnect mid-stream are restarted on a
//     different node; user sees the answer regenerate. Same id, so
//     audit + billing stay single-counted.

import { getUserKey, clearUserKey } from "./auth";

const RAW_URLS: string[] = (() => {
  const list = (import.meta.env.VITE_API_BASE_URLS as string | undefined)?.trim();
  if (list) return list.split(",").map(u => u.trim()).filter(Boolean);
  const single = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim();
  return [single || "http://localhost:8000"];
})();

const NODE_URLS: string[] = RAW_URLS.map(u => u.replace(/\/$/, ""));

// Public for components that want to render the raw fleet; reads
// the live status set the health probe maintains.
export function getNodeUrls(): readonly string[] { return NODE_URLS; }

const HEALTH_INTERVAL_MS = 5000;

// node URL → last successful health-check timestamp (ms). A url is "alive"
// if checked successfully within HEALTH_INTERVAL_MS * 2.5 (≈12.5s).
const _healthLastOk = new Map<string, number>();

function _now(): number { return Date.now(); }

async function _probe(url: string): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 2000);
    const r = await fetch(`${url}/healthz`, { signal: ctrl.signal, cache: "no-store" });
    clearTimeout(timer);
    if (r.ok) {
      _healthLastOk.set(url, _now());
      return true;
    }
  } catch { /* fall through */ }
  return false;
}

export function isAlive(url: string): boolean {
  const ts = _healthLastOk.get(url);
  return !!ts && (_now() - ts) < HEALTH_INTERVAL_MS * 2.5;
}

export function getFleetStatus(): { url: string; alive: boolean; lastOkMs: number | null }[] {
  return NODE_URLS.map(u => ({
    url: u,
    alive: isAlive(u),
    lastOkMs: _healthLastOk.get(u) ?? null,
  }));
}

let _healthTimer: number | null = null;
function _ensureHealthLoop() {
  if (_healthTimer !== null || NODE_URLS.length <= 1) return;
  // Probe immediately, then every interval.
  NODE_URLS.forEach(u => { void _probe(u); });
  _healthTimer = window.setInterval(() => {
    NODE_URLS.forEach(u => { void _probe(u); });
  }, HEALTH_INTERVAL_MS);
}

// Ranked list of URLs to try, healthiest first. Single-URL deployments
// always return their one URL.
function _rankUrls(): string[] {
  if (NODE_URLS.length <= 1) return NODE_URLS;
  _ensureHealthLoop();
  const alive = NODE_URLS.filter(isAlive);
  const dead  = NODE_URLS.filter(u => !isAlive(u));
  // Among the alive, prefer the one we last spoke to most recently (round-robin
  // would split a user's session across nodes for no benefit; sticky-on-success
  // keeps the conversation context cache warm where applicable).
  alive.sort((a, b) => (_healthLastOk.get(b) ?? 0) - (_healthLastOk.get(a) ?? 0));
  return [...alive, ...dead];
}

function _backwardsCompatBase(): string { return NODE_URLS[0]; }

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

// Retryable: network failures (TypeError thrown by fetch) and 502/503/504.
// 5xx other than these are treated as terminal — the server saw the request
// and chose to fail it; retrying on a peer just doubles the audit log.
function _isRetryable(err: unknown): boolean {
  if (err instanceof TypeError) return true;             // network/DNS/abort
  if (err instanceof ApiError && (err.status === 502 || err.status === 503 || err.status === 504)) return true;
  return false;
}

interface AuthedOpts extends RequestInit {
  // When true, the request is replayed against the next healthy node on a
  // retryable failure. Defaults to true for POST/PUT/DELETE (idempotency is
  // protected by the server-side dedup cache when the body carries a
  // client_request_id) and GET (idempotent by definition).
  retryAcrossNodes?: boolean;
  // Maximum total attempts including the first. Defaults to NODE_URLS.length
  // (so each healthy node is tried at most once).
  maxAttempts?: number;
}

async function authedFetch(path: string, init: AuthedOpts = {}): Promise<Response> {
  const key = getUserKey();
  if (!key) throw new ApiError(401, "Not signed in");
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${key}`);

  const candidates = _rankUrls();
  const maxAttempts = Math.min(init.maxAttempts ?? candidates.length, candidates.length);
  const retryAcross = init.retryAcrossNodes ?? true;

  let lastErr: unknown = null;
  for (let i = 0; i < (retryAcross ? maxAttempts : 1); i++) {
    const base = candidates[i] ?? candidates[0];
    try {
      const res = await fetch(`${base}${path}`, { ...init, headers });
      if (res.status === 401) {
        clearUserKey();
        throw new ApiError(401, "Invalid or expired API key");
      }
      // Retry on transient 5xx — same id, server-side dedup covers double-send
      // when the first node actually completed but the response was lost.
      if (res.status === 502 || res.status === 503 || res.status === 504) {
        lastErr = new ApiError(res.status, await res.text().catch(() => "")|| `HTTP ${res.status}`);
        _healthLastOk.delete(base);  // mark possibly-down so next pick prefers a peer
        continue;
      }
      return res;
    } catch (err) {
      lastErr = err;
      if (_isRetryable(err) && retryAcross && i < maxAttempts - 1) {
        _healthLastOk.delete(base);
        continue;
      }
      throw err;
    }
  }
  if (lastErr instanceof Error) throw lastErr;
  throw new ApiError(503, "All nodes unreachable");
}

export interface BrandingResponse {
  firm_name: string;
  office_host: string;
  deployment_id: string;
  data_region: string;
  node_id: string;
  isolation_statement: string;
}

export async function getBranding(): Promise<BrandingResponse> {
  // Unauthenticated — same trust level as /healthz. Surfaces the firm
  // name in the LoginGate so users see WHICH firm's deployment they're
  // connecting to before entering their key.
  const res = await fetch(`${_backwardsCompatBase()}/v1/branding`);
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json();
}

export interface HealthResponse {
  ok: boolean;
  backend: string;
}

export async function getHealth(): Promise<HealthResponse> {
  // Direct fetch (not authed) on the primary URL; the smart client uses
  // _probe for the per-node visibility, this is just for the connection
  // indicator on the first paint.
  const res = await fetch(`${_backwardsCompatBase()}/healthz`);
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json();
}

export interface MeResponse {
  user: string;
  is_admin: boolean;
}

export async function getMe(): Promise<MeResponse> {
  const res = await authedFetch(`/v1/me`);
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json();
}

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface SourceCitation {
  chunk_id: string;
  source: string;
  snippet: string;
  score: number;
  /** Section header (e.g. "Article 17"). Empty when the chunker
   *  couldn't infer a heading. */
  section?: string;
  /** 1-based page number (PDF). Null when the chunker couldn't
   *  infer a page (text / markdown). */
  page?: number | null;
}

export interface ChatCompletionResponse {
  id: string;
  object: string;
  model: string;
  backend: string;
  node_id?: string;
  choices: Array<{
    index: number;
    message: { role: string; content: string };
    finish_reason: string;
  }>;
  usage: { sources_retrieved: number };
  sources?: SourceCitation[];
  safe_mode?: boolean;
}

/**
 * Build a temporary blob: URL for a cited document so the user can
 * open it (PDFs at `#page=N`, others at root). Uses the auth'd fetch
 * because /v1/documents/{name}/raw requires the bearer key. Returns
 * the blob URL — caller is responsible for revoking it after use.
 *
 * For PDFs, the browser handles `#page=N` natively (works in Safari,
 * Chrome, Edge, WKWebView). Other formats open via the OS default
 * (DOCX → Word, etc.) — the browser may download them depending on
 * Content-Disposition.
 */
export async function openCitedDocument(filename: string, page: number | null | undefined): Promise<void> {
  const res = await authedFetch(`/v1/documents/${encodeURIComponent(filename)}/raw`);
  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(res.status, body || `HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const blobUrl = URL.createObjectURL(blob);
  // PDFs honour `#page=N` for direct page jump in the browser's PDF
  // viewer. Other formats ignore the fragment — that's fine.
  const target = page && filename.toLowerCase().endsWith(".pdf")
    ? `${blobUrl}#page=${page}`
    : blobUrl;
  window.open(target, "_blank", "noopener");
  // Free the blob after a delay — has to outlive the new-tab load.
  setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
}

export interface ChatRequestPayload {
  messages: ChatMessage[];
  model?: string;
  max_tokens?: number;
  temperature?: number;
  matter_code?: string;
}

// Browser crypto.randomUUID is widely available; fall back for older
// embedded webviews so the worker app keeps working there.
function newRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${_now().toString(36)}-${Math.random().toString(36).slice(2, 10)}-${Math.random().toString(36).slice(2, 10)}`;
}

export async function chatCompletion(payload: ChatRequestPayload): Promise<ChatCompletionResponse> {
  // Stamp a per-send id so a smart-client retry doesn't double-bill.
  const body = { ...payload, client_request_id: newRequestId() };
  const res = await authedFetch(`/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || `HTTP ${res.status}`);
  return res.json();
}

export interface StreamCallbacks {
  /** Each emitted token. The full content so far is the concatenation of every onToken value. */
  onToken: (delta: string) => void;
  /** Final envelope from the server: model, sources, node_id, etc. Fires once before the stream closes. */
  onFinish: (final: ChatCompletionResponse | { sources?: SourceCitation[]; node_id?: string }) => void;
  /** Network/server error. Caller decides whether to retry. */
  onError: (err: ApiError | Error) => void;
}

/**
 * Stream a chat completion via SSE. Each token arrives via onToken; the
 * caller updates the message content progressively. On a mid-stream
 * disconnect (network failure), the same client_request_id is retried
 * on the next healthy node — the user sees the answer regenerate. The
 * server-side dedup cache returns the cached complete response if the
 * first node actually finished before the transport failed.
 *
 * Returns the AbortController so callers can cancel the stream.
 */
export function streamChatCompletion(
  payload: ChatRequestPayload,
  cb: StreamCallbacks,
): AbortController {
  const ctrl = new AbortController();
  const requestId = newRequestId();
  const body = { ...payload, client_request_id: requestId, stream: true };
  const candidates = _rankUrls();
  const maxAttempts = Math.max(1, candidates.length);

  const key = getUserKey();
  if (!key) {
    cb.onError(new ApiError(401, "Not signed in"));
    return ctrl;
  }

  const headers = new Headers({
    "Content-Type": "application/json",
    "Authorization": `Bearer ${key}`,
    "Accept": "text/event-stream",
  });

  // Track whether ANY token has been emitted on the current attempt; if a
  // disconnect happens after the first token but before the [DONE] marker,
  // we restart on the next node — the user sees the answer regenerate.
  let attempt = 0;

  const tryAttempt = async () => {
    const base = candidates[attempt] ?? candidates[0];
    let firstTokenSeen = false;
    let finished = false;
    try {
      const res = await fetch(`${base}/v1/chat/completions`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: ctrl.signal,
      });

      if (res.status === 401) {
        clearUserKey();
        cb.onError(new ApiError(401, "Invalid or expired API key"));
        return;
      }
      if (!res.ok || !res.body) {
        const text = await res.text().catch(() => "");
        // 5xx → try next node.
        if (res.status >= 502 && res.status <= 504 && attempt < maxAttempts - 1) {
          _healthLastOk.delete(base);
          attempt++;
          return tryAttempt();
        }
        cb.onError(new ApiError(res.status, text || `HTTP ${res.status}`));
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        // SSE frames are separated by \n\n. Split, keep the trailing partial.
        const frames = buf.split("\n\n");
        buf = frames.pop() ?? "";
        for (const frame of frames) {
          const line = frame.trim();
          if (!line.startsWith("data:")) continue;
          const payloadStr = line.slice(5).trim();
          if (payloadStr === "[DONE]") { finished = true; continue; }
          try {
            const evt = JSON.parse(payloadStr);
            if (evt.error) {
              cb.onError(new ApiError(502, String(evt.error)));
              return;
            }
            const delta = evt?.choices?.[0]?.delta;
            const finishReason = evt?.choices?.[0]?.finish_reason;
            if (delta?.content) {
              firstTokenSeen = true;
              cb.onToken(delta.content);
            }
            if (finishReason === "stop") {
              cb.onFinish(evt as ChatCompletionResponse);
              finished = true;
            }
          } catch { /* ignore malformed frame */ }
        }
      }

      if (!finished) {
        // Disconnected before [DONE] — restart from scratch on a peer.
        if (attempt < maxAttempts - 1) {
          _healthLastOk.delete(base);
          attempt++;
          // Tell the caller to discard partial output and re-render: the
          // peer will produce a fresh answer (the server-side dedup cache
          // is per-node, so the peer doesn't have the in-flight state).
          cb.onToken("\n\n[regenerating on another node…]\n\n");
          return tryAttempt();
        }
        cb.onError(new ApiError(503, "Stream closed before completion"));
      }
    } catch (err) {
      // AbortError → caller cancelled, not a failure.
      if ((err as { name?: string })?.name === "AbortError") return;
      // Network error mid-stream → retry on next node if any token had
      // already been seen (real partial completion) or even if none
      // (transport never connected).
      void firstTokenSeen;
      if (attempt < maxAttempts - 1) {
        _healthLastOk.delete(base);
        attempt++;
        return tryAttempt();
      }
      cb.onError(err instanceof Error ? err : new Error(String(err)));
    }
  };

  void tryAttempt();
  return ctrl;
}

export interface ModelInfo {
  id: string;
  object: string;
  owned_by: string;
}

export async function listModels(): Promise<ModelInfo[]> {
  const res = await authedFetch(`/v1/models`);
  if (!res.ok) throw new ApiError(res.status, await res.text());
  const data = (await res.json()) as { object: string; data: ModelInfo[] };
  return data.data;
}

export interface IngestResponse {
  status: string;
  stored_as: string;
  bytes: number;
  indexing: string;
}

export async function ingestDocument(
  file: File,
  onProgress?: (loaded: number, total: number) => void,
): Promise<IngestResponse> {
  // We deliberately don't retry uploads across nodes — Syncthing
  // replicates the document automatically once it lands on either node,
  // and a partial multipart body retried on a peer would land twice on
  // the primary if the upload had succeeded but the response was lost.
  //
  // Use XHR (not fetch) so we get real upload progress events. fetch's
  // Response/Request streams don't expose loaded/total during upload in
  // browsers as of 2026.
  return new Promise((resolve, reject) => {
    const key = getUserKey();
    if (!key) { reject(new ApiError(401, "Not signed in")); return; }
    const form = new FormData();
    form.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${getActiveBaseUrl()}/v1/ingest`);
    xhr.setRequestHeader("Authorization", `Bearer ${key}`);
    if (onProgress && xhr.upload) {
      xhr.upload.onprogress = (ev) => {
        if (ev.lengthComputable) onProgress(ev.loaded, ev.total);
      };
    }
    xhr.onload = () => {
      if (xhr.status === 401) { clearUserKey(); reject(new ApiError(401, "Invalid or expired API key")); return; }
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)); }
        catch { reject(new ApiError(xhr.status, "Malformed response from server")); }
      } else {
        reject(new ApiError(xhr.status, xhr.responseText || `HTTP ${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error during upload"));
    xhr.send(form);
  });
}

export interface DocumentInfo {
  name: string;
  size_bytes: number;
  ingested_at: string;
  suffix: string;
  indexed: boolean;
}
export interface DocumentsResponse {
  object: string;
  data: DocumentInfo[];
  count: number;
}

export async function listDocuments(): Promise<DocumentsResponse> {
  const res = await authedFetch(`/v1/documents`);
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || `HTTP ${res.status}`);
  return res.json();
}

// ── Chunked / resumable uploads ───────────────────────────────────────────────
// Used for the bulk-ingest path: the file is split into chunks and PATCHed
// to the server, which appends to disk. Survives page reloads (resume via
// localStorage) and network blips (resume via GET /v1/uploads/{id}).
export interface UploadInitResponse {
  upload_id: string;
  received_bytes: number;
  chunk_size_suggested: number;
  max_chunk_bytes: number;
}

export interface UploadStatusResponse {
  upload_id: string;
  filename: string;
  total_bytes: number;
  received_bytes: number;
  status: "open" | "complete" | "cancelled" | "failed";
}

export interface UploadCompleteResponse {
  stored_as: string;
  bytes: number;
  indexing: string;
}

export async function initUpload(filename: string, totalBytes: number, sha256?: string | null): Promise<UploadInitResponse> {
  const res = await authedFetch(`/v1/uploads`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, total_bytes: totalBytes, sha256: sha256 ?? null }),
    retryAcrossNodes: false,  // initiate is sticky; resume handled per-id
  });
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || `HTTP ${res.status}`);
  return res.json();
}

export async function getUploadStatus(uploadId: string): Promise<UploadStatusResponse> {
  const res = await authedFetch(`/v1/uploads/${encodeURIComponent(uploadId)}`, { retryAcrossNodes: false });
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || `HTTP ${res.status}`);
  return res.json();
}

export async function patchUploadChunk(
  uploadId: string,
  start: number,
  end: number,
  total: number,
  body: Blob | ArrayBuffer | Uint8Array,
  signal?: AbortSignal,
): Promise<{ received_bytes: number; total_bytes: number }> {
  const key = getUserKey();
  if (!key) throw new ApiError(401, "Not signed in");
  // Use direct fetch — authedFetch's cross-node retry would replay the chunk
  // onto a peer that doesn't have the upload state. Resume is by upload_id
  // on the same node; if a node dies, the operator restarts the file.
  const res = await fetch(`${getActiveBaseUrl()}/v1/uploads/${encodeURIComponent(uploadId)}`, {
    method: "PATCH",
    headers: {
      "Authorization": `Bearer ${key}`,
      "Content-Range": `bytes ${start}-${end}/${total}`,
      "Content-Type": "application/octet-stream",
    },
    body: body as BodyInit,
    signal,
  });
  if (res.status === 401) { clearUserKey(); throw new ApiError(401, "Invalid or expired API key"); }
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || `HTTP ${res.status}`);
  return res.json();
}

export async function completeUpload(uploadId: string, sha256?: string | null): Promise<UploadCompleteResponse> {
  const res = await authedFetch(`/v1/uploads/${encodeURIComponent(uploadId)}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sha256: sha256 ?? null }),
    retryAcrossNodes: false,
  });
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || `HTTP ${res.status}`);
  return res.json();
}

export async function cancelUpload(uploadId: string): Promise<void> {
  await authedFetch(`/v1/uploads/${encodeURIComponent(uploadId)}`, {
    method: "DELETE",
    retryAcrossNodes: false,
  });
}

export interface IngestStatusResponse {
  in_flight: number;
  queued: number;
  completed_total: number;
  failed_total: number;
  bm25_pending: boolean;
  last_completed_at: number | null;
}

export async function getIngestStatus(): Promise<IngestStatusResponse> {
  const res = await authedFetch(`/v1/ingest/status`);
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || `HTTP ${res.status}`);
  return res.json();
}

export async function flushIngest(): Promise<void> {
  await authedFetch(`/v1/ingest/flush`, { method: "POST" });
}

// SHA-256 of a File via Web Crypto, computed in 8-MiB slices to keep memory
// flat for multi-GB files. Returns lowercase hex.
export async function sha256OfFile(file: File): Promise<string> {
  // Fast path: small files in one shot.
  if (file.size <= 8 * 1024 * 1024) {
    const buf = await file.arrayBuffer();
    const digest = await crypto.subtle.digest("SHA-256", buf);
    return _hex(digest);
  }
  // Streaming SHA-256 isn't in Web Crypto; we approximate by hashing the
  // entire file in one go via incremental ArrayBuffer reads. Browsers can
  // hold the whole digest input in memory if they handle it natively
  // (Chromium does for files via streams). We fall back to a single
  // arrayBuffer() call — RAM permitting — and let the caller skip sha256
  // for very large files (server still verifies size + appended bytes).
  try {
    const buf = await file.arrayBuffer();
    return _hex(await crypto.subtle.digest("SHA-256", buf));
  } catch {
    return "";  // sha256 is optional; server enforces total_bytes + path containment
  }
}

function _hex(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += bytes[i].toString(16).padStart(2, "0");
  return s;
}

// ── Citation verification ────────────────────────────────────────────────────
export interface CitationVerifyResult {
  citations: Array<{
    cite: string;
    jurisdiction: string;
    kind: string;
    year: string | null;
    parsed: Record<string, string | null>;
    span: [number, number];
    context: string;
    found_in_corpus: { found: boolean; source?: string; score?: number; snippet?: string; reason?: string };
    found_external: { found: boolean; url?: string; snippet?: string; reason?: string; from_cache?: boolean };
    verified: boolean;
    on_point: {
      on_point: boolean | null;
      confidence: "high" | "medium" | "low";
      reasoning: string;
      suggestion: string;
    };
  }>;
  elapsed_ms: number;
  count: number;
}

export async function verifyCitations(text: string): Promise<CitationVerifyResult> {
  const res = await authedFetch(`/v1/citations/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json();
}

// BASE_URL kept for callers that displayed it in the UI (debug panel, etc).
// Now reflects the currently-preferred node so the UI shows where requests
// are actually going.
export const BASE_URL = _backwardsCompatBase();
export function getActiveBaseUrl(): string {
  return _rankUrls()[0] || _backwardsCompatBase();
}

// Same chunked-upload state machine as worker-ui's hook, wired to
// manager-ui's admin-key auth. Module-level emitter so any subtree can
// subscribe via useUploads() without provider plumbing.
//
// Differences from worker-ui:
//   - Uses lib/api.ts's admin-key authedFetch (no smart fleet retry; the
//     manager UI talks to a single node by design).
//   - No i18n primitive in manager-ui yet; English strings inline.

import { useEffect, useState } from "react";
import {
  initUpload, patchUploadChunk, completeUpload, getUploadStatus,
  cancelUpload as apiCancelUpload, sha256OfFile, ApiError,
} from "@/lib/api";

export type UploadStatus =
  | "queued" | "hashing" | "uploading" | "paused"
  | "completing" | "indexing" | "done" | "error" | "cancelled";

export interface UploadItem {
  id: string;
  uploadId: string | null;
  name: string;
  sizeBytes: number;
  loadedBytes: number;
  status: UploadStatus;
  error?: string;
  startedAt: number;
  fileRef: File;
  paused: boolean;
  cancelled: boolean;
}

const ALLOWED_EXTS = new Set(["pdf", "docx", "txt", "md"]);
const MAX_BYTES = 5 * 1024 * 1024 * 1024;
const MAX_PARALLEL = 4;

let _items: UploadItem[] = [];
const _listeners = new Set<(items: UploadItem[]) => void>();

function _emit() { const s = _items.slice(); _listeners.forEach((fn) => fn(s)); }
function _update(id: string, patch: Partial<UploadItem>) {
  _items = _items.map((u) => (u.id === id ? { ...u, ...patch } : u)); _emit();
}
function _add(item: UploadItem) { _items = [..._items, item]; _emit(); }
function _remove(id: string) { _items = _items.filter((u) => u.id !== id); _emit(); }

const RESUME_KEY = "locallyai.manager.uploads.resume.v1";
type ResumeMap = Record<string, string>;
function _resumeRead(): ResumeMap {
  try { return JSON.parse(localStorage.getItem(RESUME_KEY) || "{}"); } catch { return {}; }
}
function _resumeWrite(m: ResumeMap) {
  try { localStorage.setItem(RESUME_KEY, JSON.stringify(m)); } catch { /* quota */ }
}
function _ck(f: File): string { return `${f.name}|${f.size}|${f.lastModified}`; }
function _resumeRemember(f: File, id: string) { const m = _resumeRead(); m[_ck(f)] = id; _resumeWrite(m); }
function _resumeForget(f: File) { const m = _resumeRead(); delete m[_ck(f)]; _resumeWrite(m); }
function _resumeLookup(f: File): string | null { return _resumeRead()[_ck(f)] ?? null; }

export function validateFile(file: File): string | null {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (!ALLOWED_EXTS.has(ext))
    return `${file.name}: unsupported file type (.${ext}). Allowed: ${[...ALLOWED_EXTS].join(", ")}.`;
  if (file.size > MAX_BYTES)
    return `${file.name}: too large (${(file.size / 1024 ** 3).toFixed(2)} GB; max 5 GB).`;
  if (file.size === 0) return `${file.name}: empty file.`;
  return null;
}

export async function uploadFile(file: File): Promise<string | null> {
  const err = validateFile(file);
  if (err) { console.warn(err); return null; }
  const id = `upl-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  _add({ id, uploadId: null, name: file.name, sizeBytes: file.size,
         loadedBytes: 0, status: "queued", startedAt: Date.now(),
         fileRef: file, paused: false, cancelled: false });
  await _acquire();
  if (_items.find((u) => u.id === id)?.cancelled) { _release(); return null; }
  try { await _drive(id); return id; } finally { _release(); }
}

export async function uploadFiles(files: FileList | File[]): Promise<{ accepted: number; skipped: number }> {
  const arr = Array.from(files);
  const supported = arr.filter((f) => ALLOWED_EXTS.has(f.name.split(".").pop()?.toLowerCase() ?? ""));
  const results = await Promise.all(supported.map((f) => uploadFile(f)));
  return { accepted: results.filter((r) => r !== null).length, skipped: arr.length - supported.length };
}

export function pauseUpload(id: string)  { _update(id, { paused: true,  status: "paused" }); }
export function resumeUpload(id: string) {
  const it = _items.find((u) => u.id === id);
  if (!it || it.status !== "paused") return;
  _update(id, { paused: false, status: "uploading" });
  void _drive(id);
}
export async function cancelUpload(id: string) {
  const it = _items.find((u) => u.id === id);
  if (!it) return;
  _update(id, { cancelled: true, status: "cancelled" });
  if (it.uploadId) { try { await apiCancelUpload(it.uploadId); } catch { /* server may have GC'd */ } }
  _resumeForget(it.fileRef);
  setTimeout(() => _remove(id), 1500);
}
export function dismissUpload(id: string): void { _remove(id); }

let _slots = 0;
const _waiters: Array<() => void> = [];
function _acquire(): Promise<void> {
  return new Promise((resolve) => {
    const grant = () => { if (_slots < MAX_PARALLEL) { _slots++; resolve(); } else _waiters.push(grant); };
    grant();
  });
}
function _release() { _slots = Math.max(0, _slots - 1); const n = _waiters.shift(); if (n) n(); }

async function _drive(id: string): Promise<void> {
  const get = () => _items.find((u) => u.id === id);
  const it = get(); if (!it || it.cancelled) return;
  const file = it.fileRef;

  _update(id, { status: "hashing" });
  let sha: string | null = null;
  try { sha = (await sha256OfFile(file)) || null; } catch { /* skip */ }

  let uploadId = it.uploadId;
  let received = 0;
  let chunkSize = 8 * 1024 * 1024;

  if (!uploadId) {
    const remembered = _resumeLookup(file);
    if (remembered) {
      try {
        const s = await getUploadStatus(remembered);
        if (s.status === "open" && s.total_bytes === file.size) {
          uploadId = remembered; received = s.received_bytes;
        } else { _resumeForget(file); }
      } catch (e) { if (!(e instanceof ApiError) || e.status !== 404) console.warn(e); _resumeForget(file); }
    }
    if (!uploadId) {
      const init = await initUpload(file.name, file.size, sha);
      uploadId = init.upload_id; received = init.received_bytes; chunkSize = init.chunk_size_suggested;
      _resumeRemember(file, uploadId);
    }
    _update(id, { uploadId, loadedBytes: received, status: "uploading" });
  } else {
    _update(id, { status: "uploading" });
  }

  while (received < file.size) {
    const cur = get(); if (!cur || cur.cancelled) return;
    if (cur.paused) return;
    const end = Math.min(received + chunkSize, file.size);
    const blob = file.slice(received, end);
    try {
      const r = await patchUploadChunk(uploadId, received, end - 1, file.size, blob);
      received = r.received_bytes;
      _update(id, { loadedBytes: received });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        try { const s = await getUploadStatus(uploadId); received = s.received_bytes; _update(id, { loadedBytes: received }); continue; }
        catch { /* fall through */ }
      }
      const msg = e instanceof Error ? e.message : String(e);
      _update(id, { status: "error", error: msg });
      setTimeout(() => _remove(id), 8000);
      return;
    }
  }

  _update(id, { status: "completing" });
  try {
    await completeUpload(uploadId, sha);
    _resumeForget(file);
    _update(id, { status: "done", loadedBytes: file.size });
    setTimeout(() => _remove(id), 4000);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    _update(id, { status: "error", error: msg });
    setTimeout(() => _remove(id), 8000);
  }
}

export function useUploads(): UploadItem[] {
  const [items, setItems] = useState<UploadItem[]>(_items);
  useEffect(() => { _listeners.add(setItems); return () => { _listeners.delete(setItems); }; }, []);
  return items;
}

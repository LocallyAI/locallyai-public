// Centralised upload state. Used by:
//   - UploadDropZone (page-level drag-and-drop overlay)
//   - Composer's Attach button (file picker; folder picker too)
//   - UploadChip (in-flight progress + pause/resume/cancel controls)
//   - Manager-ui's Corpus panel (same hook, different mount)
//
// Uploads use the chunked /v1/uploads/* protocol — files are split into
// 8 MiB chunks (server-suggested) and PATCHed sequentially. This means:
//   - Multi-gigabyte files don't blow up RAM on either side.
//   - Pause/resume is just "stop sending chunks" / "resume from offset".
//   - Page reload mid-upload? localStorage maps the file's
//     {name,size,lastModified} → upload_id; on next attempt we GET the
//     server's received_bytes and resume from there.
//
// Module-level event-emitter (not React Context) so the hook can be
// consumed from any subtree without provider plumbing.

import { useEffect, useState } from "react";
import {
  initUpload, patchUploadChunk, completeUpload, getUploadStatus,
  cancelUpload as apiCancelUpload, sha256OfFile, ApiError,
} from "@/lib/api";
import { toast } from "sonner";
import { t } from "@/lib/i18n";

export type UploadStatus =
  | "queued"
  | "hashing"
  | "uploading"
  | "paused"
  | "completing"
  | "indexing"
  | "done"
  | "error"
  | "cancelled";

export interface UploadItem {
  id:           string;
  uploadId:     string | null;     // server-assigned, null until init responds
  name:         string;
  sizeBytes:    number;
  loadedBytes:  number;
  status:       UploadStatus;
  error?:       string;
  startedAt:    number;
  fileRef:      File;              // kept for resume + cancel; module-only
  paused:       boolean;
  cancelled:    boolean;
}

const ALLOWED_EXTS = new Set(["pdf", "docx", "txt", "md"]);
// 5 GiB matches the server cap (LOCALLYAI_MAX_UPLOAD_BYTES). The server
// rejects anything larger, so we bail early with a clearer error.
const MAX_BYTES = 5 * 1024 * 1024 * 1024;

// Concurrency cap on simultaneous uploads — too many in parallel saturates
// the LAN / GPU and degrades each one. 4 is the empirical sweet spot for
// HTTP/1.1 over a single tab.
const MAX_PARALLEL = 4;

// Module-level state.
let _items: UploadItem[] = [];
const _listeners = new Set<(items: UploadItem[]) => void>();

function _emit() {
  const snapshot = _items.slice();
  _listeners.forEach((fn) => fn(snapshot));
}
function _update(id: string, patch: Partial<UploadItem>) {
  _items = _items.map((u) => (u.id === id ? { ...u, ...patch } : u));
  _emit();
}
function _add(item: UploadItem) {
  _items = [..._items, item];
  _emit();
}
function _remove(id: string) {
  _items = _items.filter((u) => u.id !== id);
  _emit();
}

// ── localStorage resume map ───────────────────────────────────────────────────
// Key: `${name}|${size}|${lastModified}` (collision-resistant for the same file
// on the same machine; not portable across machines, which is fine — chunks
// only resume on the same browser they started on).
const RESUME_KEY = "locallyai.uploads.resume.v1";
type ResumeMap = Record<string, string>;  // contentKey → uploadId

function _resumeRead(): ResumeMap {
  try { return JSON.parse(localStorage.getItem(RESUME_KEY) || "{}"); }
  catch { return {}; }
}
function _resumeWrite(m: ResumeMap) {
  try { localStorage.setItem(RESUME_KEY, JSON.stringify(m)); } catch { /* quota */ }
}
function _contentKey(f: File): string {
  return `${f.name}|${f.size}|${f.lastModified}`;
}
function _resumeRemember(f: File, uploadId: string) {
  const m = _resumeRead(); m[_contentKey(f)] = uploadId; _resumeWrite(m);
}
function _resumeForget(f: File) {
  const m = _resumeRead(); delete m[_contentKey(f)]; _resumeWrite(m);
}
function _resumeLookup(f: File): string | null {
  return _resumeRead()[_contentKey(f)] ?? null;
}

// ── Validation ────────────────────────────────────────────────────────────────
export function validateFile(file: File): string | null {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (!ALLOWED_EXTS.has(ext)) {
    return `${file.name}: unsupported file type (.${ext}). Allowed: ${[...ALLOWED_EXTS].join(", ")}.`;
  }
  if (file.size > MAX_BYTES) {
    return `${file.name}: too large (${(file.size / 1024 / 1024 / 1024).toFixed(2)} GB; max 5 GB).`;
  }
  if (file.size === 0) {
    return `${file.name}: empty file.`;
  }
  return null;
}

// ── Upload lifecycle ──────────────────────────────────────────────────────────
export async function uploadFile(file: File): Promise<string | null> {
  const err = validateFile(file);
  if (err) { toast.error(err); return null; }

  const id = `upl-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const item: UploadItem = {
    id,
    uploadId: null,
    name: file.name,
    sizeBytes: file.size,
    loadedBytes: 0,
    status: "queued",
    startedAt: Date.now(),
    fileRef: file,
    paused: false,
    cancelled: false,
  };
  _add(item);

  // Wait for a free slot.
  await _acquireSlot();
  if (_items.find((u) => u.id === id)?.cancelled) {
    _releaseSlot();
    return null;
  }

  try {
    await _drive(id);
    return id;
  } finally {
    _releaseSlot();
  }
}

export async function uploadFiles(files: FileList | File[]): Promise<number> {
  const arr = Array.from(files);
  const results = await Promise.all(arr.map((f) => uploadFile(f)));
  return results.filter((r) => r !== null).length;
}

// Pause / resume / cancel controls — wired to the chip buttons.
export function pauseUpload(id: string) { _update(id, { paused: true, status: "paused" }); }
export function resumeUpload(id: string) {
  const it = _items.find((u) => u.id === id);
  if (!it || it.status !== "paused") return;
  _update(id, { paused: false, status: "uploading" });
  void _drive(id);  // pick up where we left off
}
export async function cancelUpload(id: string) {
  const it = _items.find((u) => u.id === id);
  if (!it) return;
  _update(id, { cancelled: true, status: "cancelled" });
  if (it.uploadId) {
    try { await apiCancelUpload(it.uploadId); } catch { /* server may have already GC'd */ }
  }
  _resumeForget(it.fileRef);
  setTimeout(() => _remove(id), 1500);
}
export function dismissUpload(id: string): void { _remove(id); }

// ── Concurrency gate ──────────────────────────────────────────────────────────
let _slotsInUse = 0;
const _slotWaiters: Array<() => void> = [];
function _acquireSlot(): Promise<void> {
  return new Promise((resolve) => {
    const tryGrant = () => {
      if (_slotsInUse < MAX_PARALLEL) { _slotsInUse++; resolve(); }
      else _slotWaiters.push(tryGrant);
    };
    tryGrant();
  });
}
function _releaseSlot() {
  _slotsInUse = Math.max(0, _slotsInUse - 1);
  const next = _slotWaiters.shift();
  if (next) next();
}

// ── Driver ────────────────────────────────────────────────────────────────────
async function _drive(id: string): Promise<void> {
  const get = () => _items.find((u) => u.id === id);
  const it = get();
  if (!it || it.cancelled) return;

  const file = it.fileRef;

  // 1. Hash (best-effort; server enforces correctness).
  _update(id, { status: "hashing" });
  let sha: string | null = null;
  try { sha = (await sha256OfFile(file)) || null; } catch { /* skip */ }

  // 2. Resume? Or init fresh?
  let uploadId = it.uploadId;
  let received = 0;
  let chunkSize = 8 * 1024 * 1024;

  if (!uploadId) {
    const remembered = _resumeLookup(file);
    if (remembered) {
      try {
        const s = await getUploadStatus(remembered);
        if (s.status === "open" && s.total_bytes === file.size) {
          uploadId = remembered;
          received = s.received_bytes;
        } else {
          _resumeForget(file);
        }
      } catch (e) {
        // 404 / 403 / network — start fresh.
        if (!(e instanceof ApiError) || e.status !== 404) {
          console.warn("Resume probe failed, starting fresh:", e);
        }
        _resumeForget(file);
      }
    }
    if (!uploadId) {
      const init = await initUpload(file.name, file.size, sha);
      uploadId = init.upload_id;
      received = init.received_bytes;
      chunkSize = init.chunk_size_suggested;
      _resumeRemember(file, uploadId);
    }
    _update(id, { uploadId, loadedBytes: received, status: "uploading" });
  } else {
    _update(id, { status: "uploading" });
  }

  // 3. Push chunks.
  while (received < file.size) {
    const cur = get();
    if (!cur || cur.cancelled) return;
    if (cur.paused) return;  // resume() will recall _drive

    const end = Math.min(received + chunkSize, file.size);
    const blob = file.slice(received, end);
    try {
      const r = await patchUploadChunk(uploadId, received, end - 1, file.size, blob);
      received = r.received_bytes;
      _update(id, { loadedBytes: received });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      // 409 means the server's offset disagrees with ours — re-sync and try again.
      if (e instanceof ApiError && e.status === 409) {
        try {
          const s = await getUploadStatus(uploadId);
          received = s.received_bytes;
          _update(id, { loadedBytes: received });
          continue;
        } catch { /* fall through to error */ }
      }
      _update(id, { status: "error", error: msg });
      toast.error(
        t("upload.failed", "{name}: upload failed — {error}")
          .replace("{name}", file.name).replace("{error}", msg),
      );
      setTimeout(() => _remove(id), 8000);
      return;
    }
  }

  // 4. Finalise.
  _update(id, { status: "completing" });
  try {
    await completeUpload(uploadId, sha);
    _resumeForget(file);
    _update(id, { status: "done", loadedBytes: file.size });
    toast.success(
      t("upload.success", "{name} uploaded — indexing in background")
        .replace("{name}", file.name),
    );
    setTimeout(() => _remove(id), 4000);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    _update(id, { status: "error", error: msg });
    toast.error(
      t("upload.failed", "{name}: upload failed — {error}")
        .replace("{name}", file.name).replace("{error}", msg),
    );
    setTimeout(() => _remove(id), 8000);
  }
}

// ── React hook ────────────────────────────────────────────────────────────────
export function useUploads(): UploadItem[] {
  const [items, setItems] = useState<UploadItem[]>(_items);
  useEffect(() => {
    _listeners.add(setItems);
    return () => { _listeners.delete(setItems); };
  }, []);
  return items;
}

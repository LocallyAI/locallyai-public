# Bulk corpus ingestion

When to read: **before** loading the firm's archive (anything from a
handful of PDFs to gigabytes spread across thousands of files).
For small one-offs (a single contract a user wants to ask about), the
worker UI's drag-and-drop is enough — see [daily.md](daily.md).

## What this chapter covers

- The frictionless bulk-load path: drag a folder into the manager UI.
- What the system does behind the scenes (chunked uploads, indexing
  queue, batched search-index rebuild).
- Pause / resume / cancel — including across page reloads and laptop
  sleep.
- How to verify the corpus landed and is searchable.
- Limits, knobs, and "what if" answers.

## Hard rules

1. **Don't put client documents in `data/`.** That directory is for
   seed/demo content shipped with the build. Real client documents
   land in `storage/uploads/` via the upload flow described here. The
   server treats both as one corpus, but `data/` is committed-to-git
   territory; `storage/` is gitignored.
2. **Don't shut the box down mid-batch.** Indexing happens in the
   background after each upload completes. You can close the browser
   tab — uploads survive the page (resume via localStorage) and
   indexing survives because it runs server-side. But killing the
   server process mid-batch loses the indexing queue (uploads on disk
   are safe; un-indexed files will be picked up on the next manual
   re-ingest, see *Recovery* below).
3. **Don't bypass the UI to copy files into `storage/uploads/`
   manually.** The audit log records who uploaded what; manually
   placed files have no owner attribution and won't be indexed until
   someone runs `python -m ingest`.

---

## The path users follow

### Step 1 — Sign in to the manager UI

1. Open `https://<manager-host>:5173/` (or whatever URL your
   deployment uses).
2. Paste the LOCALLYAI_ADMIN_KEY when prompted.
3. Click **Documents** in the sidebar.

### Step 2 — Drop or pick

You have three options on the **Documents** page:

| Option | When to use |
|---|---|
| **Drag a folder** onto the dashed drop zone | Loading an archive — fastest. The browser walks the folder and hands the server the flat file list. |
| **Select folder** button | Same result as drag; useful when the folder is in a sidebar your browser can't reach by drag. |
| **Select files** button | Picking a handful of specific files. |

Unsupported file types (anything other than PDF, DOCX, TXT, MD) are
**silently skipped** when you pick a folder — the page tells you how
many it skipped. When you pick individual files, the rejection is
loud (a per-file error toast).

### Step 3 — Watch the queue

Two indicators tell you what's happening:

- **In-flight uploads** panel — one row per file actively transferring.
  Shows MB transferred / total, percentage, file-type icon, and
  pause / resume / cancel controls.
- **Indexing queue ticker** — appears above the library when the
  server is doing background work. Reads `Indexing N docs · M done`
  while the queue drains, then `Updating search index` while BM25
  rebuilds, then disappears.

The library table refreshes automatically as files complete. Each row
shows an **Indexed** (green) or **Pending** (blue spinner) badge.

### Step 4 — When the spinner stops, you're done

The queue ticker disappears when:
- All uploads have finished, AND
- All indexing has completed, AND
- The BM25 search index has been rebuilt.

**You don't have to click anything.** The system handles the rebuild
on its own after the queue has been quiet for 30 seconds. If you're
impatient (you've finished a batch and want to query immediately),
the ticker shows a **Rebuild now** button that fires the rebuild
straight away.

---

## What's actually happening

### Chunked, resumable uploads

The browser splits each file into 8 MiB chunks and sends them
sequentially via `PATCH /v1/uploads/{id}` with `Content-Range`
headers. The server appends each chunk straight to disk in
`storage/uploads/.parts/<id>.part`, never holding the full file in
RAM. This is what makes gigabyte-scale uploads safe.

After every chunk the server persists `received_bytes` to a meta
file. So:
- **Network blip mid-upload?** The browser auto-resumes from the
  server's reported offset.
- **Closed the tab?** localStorage remembers the upload-id keyed by
  `{filename, size, lastModified}`. On the next attempt the browser
  resumes that upload-id from where it left off.
- **Laptop slept overnight?** Same as the tab close — resume on
  reopen.
- **Server restart?** Meta files persist; uploads abandoned > 24 h
  are GC'd by the watchdog (configurable via
  `LOCALLYAI_UPLOAD_GC_SECONDS`).

When all chunks are in, the server computes a streaming SHA-256 over
the assembled file. If the client provided one at init it must
match; mismatch fails the upload and deletes the partial.

### Indexing queue

On `complete`, the server enqueues the file into a 2-worker thread
pool (`LOCALLYAI_INGEST_WORKERS`). Each worker:
1. Extracts text (PyMuPDF for PDF; python-docx for DOCX; plain read
   for TXT/MD).
2. Splits into 500-token windows with 50-token overlap.
3. Calls the embed model (`nomic-embed-text:latest` for UK, `BAAI/bge-m3`
   for KSA — see [setup-saudi.md](setup-saudi.md)).
4. Upserts vectors into Qdrant in 64-point batches.
5. Updates `.ingest_state.json` with the file's SHA-256.

### Batched BM25 rebuild

The lexical search index is **not** rebuilt per file. That used to be
the case and was O(n²) — every rebuild rescans every point in
Qdrant. The new behaviour:

- BM25 is marked dirty when any file is queued.
- A coordinator thread waits 30 seconds of complete queue silence
  (no in-flight, no queued, no new completion).
- On silence, BM25 is rebuilt **once** for the whole batch.

So a 1,000-file load triggers one rebuild, not a thousand. The
window during which late uploads aren't yet lexically searchable is
shown by the ticker as `Updating search index`.

---

## Limits & knobs

| Setting | Default | Override | What it does |
|---|---|---|---|
| Per-file size cap | 5 GiB | `LOCALLYAI_MAX_UPLOAD_BYTES` (bytes) | Hard ceiling per file. |
| Chunk size suggested | 8 MiB | (server-driven) | What the browser uses per `PATCH`. |
| Chunk size max | 16 MiB | (compiled-in) | Hard ceiling per chunk. |
| Parallel uploads | 4 | (compiled-in) | How many files transfer at once. |
| Indexing workers | 2 | `LOCALLYAI_INGEST_WORKERS` | Concurrent indexing jobs. |
| Quiet period before rebuild | 30 s | `LOCALLYAI_INGEST_QUIET_SECONDS` | How long the queue must be silent before BM25 fires. |
| Stale-upload GC | 24 h | `LOCALLYAI_UPLOAD_GC_SECONDS` | Abandoned partials are deleted after this. |

---

## Verification

After a batch lands and the ticker disappears, verify:

```bash
# 1. Files on disk — grouped by extension
ls storage/uploads/*.pdf storage/uploads/*.docx storage/uploads/*.txt storage/uploads/*.md 2>/dev/null | wc -l

# 2. Indexed vs disk
.venv/bin/python -c "
import json, pathlib
state = json.loads(pathlib.Path('.ingest_state.json').read_text())
on_disk = [p.name for p in pathlib.Path('storage/uploads').iterdir() if p.is_file() and not p.name.startswith('.')]
indexed = set(state.keys())
print('on disk:   ', len(on_disk))
print('indexed:   ', len(indexed))
print('un-indexed:', sorted(set(on_disk) - indexed)[:10])
"

# 3. Live ingest status (manager admin key)
curl -sk -H "Authorization: Bearer $LOCALLYAI_ADMIN_KEY" \
  https://localhost:8000/v1/ingest/status | python -m json.tool
# Expected when idle: in_flight=0, queued=0, bm25_pending=false
```

If `un-indexed` is non-empty after the queue is idle, see *Recovery*
below.

---

## Recovery scenarios

### "I uploaded 200 files but the queue says 5 failed"

Tail the API log:
```bash
tail -200 logs/api.log | grep -E "(Background index failed|Ingest failed)"
```
Common causes:
- Corrupted PDF — PyMuPDF logs the page that broke. Drop that file.
- Embed model not running — start it (`ollama serve`) and re-ingest:
  ```bash
  .venv/bin/python -m ingest
  ```
  This walks both `data/` and `storage/uploads/`, hashes each file
  against `.ingest_state.json`, and indexes only what's missing.

### "I killed the server mid-batch and now some files aren't searchable"

The chunked uploads on disk are intact (they fsynced after every
chunk). Run:
```bash
.venv/bin/python -m ingest
```
and the queue picks up everything that wasn't already indexed.

### "I want to start over"

```bash
# Drop the search collection and force-reindex everything.
.venv/bin/python -c "
from config import make_qdrant_client, COLLECTION_NAME
c = make_qdrant_client()
c.delete_collection(COLLECTION_NAME)
"
rm .ingest_state.json
.venv/bin/python -m ingest --force
```
Files in `storage/uploads/` and `data/` are preserved; only the
vectors and BM25 index are rebuilt. On a Mac Studio with a 100k-doc
corpus, expect ~30–60 minutes of indexing.

---

## What this chapter does NOT cover

- Single-document upload from the worker UI → see [daily.md](daily.md).
- Deleting a document from the corpus → see [maintenance.md](maintenance.md).
- Subject-access exports of upload metadata → see
  [compliance.md](compliance.md) (UK) or
  [compliance-saudi.md](compliance-saudi.md) (KSA).

# LocallyAI — Demo Mode

This folder is the demonstration kit. It ships a small synthetic corpus of UK law-firm documents and a one-command end-to-end demo runner so you can show the full RAG pipeline working without uploading any client data.

## What's inside

| File | Purpose |
|---|---|
| `data/nda_template.md` | Mutual non-disclosure agreement template |
| `data/gdpr_data_processing_policy.md` | Internal UK GDPR / DPA 2018 processing policy |
| `data/conflict_check_procedure.md` | SRA-aligned conflict-check procedure |
| `data/standard_lease_clauses.md` | FRI commercial lease clause library |
| `data/client_engagement_letter.md` | Client engagement letter template |
| `run_demo.py` | Sends 5 representative queries and reports source-retrieval counts |

All five `.md` files are **synthetic**. There is no real client data, no real partner names, no real DPO email. They exist only to give the RAG pipeline something realistic to retrieve from.

## How demo mode works at install time

When you run `bash install.sh` in the parent `production/` folder, you'll be asked:

```
Choose deployment mode:
  1. Production — empty knowledge base; you ingest your own documents.
  2. Demo       — copy the 5 sample legal documents into data/ and ingest them.

Mode [1=production / 2=demo, default 1]:
```

If you pick **Demo (2)**, the installer will:

1. Copy `demo/data/*.md` into `production/data/` alongside `welcome.md`.
2. Run `python ingest.py` so the Qdrant collection is populated before the service starts taking queries.

The result: `python chat.py` works immediately on a non-trivial corpus.

## Running the demo

After install, with the admin key you saved:

```bash
cd /path/to/production
python demo/run_demo.py --key <paste-admin-key>
```

You'll see five queries, each targeted at one of the seeded documents. A passing demo looks like:

```
── Query 1/5: NDA — duration & exclusions ─────────────────────────
Q: Under our standard mutual NDA, how long does the confidentiality
   obligation last after disclosure, and what categories of information
   are excluded from the obligation?
A: The confidentiality obligation under the standard mutual NDA lasts
   for two years from the date of disclosure. The following categories
   are excluded: information that becomes publicly available...
   sources_retrieved=3  (4.2s)

...

── Summary ──
  Queries run:           5
  Failed:                0
  Total source chunks:   14

  Demo PASSED. RAG pipeline is live end-to-end.
```

If `sources_retrieved=0` on every query, ingestion didn't happen — re-run from `production/`:

```bash
python ingest.py --force
```

## Switching from demo to production after install

The demo files copied into `production/data/` are real Markdown — they live in the same place client documents would. To clean them out:

```bash
cd /path/to/production
ls data/
# nda_template.md  gdpr_data_processing_policy.md  conflict_check_procedure.md
# standard_lease_clauses.md  client_engagement_letter.md  welcome.md

# Remove demo files (keep welcome.md if you like)
rm data/nda_template.md data/gdpr_data_processing_policy.md \
   data/conflict_check_procedure.md data/standard_lease_clauses.md \
   data/client_engagement_letter.md

# Drop your real documents in
cp ~/Documents/firm-precedents/*.{pdf,docx,md} data/

# Force a full re-index so old demo vectors are replaced
python ingest.py --force
```

## Adding your own demo documents

Drop any `.pdf`, `.docx`, `.txt`, or `.md` file into `data/` (this folder), and on next install in demo mode it'll get copied across. Keep them synthetic — this folder ships in the GitHub repo and is visible to anyone who clones.

## Why ship demo data instead of pre-built Qdrant storage?

Qdrant's on-disk format is version-sensitive — shipping a pre-built `storage/` folder would break the moment the receiving box runs a different Qdrant version. Shipping the source documents and re-running `ingest.py` on the receiver's machine produces a fresh, correct index every time, regardless of versions or platforms.

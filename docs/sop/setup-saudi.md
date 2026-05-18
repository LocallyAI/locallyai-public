# Setup — Saudi (KSA / PDPL) deployment

End state: a LocallyAI deployment configured for the Kingdom of Saudi
Arabia. Audit log carries `data_region: "KSA"`; RoPA frames the
deployment under PDPL + ISO 27001; embed model is multilingual; the
worker UI defaults to Arabic with RTL layout; demo documents are
Saudi-flavoured (DIFC NDA, PDPL policy, M&A confidentiality letter,
restructuring memo, bilingual welcome).

Time required: 30–60 min on top of the base [setup-mac-single.md](setup-mac-single.md)
or [setup-windows.md](setup-windows.md) procedure.

> **Read first:** the base setup chapter for your OS. This chapter
> overrides specific steps inside it (the region picker; the demo doc
> source; the embed model). Everything else is identical.

---

## 0. Pre-flight (Saudi-specific)

In addition to the standard pre-flight from the base setup chapter:

1. **Confirm the deployment box is physically located in KSA.** This is
   how the data-residency claim in the RoPA holds. If the box will be
   moved across borders, that's a separate processing event the DPO
   must record.
2. **Have the Saudi-qualified counsel reachable.** The DPA template
   `DPA_DRAFT_SA.md` includes notes for them at the end; expect to
   review with them before going live.
3. **BitLocker (Windows) / FileVault (Mac) MUST be on.** Same as UK,
   non-negotiable. PDPL Art. 19 expects it.
4. **Decide the audit retention period** with Saudi counsel. The
   default is 365 days; PDPL doesn't fix a specific number, so the
   firm picks one that matches its other regulatory and professional
   record-keeping obligations (Saudi Bar rules, SAMA / CMA / ZATCA
   requirements as applicable).

## 1. Run the install with KSA region

### Mac

```bash
cd ~/locallyai
LOCALLYAI_DATA_REGION=KSA bash install.sh
```

(Or run `bash install.sh` and pick `2` at the region prompt.)

What changes from the UK path:

- The `.env` will contain `LOCALLYAI_DATA_REGION=KSA`.
- The TLS cert subject will be `C=SA` (not `C=GB`).
- If you chose Demo mode, the corpus copied into `data/` is
  `demo/data_sa/*.md` (Saudi flavour) rather than `demo/data/*.md`.
- The default embed model resolves to `intfloat/multilingual-e5-base`
  if you don't explicitly set `EMBED_MODEL` in `.env`. Verified
  cross-lingual: an Arabic question against an English-only corpus
  retrieves the right English chunks via the multilingual vector
  bridge (RRF top-1 ≈ 0.0164, just above the relevance floor).
- For the LLM side, pick a multilingual model that fits your RAM:
  - **MLX (recommended on Apple Silicon)**: `mlx-community/Qwen2.5-3B-Instruct-4bit`
    (~2 GB disk, ~3 GB RAM, native Arabic). Set `LOCALLYAI_BACKEND=mlx`
    + `MLX_MODEL=mlx-community/Qwen2.5-3B-Instruct-4bit`.
    Larger options: `Qwen2.5-7B-Instruct-4bit` (~5 GB) or
    `Qwen2.5-14B-Instruct-4bit` (~9 GB) for stronger Arabic.
  - **Ollama**: `qwen2.5:7b` or `qwen2.5:14b` work too, but **Ollama
    versions ≤ 0.22.1 fail to load any model on recent macOS** (Metal
    `static_assert: half vs bfloat`). Update to the latest Ollama
    before relying on it; MLX has no such issue.
- Llama 3.2 (any size) does NOT officially support Arabic — don't
  pick `mlx-community/Llama-3.2-*-Instruct-4bit` for KSA.

### Windows

```powershell
PowerShell -ExecutionPolicy Bypass -File .\install.ps1 -DataRegion KSA
```

(Or run without `-DataRegion` and pick `2` at the prompt.)

## 2. Save credentials (Saudi-specific entries)

In addition to the standard credential register entries:

- [ ] `LocallyAI / SA / admin key`
- [ ] `LocallyAI / SA / FileVault or BitLocker recovery`
- [ ] `LocallyAI / SA / Saudi-counsel contact` (the lawyer who will
      sign off the DPA and respond on PDPL questions)
- [ ] `LocallyAI / SA / SDAIA breach-reporting contact` (so the DPO
      knows where to file under PDPL Art. 31)

## 3. Verify the region stamping

```bash
ADMIN_KEY=$(grep '^LOCALLYAI_ADMIN_KEY=' .env | cut -d= -f2)
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/admin/processing-record \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('version:', d['version']); print('data_region:', d['data_region']); print('breach:', d['breach_notification'])"
```

Expected:
```
version: 1.3
data_region: KSA
breach: PDPL Art. 31 (notification to SDAIA + data subjects)
```

Also verify a fresh chat carries the region in the audit entry:

```bash
USER_KEY=$(python manage_users.py list | grep -v '^name' | head -1 | awk '{print $NF}')  # placeholder
curl -sk -X POST -H "Authorization: Bearer $USER_KEY" -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"hi"}],"max_tokens":5}' \
  https://localhost:8000/v1/chat/completions >/dev/null
tail -1 logs/audit.log | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print('data_region:', d.get('data_region'))"
```

Expected: `data_region: KSA`.

## 4. Worker UI: default Arabic, runtime English toggle

By default a KSA-region build of the worker UI loads in Arabic with
`<html dir="rtl">`. Users can toggle to English from the language
control in the sidebar (or, until that control ships in your firm's
build, by running this in the browser console once):

```js
localStorage.setItem("locallyai_lang", "en"); location.reload();
```

To switch back to Arabic:

```js
localStorage.setItem("locallyai_lang", "ar"); location.reload();
```

To make Arabic the build-time default for the worker UI when
distributing to users, set in `apps/worker-ui/.env.local` before
running `npm run build`:

```
VITE_DEFAULT_LANG=ar
```

> **Translation quality:** Arabic strings shipped in
> `apps/worker-ui/src/i18n/ar.json` are starter values from the
> codebase. Have the firm's Arabic-speaking partner / lawyer revise
> them before client demo. Edit the JSON file directly; rebuild;
> redistribute. The keys correspond 1:1 to `en.json`.

## 5. RTL layout sanity check

In a browser:

1. Visit the worker UI.
2. Toggle to Arabic per §4.
3. Verify:
   - The sidebar is on the **right** (not left).
   - The composer's Send button is on the **left** (the inline-end).
   - The Sources panel is on the **left**.
   - Search-icon inside the search box is on the **right**.
   - Text within messages is right-aligned.
   - The composer Send-button arrow flips direction (was up; now up
     mirrored — Tailwind's `rtl:rotate-180` modifier handles it).
4. Send a chat in Arabic — confirm the model responds in Arabic
   (the `LOCALLYAI_DATA_REGION=KSA` system-prompt suffix instructs
   it to mirror the user's language).
5. Send a chat in English — confirm the model responds in English.

If any of those fail, see [incidents-software.md](incidents-software.md)
or [incidents-service.md](incidents-service.md).

## 6. Hijri date verification

The manager UI's audit panel renders timestamps using
`Intl.DateTimeFormat(lang, { calendar: "islamic-umalqura" })` when
the language is Arabic. Switch the manager UI to Arabic via the same
localStorage trick; the audit timestamp column should show:

> 15 ذو القعدة 1447 هـ 18:42

instead of:

> 15 May 2026 18:42

(Times are also in Asia/Riyadh by default in Arabic mode.)

## 7. Bilingual demo

If you ran demo mode, ingest is already done. Test the bilingual
welcome doc:

```bash
USER_KEY=<paste>
# English query:
curl -sk -X POST -H "Authorization: Bearer $USER_KEY" -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"What does LocallyAI do? Cite the welcome document."}]}' \
  https://localhost:8000/v1/chat/completions | python3 -m json.tool

# Arabic query (use a UTF-8-clean shell):
curl -sk -X POST -H "Authorization: Bearer $USER_KEY" -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"ماذا يفعل LocallyAI؟ استشهد بوثيقة الترحيب."}]}' \
  https://localhost:8000/v1/chat/completions | python3 -m json.tool
```

Both should return `usage.sources_retrieved >= 1` with `welcome_ar.md`
in the cited sources, and the response text should be in the same
language as the query.

If the Arabic query produces 0 sources: either the model isn't
multilingual (verify `EMBED_MODEL` resolves to
`intfloat/multilingual-e5-base` or another multilingual one), or the
ingest didn't include `welcome_ar.md` (check `data/`).

## 8. DPA review

Hand `DPA_DRAFT_SA.md` to Saudi counsel. The end of the document has
a marked-up note for them. Items that need their explicit confirmation
before client signature:

- §5.4 Sub-processor terminology — the precise PDPL Arabic legal term.
- §5.5 Cross-border transfer wording — Art. 29 expected language.
- §6 Breach notification window — 24h to Controller is conservative
  vs Art. 31 "without undue delay"; confirm acceptable.
- Whole document — translate to Arabic for execution; both versions
  initialled on every page; Arabic version controlling on conflict.

## 9. Schedule the audit

Same as the base setup — the weekly audit (`scripts/audit_install.sh`
on Mac, `audit_install.ps1` on Windows) runs unchanged on a KSA
deployment. Just set up the launchd job / scheduled task per the base
chapter.

## 10. You are done

Continue to [daily.md](daily.md) and [compliance-saudi.md](compliance-saudi.md).
The Saudi chapter of the SOP covers Art. 31 breach response,
Arabic-language subject access, the SDAIA notification path, and
PDPL-specific record-keeping.

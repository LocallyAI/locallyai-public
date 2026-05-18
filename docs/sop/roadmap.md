# Roadmap

What's *not* shipped, when it's expected, and *why* it isn't shipped
yet. This document exists so deferred features don't disappear into
GitHub issues — operators reading the SOP set should be able to find
what's coming and what to tell firms when they ask.

Order is rough priority, not commitment. Dates are best-estimates;
actual ship-dates depend on customer pull.

---

## Time-entry suggestion (Q3 2026)

**What it is:** for every chat session that touches a matter, the
assistant suggests time-entries for the lawyer to record (e.g.
*"0.4h drafting cease-and-desist re: Acme matter, 2026-05-12 14:30"*).
Posted into the firm's billing system on a single click.

**Why it's deferred:** needs DMS + calendar/email integration
LocallyAI doesn't have yet. The DMS integration design doc exists
([`dms-integration.md`](dms-integration.md)) and is the prerequisite.
Without DMS hooks, the suggestion has nowhere to land — the lawyer
would still have to type it into Aderant / Elite / iManage / Clio /
PracticeLeague manually, which is the workflow we're trying to remove.

**Sequencing:**
1. DMS connector v1 (read-only) — reads matter list + recent
   timekeeper IDs into LocallyAI
2. DMS connector v2 (write) — posts time entries back via the DMS's
   own API. Per-DMS adapters; iManage + NetDocuments first
3. Time-entry suggestion engine — analyses chat session, classifies
   work type, drafts narrative + duration, presents for review
4. Worker-UI ribbon at the end of each session: "Add 0.4h to matter
   2026-046"

**Stakeholders:** every firm that bills hourly. Particularly valuable
for firms using ARR billing software where every minute unbilled is
margin lost.

---

## Form-filling (post-2026)

**What it is:** point LocallyAI at a blank form (court filing, IP
application, regulatory submission) and have it pre-fill from matter
context. Lawyer reviews + signs.

**Why it's deferred:** the legal risk surface is large. A wrongly-filled
form filed in court is a billable mistake; a wrongly-filled form filed
with the SRA / KSA bar is a regulatory incident. We need:

- Per-firm template library + ACL
- Field-level audit trail (which AI suggestion did the lawyer accept,
  modify, or reject?)
- Mandatory human-in-the-loop signoff gate (no auto-submit, ever)
- DMS integration so the filled form lands in the matter file, not
  the user's Downloads folder

The design space isn't ready. Starting it before the DMS connector
ships would mean rebuilding the form-filling UI to integrate with
DMS retroactively.

---

## US case-law external verification (Q4 2026)

**What it is:** extend the citation checker's external-verification
stage to US federal + state cases. The parser already extracts US
citations correctly; we just don't have a free public API to verify
them against.

**Why it's deferred:** US case-law is locked behind subscription
databases (Westlaw, Lexis, Bloomberg Law, Fastcase). Free options
(CourtListener, Free Law Project, Justia) have spotty coverage.
Per-firm-paid Westlaw / Lexis access is the right answer but requires
each firm to provide their own API credentials.

**Workaround until shipped:** the in-corpus + LLM-on-point checks
still run for US cites — only the external "this case exists in
BAILII" step is unavailable. The US badge says
*"external check unavailable"* rather than returning a false negative.

---

## KSA case-law external verification (no committed date)

**What it is:** verify KSA shariah-court citations + royal decrees
against an authoritative external source.

**Why it's deferred:** there is no public KSA case-law database
equivalent to BAILII. Decisions are not systematically published; the
Saudi Bar Association has discussed digitisation but no API exists.

**Workaround until shipped:** the parser extracts KSA citations
correctly; in-corpus + LLM-on-point still run. External verification
is explicitly marked *"no public KSA case database"* in the response
so users understand why the badge is amber rather than green.

This will land when (a) Saudi government publishes a case database
with an API, OR (b) one of our KSA firms wants to bring a private
case database in-house and license access to LocallyAI.

---

## DMS integration v1 (Q3 2026)

**What it is:** read-only sync from iManage / NetDocuments / Worldox
matter lists into LocallyAI. Powers conflict-check filtering, time-entry
suggestion, and matter-aware retrieval.

**Why it's deferred:** prioritisation. The conflict-check + comparison
+ citation-checker features (now shipped) have higher per-firm value
in user research; the DMS work is plumbing that unlocks the *next*
wave.

**Design doc:** [`dms-integration.md`](dms-integration.md).

---

## Multi-modal (image / scanned-PDF) ingest (no committed date)

**What it is:** ingest scanned PDFs without a text layer by running OCR
in the ingest pipeline, then proceeding as normal.

**Why it's deferred:** OCR quality varies wildly across legal-document
types. Tesseract is acceptable on clean scans, awful on faxed or
photocopied docs. PaddleOCR + a proper layout-aware model
(layoutlmv3, donut) would be better but ~5 GB resident, which
displaces inference RAM budget. Sizing tool needs an OCR-mode tier
before we ship this.

**Workaround until shipped:** firms that need OCR run it externally
(ABBYY FineReader, Adobe Acrobat OCR) before ingest. The ingest
pipeline returns 415 for empty-text PDFs, which is the right
behaviour — silently ingesting an OCR-failed PDF as a 0-chunk
document was a confusing footgun for several firms.

---

## How to read this doc

If a firm asks *"can LocallyAI do X?"* and X is on this list:
> "Not yet — it's on the roadmap. Here's the timeline. Here's the
> workaround until it ships." Then link them to the relevant section.

If a firm asks for X and X is **not** on this list:
> Open a roadmap-suggestion entry on GitHub Issues; the founder
> reviews these monthly. If three or more firms ask for the same
> thing, it lands on this list.

If you (operator) think something on this list is mis-prioritised:
ping the founder. The roadmap is opinionated but not adversarial —
operator field experience is the ground truth on what firms actually
need.

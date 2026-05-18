# Document Management System (DMS) integration — design

This chapter scopes how LocallyAI integrates with the firm's existing
Document Management System (DMS) instead of (or alongside) the
LocallyAI-managed corpus in `data/uploads/`. **Status: design;
implementation phased per below.**

## Why this matters

For firms above ~25 lawyers with an existing DMS (iManage,
NetDocuments, OpenText eDOCS, SharePoint, Worldox, Clio, HighQ), the
firm's documents are already in the DMS — versioned, ACL'd, audit-
logged, sometimes integrated with their practice management. Asking
those firms to "upload your corpus to LocallyAI separately" is a
non-starter:

1. **Duplication**: every doc lives twice; updates in DMS don't
   propagate; ACL changes in DMS don't propagate.
2. **Compliance double-tracking**: the firm has to maintain audit
   trails in both systems.
3. **Workflow break**: lawyers save into the DMS as they always have;
   LocallyAI is "the AI search box that doesn't know about my files
   from yesterday."

Solving this unlocks the larger-firm market (50-500 lawyers) where
the DMS is non-negotiable.

## Targets, ranked by UK + KSA market relevance

| DMS | Market | API maturity | Integration effort |
|---|---|---|---|
| **iManage Work** | Dominant in UK BigLaw + US AmLaw 200; ~60% of top-200 UK firms | REST API + SOAP legacy; OAuth2 | High — auth complexity, ACL semantics, matter-aware folders |
| **NetDocuments** | Dominant US mid-market, growing UK/KSA mid-market | REST API + ODA + webhooks; OAuth2 | Medium — clean REST, well-documented |
| **SharePoint** | Common at corporate-counsel + non-litigation UK firms | Microsoft Graph; OAuth2 + app perms | Medium-high — permissions model is intricate |
| **OpenText eDOCS** | Older UK firms (legacy installs); declining | SOAP only; on-prem | High — SOAP fatigue, licence model |
| **Worldox** | Small UK firms, mostly Windows | Limited; mostly file-system | Low coverage value |
| **Clio** | Small UK firms (sole practitioners) | REST API; OAuth2 | Low effort, but small-firm market = competing with Clio's own AI features |
| **HighQ** (Thomson Reuters) | Litigation collaboration | REST API | Medium |

**Recommended priority**: NetDocuments first (cleanest API, decent
market, MVP teaches us the integration shape) → iManage second
(largest market value, harder integration) → SharePoint third
(corporate-counsel angle).

## Integration patterns

Three options, in increasing order of operational sophistication:

### A. Periodic full sync (MVP)

Cron job: every N minutes, query the DMS for "documents modified
since last sync," pull them, ingest into LocallyAI's local corpus.
ACLs from DMS get translated to LocallyAI's `doc_acls.json` per the
mapping below.

**Pros**: simplest. No webhook plumbing. Same retrieval pipeline.
**Cons**: latency (a doc updated in DMS is searchable in LocallyAI
N/2 minutes later on average); doubles storage cost; complexity of
"deleted in DMS" propagation.

### B. Real-time webhook + pull

DMS webhook → LocallyAI receives notification → pulls the affected
document → re-ingests/deletes accordingly.

**Pros**: real-time. Bandwidth-efficient.
**Cons**: webhook plumbing per DMS; need to expose a webhook endpoint
to the DMS (which, for cloud DMS like NetDocuments, means LocallyAI's
office Mac needs an internet-reachable webhook URL — Tailscale Funnel
or a Cloudflare Tunnel can solve this).

### C. Live retrieval (no local mirror)

Query time → embed query → search DMS via its native search API
(if it has one — NetDocuments + iManage do) → fetch top-K docs →
rerank → answer.

**Pros**: zero storage duplication; ACL is whatever the DMS says it
is at the moment of query.
**Cons**: every query becomes a remote call (latency); the DMS's
search is keyword-based, not semantic; the LLM sees stale chunks
(no embedding for the DMS contents).

**Recommended for v1**: pattern A (periodic full sync) with a
webhook-trigger overlay where the DMS supports it. Migrate to pure
pattern B for firms whose DMS supports comprehensive webhooks.

## ACL translation

Each DMS expresses ACLs differently. LocallyAI's `doc_acls.json` is
a flat per-doc list of `allowed_users`; we map from the DMS's native
shape:

| DMS ACL primitive | LocallyAI mapping |
|---|---|
| **iManage**: matter-based access + group membership + ethical walls | Resolve at sync time: enumerate users with read on the doc → `allowed_users`. Capture matter # → `matter_code`. Capture ethical-wall membership → `ethical_wall` |
| **NetDocuments**: cabinet-level + folder-level + per-doc ACLs; group + role hierarchy | Same approach — flatten to user list at sync time |
| **SharePoint**: site / library / item permissions, security groups | Resolve via Graph API; groups expanded to user lists |
| **OpenText eDOCS**: workspace-level + folder + doc | SOAP query per doc; flatten |

**Critical**: the DMS is the source of truth for ACLs. When DMS ACL
changes, the next sync (or webhook) MUST update LocallyAI's
`doc_acls.json` AND push the new payload into Qdrant via the
existing `_update_chunk_acl_payloads` helper.

**Username mapping**: each user must exist in BOTH the DMS and
LocallyAI. Recommend the firm IT person creates LocallyAI users with
the same username/email used in the DMS, OR maintain a translation
table at `SHARED_DIR/dms_user_mapping.json` for installs where
usernames differ.

## Document identity

The DMS's stable doc ID becomes LocallyAI's `source` field:

```
source = "<dms_prefix>:<dms_doc_id>"   e.g. "imanage:NRF1.123456"
```

This lets:
- Re-ingest replace the old version without ghost copies
- Deletion in DMS → propagate to LocallyAI by source-ID match
- Audit log entries reference the canonical DMS doc ID (not a
  LocallyAI-internal filename), making cross-system audit possible

## Audit + compliance impact

Several DPA + compliance implications:

1. **Sub-processor disclosure**: if LocallyAI's office Mac is hitting
   the DMS's API, the DMS isn't a *new* sub-processor (the firm
   already has it) — but the DPA's sub-processor table (`§6.2`)
   should be updated to acknowledge LocallyAI's read access. Vendor-
   side: add a clause noting "LocallyAI reads from the firm's DMS
   at the firm's direction; LocallyAI does not transmit DMS contents
   off the office Mac."

2. **DMS access logs**: most DMSes log every doc fetch. The firm's
   DMS audit logs will show LocallyAI service-account access. The
   firm's IT should pre-authorise the integration in their internal
   change-control before LocallyAI starts hitting the DMS.

3. **Sync as ongoing processing**: GDPR Art. 30 RoPA should be
   updated when a firm enables DMS sync — the processing activity
   "ingest + index documents from DMS" gets added with its lawful
   basis (Art. 6(1)(b) contract performance).

4. **Backup-restore implications**: ISO 27001 A.8.13. If the DMS is
   a cloud service, LocallyAI's local mirror IS a backup of sorts —
   but it shouldn't be relied on for restore (the DMS is the SoT).
   Document this in the firm's BCP.

## MVP implementation plan (NetDocuments)

Phase 1 — connector module:
- New `dms_connectors/netdocuments.py` — OAuth2 client, list-by-modified, fetch-doc, fetch-acl
- New `dms_sync.py` — orchestrator that calls connector → ingest pipeline → ACL update
- New `~/locallyai/.env` settings: `LOCALLYAI_DMS_PROVIDER=netdocuments`, `NETDOCS_CLIENT_ID`, `NETDOCS_CLIENT_SECRET`, `NETDOCS_REPOSITORY_ID`
- launchd job: `app.locallyai.dms-sync` runs every 15 min

Phase 2 — webhook receiver:
- New endpoint `POST /v1/dms/webhook` (HMAC-validated against shared secret)
- Triggers immediate sync of the affected document
- Tailscale Funnel or Cloudflare Tunnel for inbound webhook URL

Phase 3 — connector parity:
- iManage Work connector
- SharePoint connector
- OpenText eDOCS connector (last; SOAP)

Phase 4 — bidirectional (optional):
- Push LocallyAI-authored documents back into DMS (where the firm
  wants drafts to land in their system of record)
- Out of scope unless a firm specifically requests

## Risks + open questions

- **OAuth2 service accounts**: each DMS has its own model. NetDocuments
  uses "App Code" credentials; iManage uses "Service User" + customer
  secret. Onboarding effort per firm includes obtaining + storing
  these. Encrypt at rest under FileVault + 0o600 .env (already done).
- **Rate limits**: cloud DMSes throttle. NetDocuments: 100 req/sec
  per app. iManage: customer-configurable. Sync logic must back off
  on 429.
- **Retention asymmetry**: LocallyAI honours its own retention policy
  for `audit.log` + chunks. DMS retention is independent. If a firm
  deletes from DMS but LocallyAI retains the embedding, that's a
  GDPR Art. 5(1)(e) risk — sync MUST propagate deletions.
- **Cross-region (KSA + DMS in EU/US)**: PDPL Art. 29 cross-border
  transfer constraints apply to LocallyAI reading from a non-KSA DMS.
  KSA firms with KSA-residency requirements likely need their DMS to
  be KSA-hosted (or separate corpus). Document in setup-saudi.md once
  the connector ships.
- **Conflict checks**: many UK firms run conflict checks via their
  practice management. LocallyAI sync should respect the conflict
  status of a matter (don't ingest documents from a matter the
  asking lawyer is conflicted out of). Phase 3 work.

## Decision points the user / business needs to make before code starts

1. **Which DMS first?** — recommend NetDocuments. Confirm.
2. **Sync vs live retrieval as default?** — recommend sync (pattern A)
   with webhook overlay. Confirm.
3. **Username mapping**: do we require firm to align usernames or
   ship a translation table? — recommend the latter (more flexible).
4. **Funding**: who pays for the connector dev? — likely vendor
   capex amortised across firms; the larger-firm subscription tier
   could be priced higher to reflect.
5. **Pilot firm**: which firm goes first? — needs an existing-DMS
   firm willing to be the dogfood install. Likely the cofounder's
   contact rather than a paying client for v0.

## File references

- `doc_acls.py` — the ACL store this integration must keep in sync
- `ingest.py` — the pipeline DMS-fetched docs flow through
- `_update_chunk_acl_payloads` (api.py) — push ACL changes from DMS
  into Qdrant payloads
- (future) `dms_connectors/`, `dms_sync.py`
- (future) `docs/sop/dms-onboarding.md` — per-firm DMS connector
  setup runbook

# Per-document access control (ACL)

The default LocallyAI behaviour is that every authenticated user can
retrieve from every chunk in the firm's corpus. This is fine for small
firms with shared knowledge bases. For firms with **partner-only
documents**, **matter-restricted documents**, or **ethical walls**
(M&A acquirer/target separation), this behaviour becomes a
confidentiality risk and (in UK) a potential SRA Code Outcome 4
violation.

This chapter is the reference for the per-document ACL feature that
solves it.

## The model

Each document has an ACL entry keyed by source filename. The entry
specifies:

- `allowed_users` — list of usernames that can retrieve from this
  document. Wildcard `"*"` means everyone in the firm.
- `matter_code` — optional client/matter identifier (audit + future
  matter-level filtering).
- `ethical_wall` — optional list of group tags (informational; useful
  for ethical-wall queries the DPO runs against the audit trail).

Documents not in the ACL file fall back to the **default policy**:

- `LOCALLYAI_DOC_ACL_DEFAULT=open` (default) — `allowed_users=["*"]`
  → everyone in the firm. Preserves behaviour for installs that don't
  use ACLs.
- `LOCALLYAI_DOC_ACL_DEFAULT=restricted` → `allowed_users=[]` → no
  one until explicitly granted. Required posture for firms with
  strict access control.

## Where ACLs live

| Data | Where | Why |
|---|---|---|
| Authoritative ACL table | `SHARED_DIR/doc_acls.json` | Fcntl-locked, atomic write, replicated across HA peers via Syncthing |
| Per-chunk payload (allowed_users + matter_code) | Qdrant collection payload | Optimisation — lets dense retrieval filter at query time without a round-trip to the ACL file |
| Filter at query time | `retrieval.py` post-filter via `doc_acls.is_allowed` | **Canonical authority** — Qdrant payload is an optimisation, not the security boundary |

The post-filter is the canonical authority because it correctly handles
the legacy case (chunks ingested before this feature shipped have no
`allowed_users` payload field, so a Qdrant filter alone would silently
drop them). The post-filter looks them up in `doc_acls.json` and applies
the default policy.

## Operator workflow — set an ACL

### Manager UI (recommended)

1. Manager UI → **Documents** tab
2. Click the **shield icon** next to the document you want to restrict
3. Edit:
   - **Allowed users** — comma-separated. Use `*` for everyone-in-firm.
     Use specific names (e.g. `Alice, Bob, Charlie`) to restrict.
   - **Matter code** — optional, for audit + future matter-level filters
   - **Ethical-wall tags** — optional, informational
4. Click **Save ACL**. The change is written to `doc_acls.json` AND
   pushed into every Qdrant chunk's payload (`chunks_updated` shown
   in the result toast).

### CLI (vendor / scripting)

```bash
# Get current ACL (or default if none set)
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/v1/documents/<filename>/acl

# Set ACL — restrict to two users on a matter
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -X PUT --data '{"allowed_users":["Alice","Bob"],"matter_code":"M-2026-0042"}' \
  https://localhost:8000/v1/documents/<filename>/acl

# Reset to default policy
curl -sk -H "Authorization: Bearer $ADMIN_KEY" -X DELETE \
  https://localhost:8000/v1/documents/<filename>/acl

# Bulk: list every explicit ACL entry
curl -sk -H "Authorization: Bearer $ADMIN_KEY" \
  https://localhost:8000/v1/documents/acls
```

## Behaviour at retrieval time

When a user queries the API:

1. The query hits `/v1/chat/completions` with the user's per-user API key
2. `validate_key` resolves the user's username
3. `retrieve(query, user=username)` runs the hybrid retrieval pipeline
4. Dense retrieval (Qdrant) returns a candidate pool widened to `top_k * 4`
5. Sparse retrieval (BM25) returns a candidate pool widened to `top_k * 2`
6. RRF fusion ranks the merged candidates
7. **ACL post-filter**: each candidate's `source` is checked against
   `doc_acls.is_allowed(source, user)`; disallowed chunks are dropped
8. Final result trimmed to `top_k`
9. Audit log records the pseudonymised user, matter_code (if known),
   and number of sources retrieved

Admin user bypasses the ACL filter (sees the full corpus); this is
intentional so the DPO can audit document content independently.

## Edge cases

| Case | Behaviour |
|---|---|
| Document has no explicit ACL | Falls back to `LOCALLYAI_DOC_ACL_DEFAULT` (default: open) |
| User exists but isn't in any document's `allowed_users` and no `*` docs exist | Receives no chunks. Chat completion proceeds with the LLM's general knowledge only. Audit log records `sources=0` |
| Wildcard `*` and specific names are both present | `*` wins (everyone allowed) |
| Document deleted via `/v1/documents/{name}` | ACL entry remains in `doc_acls.json` (orphaned, no harm) — no cleanup needed but a periodic job could prune entries with no matching Qdrant points |
| HA: ACL set on Mac-A but not yet synced to Mac-B | Mac-B reads stale `doc_acls.json`. Syncthing typically replicates within 30 s; affected Mac-B users may briefly see chunks they shouldn't until sync completes. **Mitigation**: critical ACL changes should be made with both nodes online + Syncthing actively running |
| Audit log has historical entries from before the user was removed from an ACL | Historical entries remain (audit chain is immutable). The retroactive view is correct: "user X retrieved Y at time T when they had access" |

## Auditing

The `compliance.md` Article 32 evidence pack should include:

- A point-in-time export of `doc_acls.json` (the ACL state at audit
  time)
- The audit log's matter_code field (if firms set it; surfaces in the
  compliance snapshot)
- The DPO's ethical-wall ledger (manually maintained alongside ACLs)

## Performance impact

For a firm with 50k documents → ~1M chunks in Qdrant:

- **Without ACL** (legacy): dense retrieval returns top 10 candidates;
  RRF + BM25 → top 5. Total ~50 ms.
- **With ACL**: candidate pool widened to top 40; ACL post-filter
  drops 0-30%; final top 5. Total ~80-120 ms.

The 30-70 ms overhead is the cost of correctness. If a firm's ACL
matrix is sparse (most docs `*`, few restricted), the overhead is
closer to 30 ms.

## Threats this defends against

| Threat | Defended? |
|---|---|
| Junior associate retrieves partner-only document via curiosity query | YES (post-filter drops it) |
| Lawyer on opposing side of the same matter retrieves restricted material | YES if the ACL is set; **firm must set the ACL** |
| Compromised user key retrieves docs the user couldn't see | YES — ACL is per-user, not per-key |
| Admin retrieves anything (including restricted) | YES (intentional — admin can audit corpus) |
| Stale Qdrant payload still allows a user the ACL has revoked | NO — post-filter via `doc_acls.is_allowed` is canonical. Qdrant payload is for performance only |

## Threats this does NOT defend against

| Threat | Why not |
|---|---|
| Attacker with raw filesystem access reads `data/uploads/` directly | ACL only controls retrieval; on-disk files are protected by FileVault + 0o640 ACLs |
| User who has access to Doc A retrieves Doc A and shares the response with someone who shouldn't see it | ACL controls retrieval, not what users do with retrieved content. Train users; audit log captures their retrieval |
| Document leak via inferred content (the LLM was trained on a similar published document) | Out of scope — pre-training leakage is a model-level concern |

## Related files

- `doc_acls.py` — ACL store + helpers
- `retrieval.py:HybridRetriever.retrieve` — post-filter call site
- `api.py` — `/v1/documents/{name}/acl` endpoints (GET, PUT, DELETE) +
  `/v1/documents/acls` (bulk list)
- `apps/manager-ui/src/routes/documents.tsx` — ACL editor modal
- `ingest.py:ingest_file` — stamps `allowed_users` into chunk payload
  at ingest time (default `["*"]` or per-doc-acl override)

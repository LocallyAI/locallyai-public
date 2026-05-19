# 0003 — HMAC-chained audit log for tamper evidence

- **Status:** accepted
- **Date:** 2026-05-04
- **Deciders:** single-author
- **Tags:** security, compliance, audit

## Context

Regulated firms (UK SRA, UK ICO, KSA SDAIA, EU/UK GDPR, ISO 27001:2022 §A.8.15) must retain an audit trail of access to client data and of system administrative actions. For an AI platform that answers questions about client documents, the audit log is the **primary compliance artifact** — it's what the DPO produces during a regulator inspection or a subject-access request to demonstrate "we can show who asked what, when, and what came back."

Two non-functional requirements drive the design:

1. **Tamper evidence.** A malicious operator with shell access on the office Mac should not be able to silently rewrite history — to scrub a query they shouldn't have made, or to fabricate one they didn't. The integrity check must work *offline*, with no external dependency, and must detect single-byte mutation.
2. **Privacy.** The audit log itself must not become a re-identification vector. User identities are pseudonymised (SHA-256 with a long-lived `LOCALLYAI_AUDIT_SALT`). Query content is not logged at all — only a SHA-256 hash of the query for traceability.

Standard logging libraries give neither property.

## Decision

Every audit-log entry is appended to `logs/audit.log` as a JSON line containing an `_chain_hmac` field. The HMAC is computed as `HMAC-SHA256(LOCALLYAI_AUDIT_HMAC_KEY, prev_hmac || canonical_json(entry))`. A separate verifier (`scripts/verify_audit_chain.py`, exposed at `/admin/audit-verify`) walks the file from the genesis entry and recomputes each HMAC; any mismatch returns `TAMPERED` with the line number.

The same chain shape is reused for the billing log (`logs/billing.log`) and the conflicts log (`SHARED_DIR/conflicts.log`) — one mental model for "tamper-evident append-only log" across the platform.

Salt and HMAC key are 256-bit secrets generated at install time, stored in `.env` (mode 0600), and rotated via documented procedures (`scripts/rotate_audit_salt.sh` retains old salts as `LOCALLYAI_AUDIT_SALT_ERA_N` so old pseudonyms remain re-identifiable for GDPR erasure / SAR).

See `api.py:_chain_hmac` (canonical implementation), `audit_reader.py:iter_filtered` (verifier-side reader), `conflicts.py` (reuse).

## Alternatives considered

- **Plain append-only file with OS-level write-once protection** (e.g. macOS extended attributes, immutable bit). Rejected because (a) the office Mac is operated by the firm's IT person who has root, so OS-level "immutability" is removable in one command, and (b) it gives no integrity signal — a regulator asking "how do you know nobody tampered?" gets no real answer.
- **Merkle tree** over batched entries (Certificate-Transparency-style). Materially stronger — supports efficient consistency proofs and Sparse Merkle Tree exclusions. Rejected because (a) the operational complexity is large (root publication cadence, witness server, gossip protocol) and (b) for a single-firm audit log there's no third party to publish the root to that improves trust — the firm trusts its own deployment, not a CT log mirror.
- **Per-line signature with an asymmetric key** (Ed25519). Stronger guarantee — even an operator with the symmetric HMAC key cannot mint valid entries if the signing key lives on a separate device. Rejected for v1 because (a) per-line signing was ~10× slower than HMAC at the audit-event rate the platform sees, and (b) custody of a separate signing key adds an operational story the small firm doesn't have appetite for. **Worth reconsidering** if the platform ever runs in a multi-tenant context where the operator is distinct from the audit verifier.
- **Cloud audit store** (CloudTrail / GCP Audit Logs / a dedicated SIEM). Rejected because the entire point of LocallyAI is on-premises confidentiality — shipping the audit log to a cloud provider would defeat the regulatory thesis (the cloud provider becomes a sub-processor with access to query-pattern metadata about client matters).
- **PostgreSQL with `pg_partman` + write-once tables.** Rejected on the same single-Mac-zero-ops grounds as the vector-store choice (ADR-0002) — adding Postgres just for the audit log is heavy.
- **Blockchain** (private chain, Hyperledger Fabric, etc.). Considered for completeness. Rejected because the cryptographic guarantee a blockchain offers — Byzantine fault tolerance across mutually-distrusting nodes — is not a property anyone is paying for on a single-firm deployment. It's the same HMAC chain wrapped in a much heavier framework.

## Consequences

### Positive

- **Single-byte mutation detection.** Any edit, insert, or delete inside the file is detectable by the verifier. The verifier runs in <100 ms over a year of audit entries on the recommended hardware.
- **Offline verification.** The DPO can run the verifier without network access — important for air-gapped deployments.
- **Same pattern reused** for billing log (revenue integrity) and conflicts log (regulatory artifact for SRA conflict-of-interest checks). One verifier shape, three log streams.
- **Salt rotation is non-destructive.** Old salts retained as eras; subject-access requests can still re-identify historical entries.

### Negative

- **An attacker with the HMAC key can rewrite the chain.** The key lives in `.env` mode 0600 on the office Mac. If the Mac is rooted, the chain's integrity guarantee evaporates. Mitigation: the audit-log integrity story relies on (a) FileVault disk encryption protecting `.env` at rest, (b) the launchd service running as the install user not root, and (c) the documented incident-response procedure for suspected key compromise (rotate key + flag chain as "guarded only post-rotation"). Worth strengthening with asymmetric signing in v2 if a multi-tenant deployment ever lands.
- **Append-only is enforced only by convention** — the file is a regular file. A program could open it `w+` and truncate. The verifier catches it (the chain breaks) but doesn't prevent it. Acceptable for the threat model (operator-error or malicious-operator scenarios produce a detectable TAMPERED state).
- **Canonical-JSON normalisation matters.** Two JSON serialisations of the same entry (key order, whitespace) produce different HMACs. The implementation uses `json.dumps(sort_keys=True, separators=(',', ':'))`; the verifier uses identical settings. Any drift here would produce false-positive TAMPERED results.

### Neutral

- The chain genesis is a constant `"0" * 64`. No prelude entry; the first real entry is hashed against the all-zeros prior.
- Audit entries store `user_hash`, not `user`. Pseudonymisation is on by default; the salt is mandatory at install (server refuses to start without it).

## References

- `api.py:_chain_hmac` — canonical HMAC computation
- `api.py:_write_audit` — append-with-chain function
- `audit_reader.py:iter_filtered`, `tail`, `count_lines` — reader primitives
- `scripts/verify_audit_chain.py` — full-chain verifier
- `conflicts.py:_chain_hmac` — reuse for conflict log
- `docs/sop/compliance.md` — GDPR / SRA / PDPL audit-log obligations
- `docs/runbooks/audit-chain-broken.md` — what to do when verifier returns TAMPERED
- ISO 27001:2022 §A.8.15 (Logging), §A.8.16 (Monitoring activities)
- GDPR Art. 25 (Data protection by design and by default)

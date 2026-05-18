# ISO/IEC 27001:2022 — control mapping for LocallyAI HA

This document maps each Annex A control LocallyAI claims to the
**concrete mechanism** in the codebase or operations playbook plus the
**verification command** an internal or external auditor can run to
confirm the control is in place. It is the canonical evidence file
referenced from the deploying organisation's Statement of Applicability.

Scope: 2-node Mac edition AND 2-node Windows edition. Single-node
deployments are covered where noted (the same controls apply to a
fleet-of-one).

| Annex A control | Mechanism | Verification |
|---|---|---|
| **A.5.30 — ICT readiness for business continuity** | Two homogeneous nodes (all-Mac OR all-Windows). Worker-ui smart client retries in-flight requests on the next healthy peer. Per-node Qdrant with replication_factor=2 keeps RAG queryable while one node is down. | `curl -k https://<node>:8000/admin/fleet/nodes` shows >=1 alive after a node is stopped; `curl -k …/admin/fleet/qdrant-health` shows peer state. |
| **A.5.33 — Protection of records** | HMAC-chained audit log per node (writer + verifier in `api.py`); rotation preserves chain integrity across archives; retention reset breaks the chain link to dropped archives so the verifier cannot be silently tricked. | `curl -k …/admin/fleet/audit-verify` returns `fleet_status:"ok"`. |
| **A.8.3 — Information access restriction** | API key auth (`config.validate_key`) with constant-time comparison. Admin endpoints behind a separate `LOCALLYAI_ADMIN_KEY`. Per-key TTLs (default 90d, service accounts may opt to 0=never). | `manage_users.py list` shows expiry dates; an expired key fails auth and writes `auth_failure` to `security.log`. |
| **A.8.5 — Secure authentication** | IP-based lockout after repeated failures (sliding window). Failed-auth fingerprint salted with `LOCALLYAI_AUDIT_SALT` so credential material is never logged. | Send 10 bad keys from one IP → next request returns 429. `grep auth_locked_attempt logs/security.log`. |
| **A.8.13 — Information backup** | Qdrant snapshots are operator-driven (see `docs/qdrant-ha.md`). Audit archives are gz-rotated daily and kept for `LOCALLYAI_AUDIT_RETENTION_DAYS` (default 365). | `ls logs/audit-*.log.gz` shows archives; `curl -X POST <qdrant>/collections/<name>/snapshots` creates a backup. |
| **A.8.14 — Redundancy of information processing facilities** | Two API nodes; smart-client failover; 2-node Qdrant cluster with replication_factor=2 and write_consistency=2 (writes refused during partition rather than silent divergence). | Stop one node service → second continues to serve requests; `…/admin/fleet/qdrant-health` from the surviving node shows the partition. |
| **A.8.15 — Logging** | `_write_audit` writes pseudonymised user + model + sources + latency + matter code + node_id, HMAC-chained per node. Billing log writes the same minus the pseudonymisation, behind admin-only access. | `tail -1 logs/audit.log | jq` shows the structured entry; `…/admin/audit-verify` returns ok. |
| **A.8.16 — Monitoring activities** | Sentinel thread runs every 60s: memory, disk, log growth, Qdrant lock, daily rotation, breach detection on `security.log`, sync-conflict quarantine, fleet heartbeat. Critical events post to `/admin/monitor/alerts` and (in HA) aggregate via `/admin/fleet/alerts`. | `curl -k …/admin/monitor` returns JSON; trigger 10 bad-auth attempts → `breach_detector` alert appears. |
| **A.8.23 — Web filtering** | Out of scope (LocallyAI does not browse). Network-level controls live with the deploying organisation. | n/a |
| **A.8.24 — Use of cryptography** | TLS 1.2+ via uvicorn `--ssl-keyfile/--ssl-certfile`. Self-signed RSA-4096 cert generated at install (Mac: `install.sh openssl`, Windows: `New-SelfSignedCertificate`). HMAC-SHA-256 for the audit chain (key in `LOCALLYAI_AUDIT_HMAC_KEY`). SHA-256-with-salt pseudonymisation. **Salt rotation** via `manage_users.py rotate-audit-salt` — generates a new salt, demotes the previous to `LOCALLYAI_AUDIT_SALT_ERA_1`, stamps a `salt_era_boundary` entry into the HMAC-chained audit log, retains up to N retired eras for GDPR Art. 15 subject-access on old records. | `openssl s_client -connect localhost:8000` shows TLS handshake; `audit_install.{sh,ps1}` checks key file ACL; `curl …/admin/processing-record \| jq .pseudonymity` shows current era + retained-era count + key-material findings. |
| **A.5.34 — Privacy and protection of PII** | Pseudonymisation per A.8.24 above. Audit-log entries contain `salt_era` so the verifier picks the right salt. Real names live only in billing.log (admin-only access path). | `tail -1 logs/audit.log \| jq` shows `user_hash` + `salt_era` (no real name); `tail -1 logs/billing.log \| jq .user` shows the real name (admin endpoint required). |
| **A.8.10 — Information deletion** | `manage_users.py rotate-audit-salt --keep-eras N` drops the oldest retained salt(s) when the count exceeds N — those historical pseudonyms become unrecoverable, matching the principle that pseudonymisation key material itself is regulated and should be retired on schedule. Erasure (`manage_users.py erase`) also writes a tombstone for **every era** so an erased pseudonym is honoured regardless of which salt was active when it was originally written. | `python manage_users.py rotate-audit-salt --keep-eras 0` and verify `audit.log` boundary entry; `python manage_users.py erase X` then inspect `erasure.log` for one row per era. |
| **A.5.31 — Legal, statutory, regulatory, contractual requirements** | RoPA at `/admin/processing-record` v1.2 surfaces the live HA topology + pseudonymity posture. `verify_key_material()` runs at every startup and emits warns into the boot log for any GDPR Art. 4(5) / PDPL art. 8 / art. 19 deviation (short salt, world-readable .env, colocated key material on unencrypted disk, etc.). | Review the boot log: `grep "key-material:" logs/launchd_error.log`. Expect "ok" lines for every check or actionable warns. |
| **A.8.25 — Secure development life cycle** | All changes go through commit-by-commit review. Per-phase tags (`v0.ha-phase{1..7}`) provide rollback points. `audit_install` runs after every change. | `git log --oneline`; `audit_install.{sh,ps1}` exit code. |
| **A.8.26 — Application security requirements** | Pydantic models validate every request body. Constrained patterns on `matter_code`, `client_request_id`. Constant-time comparison on auth. RAG context wrapped in `<<<DOC N START/END>>>` delimiters with explicit injection-resistant system prompt. | `curl` with malformed body returns 422; ingest a poisoned doc with "ignore previous instructions" — model refuses, `rag_suspicious_chunk` event in `security.log`. |
| **A.8.28 — Secure coding** | Prompt-injection mitigations as above. No `eval`/`exec` on user input. Subprocess calls use list form (no shell). Secrets only loaded via `dotenv`, never on the command line. | `grep -rn "shell=True" *.py` returns no matches; `grep -rn "eval(" *.py` returns no matches. |
| **A.8.29 — Security testing in development** | `tests/ha_chaos.py` exercises the failover surface (idempotency cache, fleet endpoints, tail-truncation detection, sync-conflict quarantine). | `python tests/ha_chaos.py` returns exit 0. |
| **A.8.30 — Outsourced development / supply chain** | `mlx_inference._read_pin` enforces Hugging Face commit pin per model (`.model_lock`); load-time mismatch logs a "MODEL INTEGRITY DRIFT" warning. Dependency lock files (`requirements.txt`, `package-lock.json`) committed. | `cat .model_lock`; first start with a tampered model produces the drift warning in `logs/launchd_error.log` (or Windows `logs/service.log`). |

## How an auditor uses this document

1. Each row is one claim. Run the **verification** column on a live
   deployment; the expected output is described in plain language.
2. If a row's verification fails, the deploying organisation has either
   (a) not finished the HA setup (run the relevant phase script in
   `scripts/`), or (b) regressed a control (the audit chain detects
   the actual data tampering; the operational regression is tracked in
   the fleet dashboard's alert summary).
3. The HA-specific rows (A.5.30, A.5.33 archive replay, A.8.13 snapshot,
   A.8.14, A.8.16 sync conflicts) collapse to single-node equivalents
   when `LOCALLYAI_HA` is unset; the `single-node deployments` column
   in `docs/ha-2node-clients.md` covers what each control still gives
   you.

## Compliance-relevant operational knobs

| Env var | Default | Effect on audit |
|---|---|---|
| `LOCALLYAI_AUDIT_HMAC_KEY` | unset | UNSET = chain disabled, `…/admin/audit-verify` returns `status:"skipped"` and the deployment is **not** A.8.15-compliant. Always set in production. |
| `LOCALLYAI_AUDIT_SALT` | unset | UNSET = pseudonyms can be reversed via brute-force. **Not** A.8.3-compliant. Use 64 hex chars (32 bytes entropy). |
| `LOCALLYAI_AUDIT_SALT_ERA_<N>` | unset | Retired salts (1, 2, 3, …) — populated by `rotate-audit-salt`. Keep them while you may need to re-identify pseudonyms in old audit entries (Art. 15 subject-access); drop them via `--keep-eras 0` when the data is past retention (A.8.10 information deletion). |
| `LOCALLYAI_ADMIN_KEY` | unset | UNSET = no admin endpoints reachable, including the audit verifier. Always set. |
| `LOCALLYAI_AUDIT_RETENTION_DAYS` | 365 | Lower for shorter retention; rotation drops audit archives past this and resets `.audit_chain` to start a new chain era. |
| `LOCALLYAI_HA` | unset | Set to 1 to enable cluster-aware Qdrant collection creation. |
| `LOCALLYAI_KEY_TTL_DAYS` | 90 | Default expiry for newly issued user keys (`manage_users.py add`). |
| `LOCALLYAI_MAX_CONCURRENT_INFERENCE` | 6 | Concurrency gate ceiling (A.5.30 ICT readiness — bounds memory pressure under load). |
| `LOCALLYAI_INFERENCE_QUEUE_MAX` | 24 | Queue depth before 503 backpressure. |

## Salt-rotation playbook (GDPR Art. 32 / ISO 27001 A.8.24 + A.8.10)

When to rotate:

- On a regular schedule (e.g. every 12 months), as a documented Art. 32
  control. Frequency is firm-policy; the OWASP minimum is annual.
- Immediately if `.env` has been exposed (e.g. shared in a screenshot,
  leaked in a backup, accessed by a no-longer-authorised admin).
- Before a major deployment milestone (e.g. moving from pilot to GA)
  so any pre-pilot pseudonyms become unrecoverable to anyone outside
  the deployed key-material set.

How:

```bash
python manage_users.py rotate-audit-salt --keep-eras 4
# … then restart the service so the API picks up the new salt:
launchctl kickstart -k gui/$(id -u)/com.locallyai.server
# or on Windows:
Restart-Service LocallyAIServer
```

The rotation:
1. Generates a new 32-byte salt.
2. Demotes the current to `LOCALLYAI_AUDIT_SALT_ERA_1`, shifts existing
   `ERA_N` entries to `ERA_(N+1)`.
3. Drops eras beyond `--keep-eras` — those historical audit-log
   pseudonyms become unrecoverable on subject-access (this is
   intentional: pseudonymisation key material is itself regulated).
4. Stamps a `salt_era_boundary` entry into the HMAC-chained audit log
   under the OLD salt, so the chain at the moment of rotation stays
   intact and the boundary is visible to auditors.
5. Rewrites `.env` preserving comments and key order.

After restart, every new audit entry carries `salt_era: <new era id>`.
The verifier picks the right salt per entry by era id.

## Records of Processing Activities (GDPR Art. 30)

`/admin/processing-record` returns the live RoPA as JSON, version 1.1
since this phase. The HA topology (active node list, shared-storage
path, Qdrant topology, sync layer) is included so a DPO can show
auditors that the controller has documented the new failure modes
introduced by the multi-node deployment.

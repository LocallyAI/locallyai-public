# Data isolation — what stays at the firm, what leaves

When to read: any time a firm's compliance team, DPO, or external
auditor asks **"can data from this firm reach another firm, or the
vendor?"** Also: cross-firm threat-model assessment, data-protection
impact assessment (DPIA) inputs, ISO 27001 A.5.34 / A.8.10 evidence.

## TL;DR

Each firm runs LocallyAI **on their own Mac**. There is no shared
database, no shared API, no multi-tenant server anywhere in the stack.
Cross-firm data leakage is **impossible by architecture** — no shared
infrastructure exists for it to leak through.

The only network traffic that leaves a firm's Mac is:
1. **GitHub release pulls** (vendor pushes a new release; the firm pulls).
2. **Cloudflare Worker poll** (kill-switch status check, every 60 s).
3. **HuggingFace model downloads** (when the operator picks a new LLM
   from the manager UI's Models page).

None of those carry firm-identifying data, user identifiers, or
document content.

## Architecture diagram

```
                              ┌──────────────────────────────────┐
                              │  VENDOR (LocallyAI engineer)     │
                              │                                  │
                              │  GitHub repo: source code        │
                              │  CF Worker:   kill-switch JSON   │
                              │  HuggingFace: public model files │
                              └────────────┬─────────────────────┘
                                           │
                         outbound only ────┼─────  no data sent BACK
                                           │
                                           ▼
   ┌───────────────────────────────────────────────────────────┐
   │                  FIRM A's office Mac                      │
   │                                                            │
   │  Documents (storage/uploads, data/)        Local-only      │
   │  Vector DB (Qdrant in storage/)            Local-only      │
   │  Audit log (logs/audit.log + HMAC chain)   Local-only      │
   │  Billing log                               Local-only      │
   │  Users + API keys (users.json)             Local-only      │
   │  TLS keys (tls/)                           Local-only      │
   │  Conversation history (browser localStorage) Local-only    │
   │  All chat completions, retrieval, embed    Local-only      │
   │                                                            │
   │  Outbound:   github.com (pull releases)                    │
   │              raw.githubusercontent.com (releases assets)   │
   │              api.github.com (release metadata)             │
   │              huggingface.co (model downloads)              │
   │              <vendor-cf-worker>.workers.dev (kill switch)  │
   │                                                            │
   │  Inbound:    LAN only (staff laptops on office subnet)     │
   │                                                            │
   └────────────────────────────────────────────────────────────┘

   ┌────────────────────────────────────────────────────────────┐
   │                  FIRM B's office Mac                       │
   │                                                            │
   │  Same architecture. ZERO network path between A and B.     │
   │  No shared storage. No shared API endpoint. No shared      │
   │  database. No shared identifier in any vendor system.      │
   │                                                            │
   └────────────────────────────────────────────────────────────┘
```

## Optional vendor health telemetry (opt-in, anonymised)

When the firm explicitly enables `LOCALLYAI_TELEMETRY=on` in `.env`,
the office Mac posts a small JSON heartbeat every 5 minutes to the
vendor's monitoring Worker. This is the **only** firm-to-vendor data
channel and is **off by default** — the vendor doesn't see anything
unless the firm consents.

### What the heartbeat carries
| Field | Type | Purpose |
|---|---|---|
| `firm_id` | 16-hex SHA-256 of firm name | Stable identifier across heartbeats; one-way (not the name itself) |
| `node_id` | string | Deployment hostname (already in audit log) |
| `version` | semver | Currently-applied release tag |
| `healthz_ok` | bool | API responding to /healthz |
| `sentinel_ok` | bool | Watchdog thread alive |
| `backend` | "mlx" / "ollama" / "lmstudio" | Inference backend |
| `region` | "UK" / "KSA" | For routing the right on-call |
| `uptime_seconds` | int | Process uptime (catches restart loops) |
| `free_disk_gb` | float | Disk-pressure gauge |
| `free_mem_gb` | float | OOM gauge |
| `error_count_24h` | int | Sentinel-counted errors |
| `self_heals_24h` | `{action: count}` | Self-heal action counters |
| `last_audit_event` | string | Category only ("chat_completion" / "document_deleted") |
| `pending_alerts` | `[{code, severity, message≤200chars}]` | New events since last heartbeat |
| `macos_version` | string | Marketing version (`14.4`); vendor uses this to flag firms on un-tested macOS per `maintenance.md §macos-version-policy` |
| `macos_build` | string | Build number (`23E214`); distinguishes CVE patch revisions |
| `python_version` | string | `<major>.<minor>.<patch>` of the venv's Python |
| `backend_version` | string | mlx-lm / ollama / lms version (catches a regressed dep) |

### What the heartbeat NEVER carries
- ❌ Firm name (only the SHA-256 hash)
- ❌ User names (real or pseudonymised)
- ❌ Document content, filenames, or paths
- ❌ Chat queries or responses
- ❌ Audit log entries (only category counts)
- ❌ Billing entries
- ❌ Conversation history
- ❌ TLS keys / admin keys / HMAC keys
- ❌ Embeddings or vector data
- ❌ IP addresses (the receiving Worker can SEE the source IP, but it's
       not echoed into the dashboard or persisted alongside the heartbeat)

### Vendor side
Heartbeats land in a Cloudflare Worker behind a TOTP-gated dashboard
(see `docs/monitor/cloudflare-worker/README.md`). Critical alerts
trigger email/Slack notification; if unacknowledged 3.5h after firing,
the worker re-notifies as an SLA escalation.

### Disabling

```bash
# Edit .env:
LOCALLYAI_TELEMETRY=off
# Restart API
launchctl kickstart -k "gui/$(id -u)/app.locallyai.api"
```

The firm can disable at any time. The vendor's dashboard will show
"no recent heartbeat" within ~10 min and stop displaying that firm
after 7 days (KV expiry).

### Per-firm field exclusion (`LOCALLYAI_TELEMETRY_FIELDS`)

A firm that responds to the expansion notice template asking to
keep their heartbeat restricted to a subset gets `LOCALLYAI_TELEMETRY_FIELDS`
set in their `.env` to the comma-separated allowlist of fields they
agreed to. Example:

```
LOCALLYAI_TELEMETRY_FIELDS=node_id,version,healthz_ok,sentinel_ok,free_disk_gb,free_mem_gb
```

`firm_id`, `schema_version`, and `timestamp` are always retained — the
dashboard joins on `firm_id`, so excluding it would orphan the
heartbeats. Everything else is opt-out per firm. Operator workflow:

1. Receive the firm's email response with their preferred field list.
2. Update the firm's `.env` over the maintenance channel.
3. `launchctl kickstart -k "gui/$(id -u)/app.locallyai.api"`.
4. Record the agreed field set in `vendor-records/firms/<slug>-cs-log.md`.

### Field-set change log

The fields above are the current set as of 2026-05-12. Red-team
finding 10.4: vendor must notify opt-in firms before expanding the
field set, because the original consent covered the field set as it
existed at consent time.

| Date | Fields added | Re-disclosure status |
|---|---|---|
| 2026-05-10 | (initial set) | n/a |
| 2026-05-12 | `macos_version`, `macos_build`, `python_version`, `backend_version` | Notice template at `vendor-records/templates/telemetry-field-expansion-notice.md`; vendor sends to every opt-in firm before the next telemetry release lands on their Mac |

---

## Every network call the system makes (egress allowlist)

| Destination | Protocol | What's in the request | Who can see it | Frequency |
|---|---|---|---|---|
| `github.com` / `api.github.com` / `raw.githubusercontent.com` / `objects.githubusercontent.com` | HTTPS | Standard `git fetch` / `gh release download` — public read on the LocallyAI repo | GitHub (request log: IP + tag pulled) | Daily sentinel + on-demand from manager UI |
| `huggingface.co` | HTTPS | `huggingface_hub.snapshot_download` — public model download | HuggingFace (request log: IP + model id) | Only when operator picks a new model |
| `<vendor-cf-worker>.workers.dev` (or operator-specified URL) | HTTPS GET | empty — no body | Cloudflare (request log: IP only) | Every 60 s |
| `<office-mac-host>:8000` | HTTPS | API requests from staff laptops | Office Mac itself + LAN | Continuous (per use) |
| `localhost:11434` (only if `LOCALLYAI_BACKEND=ollama`) | HTTP | Inference calls | Loopback only | Per chat |

Things that DO NOT leave the firm's Mac:
- ❌ Document content (uploaded PDFs, DOCX, etc.)
- ❌ Embeddings (vectors stay in the embedded Qdrant store)
- ❌ Chat queries
- ❌ Chat responses
- ❌ User identifiers (real names — pseudonymised before audit log)
- ❌ Audit log entries
- ❌ Billing records
- ❌ TLS private key
- ❌ Admin key
- ❌ HMAC audit-chain key
- ❌ Audit pseudonymisation salt
- ❌ Conversation history (lives in each user's browser localStorage)

## Cross-firm threat model — exhaustive

| Attack | Why it's blocked |
|---|---|
| Firm B tries to query Firm A's documents over the network | Firm B has no network route to Firm A's Mac. They're on different LANs entirely. |
| Vendor sees Firm A's documents | Vendor never receives them. The firm's Mac never uploads document content to any vendor endpoint. |
| Vendor identifies which firm pulled a release | Vendor sees an IP making a request. No firm identifier in the request. (For deeper anonymity, the firm can pull through a corporate VPN.) |
| Compromised LocallyAI GitHub account injects firm-specific code | All releases are GPG-signed; firm refuses unsigned tags. Code path identical for all firms — no per-firm code in the repo. |
| Compromised CF Worker leaks firm IPs | Worker logs only show IPs (which equal a firm's egress IP, not firm name). Operator can disable Worker logging via `wrangler tail` config. |
| Manager UI accidentally shows another firm's data | Impossible — the manager UI talks only to the firm's own API. No cross-deployment endpoints exist. |

## Per-user isolation WITHIN a firm

Different concern, separately addressed:

| What | Per-user isolation? |
|---|---|
| Conversation history | Yes — stored in each browser's localStorage, never shared |
| Document corpus | **Shared** — every user in the firm queries the same KB. By design (the firm's collective knowledge base). |
| Audit log | All entries pseudonymised. Manager UI sees the pseudonymised log; can re-identify only with the audit salt held in `.env`. |
| Billing log | Real names visible in admin-only billing endpoint (for invoicing). |

If your firm needs **per-user document scoping** (e.g. partner-only
documents lawyers below a certain rank can't see), that's not in the
current build. Talk to the vendor about per-user ACL on the corpus —
it's a roadmap item.

## How to verify isolation on a deployed firm

```bash
# 1. List every active outbound TCP connection from the LocallyAI
#    process tree. Should only show the destinations in the table above.
bash scripts/audit_egress.sh

# 2. Tail the audit log live. Should NEVER show firm-X's filename
#    or content if you're on firm-Y's Mac.
tail -f logs/audit.log

# 3. Confirm the firm name surfaces correctly in the UIs.
curl -sk https://localhost:8000/v1/branding | python3 -m json.tool
# Expected: firm_name matches LOCALLYAI_FIRM_NAME in .env, no leaks

# 4. Verify CORS rejects cross-LAN browser origins.
curl -sk -I -H "Origin: https://other-firm.local" \
  -X OPTIONS https://localhost:8000/v1/me
# Expected: missing access-control-allow-origin header (CORS rejects)

# 5. Examine network traffic during normal use. The egress allowlist
#    in docs/egress-allowlist/README.md lists every host the system
#    is permitted to contact; an IDS/firewall flagging anything else
#    is a real finding.
```

## Compliance mapping

| Control | Where this chapter answers it |
|---|---|
| GDPR Art. 5(1)(c) data minimisation | What leaves the Mac (the egress table) |
| GDPR Art. 5(1)(e) storage limitation | Audit log + retention rotation in maintenance.md |
| GDPR Art. 32 security of processing | TLS, HMAC chain, pseudonymisation, kill-switch |
| ISO 27001 A.5.34 privacy / PII protection | Per-firm Mac, no shared infrastructure |
| ISO 27001 A.8.10 information deletion | docs/sop/decommission.md (secure wipe) |
| ISO 27001 A.8.20 network security | Egress allowlist + LAN-only inbound |
| ISO 27001 A.8.22 segregation of networks | Each firm = own physical Mac, own subnet |
| KSA PDPL Art. 22 data localisation | Data physically stays on the firm's Saudi-hosted Mac |
| UK DPA 2018 / GDPR Art. 28 processor obligations | DPA template in `docs/DPA_DRAFT.md` |

"""Compliance snapshot: aggregates RoPA, audit chain, DPIA, breach
register, incident log, erasure log, retention, training, backups for
`GET /admin/compliance/snapshot`.

PR-5 of the api.py → api/ refactor: extracted from api/__init__.py.

Exposes a `router = APIRouter()` that api/__init__.py mounts via
`app.include_router(router)`. Routes are mounted WITHOUT a prefix so the
public path remains `/admin/compliance/snapshot`. The route returns a
single signed document (HMAC-chained to the same key as the audit log)
so the DPO can archive a monthly copy and prove to a regulator that
the contents weren't altered — see `scripts/verify_compliance_snapshot.py`.

Pure-body helpers `processing_record_body()` and `audit_verify_body()`
live in api/_shared.py so this module can call them directly without
going through the auth'd admin route handlers.
"""
from __future__ import annotations

import hmac as _hmac_mod
import json
import logging
import os
from datetime import UTC

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from api._shared import (
    _AUDIT_HMAC_KEY,
    _BACKUP_ATTESTATIONS_FILE,
    _TRAINING_RECORDS_FILE,
    AUDIT_LOG,
    SECURITY_LOG,
    _admin_auth,
    audit_verify_body,
    processing_record_body,
)
from config import (
    BASE_DIR,
    BILLING_LOG,
)
from config import NODE_ID as _NODE_ID

log = logging.getLogger("api")

router = APIRouter()


# ── DPO compliance snapshot ────────────────────────────────────────────────
# Single-document aggregation of every compliance-relevant signal, signed
# at the document level so the DPO can archive a copy monthly and prove to
# a regulator (or to internal audit) that the contents weren't altered.
# Replaces "the DPO trusts our docs" with "the DPO has a signed monthly
# artifact they can verify themselves."

# Sub-processor table mirrors DPA_DRAFT.md §6.2. Hard-extracted here
# because the DPA file format may change; this is the structured source
# of truth for the snapshot. Update both together.
_COMPLIANCE_SUB_PROCESSORS = [
    {"name": "Cloudflare", "role": "Worker hosting (vendor monitor + kill switch)",
     "observable": "Anonymised heartbeats: firm_id (SHA-256 hash), node_id, version, health gauges, alert codes",
     "client_data_exposure": "None",
     "soc2_url": "https://www.cloudflare.com/trust-hub/compliance-resources/",
     "soc2_last_reviewed": "2026-04-01"},
    {"name": "GitHub", "role": "Code repository hosting + signed release tag distribution",
     "observable": "Source code (no Client Data) + commit metadata (vendor identities)",
     "client_data_exposure": "None",
     "soc2_url": "https://github.com/security/trust",
     "soc2_last_reviewed": "2026-04-01"},
    {"name": "Hugging Face", "role": "Anonymous public model + embedder downloads at install time",
     "observable": "Source IP of the download (one transaction per install)",
     "client_data_exposure": "None",
     "soc2_url": "https://huggingface.co/security",
     "soc2_last_reviewed": "2026-04-01"},
    {"name": "Resend", "role": "Outbound email for vendor alerts",
     "observable": "Subject + body of vendor alert emails (firm_id hash + structured codes)",
     "client_data_exposure": "None",
     "soc2_url": "https://resend.com/security",
     "soc2_last_reviewed": "2026-04-01"},
    {"name": "Slack (optional)", "role": "Vendor on-call channel for alert echo",
     "observable": "Same content as Resend emails when sink enabled",
     "client_data_exposure": "None",
     "soc2_url": "https://slack.com/trust/compliance",
     "soc2_last_reviewed": "2026-04-01"},
    {"name": "Apple (future)", "role": "Code-signing certificate for client apps",
     "observable": "App bundle contents (no Client Data)",
     "client_data_exposure": "None",
     "soc2_url": "https://www.apple.com/legal/privacy/data/en/",
     "soc2_last_reviewed": "—"},
]

# Heartbeat field set as of this code version. Bumping the version means
# the vendor must re-disclose to opt-in firms before the bump ships.
_COMPLIANCE_TELEMETRY_FIELD_SET = {
    "version": "2026-05-12",
    "fields": [
        "firm_id", "node_id", "version", "healthz_ok", "sentinel_ok", "backend",
        "region", "uptime_seconds", "free_disk_gb", "free_mem_gb", "error_count_24h",
        "self_heals_24h", "last_audit_event", "pending_alerts",
        "macos_version", "macos_build", "python_version", "backend_version",
    ],
    "never_carries": [
        "firm name", "user names", "document content / filenames / paths",
        "chat queries or responses", "audit log entries (only category counts)",
        "billing entries", "conversation history", "TLS / admin / HMAC keys",
        "embeddings or vector data", "IP addresses (Worker sees but does not persist)",
    ],
}


def _compliance_retention_status() -> dict:
    """Per-stream retention horizons + oldest-entry timestamps so the DPO
    can confirm GDPR Art. 5(1)(e) storage-limitation in one glance."""
    out = {}
    streams = [
        ("audit", AUDIT_LOG, int(os.environ.get("LOCALLYAI_AUDIT_RETENTION_DAYS", "365"))),
        ("billing", BILLING_LOG, int(os.environ.get("LOCALLYAI_BILLING_RETENTION_DAYS", "2555"))),
        ("security", SECURITY_LOG, int(os.environ.get("LOCALLYAI_SECURITY_RETENTION_DAYS", "365"))),
    ]
    for name, path, days in streams:
        info = {"configured_days": days, "exists": path.exists()}
        if path.exists():
            info["size_bytes"] = path.stat().st_size
            try:
                # Read the first line for oldest-entry timestamp without preloading the file
                with open(path, encoding="utf-8", errors="replace") as fh:
                    first = fh.readline().strip()
                if first:
                    try:
                        e = json.loads(first)
                        info["oldest_entry_at"] = e.get("timestamp", "")
                    except Exception:
                        info["oldest_entry_at"] = "unparseable"
            except Exception:
                info["oldest_entry_at"] = ""
        out[name] = info
    return out


def _compliance_erasure_summary() -> dict:
    """Last 5 erasures + total count from the shared erasure ledger.
    Pseudonyms only — by design, the ledger never holds raw names."""
    from config import ERASURE_LOG
    if not ERASURE_LOG.exists():
        return {"total_erasures": 0, "last_5": []}
    from audit_reader import count_lines, iter_filtered
    total = count_lines(ERASURE_LOG)
    # Take the last 5 erasure events. iter_filtered streams the whole file —
    # acceptable here because erasure.log is ledger-paced, not query-paced.
    all_events = [e for e in iter_filtered(ERASURE_LOG, lambda e: e.get("event") == "erasure")]
    return {"total_erasures": total, "last_5": all_events[-5:]}


def _compliance_audit_log_sample(n: int = 30) -> list:
    """Last n audit entries — already pseudonymised + content-hash only,
    so safe to embed verbatim. Auditors want to see the SHAPE of what
    gets logged (timestamp, user_hash, model, sources, latency,
    matter_code, query_hash) — not just an "entries: N" count.
    """
    if not AUDIT_LOG.exists():
        return []
    from audit_reader import tail
    out: list = []
    for line in tail(AUDIT_LOG, n):
        try:
            e = json.loads(line)
            # Strip the chain HMAC so the sample is purely behavioural;
            # the chain integrity is reported separately by audit-verify.
            e.pop("_chain_hmac", None)
            out.append(e)
        except Exception:
            continue
    return out


def _compliance_incident_register(days: int = 90) -> list:
    """Full security.log entries from the last `days` days, ordered
    most-recent first. Auditors following a breach inspection want the
    actual records, not the bucketed counts in `breach_events_30d`.
    """
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    if not SECURITY_LOG.exists():
        return []
    cutoff = _dt.now(UTC) - _td(days=days)
    from audit_reader import iter_filtered

    def _within(e: dict) -> bool:
        ts_str = (e.get("timestamp", "") or "").strip()
        if not ts_str:
            return False
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        try:
            ts = _dt.fromisoformat(ts_str)
        except Exception:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts >= cutoff

    entries = list(iter_filtered(SECURITY_LOG, _within))
    # Most-recent first; cap at 100 to keep snapshot bounded.
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries[:100]


def _compliance_training_records() -> dict:
    """Training-records summary for ISO 27001 A.6.3 (information
    security awareness). Reports unique users trained + last training
    event + per-topic counts. Records are added via
    /admin/training-records (separate route)."""
    if not _TRAINING_RECORDS_FILE.exists():
        return {"total_records": 0, "users_trained": 0, "topics": {}, "last_recorded_at": None}
    try:
        records = json.loads(_TRAINING_RECORDS_FILE.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            return {"total_records": 0, "users_trained": 0, "topics": {}, "last_recorded_at": None}
    except Exception:
        return {"total_records": 0, "users_trained": 0, "topics": {}, "last_recorded_at": None}
    users = {r.get("user", "") for r in records if r.get("user")}
    topics: dict = {}
    last_ts = ""
    for r in records:
        t = r.get("topic", "unspecified")
        topics[t] = topics.get(t, 0) + 1
        ts = r.get("completed_at", "")
        if ts > last_ts:
            last_ts = ts
    return {
        "total_records": len(records),
        "users_trained": len(users),
        "topics": topics,
        "last_recorded_at": last_ts or None,
    }


def _compliance_backup_attestations() -> dict:
    """Backup-restore test attestations for ISO 27001 A.8.13 / A.8.14.
    Operator records each successful restore-from-backup test (ad-hoc
    or scheduled) via /admin/backup-attestations. Snapshot reports the
    most recent 5 + the cadence."""
    if not _BACKUP_ATTESTATIONS_FILE.exists():
        return {"total": 0, "last_5": [], "last_test_at": None}
    try:
        records = json.loads(_BACKUP_ATTESTATIONS_FILE.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            return {"total": 0, "last_5": [], "last_test_at": None}
    except Exception:
        return {"total": 0, "last_5": [], "last_test_at": None}
    records.sort(key=lambda r: r.get("tested_at", ""), reverse=True)
    return {
        "total": len(records),
        "last_5": records[:5],
        "last_test_at": records[0].get("tested_at") if records else None,
    }


def _compliance_dpia(ropa: dict) -> dict:
    """Auto-generated DPIA per GDPR Art. 35 / KSA PDPL Art. 33. The
    template is auto-filled from the live RoPA where the answer is
    deterministic (lawful basis, recipients, transfers, retention,
    security measures); free-text fields are left for the firm's DPO
    to complete (necessity & proportionality assessment, risk-to-rights
    assessment, mitigations beyond defaults). Treat the auto-generated
    sections as the vendor's input; the firm-completed sections as
    the controller's input.
    """
    purposes = ropa.get("purposes", [])
    cats = ropa.get("categories_of_data", [])
    return {
        "version": "1.0",
        # Filled in at snapshot time by the caller — see compliance_snapshot()
        # which overwrites this with the actual generation timestamp.
        "generated_at": None,
        "regulation": "GDPR Art. 35 / UK DPA 2018 / KSA PDPL Art. 33 / UAE PDPL Art. 22",
        # Section A — context (auto from RoPA)
        "controller": ropa.get("controller", {}),
        "processing_purposes": purposes,
        "categories_of_data_subjects": ["lawyers / fee-earners (the firm's staff)",
                                         "clients of the firm (data subjects whose information appears in queries / documents)"],
        "categories_of_personal_data": cats,
        "recipients": ropa.get("recipients", []),
        "international_transfers": ropa.get("international_transfers", ""),
        "retention": ropa.get("retention", {}),

        # Section B — necessity & proportionality (auto where derivable;
        # firm-completed where judgment is required)
        "necessity_and_proportionality": {
            "lawful_basis_per_processing_activity": purposes,
            "purpose_limitation_assessment": (
                "Each processing activity has a single declared purpose; the "
                "audit log captures every query so any drift can be detected "
                "(GDPR Art. 5(1)(b) tamper-evidence)."
            ),
            "data_minimisation_assessment": (
                "Audit log stores SHA-256 query hash only, not query content. "
                "User identifiers in audit log are pseudonymised with rotating "
                "salt eras (GDPR Art. 25). Vendor heartbeats carry firm_id hash "
                "only; never carry document or chat content."
            ),
            "accuracy_assessment": "Firm completes — describes review processes for AI output.",
            "storage_limitation_assessment": (
                "Per-stream retention configured: audit "
                f"{ropa.get('retention',{}).get('audit_log_days','—')}d; billing "
                f"{ropa.get('retention',{}).get('billing_log_days','—')}d. Erasure "
                "ledger honoured across HA peers (manage_users.py erase)."
            ),
        },

        # Section C — risk identification (firm-driven; defaults sketched)
        "risks_to_rights_and_freedoms": [
            {"risk": "Unauthorised access to legally privileged content",
             "likelihood": "Low (LAN-only, mTLS, per-user keys, lockout)",
             "severity": "High (privilege loss; SRA implications)",
             "mitigations": [
                 "TLS 1.2+ in transit (RSA-4096 self-signed cert)",
                 "FileVault / BitLocker at-rest encryption",
                 "Per-user API keys with rate limiting + IP-based lockout",
                 "Pseudonymisation of user identifiers in audit log",
                 "HMAC-chained audit log (tamper-evident)",
             ]},
            {"risk": "Re-identification of audit subjects from pseudonyms",
             "likelihood": "Low (salt rotated on era boundaries; salt at rest under FileVault)",
             "severity": "Medium",
             "mitigations": [
                 "LOCALLYAI_AUDIT_SALT ≥ 32 chars enforced at startup",
                 "0o600 ACL on .env",
                 "Salt eras retained for subject-access; rotation on incident",
             ]},
            {"risk": "AI-generated output relied on without human review",
             "likelihood": "Medium (depends on firm training)",
             "severity": "High (professional indemnity, SRA Outcome 7)",
             "mitigations": [
                 "DPA Clause 9.4 disclaims vendor liability for unreviewed output",
                 "Persistent UI disclaimer on every AI response",
                 "Firm completes — describe training + supervision controls.",
             ]},
        ],

        # Section D — controller sign-off (firm fills)
        "controller_sign_off": {
            "dpo_name": "—",
            "dpo_signature_date": "—",
            "consultation_with_data_subjects": "—",
            "supervisory_authority_consultation_required": False,
        },
    }


def _compliance_breach_events_30d() -> list:
    """Tail security.log for events in the last 30 days. Bucketed by code+severity."""
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    if not SECURITY_LOG.exists():
        return []
    cutoff = _dt.now(UTC) - _td(days=30)
    from audit_reader import iter_filtered

    def _within_window(e: dict) -> bool:
        ts_str = (e.get("timestamp", "") or "").strip()
        if not ts_str:
            return False
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        try:
            ts = _dt.fromisoformat(ts_str)
        except Exception:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts >= cutoff

    buckets: dict = {}
    for e in iter_filtered(SECURITY_LOG, _within_window):
        code = e.get("code") or e.get("event") or "unknown"
        sev = e.get("severity") or e.get("level") or "info"
        k = f"{sev}:{code}"
        buckets[k] = buckets.get(k, 0) + 1
    return [{"severity_code": k, "count": v} for k, v in sorted(buckets.items())]


def _compliance_conflict_checks() -> dict:
    """Conflict-check counts for the DPO snapshot. Auditors look for
    evidence the firm runs checks systematically — count + status mix is
    enough; the underlying log entries pseudonymise party names already."""
    try:
        from conflicts import summary_for_compliance as _conf_summary
        return _conf_summary()
    except Exception as exc:
        return {"error": f"conflict-check log unreadable: {exc}",
                "total": 0, "last_30d": 0, "status_counts": {}}


@router.get("/admin/compliance/snapshot")
def compliance_snapshot(format: str = "json", key: str = Depends(_admin_auth)):
    """DPO monthly snapshot. Single signed document aggregating RoPA +
    audit-verify + key-material + sub-processors + telemetry disclosure +
    retention status + erasure summary + recent breach events.

    `format=json` (default) returns the raw bundle.
    `format=html` returns a printable single-page report — the DPO
    prints to PDF locally with Cmd-P and files the result for their
    monthly internal-audit cycle.

    The bundle is HMAC-signed at the document level using the same key
    that protects the audit chain. Saved copies can be verified offline
    with `scripts/verify_compliance_snapshot.py`.
    """
    import hashlib as _hl_compl
    from datetime import datetime as _dt

    from config import DATA_REGION as _data_region
    try:
        with open(BASE_DIR / "release_manifest.json", encoding="utf-8") as _mf:
            _release_version = json.load(_mf).get("version", "unknown")
    except Exception:
        _release_version = "unknown"
    deployment = {
        "deployment_id": os.environ.get("LOCALLYAI_DEPLOYMENT_ID", "locallyai"),
        "firm_id": _hl_compl.sha256(
            f"locallyai-firm:{os.environ.get('LOCALLYAI_FIRM_NAME', '').strip()}".encode()
        ).hexdigest()[:16],
        "node_id": _NODE_ID,
        "region": _data_region,
        "version": _release_version,
    }

    ropa = processing_record_body()
    dpia = _compliance_dpia(ropa)
    dpia["generated_at"] = _dt.now(UTC).isoformat()
    bundle = {
        "version": "1.1",
        "generated_at": _dt.now(UTC).isoformat(),
        "deployment": deployment,
        "ropa": ropa,
        "dpia": dpia,
        "audit_chain": audit_verify_body(),
        "audit_log_sample": _compliance_audit_log_sample(30),
        "key_material": __import__("config").verify_key_material(),
        "sub_processors": _COMPLIANCE_SUB_PROCESSORS,
        "telemetry_disclosure": {
            **_COMPLIANCE_TELEMETRY_FIELD_SET,
            "active_allowlist": [
                f.strip() for f in os.environ.get("LOCALLYAI_TELEMETRY_FIELDS", "").split(",") if f.strip()
            ],
        },
        "retention_status": _compliance_retention_status(),
        "erasure_log": _compliance_erasure_summary(),
        "training_records": _compliance_training_records(),
        "backup_attestations": _compliance_backup_attestations(),
        "incident_register_90d": _compliance_incident_register(90),
        "breach_events_30d": _compliance_breach_events_30d(),
        "conflict_checks":     _compliance_conflict_checks(),
    }

    # Document-level HMAC. Same key as the audit chain so the DPO doesn't
    # need a separate key to verify. Sort keys for deterministic signing.
    body_json = json.dumps(bundle, sort_keys=True, default=str)
    bundle["snapshot_hmac"] = (
        _hmac_mod.new(_AUDIT_HMAC_KEY, body_json.encode(), "sha256").hexdigest()
        if _AUDIT_HMAC_KEY else ""
    )

    if format == "html":
        ts = bundle["generated_at"][:10]
        dep = bundle["deployment"]["deployment_id"]
        fname = f"compliance-snapshot-{dep}-{ts}.html"
        return HTMLResponse(
            content=_render_compliance_snapshot_html(bundle),
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    return bundle


def _render_compliance_snapshot_html(bundle: dict) -> str:
    """Single-page printable HTML. Embeds the JSON bundle in a
    machine-parseable <script type=\"application/json\"> tag so
    verify_compliance_snapshot.py can extract + re-verify it."""
    import html as _html
    dep = bundle["deployment"]
    audit = bundle["audit_chain"]
    keymat = bundle["key_material"]
    retention = bundle["retention_status"]
    erasure = bundle["erasure_log"]
    breaches = bundle["breach_events_30d"]
    subs = bundle["sub_processors"]
    tele = bundle["telemetry_disclosure"]
    dpia = bundle.get("dpia", {})
    audit_sample = bundle.get("audit_log_sample", [])
    incidents = bundle.get("incident_register_90d", [])
    training = bundle.get("training_records", {})
    backups = bundle.get("backup_attestations", {})

    def _esc(x): return _html.escape(str(x))

    def _status_pill(level: str) -> str:
        color = {"ok": "#16a34a", "skipped": "#737373", "warn": "#d97706",
                 "fail": "#dc2626", "TAMPERED": "#dc2626"}.get(level, "#737373")
        return f'<span class="pill" style="background:{color}">{_esc(level)}</span>'

    keymat_rows = "".join(
        f"<tr><td>{_esc(f.get('code',''))}</td><td>{_status_pill(f.get('level','info'))}</td><td>{_esc(f.get('message',''))}</td></tr>"
        for f in keymat
    )
    sub_rows = "".join(
        f"<tr><td>{_esc(s['name'])}</td><td>{_esc(s['role'])}</td><td>{_esc(s['observable'])}</td>"
        f"<td>{_esc(s['client_data_exposure'])}</td>"
        f"<td>{_esc(s.get('soc2_last_reviewed','—'))}"
        + (f"<br/><a href=\"{_esc(s.get('soc2_url',''))}\">SOC2</a>" if s.get('soc2_url') else "")
        + "</td></tr>"
        for s in subs
    )
    audit_sample_rows = "".join(
        f"<tr><td>{_esc(e.get('timestamp',''))[:19]}</td>"
        f"<td><code>{_esc(e.get('user_hash','—'))[:16]}</code></td>"
        f"<td>{_esc(e.get('model','—'))[:30]}</td>"
        f"<td style='text-align:right'>{_esc(e.get('sources','—'))}</td>"
        f"<td style='text-align:right'>{_esc(e.get('latency_ms','—'))}</td>"
        f"<td><code>{_esc(e.get('query_hash','—'))[:12]}</code></td>"
        f"<td>{_esc(e.get('matter_code',''))}</td></tr>"
        for e in audit_sample
    ) or '<tr><td colspan="7" style="color:#737373">No audit entries yet.</td></tr>'
    incident_rows = "".join(
        f"<tr><td>{_esc(i.get('timestamp',''))[:19]}</td>"
        f"<td>{_esc(i.get('event','') or i.get('code','—'))}</td>"
        f"<td>{_esc(i.get('severity') or i.get('level','info'))}</td>"
        f"<td>{_esc((i.get('message') or i.get('detail',''))[:200])}</td></tr>"
        for i in incidents
    ) or '<tr><td colspan="4" style="color:#737373">No incidents recorded in the last 90 days.</td></tr>'
    dpia_risks = "".join(
        f"<tr><td>{_esc(r.get('risk',''))}</td>"
        f"<td>{_esc(r.get('likelihood',''))}</td>"
        f"<td>{_esc(r.get('severity',''))}</td>"
        f"<td><ul style='margin:0;padding-left:18px'>"
        + "".join(f"<li>{_esc(m)}</li>" for m in r.get('mitigations', []))
        + "</ul></td></tr>"
        for r in dpia.get("risks_to_rights_and_freedoms", [])
    )
    training_topic_rows = "".join(
        f"<tr><td>{_esc(t)}</td><td style='text-align:right'>{_esc(c)}</td></tr>"
        for t, c in training.get("topics", {}).items()
    ) or '<tr><td colspan="2" style="color:#737373">No training records yet.</td></tr>'
    backup_rows = "".join(
        f"<tr><td>{_esc(r.get('tested_at',''))[:19]}</td>"
        f"<td>{_esc(r.get('test_type',''))}</td>"
        f"<td>{_esc(r.get('result',''))}</td>"
        f"<td>{_esc(r.get('operator',''))}</td>"
        f"<td>{_esc(r.get('notes',''))[:80]}</td></tr>"
        for r in backups.get("last_5", [])
    ) or '<tr><td colspan="5" style="color:#737373">No backup tests attested yet.</td></tr>'
    retention_rows = "".join(
        f"<tr><td>{_esc(name)}</td><td>{_esc(info.get('configured_days',''))}d</td>"
        f"<td>{_esc(info.get('oldest_entry_at','—'))}</td>"
        f"<td>{_esc(info.get('size_bytes','—'))}</td></tr>"
        for name, info in retention.items()
    )
    erasure_rows = "".join(
        f"<tr><td>{_esc(e.get('timestamp',''))}</td><td><code>{_esc(e.get('pseudonym',''))[:16]}</code></td><td>{_esc(e.get('salt_era',''))}</td></tr>"
        for e in erasure.get("last_5", [])
    ) or '<tr><td colspan="3" style="color:#737373">No erasures recorded.</td></tr>'
    breach_rows = "".join(
        f"<tr><td>{_esc(b['severity_code'])}</td><td style=\"text-align:right\">{_esc(b['count'])}</td></tr>"
        for b in breaches
    ) or '<tr><td colspan="2" style="color:#737373">No breach events in the last 30 days.</td></tr>'

    conflicts_summary = bundle.get("conflict_checks", {}) or {}
    conflict_status_rows = "".join(
        f"<tr><td>{_esc(s)}</td><td style=\"text-align:right\">{_esc(c)}</td></tr>"
        for s, c in (conflicts_summary.get("status_counts") or {}).items()
    ) or '<tr><td colspan="2" style="color:#737373">No conflict checks recorded in the last 30 days.</td></tr>'

    embedded_json = json.dumps(bundle, sort_keys=True, default=str)
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>LocallyAI compliance snapshot — {_esc(dep['deployment_id'])} — {_esc(bundle['generated_at'][:10])}</title>
<style>
  body {{ font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; max-width: 980px; margin: 32px auto; padding: 0 24px; color: #18181b; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2 {{ font-size: 16px; margin: 32px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #e4e4e7; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
  th {{ font-weight: 600; color: #52525b; background: #fafafa; }}
  code {{ font: 12px ui-monospace, "SF Mono", monospace; background: #f4f4f5; padding: 1px 5px; border-radius: 3px; }}
  .meta {{ color: #71717a; font-size: 12px; }}
  .pill {{ display: inline-block; padding: 1px 8px; border-radius: 10px; color: white; font-size: 11px; font-weight: 600; }}
  .deck {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin: 12px 0; }}
  .stat {{ padding: 10px 12px; border: 1px solid #e4e4e7; border-radius: 6px; }}
  .stat .v {{ font-size: 18px; font-weight: 600; }}
  .stat .k {{ color: #71717a; font-size: 12px; }}
  .sig {{ margin-top: 32px; padding: 12px; background: #fafafa; border: 1px solid #e4e4e7; border-radius: 6px; font: 11px ui-monospace, monospace; word-break: break-all; }}
  .sig .label {{ font: 600 12px -apple-system; color: #52525b; margin-bottom: 4px; }}
  @media print {{ body {{ margin: 0; max-width: none; }} h2 {{ page-break-after: avoid; }} table {{ page-break-inside: avoid; }} }}
</style>
</head><body>
<h1>LocallyAI compliance snapshot</h1>
<div class="meta">
  Deployment: <code>{_esc(dep['deployment_id'])}</code>
  · Region: <code>{_esc(dep['region'])}</code>
  · firm_id: <code>{_esc(dep['firm_id'])}</code>
  · Node: <code>{_esc(dep['node_id'])}</code>
  · Version: <code>{_esc(dep['version'])}</code>
  · Generated: <code>{_esc(bundle['generated_at'])}</code>
</div>

<h2>At a glance</h2>
<div class="deck">
  <div class="stat"><div class="k">Audit chain</div><div class="v">{_status_pill(audit.get('status','?'))}</div></div>
  <div class="stat"><div class="k">Key-material findings</div><div class="v">{len([f for f in keymat if f.get('level') != 'ok'])} non-OK / {len(keymat)} total</div></div>
  <div class="stat"><div class="k">Erasures recorded</div><div class="v">{erasure.get('total_erasures', 0)}</div></div>
  <div class="stat"><div class="k">Breach events (30d)</div><div class="v">{sum(b['count'] for b in breaches)}</div></div>
</div>

<h2>Records of Processing Activities (RoPA)</h2>
<table>
  <tr><th>Field</th><th>Value</th></tr>
  <tr><td>RoPA version</td><td>{_esc(bundle['ropa'].get('version',''))}</td></tr>
  <tr><td>Controller deployment_id</td><td>{_esc(bundle['ropa'].get('controller',{}).get('deployment_id',''))}</td></tr>
  <tr><td>International transfers</td><td>{_esc(bundle['ropa'].get('international_transfers',''))}</td></tr>
  <tr><td>Erasure procedure</td><td>{_esc(bundle['ropa'].get('data_subject_rights',{}).get('erasure',''))}</td></tr>
</table>
<p class="meta">Full RoPA available via <code>GET /admin/processing-record</code>.</p>

<h2>Audit chain integrity</h2>
<table>
  <tr><th>Field</th><th>Value</th></tr>
  <tr><td>Status</td><td>{_status_pill(audit.get('status','?'))}</td></tr>
  <tr><td>Entries verified</td><td>{_esc(audit.get('entries','—'))}</td></tr>
  <tr><td>Node</td><td><code>{_esc(audit.get('node_id','—'))}</code></td></tr>
  {f'<tr><td>Reason</td><td>{_esc(audit.get("reason",""))}</td></tr>' if audit.get('status') != 'ok' else ''}
</table>

<h2>Key-material posture</h2>
<table>
  <tr><th>Code</th><th>Level</th><th>Message</th></tr>
  {keymat_rows or '<tr><td colspan="3" style="color:#737373">No findings.</td></tr>'}
</table>

<h2>Sub-processors (DPA Schedule §6.2)</h2>
<table>
  <tr><th>Name</th><th>Role</th><th>What they observe</th><th>Client data exposure</th><th>SOC2 reviewed</th></tr>
  {sub_rows}
</table>

<h2>DPIA (Data Protection Impact Assessment — GDPR Art. 35)</h2>
<p class="meta">Auto-generated from RoPA. Sections marked "—" are firm-completed (controller sign-off, training/supervision narrative). The vendor's inputs are deterministic; the firm's risk-to-rights assessment requires DPO judgement.</p>
<h3 style="font-size:13px;margin:10px 0 4px">Necessity & proportionality</h3>
<table>
  <tr><th>Aspect</th><th>Assessment</th></tr>
  <tr><td>Purpose limitation</td><td>{_esc(dpia.get('necessity_and_proportionality',{}).get('purpose_limitation_assessment',''))}</td></tr>
  <tr><td>Data minimisation</td><td>{_esc(dpia.get('necessity_and_proportionality',{}).get('data_minimisation_assessment',''))}</td></tr>
  <tr><td>Accuracy</td><td>{_esc(dpia.get('necessity_and_proportionality',{}).get('accuracy_assessment',''))}</td></tr>
  <tr><td>Storage limitation</td><td>{_esc(dpia.get('necessity_and_proportionality',{}).get('storage_limitation_assessment',''))}</td></tr>
</table>
<h3 style="font-size:13px;margin:14px 0 4px">Risks to rights & freedoms</h3>
<table>
  <tr><th>Risk</th><th>Likelihood</th><th>Severity</th><th>Mitigations</th></tr>
  {dpia_risks}
</table>
<p class="meta">Controller sign-off (firm-completed): DPO {_esc(dpia.get('controller_sign_off',{}).get('dpo_name','—'))} · signed {_esc(dpia.get('controller_sign_off',{}).get('dpo_signature_date','—'))}</p>

<h2>Audit-log sample — last 30 entries</h2>
<p class="meta">Pseudonymised + query-hash only (no content). Provided so auditors can see the SHAPE of what's logged, not just the integrity count.</p>
<table>
  <tr><th>Timestamp</th><th>User hash</th><th>Model</th><th style="text-align:right">Sources</th><th style="text-align:right">Latency ms</th><th>Query hash</th><th>Matter code</th></tr>
  {audit_sample_rows}
</table>

<h2>Incident register — last 90 days</h2>
<p class="meta">Source: <code>security.log</code>. Full entries; the bucketed summary below complements but does not replace this view.</p>
<table>
  <tr><th>Timestamp</th><th>Event / code</th><th>Severity</th><th>Message</th></tr>
  {incident_rows}
</table>

<h2>Training records (ISO 27001 A.6.3)</h2>
<p>Total records: {training.get('total_records', 0)} · unique users trained: {training.get('users_trained', 0)} · last recorded: <code>{_esc(training.get('last_recorded_at') or '—')}</code></p>
<table>
  <tr><th>Topic</th><th style="text-align:right">Records</th></tr>
  {training_topic_rows}
</table>

<h2>Backup test attestations (ISO 27001 A.8.13 / A.8.14)</h2>
<p>Total tests: {backups.get('total', 0)} · last test: <code>{_esc(backups.get('last_test_at') or '—')}</code></p>
<table>
  <tr><th>Tested at</th><th>Test type</th><th>Result</th><th>Operator</th><th>Notes</th></tr>
  {backup_rows}
</table>

<h2>Telemetry disclosure</h2>
<p>Heartbeat field-set version: <code>{_esc(tele['version'])}</code> · Active allowlist: <code>{_esc(tele['active_allowlist'] or 'all fields')}</code></p>
<table>
  <tr><th>Always carries</th><th>Never carries</th></tr>
  <tr>
    <td>{', '.join(_esc(f) for f in tele['fields'])}</td>
    <td>{', '.join(_esc(f) for f in tele['never_carries'])}</td>
  </tr>
</table>

<h2>Retention status</h2>
<table>
  <tr><th>Stream</th><th>Configured</th><th>Oldest entry</th><th>Size (bytes)</th></tr>
  {retention_rows}
</table>

<h2>Erasure log (last 5)</h2>
<table>
  <tr><th>Timestamp</th><th>Pseudonym</th><th>Salt era</th></tr>
  {erasure_rows}
</table>

<h2>Breach events (last 30 days, bucketed)</h2>
<table>
  <tr><th>Severity:Code</th><th style="text-align:right">Count</th></tr>
  {breach_rows}
</table>

<h2>Conflict checks</h2>
<p>Total recorded: {conflicts_summary.get('total', 0)} · last 30 days: {conflicts_summary.get('last_30d', 0)}</p>
<table>
  <tr><th>Status (last 30 days)</th><th style="text-align:right">Count</th></tr>
  {conflict_status_rows}
</table>
<p class="meta">Party names in <code>conflicts.log</code> are SHA-256 pseudonyms (same salt as audit-log user pseudonyms). The check itself is the audit-trail evidence; party identities live only in operator UI sessions.</p>

<div class="sig">
  <div class="label">Snapshot HMAC (verify with <code>python scripts/verify_compliance_snapshot.py &lt;file&gt;</code>):</div>
  {_esc(bundle.get('snapshot_hmac','(unsigned — LOCALLYAI_AUDIT_HMAC_KEY not set)'))}
</div>

<script type="application/json" id="locallyai-compliance-snapshot">
{embedded_json}
</script>
</body></html>"""

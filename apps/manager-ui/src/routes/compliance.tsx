import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/TopBar";
import { Download, Loader2, ShieldCheck, AlertTriangle, RotateCw } from "lucide-react";
import {
  getComplianceSnapshot,
  downloadComplianceSnapshotHtml,
  addTrainingRecord,
  addBackupAttestation,
  type ComplianceSnapshot,
  type KeyMaterialFinding,
} from "@/lib/api";

export const Route = createFileRoute("/compliance")({
  head: () => ({ meta: [{ title: "Compliance — LocallyAI" }] }),
  component: CompliancePage,
});

function levelBadge(level: KeyMaterialFinding["level"]) {
  const styles: Record<string, string> = {
    ok: "bg-green-100 text-green-900 dark:bg-green-900/40 dark:text-green-200",
    warn: "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200",
    fail: "bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-200",
    info: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200",
  };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${styles[level] || styles.info}`}>
      {level.toUpperCase()}
    </span>
  );
}

function statusBadge(status: string) {
  const isOk = status === "ok";
  const isSkipped = status === "skipped";
  const cls = isOk
    ? "bg-green-100 text-green-900 dark:bg-green-900/40 dark:text-green-200"
    : isSkipped
    ? "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
    : "bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-200";
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${cls}`}>
      {status.toUpperCase()}
    </span>
  );
}

function CompliancePage() {
  const [snap, setSnap] = useState<ComplianceSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await getComplianceSnapshot();
      setSnap(s);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load compliance snapshot");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const downloadSnapshot = async () => {
    setDownloading(true);
    setError(null);
    try {
      const blob = await downloadComplianceSnapshotHtml();
      const ts = (snap?.generated_at ?? new Date().toISOString()).slice(0, 10);
      const dep = snap?.deployment.deployment_id ?? "locallyai";
      const filename = `compliance-snapshot-${dep}-${ts}.html`;

      // Path A — modern File System Access API. Works in WKWebView
      // (macOS 14+) and modern browsers; presents a real Save Sheet
      // and the user picks the location. Requires a secure context
      // (localhost qualifies).
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const w = window as any;
      if (typeof w.showSaveFilePicker === "function") {
        try {
          const handle = await w.showSaveFilePicker({
            suggestedName: filename,
            types: [{ description: "HTML report", accept: { "text/html": [".html"] } }],
          });
          const writable = await handle.createWritable();
          await writable.write(blob);
          await writable.close();
          return;
        } catch (e: unknown) {
          // User cancelled (AbortError) → silent. Other errors fall
          // through to Path B so we still try to deliver the file.
          const err = e as { name?: string };
          if (err?.name === "AbortError") return;
        }
      }

      // Path B — fallback for WKWebView versions without
      // showSaveFilePicker, and for non-WebView browsers. Uses the
      // classic blob-URL + anchor[download] pattern.
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.rel = "noopener";
      a.target = "_self";
      document.body.appendChild(a);
      a.click();
      // Give WebKit a tick before revoking the blob URL — some builds
      // race the click and the revoke if they happen in the same task.
      setTimeout(() => {
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }, 0);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Snapshot download failed");
    } finally {
      setDownloading(false);
    }
  };

  const nonOkFindings = snap?.key_material.filter((f) => f.level !== "ok") ?? [];
  const totalBreaches = snap?.breach_events_30d.reduce((acc, b) => acc + b.count, 0) ?? 0;

  return (
    <>
      <TopBar
        title="Compliance"
        description="DPO monthly snapshot — RoPA, audit chain, key material, sub-processors, retention, erasures, breach events"
      />
      <main className="flex-1 space-y-4 p-6">
        {/* Header card */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-3">
            <ShieldCheck className="h-6 w-6 text-primary" />
            <div>
              <div className="text-sm font-semibold">
                {snap ? `${snap.deployment.deployment_id} · ${snap.deployment.region || "—"}` : "Loading…"}
              </div>
              <div className="text-xs text-muted-foreground">
                {snap ? `Generated ${new Date(snap.generated_at).toLocaleString()} · firm_id ${snap.deployment.firm_id} · v${snap.deployment.version}` : ""}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={load}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs hover:bg-accent disabled:opacity-50"
            >
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCw className="h-3.5 w-3.5" />}
              Refresh
            </button>
            <button
              onClick={downloadSnapshot}
              disabled={downloading || !snap}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {downloading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
              Download monthly snapshot
            </button>
          </div>
        </div>

        {error && (
          <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-900 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-200">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none" />
            <span>{error}</span>
          </div>
        )}

        {snap && (
          <>
            {/* At-a-glance deck */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label="Audit chain" value={statusBadge(snap.audit_chain.status)} />
              <Stat label="Key-material non-OK" value={`${nonOkFindings.length} / ${snap.key_material.length}`} tone={nonOkFindings.length > 0 ? "warn" : "ok"} />
              <Stat label="Erasures (lifetime)" value={String(snap.erasure_log.total_erasures)} />
              <Stat label="Breach events (30d)" value={String(totalBreaches)} tone={totalBreaches > 0 ? "warn" : "ok"} />
            </div>

            {/* Key material */}
            <Card title="Key material posture">
              <Table headers={["Code", "Level", "Message"]}>
                {snap.key_material.length === 0 ? (
                  <tr><td colSpan={3} className="px-3 py-2 text-xs text-muted-foreground">No findings.</td></tr>
                ) : (
                  snap.key_material.map((f, i) => (
                    <tr key={i} className="border-b border-border last:border-0">
                      <td className="px-3 py-2 text-xs"><code>{f.code}</code></td>
                      <td className="px-3 py-2 text-xs">{levelBadge(f.level)}</td>
                      <td className="px-3 py-2 text-xs">{f.message}</td>
                    </tr>
                  ))
                )}
              </Table>
            </Card>

            {/* Sub-processors */}
            <Card title="Sub-processors (DPA Schedule §6.2)">
              <Table headers={["Name", "Role", "What they observe", "Client data exposure", "SOC2 reviewed"]}>
                {snap.sub_processors.map((s, i) => (
                  <tr key={i} className="border-b border-border last:border-0">
                    <td className="px-3 py-2 text-xs font-semibold">{s.name}</td>
                    <td className="px-3 py-2 text-xs">{s.role}</td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">{s.observable}</td>
                    <td className="px-3 py-2 text-xs">{s.client_data_exposure}</td>
                    <td className="px-3 py-2 text-xs">
                      <code>{s.soc2_last_reviewed || "—"}</code>
                      {s.soc2_url && (
                        <>
                          {" "}
                          <a href={s.soc2_url} target="_blank" rel="noreferrer" className="text-primary underline">
                            SOC2
                          </a>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </Table>
            </Card>

            {/* DPIA — auto-generated from RoPA + firm-completed sections */}
            {snap.dpia && (
              <Card title="DPIA (Data Protection Impact Assessment — GDPR Art. 35)">
                <div className="space-y-3 px-3 py-3 text-xs">
                  <div className="text-muted-foreground">
                    Auto-generated from RoPA. Sections marked "—" are firm-completed (controller sign-off, training/supervision narrative).
                  </div>
                  <div>
                    <div className="font-semibold mb-1">Necessity & proportionality</div>
                    <Table headers={["Aspect", "Assessment"]}>
                      {Object.entries(snap.dpia.necessity_and_proportionality).map(([k, v]) => (
                        <tr key={k} className="border-b border-border last:border-0">
                          <td className="px-3 py-2 text-xs font-semibold capitalize">{k.replace(/_/g, " ")}</td>
                          <td className="px-3 py-2 text-xs text-muted-foreground">{String(v)}</td>
                        </tr>
                      ))}
                    </Table>
                  </div>
                  <div>
                    <div className="font-semibold mb-1 mt-3">Risks to rights & freedoms</div>
                    <Table headers={["Risk", "Likelihood", "Severity", "Mitigations"]}>
                      {snap.dpia.risks_to_rights_and_freedoms.map((r, i) => (
                        <tr key={i} className="border-b border-border last:border-0 align-top">
                          <td className="px-3 py-2 text-xs font-semibold">{r.risk}</td>
                          <td className="px-3 py-2 text-xs">{r.likelihood}</td>
                          <td className="px-3 py-2 text-xs">{r.severity}</td>
                          <td className="px-3 py-2 text-xs text-muted-foreground">
                            <ul className="list-disc pl-4 space-y-0.5">
                              {r.mitigations.map((m, j) => <li key={j}>{m}</li>)}
                            </ul>
                          </td>
                        </tr>
                      ))}
                    </Table>
                  </div>
                  <div className="text-muted-foreground">
                    <span className="font-semibold">Controller sign-off (firm-completed):</span>{" "}
                    DPO {snap.dpia.controller_sign_off.dpo_name || "—"} · signed {snap.dpia.controller_sign_off.dpo_signature_date || "—"}
                  </div>
                </div>
              </Card>
            )}

            {/* Audit-log sample */}
            {snap.audit_log_sample !== undefined && (
              <Card title={`Audit-log sample — last ${snap.audit_log_sample.length} entries`}>
                <div className="px-3 py-2 text-xs text-muted-foreground border-b border-border">
                  Pseudonymised + query-hash only (no content). Auditors see the SHAPE of what's logged, not just the integrity count.
                </div>
                <Table headers={["Timestamp", "User hash", "Model", "Sources", "Latency (ms)", "Query hash", "Matter"]}>
                  {snap.audit_log_sample.length === 0 ? (
                    <tr><td colSpan={7} className="px-3 py-2 text-xs text-muted-foreground">No audit entries yet.</td></tr>
                  ) : (
                    snap.audit_log_sample.map((e, i) => (
                      <tr key={i} className="border-b border-border last:border-0">
                        <td className="px-3 py-2 text-xs"><code>{(e.timestamp || "").slice(0, 19)}</code></td>
                        <td className="px-3 py-2 text-xs"><code>{(e.user_hash || "—").slice(0, 16)}</code></td>
                        <td className="px-3 py-2 text-xs">{e.model || "—"}</td>
                        <td className="px-3 py-2 text-right text-xs">{e.sources ?? "—"}</td>
                        <td className="px-3 py-2 text-right text-xs">{e.latency_ms ?? "—"}</td>
                        <td className="px-3 py-2 text-xs"><code>{(e.query_hash || "—").slice(0, 12)}</code></td>
                        <td className="px-3 py-2 text-xs">{e.matter_code || ""}</td>
                      </tr>
                    ))
                  )}
                </Table>
              </Card>
            )}

            {/* Incident register */}
            {snap.incident_register_90d !== undefined && (
              <Card title={`Incident register — last 90 days (${snap.incident_register_90d.length} entries)`}>
                <div className="px-3 py-2 text-xs text-muted-foreground border-b border-border">
                  Source: <code>security.log</code>. Full entries (the bucketed counts above complement but don't replace this view).
                </div>
                <Table headers={["Timestamp", "Event / code", "Severity", "Message"]}>
                  {snap.incident_register_90d.length === 0 ? (
                    <tr><td colSpan={4} className="px-3 py-2 text-xs text-muted-foreground">No incidents recorded in the last 90 days.</td></tr>
                  ) : (
                    snap.incident_register_90d.map((i, idx) => (
                      <tr key={idx} className="border-b border-border last:border-0">
                        <td className="px-3 py-2 text-xs"><code>{(i.timestamp || "").slice(0, 19)}</code></td>
                        <td className="px-3 py-2 text-xs">{i.event || i.code || "—"}</td>
                        <td className="px-3 py-2 text-xs">{i.severity || i.level || "info"}</td>
                        <td className="px-3 py-2 text-xs">{(i.message || i.detail || "").slice(0, 200)}</td>
                      </tr>
                    ))
                  )}
                </Table>
              </Card>
            )}

            {/* Training records */}
            {snap.training_records !== undefined && (
              <Card title={`Training records (ISO 27001 A.6.3) — ${snap.training_records.total_records} total`}>
                <div className="px-3 py-3 text-xs space-y-2">
                  <div className="text-muted-foreground">
                    Unique users trained: <strong>{snap.training_records.users_trained}</strong> ·
                    last recorded: <code>{snap.training_records.last_recorded_at || "—"}</code>
                  </div>
                  <Table headers={["Topic", "Records"]}>
                    {Object.entries(snap.training_records.topics).length === 0 ? (
                      <tr><td colSpan={2} className="px-3 py-2 text-xs text-muted-foreground">No training records yet.</td></tr>
                    ) : (
                      Object.entries(snap.training_records.topics).map(([t, c]) => (
                        <tr key={t} className="border-b border-border last:border-0">
                          <td className="px-3 py-2 text-xs">{t}</td>
                          <td className="px-3 py-2 text-right text-xs">{c}</td>
                        </tr>
                      ))
                    )}
                  </Table>
                  <TrainingQuickAdd onAdded={load} />
                </div>
              </Card>
            )}

            {/* Backup attestations */}
            {snap.backup_attestations !== undefined && (
              <Card title={`Backup test attestations (ISO 27001 A.8.13/14) — ${snap.backup_attestations.total} total`}>
                <div className="px-3 py-3 text-xs space-y-2">
                  <div className="text-muted-foreground">
                    Last test: <code>{snap.backup_attestations.last_test_at || "—"}</code>
                  </div>
                  <Table headers={["Tested at", "Type", "Result", "Operator", "Notes"]}>
                    {snap.backup_attestations.last_5.length === 0 ? (
                      <tr><td colSpan={5} className="px-3 py-2 text-xs text-muted-foreground">No backup tests attested yet.</td></tr>
                    ) : (
                      snap.backup_attestations.last_5.map((r) => (
                        <tr key={r.id} className="border-b border-border last:border-0">
                          <td className="px-3 py-2 text-xs"><code>{(r.tested_at || "").slice(0, 19)}</code></td>
                          <td className="px-3 py-2 text-xs">{r.test_type}</td>
                          <td className="px-3 py-2 text-xs">{r.result}</td>
                          <td className="px-3 py-2 text-xs">{r.operator}</td>
                          <td className="px-3 py-2 text-xs text-muted-foreground">{(r.notes || "").slice(0, 80)}</td>
                        </tr>
                      ))
                    )}
                  </Table>
                  <BackupQuickAdd onAdded={load} />
                </div>
              </Card>
            )}

            {/* Telemetry disclosure */}
            <Card title="Telemetry disclosure">
              <div className="space-y-2 px-3 py-2 text-xs">
                <div>
                  <span className="font-semibold">Field-set version:</span> <code>{snap.telemetry_disclosure.version}</code>
                </div>
                <div>
                  <span className="font-semibold">Active allowlist:</span>{" "}
                  {snap.telemetry_disclosure.active_allowlist.length === 0 ? (
                    <span className="text-muted-foreground">all fields</span>
                  ) : (
                    <code>{snap.telemetry_disclosure.active_allowlist.join(", ")}</code>
                  )}
                </div>
                <div>
                  <span className="font-semibold">Always carries:</span>{" "}
                  <span className="text-muted-foreground">{snap.telemetry_disclosure.fields.join(", ")}</span>
                </div>
                <div>
                  <span className="font-semibold">Never carries:</span>{" "}
                  <span className="text-muted-foreground">{snap.telemetry_disclosure.never_carries.join(", ")}</span>
                </div>
              </div>
            </Card>

            {/* Retention */}
            <Card title="Retention status">
              <Table headers={["Stream", "Configured", "Oldest entry", "Size"]}>
                {Object.entries(snap.retention_status).map(([name, info]) => (
                  <tr key={name} className="border-b border-border last:border-0">
                    <td className="px-3 py-2 text-xs font-semibold">{name}</td>
                    <td className="px-3 py-2 text-xs">{info.configured_days}d</td>
                    <td className="px-3 py-2 text-xs"><code>{info.oldest_entry_at || "—"}</code></td>
                    <td className="px-3 py-2 text-xs">{info.size_bytes != null ? `${(info.size_bytes / 1024).toFixed(1)} KB` : "—"}</td>
                  </tr>
                ))}
              </Table>
            </Card>

            {/* Erasure log */}
            <Card title={`Erasure log — last 5 of ${snap.erasure_log.total_erasures}`}>
              <Table headers={["Timestamp", "Pseudonym", "Salt era"]}>
                {snap.erasure_log.last_5.length === 0 ? (
                  <tr><td colSpan={3} className="px-3 py-2 text-xs text-muted-foreground">No erasures recorded.</td></tr>
                ) : (
                  snap.erasure_log.last_5.map((e, i) => (
                    <tr key={i} className="border-b border-border last:border-0">
                      <td className="px-3 py-2 text-xs"><code>{e.timestamp || "—"}</code></td>
                      <td className="px-3 py-2 text-xs"><code>{e.pseudonym?.slice(0, 16) || "—"}</code></td>
                      <td className="px-3 py-2 text-xs"><code>{e.salt_era || "—"}</code></td>
                    </tr>
                  ))
                )}
              </Table>
            </Card>

            {/* Breach events */}
            <Card title="Breach events — last 30 days (bucketed)">
              <Table headers={["Severity:Code", "Count"]}>
                {snap.breach_events_30d.length === 0 ? (
                  <tr><td colSpan={2} className="px-3 py-2 text-xs text-muted-foreground">No breach events in the last 30 days.</td></tr>
                ) : (
                  snap.breach_events_30d.map((b, i) => (
                    <tr key={i} className="border-b border-border last:border-0">
                      <td className="px-3 py-2 text-xs"><code>{b.severity_code}</code></td>
                      <td className="px-3 py-2 text-right text-xs">{b.count}</td>
                    </tr>
                  ))
                )}
              </Table>
            </Card>

            {/* Snapshot HMAC */}
            <div className="rounded-lg border border-border bg-muted/30 p-3 font-mono text-[11px] break-all text-muted-foreground">
              <div className="mb-1 text-xs font-semibold text-foreground">Snapshot HMAC (verify with <code>scripts/verify_compliance_snapshot.py</code>):</div>
              {snap.snapshot_hmac || "(unsigned — LOCALLYAI_AUDIT_HMAC_KEY not set)"}
            </div>
          </>
        )}
      </main>
    </>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="border-b border-border px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </div>
      {children}
    </div>
  );
}

function Table({ headers, children }: { headers: string[]; children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="border-b border-border bg-muted/20">
            {headers.map((h) => (
              <th key={h} className="px-3 py-2 text-left text-xs font-semibold text-muted-foreground">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function TrainingQuickAdd({ onAdded }: { onAdded: () => void }) {
  const [user, setUser] = useState("");
  const [topic, setTopic] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!user.trim() || !topic.trim()) return;
    setBusy(true);
    try {
      await addTrainingRecord({ user: user.trim(), topic: topic.trim() });
      setUser(""); setTopic("");
      onAdded();
    } finally { setBusy(false); }
  };
  return (
    <div className="flex flex-wrap items-end gap-2 border-t border-border pt-2 mt-2">
      <div className="flex-1 min-w-32">
        <label className="block text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">User</label>
        <input value={user} onChange={(e) => setUser(e.target.value)}
          placeholder="e.g. Alice Smith"
          className="w-full rounded border border-border bg-background px-2 py-1 text-xs" />
      </div>
      <div className="flex-1 min-w-32">
        <label className="block text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">Topic</label>
        <input value={topic} onChange={(e) => setTopic(e.target.value)}
          placeholder="e.g. AI-output review process"
          className="w-full rounded border border-border bg-background px-2 py-1 text-xs" />
      </div>
      <button onClick={submit} disabled={busy || !user.trim() || !topic.trim()}
        className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
        Record training
      </button>
    </div>
  );
}

function BackupQuickAdd({ onAdded }: { onAdded: () => void }) {
  const [testType, setTestType] = useState("full restore");
  const [result, setResult] = useState("passed");
  const [operator, setOperator] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!testType.trim() || !result.trim()) return;
    setBusy(true);
    try {
      await addBackupAttestation({
        test_type: testType.trim(),
        result: result.trim(),
        operator: operator.trim(),
        notes: notes.trim(),
      });
      setOperator(""); setNotes("");
      onAdded();
    } finally { setBusy(false); }
  };
  return (
    <div className="grid grid-cols-1 sm:grid-cols-5 gap-2 border-t border-border pt-2 mt-2 items-end">
      <div>
        <label className="block text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">Test type</label>
        <select value={testType} onChange={(e) => setTestType(e.target.value)}
          className="w-full rounded border border-border bg-background px-2 py-1 text-xs">
          <option>full restore</option>
          <option>partial</option>
          <option>smoke</option>
        </select>
      </div>
      <div>
        <label className="block text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">Result</label>
        <select value={result} onChange={(e) => setResult(e.target.value)}
          className="w-full rounded border border-border bg-background px-2 py-1 text-xs">
          <option>passed</option>
          <option>failed</option>
          <option>partial</option>
        </select>
      </div>
      <div>
        <label className="block text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">Operator</label>
        <input value={operator} onChange={(e) => setOperator(e.target.value)}
          placeholder="your name"
          className="w-full rounded border border-border bg-background px-2 py-1 text-xs" />
      </div>
      <div className="sm:col-span-1">
        <label className="block text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">Notes</label>
        <input value={notes} onChange={(e) => setNotes(e.target.value)}
          placeholder="optional"
          className="w-full rounded border border-border bg-background px-2 py-1 text-xs" />
      </div>
      <button onClick={submit} disabled={busy}
        className="inline-flex items-center justify-center rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
        Record test
      </button>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "ok" | "warn" }) {
  const toneCls = tone === "warn" ? "border-amber-200 dark:border-amber-900/50" : "border-border";
  return (
    <div className={`rounded-lg border bg-card p-3 ${toneCls}`}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-base font-semibold">{value}</div>
    </div>
  );
}

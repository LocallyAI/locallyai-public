// Updates page — vendor-pushed releases of the LocallyAI server itself.
// The defence-in-depth picture (channel + GPG + manifest hashes + soak +
// kill-switch + atomic deploy with auto-rollback) is shown HERE so IT
// can see at a glance whether the chain of trust is intact before
// applying.

import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/TopBar";
import {
  RefreshCw, Shield, ShieldAlert, ShieldCheck, FileCheck, AlertTriangle,
  Loader2, GitBranch, Github, ExternalLink, PauseCircle, Clock, KeyRound,
} from "lucide-react";
import {
  listUpdates, applyUpdate,
  type UpdatesResponse, type AvailableUpdate,
} from "@/lib/api";

export const Route = createFileRoute("/updates")({
  head: () => ({ meta: [{ title: "Updates — LocallyAI" }] }),
  component: UpdatesPage,
});

function UpdatesPage() {
  const [data, setData] = useState<UpdatesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<{ tag: string; ok: boolean; detail: string; rolled_back: boolean } | null>(null);

  const load = async () => {
    try { setData(await listUpdates()); setError(null); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed to load"); }
  };
  useEffect(() => { void load(); }, []);

  const onApply = async (tag: string) => {
    if (!confirm(`Apply ${tag}? The API will restart and roll back automatically if /healthz fails. ~30–90 s.`)) return;
    setBusy(tag); setOutcome(null);
    try {
      const r = await applyUpdate(tag);
      setOutcome({ tag, ok: r.ok, detail: r.detail, rolled_back: r.rolled_back });
      await load();
    } catch (e) {
      setOutcome({ tag, ok: false, detail: e instanceof Error ? e.message : "Apply failed", rolled_back: false });
    } finally { setBusy(null); }
  };

  return (
    <>
      <TopBar
        title="System Updates"
        description="Vendor-pushed releases of the LocallyAI server itself. Every update is GPG-signed, manifest-verified, and applied atomically with auto-rollback."
      />
      <main className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-5xl space-y-6">
          {/* Channel + chain-of-trust summary */}
          {data && <ChannelStrip data={data} />}

          {/* Apply outcome banner (sticky until next load) */}
          {outcome && (
            <div className={`flex items-start gap-2 rounded-md border p-3 text-xs ${
              outcome.ok ? "border-success/30 bg-success/10 text-success"
                         : "border-destructive/30 bg-destructive/10 text-destructive"
            }`}>
              {outcome.ok ? <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
                          : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />}
              <div>
                <div className="font-semibold">
                  {outcome.ok ? "Applied" : (outcome.rolled_back ? "Rolled back" : "Failed")}: {outcome.tag}
                </div>
                <div className="mt-0.5 opacity-90">{outcome.detail}</div>
              </div>
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* Available updates */}
          <div className="rounded-lg border border-border bg-card">
            <div className="flex items-center justify-between border-b border-border p-4">
              <h2 className="text-sm font-semibold">Available updates</h2>
              <button
                onClick={() => void load()}
                className="inline-flex items-center gap-1.5 rounded-md border border-border bg-secondary px-3 py-1.5 text-xs font-medium hover:bg-accent"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Re-check GitHub
              </button>
            </div>

            {!data ? (
              <div className="px-4 py-12 text-center text-xs text-muted-foreground">
                <Loader2 className="me-2 inline h-3 w-3 animate-spin" />
                Polling release list…
              </div>
            ) : data.available.length === 0 ? (
              <div className="px-4 py-12 text-center text-xs text-muted-foreground">
                You're on the latest release for the <strong>{data.channel_status.channel}</strong> channel.
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {data.available.map((av) => (
                  <UpdateRow key={av.tag} av={av} onApply={() => onApply(av.tag)} busy={busy === av.tag} />
                ))}
              </ul>
            )}
          </div>

          {/* Defence-in-depth panel */}
          <div className="rounded-lg border border-border bg-card/40 p-4 text-xs">
            <div className="flex items-center gap-2 text-foreground">
              <Shield className="h-3.5 w-3.5 text-primary" />
              <span className="text-sm font-semibold">How updates stay safe</span>
            </div>
            <ul className="mt-3 space-y-1.5 text-muted-foreground">
              <li>1. <strong className="text-foreground">Two channels</strong> — vendor publishes to <code>dev</code> first; <code>stable</code> only sees a release after a {data?.channel_status.dev_soak_hours ?? 24}h soak window.</li>
              <li>2. <strong className="text-foreground">GPG signatures</strong> — every tag is signed with the vendor's offline key; this Mac verifies via <code>git verify-tag</code>.</li>
              <li>3. <strong className="text-foreground">SHA-256 manifest</strong> — each release ships <code>release_manifest.json</code> declaring expected file hashes; mismatches refuse to apply.</li>
              <li>4. <strong className="text-foreground">Kill switch</strong> — vendor maintains a static JSON at a separate host; this Mac polls it and refuses any blocklisted tag.</li>
              <li>5. <strong className="text-foreground">Atomic deploy</strong> — checkout, restart, and a 60 s <code>/healthz</code> probe; on failure the previous version is automatically restored.</li>
            </ul>
          </div>
        </div>
      </main>
    </>
  );
}


function ChannelStrip({ data }: { data: UpdatesResponse }) {
  const cs = data.channel_status;
  const ks = data.kill_switch;
  const trustOk = cs.gpg_available && (ks.reachable || !ks.required) && !ks.kill_switch_active;
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat icon={GitBranch} label="Channel" value={cs.channel} hint={`${cs.dev_soak_hours}h soak window`} />
        <Stat icon={FileCheck} label="Current version" value={cs.current_version} />
        <Stat
          icon={cs.gpg_available ? ShieldCheck : ShieldAlert}
          label="GPG available"
          value={cs.gpg_available ? "yes" : "no"}
          hint={cs.gpg_available ? "tag signatures verifiable" : "brew install gnupg + import key"}
          danger={!cs.gpg_available}
        />
        <Stat
          icon={ks.kill_switch_active ? PauseCircle : (ks.reachable ? ShieldCheck : ShieldAlert)}
          label="Kill switch"
          value={ks.kill_switch_active ? "ACTIVE" : (ks.reachable ? "clear" : "unreachable")}
          hint={ks.kill_switch_active ? (ks.message ?? "all updates blocked")
              : (ks.reachable ? "all clear" : (ks.required ? "required + unreachable → updates blocked" : "fail-open"))}
          danger={ks.kill_switch_active || (ks.required && !ks.reachable)}
        />
      </div>
      <div className="mt-3 flex items-center gap-2 text-[11px] text-muted-foreground">
        <Github className="h-3 w-3" />
        Source: <code className="terminal-font">{cs.github_repo}</code>
        <span className="ms-3">Auto-update tiers: <code>{cs.auto_update_tiers.join(",") || "none"}</code></span>
        {!cs.auto_update_enabled && (
          <span className="ms-2 inline-flex items-center gap-1 text-yellow-500">
            <PauseCircle className="h-3 w-3" /> auto-update OFF
          </span>
        )}
        {!trustOk && (
          <span className="ms-auto inline-flex items-center gap-1 text-destructive">
            <AlertTriangle className="h-3 w-3" /> chain of trust degraded
          </span>
        )}
      </div>
    </div>
  );
}

function Stat({ icon: Icon, label, value, hint, danger }: {
  icon: React.ComponentType<{ className?: string }>;
  label: string; value: string; hint?: string; danger?: boolean;
}) {
  return (
    <div>
      <div className="flex items-center gap-1.5 text-[10.5px] uppercase tracking-wider text-muted-foreground">
        <Icon className={`h-3 w-3 ${danger ? "text-destructive" : "text-muted-foreground"}`} />
        {label}
      </div>
      <div className={`mt-1 font-semibold ${danger ? "text-destructive" : "text-foreground"}`}>
        {value}
      </div>
      {hint && <div className="text-[10.5px] text-muted-foreground">{hint}</div>}
    </div>
  );
}

function UpdateRow({ av, onApply, busy }: {
  av: AvailableUpdate; onApply: () => void; busy: boolean;
}) {
  const checks = [
    { ok: av.gpg_verified,        label: "GPG",      detail: av.gpg_detail },
    { ok: av.manifest_verified,   label: "Manifest", detail: av.manifest_detail },
    { ok: !av.blocked_by_kill_switch, label: "Kill-switch", detail: av.blocked_reason || "clear" },
  ];
  const allChecksPass = checks.every((c) => c.ok);
  const tierColor = av.manifest.tier === "A" ? "bg-green-500/15 text-green-500"
                  : av.manifest.tier === "B" ? "bg-blue-500/15 text-blue-500"
                  : av.manifest.tier === "C" ? "bg-yellow-500/15 text-yellow-500"
                  : "bg-secondary text-foreground";
  return (
    <li className="px-4 py-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="terminal-font text-xs font-semibold">{av.tag}</span>
            <span className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${tierColor}`}>
              tier {av.manifest.tier}
            </span>
            {av.eligible_for_auto_apply && (
              <span className="inline-flex items-center gap-1 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                <Clock className="h-2.5 w-2.5" /> auto-pending
              </span>
            )}
          </div>
          {av.manifest.changelog_summary && (
            <div className="mt-1 text-xs text-muted-foreground">{av.manifest.changelog_summary}</div>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-3 text-[10.5px]">
            {checks.map((c) => (
              <span key={c.label} className={`inline-flex items-center gap-1 ${c.ok ? "text-success" : "text-destructive"}`}>
                {c.ok ? <ShieldCheck className="h-3 w-3" /> : <ShieldAlert className="h-3 w-3" />}
                {c.label}: <span className="text-muted-foreground">{c.detail}</span>
              </span>
            ))}
          </div>
        </div>
        <button
          onClick={onApply}
          disabled={busy || !allChecksPass}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />}
          {busy ? "Applying…" : "Apply"}
        </button>
      </div>
    </li>
  );
}

void ExternalLink; // imported for future use

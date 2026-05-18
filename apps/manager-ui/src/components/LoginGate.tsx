import { useEffect, useState } from "react";
import { Lock, ShieldCheck, AlertTriangle, Building2 } from "lucide-react";
import { hasAdminKey, setAdminKey } from "@/lib/auth";
import { getHealth, getMe, getBranding, ApiError, type BrandingResponse } from "@/lib/api";

interface Props {
  children: React.ReactNode;
}

type GateState = "checking" | "needs_key" | "authed";

export function LoginGate({ children }: Props) {
  const [state, setState] = useState<GateState>("checking");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const [backend, setBackend] = useState<string | null>(null);
  const [branding, setBranding] = useState<BrandingResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const h = await getHealth();
        if (!cancelled) setBackend(h.backend);
      } catch {
        // backend unreachable; surfaced when the form is submitted
      }
      try {
        const b = await getBranding();
        if (!cancelled) setBranding(b);
      } catch { /* legacy / unreachable — gate renders without firm name */ }
      if (!hasAdminKey()) {
        if (!cancelled) setState("needs_key");
        return;
      }
      try {
        await getMe();
        if (!cancelled) setState("authed");
      } catch (e: unknown) {
        if (!cancelled) {
          setState("needs_key");
          if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
            setError("Your saved admin key is no longer valid. Please sign in again.");
          }
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const trimmed = keyInput.trim();
    if (trimmed.length < 32) {
      setError("Admin key must be at least 32 characters.");
      return;
    }
    setSubmitting(true);
    setAdminKey(trimmed);
    try {
      await getMe();
      setState("authed");
    } catch (err: unknown) {
      const message =
        err instanceof ApiError
          ? err.status === 401 || err.status === 403
            ? "Invalid admin key. Check LOCALLYAI_ADMIN_KEY in the backend .env file."
            : `Sign-in failed (${err.status}): ${err.message}`
          : "Could not reach the LocallyAI server. Check that it is running and CORS is configured.";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  if (state === "checking") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background text-muted-foreground">
        <div className="text-sm">Connecting to LocallyAI…</div>
      </div>
    );
  }

  if (state === "needs_key") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4 text-foreground">
        <form
          onSubmit={submit}
          className="w-full max-w-md rounded-2xl border border-border bg-card p-7 shadow-lg"
        >
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-md border border-primary/30 bg-primary/15 text-primary">
              <Lock className="h-5 w-5" />
            </div>
            <div className="leading-tight">
              <div className="text-[15px] font-semibold">LocallyAI</div>
              <div className="text-[12px] uppercase tracking-wider text-muted-foreground">
                Management Console
              </div>
            </div>
          </div>

          {branding && (
            <div className="mt-5 flex items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-[12.5px]">
              <Building2 className="h-3.5 w-3.5 text-primary shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="font-medium text-foreground truncate">
                  Firm: {branding.firm_name}
                </div>
                <div className="text-[11px] text-muted-foreground truncate">
                  {branding.office_host || branding.deployment_id}
                </div>
              </div>
            </div>
          )}

          <h1 className="mt-6 text-[18px] font-semibold tracking-tight">Administrator sign-in</h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            Paste your <span className="terminal-font">LOCALLYAI_ADMIN_KEY</span>. It is stored
            only in this browser and is sent over the wire only as a Bearer token.
          </p>

          <label className="mt-5 block text-[12px] font-medium text-foreground">Admin key</label>
          <input
            type="password"
            autoFocus
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            placeholder="64-character hex token"
            className="mt-1.5 h-10 w-full rounded-md border border-border bg-background px-3 text-[13px] outline-none focus:border-primary"
          />

          {error && (
            <div className="mt-3 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-[12.5px] text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={submitting || keyInput.trim().length < 32}
            className="mt-5 flex h-10 w-full items-center justify-center rounded-md bg-primary text-[13px] font-medium text-primary-foreground transition-opacity disabled:opacity-40"
          >
            {submitting ? "Verifying…" : "Sign in"}
          </button>

          <div className="mt-5 flex items-center gap-2 rounded-md border border-border bg-secondary/40 px-3 py-2 text-[11.5px] text-muted-foreground">
            <ShieldCheck className="h-3.5 w-3.5 text-primary" />
            <span>
              {backend
                ? `Backend online · running on ${backend}`
                : "Backend status unknown — check that the API server is reachable."}
            </span>
          </div>

          {branding && (
            <div className="mt-2 px-1 text-[10.5px] leading-snug text-muted-foreground">
              {branding.isolation_statement}
            </div>
          )}
        </form>
      </div>
    );
  }

  return <>{children}</>;
}

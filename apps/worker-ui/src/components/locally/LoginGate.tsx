import { useEffect, useRef, useState } from "react";
import { Lock, ShieldCheck, AlertTriangle, Clipboard, Eye, EyeOff, Building2 } from "lucide-react";
import { hasUserKey, setUserKey } from "@/lib/auth";
import { getHealth, getMe, getBranding, ApiError, type BrandingResponse } from "@/lib/api";
import { t } from "@/lib/i18n";

// A 64-hex token; what manage_users.py prints. We treat anything 32+
// hex chars as plausible (admin keys, future shorter tokens).
const HEX_KEY_RE = /^[0-9a-fA-F]{32,}$/;

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
  const [reveal, setReveal] = useState(false);
  const [autopaste, setAutopaste] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;

    // Retry getHealth with backoff so a freshly-launched API (which
    // can take a few seconds to boot) doesn't show the user "cannot
    // connect to server". We give up to ~12 s of polling, then let
    // the rest of the flow continue with backend=null (degraded
    // banner, but the user can still try to sign in).
    const probeHealth = async () => {
      const delays = [0, 500, 1000, 1500, 2000, 3000, 4000];  // ~12s budget
      for (const d of delays) {
        if (cancelled) return null;
        if (d > 0) await new Promise((r) => setTimeout(r, d));
        try {
          const h = await getHealth();
          return h;
        } catch {
          // try again until budget exhausted
        }
      }
      return null;
    };

    (async () => {
      const h = await probeHealth();
      if (!cancelled && h) setBackend(h.backend);
      // Branding is best-effort — degrade silently if the endpoint
      // isn't reachable (older deployments without /v1/branding).
      try {
        const b = await getBranding();
        if (!cancelled) setBranding(b);
      } catch { /* legacy / unreachable — render without firm name */ }

      if (!hasUserKey()) {
        if (!cancelled) setState("needs_key");
        return;
      }
      try {
        await getMe();
        if (!cancelled) setState("authed");
      } catch (e: unknown) {
        if (!cancelled) {
          setState("needs_key");
          if (e instanceof ApiError && e.status === 401) {
            setError("Your saved API key is no longer valid. Please sign in again.");
          }
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Clipboard sniff — if the user landed on the gate having just copied
  // their key from the password vault, offer to paste it inline. We
  // never auto-fill (clipboard reads should be deliberate), only offer.
  // Requires a user gesture in modern browsers; we attach to the form's
  // first focus so it triggers naturally.
  const tryClipboard = async () => {
    try {
      if (!navigator.clipboard?.readText) return;
      const text = (await navigator.clipboard.readText()).trim();
      if (HEX_KEY_RE.test(text) && text.length >= 32 && text !== keyInput) {
        setAutopaste(text);
      }
    } catch {
      // Permission denied / not in secure context — no-op.
    }
  };
  useEffect(() => {
    if (state !== "needs_key") return;
    // Defer to first user interaction with the form area to satisfy
    // browser permission rules.
    const onFirstInteract = () => {
      void tryClipboard();
      window.removeEventListener("focus", onFirstInteract);
      window.removeEventListener("click", onFirstInteract);
    };
    window.addEventListener("focus", onFirstInteract, { once: true });
    window.addEventListener("click", onFirstInteract, { once: true });
    return () => {
      window.removeEventListener("focus", onFirstInteract);
      window.removeEventListener("click", onFirstInteract);
    };
  }, [state]);

  const acceptAutopaste = () => {
    if (!autopaste) return;
    setKeyInput(autopaste);
    setAutopaste(null);
    inputRef.current?.focus();
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const trimmed = keyInput.trim();
    if (trimmed.length < 32) {
      setError("API key must be at least 32 characters.");
      return;
    }
    setSubmitting(true);
    setUserKey(trimmed);

    // Retry on transport errors only — never re-spam getMe on a 401
    // (the user must fix the key) or on a definitive 403/422. A fresh
    // launchd-bootstrapped API can take a couple of seconds to be
    // reachable; retry there gives a smooth experience.
    const delays = [0, 600, 1500, 3000];
    let lastErr: unknown = null;
    for (const d of delays) {
      if (d > 0) await new Promise((r) => setTimeout(r, d));
      try {
        await getMe();
        setState("authed");
        setSubmitting(false);
        return;
      } catch (err: unknown) {
        lastErr = err;
        const isTerminal = err instanceof ApiError &&
          (err.status === 401 || err.status === 403 || err.status === 422);
        if (isTerminal) break;
        // ApiError with status 0 / TypeError / others → keep retrying.
      }
    }

    const message =
      lastErr instanceof ApiError
        ? lastErr.status === 401
          ? "Invalid API key. Ask your administrator for a fresh key."
          : `Sign-in failed (${lastErr.status}): ${lastErr.message}`
        : "Could not reach the LocallyAI server. Check that it is running.";
    setError(message);
    setSubmitting(false);
  };

  if (state === "checking") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background text-muted-foreground">
        <div className="flex items-center gap-2 text-sm animate-in fade-in duration-300">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/60 opacity-60" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
          </span>
          Connecting to LocallyAI…
        </div>
      </div>
    );
  }

  if (state === "needs_key") {
    const previewKey = keyInput.length >= 8
      ? `${keyInput.slice(0, 4)}…${keyInput.slice(-4)}`
      : null;
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4 text-foreground">
        <form
          onSubmit={submit}
          className="w-full max-w-md rounded-2xl border border-border bg-surface p-7 shadow-lg animate-in fade-in zoom-in-95 duration-200"
        >
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-md bg-primary/15 text-primary">
              <Lock className="h-5 w-5" />
            </div>
            <div className="leading-tight">
              <div className="text-[15px] font-semibold">{t("app.name")}</div>
              <div className="text-[12px] text-muted-foreground">{t("app.workspace")}</div>
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

          <h1 className="mt-6 text-[18px] font-semibold tracking-tight">{t("login.title")}</h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            {t("login.help", "Paste the API key your administrator gave you. It is stored only in this browser.")}
          </p>

          <label className="mt-5 block text-[12px] font-medium text-foreground">{t("login.api_key_label")}</label>
          <div className="relative mt-1.5">
            <input
              ref={inputRef}
              type={reveal ? "text" : "password"}
              autoFocus
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              placeholder={t("login.api_key_placeholder")}
              className="h-10 w-full rounded-md border border-border bg-background px-3 pe-9 text-[13px] outline-none transition-colors focus:border-primary"
              spellCheck={false}
              autoComplete="off"
            />
            <button
              type="button"
              onClick={() => setReveal((r) => !r)}
              className="absolute end-1.5 top-1/2 -translate-y-1/2 rounded p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              aria-label={reveal ? "Hide key" : "Reveal key"}
            >
              {reveal ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
            </button>
          </div>
          {previewKey && !reveal && (
            <div className="mt-1.5 text-[11px] text-muted-foreground tabular-nums">
              {t("login.preview", "First/last 4: ") + previewKey}
            </div>
          )}

          {/* Clipboard offer — appears only when the clipboard contained
              a plausible key the user hasn't already entered. One click
              accepts; explicit dismissal hides it. */}
          {autopaste && autopaste !== keyInput && (
            <div className="mt-3 flex items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-[12px] text-foreground animate-in fade-in slide-in-from-top-1 duration-200">
              <Clipboard className="h-3.5 w-3.5 text-primary shrink-0" />
              <span className="flex-1">
                {t("login.clipboard_offer", "Paste key from clipboard")}
                <span className="ms-1 tabular-nums text-muted-foreground">
                  ({autopaste.slice(0, 4)}…{autopaste.slice(-4)})
                </span>
              </span>
              <button
                type="button"
                onClick={acceptAutopaste}
                className="rounded bg-primary px-2 py-0.5 text-[11px] font-medium text-primary-foreground transition-opacity hover:opacity-90"
              >
                {t("login.paste", "Paste")}
              </button>
              <button
                type="button"
                onClick={() => setAutopaste(null)}
                className="rounded px-1 text-[11px] text-muted-foreground hover:text-foreground"
                aria-label="Dismiss"
              >
                ×
              </button>
            </div>
          )}

          {error && (
            <div className="mt-3 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-[12.5px] text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={submitting || keyInput.trim().length < 32}
            className="mt-5 flex h-10 w-full items-center justify-center gap-2 rounded-md bg-foreground text-[13px] font-medium text-background transition-all disabled:opacity-40 hover:opacity-90 active:scale-[0.98]"
          >
            {submitting && (
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-background/30 border-t-background" />
            )}
            {submitting ? t("login.verifying", "Verifying…") : t("login.button")}
          </button>

          <div className="mt-5 flex items-center gap-2 rounded-md border border-border bg-surface/40 px-3 py-2 text-[11.5px] text-muted-foreground">
            <ShieldCheck className="h-3.5 w-3.5 text-primary" />
            <span>
              {backend
                ? `Backend online · running on ${backend}`
                : "Backend status unknown — check that the server is reachable."}
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

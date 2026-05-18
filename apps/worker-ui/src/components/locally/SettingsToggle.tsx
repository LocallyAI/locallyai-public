// SettingsToggle — language (EN/AR) + theme (Light/Dark) switches.
// Drops into the sidebar footer / top bar — wherever a small settings
// affordance fits. Closes on outside click.
//
// Language change triggers a window reload so every t() call returns
// the new strings (the i18n primitive is module-scoped; components
// already rendered won't auto-update otherwise). Theme change is a
// CSS-only class flip on <html> — no reload needed.

import { useEffect, useRef, useState } from "react";
import { Settings, Sun, Moon, Languages, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { t, lang, setLang } from "@/lib/i18n";
import { theme, setTheme, subscribeTheme } from "@/lib/theme";

export function SettingsToggle({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);
  const [currentTheme, setCurrentTheme] = useState(theme());
  const [currentLang] = useState(lang());
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    return subscribeTheme((t) => setCurrentTheme(t));
  }, []);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pickLang = (next: "en" | "ar") => {
    if (next === currentLang) { setOpen(false); return; }
    setLang(next);
    // Force a full re-render of the app for all t() consumers.
    window.location.reload();
  };
  const pickTheme = (next: "light" | "dark") => {
    setTheme(next);
    // No reload — class flip on <html> updates CSS vars instantly.
  };

  return (
    <div ref={wrapRef} className={cn("relative", className)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
        aria-label={t("settings.title", "Settings")}
        title={t("settings.title", "Settings")}
        aria-expanded={open}
      >
        <Settings className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute end-0 bottom-full mb-2 w-56 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-lg z-50"
        >
          <div className="px-2 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
            <Languages className="h-3 w-3" /> {t("settings.language", "Language")}
          </div>
          <button
            type="button"
            onClick={() => pickLang("en")}
            className="flex w-full items-center justify-between rounded px-2 py-1.5 text-[12.5px] hover:bg-accent"
          >
            <span>{t("settings.english", "English")}</span>
            {currentLang === "en" && <Check className="h-3 w-3 text-primary" />}
          </button>
          <button
            type="button"
            onClick={() => pickLang("ar")}
            className="flex w-full items-center justify-between rounded px-2 py-1.5 text-[12.5px] hover:bg-accent"
          >
            <span>{t("settings.arabic", "العربية")}</span>
            {currentLang === "ar" && <Check className="h-3 w-3 text-primary" />}
          </button>

          <div className="my-1 h-px bg-border" />

          <div className="px-2 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
            <Sun className="h-3 w-3" /> {t("settings.theme", "Theme")}
          </div>
          <button
            type="button"
            onClick={() => pickTheme("light")}
            className="flex w-full items-center justify-between rounded px-2 py-1.5 text-[12.5px] hover:bg-accent"
          >
            <span className="flex items-center gap-2"><Sun className="h-3 w-3" /> {t("settings.light", "Light")}</span>
            {currentTheme === "light" && <Check className="h-3 w-3 text-primary" />}
          </button>
          <button
            type="button"
            onClick={() => pickTheme("dark")}
            className="flex w-full items-center justify-between rounded px-2 py-1.5 text-[12.5px] hover:bg-accent"
          >
            <span className="flex items-center gap-2"><Moon className="h-3 w-3" /> {t("settings.dark", "Dark")}</span>
            {currentTheme === "dark" && <Check className="h-3 w-3 text-primary" />}
          </button>
        </div>
      )}
    </div>
  );
}

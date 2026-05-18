// Tiny i18n primitive — no dependency, no framework. Reads the build-time
// VITE_DEFAULT_LANG (set by the install script per region: "ar" for KSA,
// "en" for UK), allows runtime override via localStorage["locallyai_lang"],
// and exposes t(key, fallback?), lang(), dir(). Translations live in
// src/i18n/en.json + src/i18n/ar.json and are bundled at build time.
//
// String-extraction policy:
//   - Deterministic UI surface (buttons, labels, placeholders, error
//     messages, empty states) is in the JSON files.
//   - LLM-generated content (chat responses, retrieved chunks) is NOT —
//     the model produces it directly in the user's language under the
//     KSA-mode "mirror the user's language" rule in api.py.
//   - Arabic strings shipped here are starter values; the deploying
//     firm's lawyer revises them. The SOP says so explicitly.

import en from "../i18n/en.json";
import ar from "../i18n/ar.json";

type Lang = "en" | "ar";
type Dict = Record<string, string>;

const DICTS: Record<Lang, Dict> = {
  en: en as Dict,
  ar: ar as Dict,
};

const STORAGE_KEY = "locallyai_lang";

function detectLang(): Lang {
  if (typeof window !== "undefined") {
    const stored = window.localStorage?.getItem(STORAGE_KEY);
    if (stored === "ar" || stored === "en") return stored;
  }
  // Build-time default. Vite replaces import.meta.env.* at build.
  const env = (import.meta as unknown as { env: { VITE_DEFAULT_LANG?: string } }).env;
  const def = env?.VITE_DEFAULT_LANG;
  if (def === "ar") return "ar";
  return "en";
}

let _lang: Lang = detectLang();

export function lang(): Lang {
  return _lang;
}

export function dir(): "ltr" | "rtl" {
  return _lang === "ar" ? "rtl" : "ltr";
}

export function setLang(next: Lang): void {
  _lang = next;
  if (typeof window !== "undefined") {
    window.localStorage?.setItem(STORAGE_KEY, next);
    // Reflect on <html> immediately so CSS picks up the change without
    // a full reload. Components that have already rendered won't update
    // their text until they re-render — callers usually trigger a
    // page-level refresh on language change.
    const html = document.documentElement;
    html.setAttribute("lang", next);
    html.setAttribute("dir", next === "ar" ? "rtl" : "ltr");
  }
}

export function t(key: string, fallback?: string): string {
  const dict = DICTS[_lang];
  if (dict && key in dict) return dict[key];
  if (fallback !== undefined) return fallback;
  // Fallback chain: requested lang → English → key itself. Never throw —
  // the UI keeps rendering even if a key is missing.
  if (_lang !== "en" && key in DICTS.en) return DICTS.en[key];
  return key;
}

// Apply lang/dir to <html> on first import so SSR-hydrated UIs match
// what the user expects before React mounts.
if (typeof document !== "undefined") {
  const html = document.documentElement;
  html.setAttribute("lang", _lang);
  html.setAttribute("dir", _lang === "ar" ? "rtl" : "ltr");
}

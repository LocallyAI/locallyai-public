// Tiny theme primitive — no dependency. Reads/writes
// localStorage["locallyai_theme"], applies/removes the `.dark` class on
// <html>, and notifies subscribers so components re-render on toggle.
//
// Default is "dark" to preserve the look the existing UI has shipped
// with — light mode is an explicit opt-in via the settings toggle.
//
// First-paint application is done by an inline script in __root.tsx
// (renders BEFORE React mounts), avoiding a flash of the wrong theme.
// This module's IIFE at the bottom is a defensive duplicate for the
// case where __root.tsx was bypassed (e.g., a standalone storybook
// page during development).

type Theme = "light" | "dark";
type Listener = (t: Theme) => void;

const STORAGE_KEY = "locallyai_theme";
const listeners = new Set<Listener>();

function detect(): Theme {
  if (typeof window === "undefined") return "dark";
  const stored = window.localStorage?.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return "dark";
}

let _theme: Theme = detect();

function apply(t: Theme): void {
  if (typeof document === "undefined") return;
  const html = document.documentElement;
  const body = document.body;
  if (t === "dark") {
    html.classList.add("dark");
    body?.classList.add("dark");
  } else {
    html.classList.remove("dark");
    body?.classList.remove("dark");
  }
}

export function theme(): Theme {
  return _theme;
}

export function setTheme(next: Theme): void {
  _theme = next;
  if (typeof window !== "undefined") {
    window.localStorage?.setItem(STORAGE_KEY, next);
  }
  apply(next);
  listeners.forEach((fn) => fn(next));
}

export function toggleTheme(): Theme {
  const next: Theme = _theme === "dark" ? "light" : "dark";
  setTheme(next);
  return next;
}

export function subscribeTheme(fn: Listener): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

// Apply on import in case the inline first-paint script didn't run.
if (typeof document !== "undefined") apply(_theme);

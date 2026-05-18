// Region/locale-aware formatters used throughout the worker UI.
//   formatDate   — Hijri (Umm al-Qura) for ar, Gregorian for en.
//   formatTime   — same locale; 24h on ar, system-default on en.
//   formatCurrency — SAR for ar, GBP for en. Number-formatted with the
//                    locale's grouping/decimal separators.
//
// All thread through `lang()` from i18n so they update when the user
// flips language at runtime. Passing an explicit lang= overrides.

import { lang as _lang } from "./i18n";

export function formatDate(d: Date, opts?: { lang?: "en" | "ar"; timeZone?: string }): string {
  const l = opts?.lang ?? _lang();
  const fmt = new Intl.DateTimeFormat(
    l === "ar" ? "ar-SA-u-ca-islamic-umalqura" : "en-GB",
    {
      year: "numeric",
      month: "long",
      day: "numeric",
      // Saudi admins viewing from elsewhere see Saudi-local times when in
      // ar mode; en mode falls back to browser locale (which for UK firm
      // staff is correct).
      timeZone: opts?.timeZone ?? (l === "ar" ? "Asia/Riyadh" : undefined),
    },
  );
  return fmt.format(d);
}

export function formatTime(d: Date, opts?: { lang?: "en" | "ar"; timeZone?: string }): string {
  const l = opts?.lang ?? _lang();
  const fmt = new Intl.DateTimeFormat(
    l === "ar" ? "ar-SA" : "en-GB",
    {
      hour: "2-digit",
      minute: "2-digit",
      hour12: l !== "ar",  // Saudi admins read 24h conventionally
      timeZone: opts?.timeZone ?? (l === "ar" ? "Asia/Riyadh" : undefined),
    },
  );
  return fmt.format(d);
}

export function formatDateTime(d: Date, opts?: { lang?: "en" | "ar"; timeZone?: string }): string {
  return `${formatDate(d, opts)} ${formatTime(d, opts)}`;
}

export function formatCurrency(amount: number, opts?: { lang?: "en" | "ar" }): string {
  const l = opts?.lang ?? _lang();
  const fmt = new Intl.NumberFormat(
    l === "ar" ? "ar-SA" : "en-GB",
    {
      style: "currency",
      currency: l === "ar" ? "SAR" : "GBP",
      maximumFractionDigits: 2,
    },
  );
  return fmt.format(amount);
}

// Time-ago using language-appropriate units. Falls back to a fixed-format
// timestamp for spans > 24h (calling code can decide further).
export function formatTimeAgo(d: Date, opts?: { lang?: "en" | "ar" }): string {
  const l = opts?.lang ?? _lang();
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 60)  return l === "ar" ? `قبل ${sec} ثانية`            : `${sec}s ago`;
  if (sec < 3600)  return l === "ar" ? `قبل ${Math.floor(sec/60)} دقيقة` : `${Math.floor(sec/60)} min ago`;
  if (sec < 86400) return l === "ar" ? `قبل ${Math.floor(sec/3600)} ساعة` : `${Math.floor(sec/3600)} hr ago`;
  return formatDateTime(d, { lang: l });
}

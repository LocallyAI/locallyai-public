// Browser-side persistence of the admin API key for the management console.
// Stored in localStorage so refresh and tab reopens don't kick the operator
// back to the login screen. The key is sent only via the Authorization
// header to the LocallyAI backend.

const KEY_NAME = "locallyai_admin_key";

export function getAdminKey(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(KEY_NAME);
}

export function setAdminKey(key: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(KEY_NAME, key);
}

export function clearAdminKey(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(KEY_NAME);
}

export function hasAdminKey(): boolean {
  return Boolean(getAdminKey());
}

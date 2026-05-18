// Browser-side persistence of the user's API key. The key is stored in
// localStorage so refreshes and tab reopens don't kick the user back to the
// login screen. The Authorization header is the only place the key ever
// leaves the device.

const KEY_NAME = "locallyai_user_key";

export function getUserKey(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(KEY_NAME);
}

export function setUserKey(key: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(KEY_NAME, key);
}

export function clearUserKey(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(KEY_NAME);
}

export function hasUserKey(): boolean {
  return Boolean(getUserKey());
}

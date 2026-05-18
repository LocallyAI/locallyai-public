// LocallyAI fleet-monitoring Worker
// ---------------------------------
// Receives anonymised heartbeats from each firm's office Mac,
// surfaces them on a TOTP-gated dashboard, and dispatches alerts
// to the vendor on-call within the 4-hour SLA window.
//
// Endpoints:
//   POST /heartbeat          firm token-auth → store snapshot, dispatch any new alerts
//   GET  /api/firms          dashboard JSON (admin TOTP)
//   GET  /api/firm/:id       per-firm drill-down (admin TOTP)
//   POST /api/ack            acknowledge an alert (admin TOTP)
//   GET  /                   dashboard HTML (served from /dashboard via [assets])
//
// Cron (every 15 min):
//   * walk unacknowledged critical alerts; re-notify if >SLA_WARN_HOURS old.
//
// Auth:
//   Firms     → static per-firm token (FIRM_TOKENS env). Token is bound to
//              firm_id at issue time so a stolen token can only impersonate
//              that one firm.
//   Vendor    → TOTP from authenticator app (same scheme as kill-switch).

interface Env {
  FIRM_STATE:               KVNamespace;
  ALERTS:                   KVNamespace;
  INTAKE_TOKENS:            KVNamespace;        // one-time-use install tokens (form → bootstrap)
  TELEMETRY_TOKENS:         KVNamespace;        // auto-issued at form-submit time; firm_id → {token, firm_name, registered_at}
  RATE_LIMITS:              KVNamespace;        // per-IP rate-limit + TOTP-replay defence + recovery code pool (round-2 A3/B1/B2/B3)
  ASSETS:                   Fetcher;
  FIRM_TOKENS:              string;            // legacy JSON map: {"<firm_id>": "<token>"} — vendor-issued via wrangler secret put. Kept for backwards-compat with firms registered via scripts/onboard_firm.sh.
  ADMIN_TOTP_SECRET_BASE32: string;
  ADMIN_RECOVERY_HASHED:    string;
  RESEND_API_KEY?:          string;
  ALERT_TO_EMAIL?:          string;
  // Override the Resend "from" address. Resend requires the sending
  // domain to be verified — until you've verified locallyai.app (or
  // your chosen sender domain), leave this unset and the Worker
  // defaults to onboarding@resend.dev, which works without verification
  // (Resend's free test sender, slight "via resend.dev" indicator in
  // some clients).
  RESEND_FROM?:             string;
  SLACK_WEBHOOK_URL?:       string;             // legacy/incoming webhook — inline messages only (no file upload)
  SLACK_BOT_TOKEN?:         string;             // Bot User OAuth Token; required for real .md attachment via files.upload
  SLACK_CHANNEL_ID?:        string;             // e.g. "C0123456789"; required alongside SLACK_BOT_TOKEN
  // GitHub PAT (or fine-grained token) with admin:repo on the
  // vendor-records repo. Used by /onboarding/deploy-key to add per-firm
  // SSH deploy keys without the operator having to paste them into the
  // GitHub UI. Set via `wrangler secret put GITHUB_DEPLOY_KEYS_PAT`.
  GITHUB_DEPLOY_KEYS_PAT?:  string;
  // Owner/repo where deploy keys land — usually the firm's per-firm
  // vendor-records repo. Defaults to LocallyAI/vendor-records-template
  // if unset; production sets per-firm repo names.
  GITHUB_DEPLOY_KEY_REPO?:  string;
  SLA_WARN_HOURS:           string;
  SLA_CRIT_HOURS:           string;
}

interface RateLimitRecord { first_seen: number; count: number; }

// ── Crypto helpers (TOTP per RFC 6238 — same as kill-switch worker) ──────────
function base32Decode(input: string): Uint8Array {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const cleaned = input.replace(/[\s=]/g, "").toUpperCase();
  let bits = "";
  for (const c of cleaned) {
    const v = alphabet.indexOf(c);
    if (v < 0) throw new Error(`Invalid base32 char: ${c}`);
    bits += v.toString(2).padStart(5, "0");
  }
  const bytes = new Uint8Array(Math.floor(bits.length / 8));
  for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(bits.slice(i * 8, i * 8 + 8), 2);
  return bytes;
}

async function totp(secret: Uint8Array, t: number): Promise<string> {
  const buf = new ArrayBuffer(8);
  new DataView(buf).setUint32(4, t, false);
  const key = await crypto.subtle.importKey("raw", secret as BufferSource,
                                             { name: "HMAC", hash: "SHA-1" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, buf));
  const offset = sig[sig.length - 1] & 0x0f;
  const code =
    ((sig[offset] & 0x7f) << 24) |
    ((sig[offset + 1] & 0xff) << 16) |
    ((sig[offset + 2] & 0xff) << 8) |
    (sig[offset + 3] & 0xff);
  return (code % 1_000_000).toString().padStart(6, "0");
}

// Round-2 B2: TOTP replay defence. The 90s acceptance window means a
// code captured on-path (screen recording, decrypting middlebox the
// firm forgot they installed) replays within window. Each successful
// match records hashed(code) in RATE_LIMITS with 120s TTL; a second
// presentation of the same code is rejected.
async function verifyTotp(secretBase32: string, supplied: string, env: Env): Promise<boolean> {
  if (!/^\d{6}$/.test(supplied)) return false;
  const replayKey = `totp:${await sha256Hex(`totp:${supplied}`)}`;
  const seen = await env.RATE_LIMITS.get(replayKey);
  if (seen) return false;
  if (!secretBase32) return false;
  const secret = base32Decode(secretBase32);
  const t = Math.floor(Date.now() / 30_000);
  let matched = false;
  // Don't break early — iterate the full window so timing doesn't leak which offset matched.
  for (const offset of [-1, 0, 1] as const) {
    if (timingSafeEqual(await totp(secret, t + offset), supplied)) matched = true;
  }
  if (matched) {
    await env.RATE_LIMITS.put(replayKey, "1", { expirationTtl: 120 });
    return true;
  }
  return false;
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

async function sha256Hex(s: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Round-2 B1 + B11: recovery codes are now single-use AND iterated to
// completion (no .some short-circuit timing leak). State lives in
// RATE_LIMITS KV — env var ADMIN_RECOVERY_HASHED is the bootstrap seed
// that's copied in on first call. On a successful match, the matched
// hash is removed from the array and the rest persisted back.
async function verifyRecovery(supplied: string, env: Env): Promise<boolean> {
  let hashes: string[] = [];
  const kvRaw = await env.RATE_LIMITS.get("recovery:hashed");
  if (kvRaw) {
    try { hashes = JSON.parse(kvRaw); } catch { hashes = []; }
  } else if (env.ADMIN_RECOVERY_HASHED) {
    try { hashes = JSON.parse(env.ADMIN_RECOVERY_HASHED); } catch { return false; }
    if (hashes.length > 0) {
      await env.RATE_LIMITS.put("recovery:hashed", JSON.stringify(hashes));
    }
  }
  if (hashes.length === 0) return false;
  const got = await sha256Hex(supplied.trim().toLowerCase());
  // Iterate every entry — no break — so we don't leak which index matched.
  let matchIdx = -1;
  for (let i = 0; i < hashes.length; i++) {
    if (timingSafeEqual(hashes[i], got)) matchIdx = i;
  }
  if (matchIdx < 0) return false;
  hashes.splice(matchIdx, 1);
  await env.RATE_LIMITS.put("recovery:hashed", JSON.stringify(hashes));
  // Tiny audit trail of consumption so the vendor sees that a recovery
  // code was burnt and can investigate.
  try {
    await env.RATE_LIMITS.put(
      `recovery:used:${Date.now()}`,
      JSON.stringify({ used_at: new Date().toISOString(), remaining: hashes.length }),
      { expirationTtl: 90 * 86400 },
    );
  } catch { /* best-effort audit */ }
  return true;
}

// Round-2 B3: brute-force defence. Counts only FAILED credential
// attempts (or refused requests when no credential is supplied) — once
// a session is established, polling rides on the session token and
// doesn't count.
const ADMIN_AUTH_RATE_LIMIT_MAX = 10;
const ADMIN_AUTH_RATE_LIMIT_WINDOW_SEC = 3600;

async function checkAdminRateLimit(ip: string, env: Env): Promise<boolean> {
  const key = `ratelimit:admin:${ip}`;
  const now = Math.floor(Date.now() / 1000);
  const raw = await env.RATE_LIMITS.get(key);
  let rec: RateLimitRecord;
  if (raw) {
    try { rec = JSON.parse(raw); } catch { rec = { first_seen: now, count: 0 }; }
  } else {
    rec = { first_seen: now, count: 0 };
  }
  if (now - rec.first_seen >= ADMIN_AUTH_RATE_LIMIT_WINDOW_SEC) {
    rec = { first_seen: now, count: 0 };
  }
  return rec.count < ADMIN_AUTH_RATE_LIMIT_MAX;
}

async function recordFailedAuth(ip: string, env: Env): Promise<void> {
  const key = `ratelimit:admin:${ip}`;
  const now = Math.floor(Date.now() / 1000);
  const raw = await env.RATE_LIMITS.get(key);
  let rec: RateLimitRecord;
  if (raw) {
    try { rec = JSON.parse(raw); } catch { rec = { first_seen: now, count: 0 }; }
  } else {
    rec = { first_seen: now, count: 0 };
  }
  if (now - rec.first_seen >= ADMIN_AUTH_RATE_LIMIT_WINDOW_SEC) {
    rec = { first_seen: now, count: 0 };
  }
  rec.count += 1;
  await env.RATE_LIMITS.put(key, JSON.stringify(rec), {
    expirationTtl: ADMIN_AUTH_RATE_LIMIT_WINDOW_SEC + 60,
  });
}

// Session tokens — IP-bound, HMAC-signed, 1h TTL. Issued after a
// successful TOTP/recovery login; the dashboard sends it on subsequent
// polls so the credential is only presented once per session. This is
// the right primitive: the credential establishes identity, the session
// token sustains it. Without this, dashboard polling repeatedly burns
// rate-limit budget AND triggers TOTP replay defence on every refresh.
const ADMIN_SESSION_TTL_SEC = 3600;

async function _sessionKey(env: Env): Promise<CryptoKey> {
  // Derive HMAC signing key from ADMIN_TOTP_SECRET_BASE32 (avoids
  // requiring operators to provision yet another secret).
  const seed = base32Decode(env.ADMIN_TOTP_SECRET_BASE32);
  return crypto.subtle.importKey(
    "raw", seed,
    { name: "HMAC", hash: "SHA-256" },
    false, ["sign", "verify"],
  );
}

async function mintSession(env: Env, ip: string): Promise<string> {
  const expires = Math.floor(Date.now() / 1000) + ADMIN_SESSION_TTL_SEC;
  const ipHash = (await sha256Hex(`session-ip:${ip}`)).slice(0, 16);
  const payload = `${expires}.${ipHash}`;
  const key = await _sessionKey(env);
  const sigBuf = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  const sigHex = [...new Uint8Array(sigBuf)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${payload}.${sigHex}`;
}

async function verifySession(token: string, env: Env, ip: string): Promise<boolean> {
  const parts = token.split(".");
  if (parts.length !== 3) return false;
  const [expStr, ipHashClaim, sigHex] = parts;
  const expires = parseInt(expStr, 10);
  if (!expires || expires < Math.floor(Date.now() / 1000)) return false;
  const expectedIpHash = (await sha256Hex(`session-ip:${ip}`)).slice(0, 16);
  if (!timingSafeEqual(ipHashClaim, expectedIpHash)) return false;
  const payload = `${expStr}.${ipHashClaim}`;
  const key = await _sessionKey(env);
  const sigBytes = new Uint8Array((sigHex.match(/../g) || []).map((h) => parseInt(h, 16)));
  return crypto.subtle.verify("HMAC", key, sigBytes, new TextEncoder().encode(payload));
}

interface AdminAuthResult { ok: boolean; sessionToken?: string }

async function adminAuth(request: Request, env: Env): Promise<AdminAuthResult> {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";

  // Session-token path: no rate-limit cost, no TOTP/recovery handling.
  // The session token is HMAC-signed with the TOTP secret and bound to
  // the source IP.
  const sessionToken = request.headers.get("X-Admin-Session") || "";
  if (sessionToken && await verifySession(sessionToken, env, ip)) {
    return { ok: true };
  }

  // Credential path. Rate-limited; failures count toward the cap.
  const auth = request.headers.get("X-Admin-TOTP") || "";
  if (!auth) return { ok: false };
  if (!await checkAdminRateLimit(ip, env)) return { ok: false };
  const totpOk = await verifyTotp(env.ADMIN_TOTP_SECRET_BASE32, auth, env);
  const credOk = totpOk || await verifyRecovery(auth, env);
  if (!credOk) {
    await recordFailedAuth(ip, env);
    return { ok: false };
  }
  // Mint a session token so subsequent polls bypass replay/rate-limit.
  return { ok: true, sessionToken: await mintSession(env, ip) };
}

// Helper: attach the freshly minted session token (if any) to the
// outgoing response so the dashboard can cache it. Caller passes the
// auth result so the token only flows out on the request that issued it.
function withSession(resp: Response, auth: AdminAuthResult): Response {
  if (!auth.sessionToken) return resp;
  const headers = new Headers(resp.headers);
  headers.set("X-Admin-Session", auth.sessionToken);
  return new Response(resp.body, { status: resp.status, statusText: resp.statusText, headers });
}

// ── Firm-token auth ─────────────────────────────────────────────────────────
async function firmAuth(request: Request, env: Env, firmId: string): Promise<boolean> {
  const bearer = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  if (!bearer) return false;
  // Primary: TELEMETRY_TOKENS KV (auto-issued at form-submit time, scaled
  // path). Lookup is firm_id → {token, firm_name, registered_at}.
  try {
    const raw = await env.TELEMETRY_TOKENS.get(`firm:${firmId}`);
    if (raw) {
      const rec = JSON.parse(raw) as { token?: string };
      if (rec.token && timingSafeEqual(rec.token, bearer)) return true;
    }
  } catch { /* fall through to legacy map */ }
  // Legacy: FIRM_TOKENS env JSON (vendor-issued via scripts/onboard_firm.sh).
  // Kept so firms registered before the auto-issuance path keep working.
  let map: Record<string, string> = {};
  try { map = JSON.parse(env.FIRM_TOKENS || "{}"); } catch { return false; }
  const expected = map[firmId];
  return !!expected && timingSafeEqual(expected, bearer);
}

// ── Types ────────────────────────────────────────────────────────────────────
interface HealthSnapshot {
  schema_version:   1;
  firm_id:          string;
  timestamp:        string;
  node_id:          string;
  region:           string;
  backend:          string;
  channel:          string;
  version:          string;
  // Operating-environment versions — added 2026-05-12. All optional
  // for backwards compatibility with firms still on the older agent.
  // Vendor uses these to flag firms running un-tested OS or backend
  // configs (per docs/sop/maintenance.md §macos-version-policy).
  // None of these are firm-attributable beyond what's already in
  // firm_id; pure platform metadata.
  macos_version?:   string;     // marketing version, e.g. "14.4"
  macos_build?:     string;     // build number, e.g. "23E214"
  python_version?:  string;     // e.g. "3.12.13"
  backend_version?: string;     // mlx-lm / ollama / lms version
  healthz_ok:       boolean;
  sentinel_ok:      boolean;
  uptime_seconds:   number;
  free_disk_gb:     number;
  free_mem_gb:      number;
  error_count_24h:  number;
  self_heals_24h:   Record<string, number>;
  last_audit_event: string;
  pending_alerts:   AlertEntry[];
}

// macOS major versions the vendor has tested + approved for fleet use.
// Update in sync with docs/sop/maintenance.md §macos-version-policy.
// Anything outside this list shows as "Unsupported OS" on the dashboard
// and triggers a low-priority "supported_os" alert in the monitor cron.
const SUPPORTED_MACOS_MAJORS = ["13", "14", "15"];

function isMacosSupported(version: string | undefined): boolean {
  if (!version) return true;  // missing = old agent; don't false-positive
  const major = version.split(".")[0];
  return SUPPORTED_MACOS_MAJORS.includes(major);
}

interface AlertEntry {
  code:        string;
  severity:    "info" | "warning" | "critical";
  message:     string;
  auto_healed: boolean;
  timestamp:   string;
}

interface StoredFirm {
  firm_id:           string;
  last_seen:         string;
  last_snapshot:     HealthSnapshot;
  // firm_name is joined in from TELEMETRY_TOKENS at list time — not
  // persisted alongside heartbeat state because the heartbeat itself
  // never carries the firm name (data-isolation: firm_id is one-way).
  firm_name?:        string;
}

interface StoredAlert extends AlertEntry {
  firm_id:        string;
  alert_id:       string;
  acknowledged:   boolean;
  acknowledged_at: string | null;
  ack_by:         string | null;
  notified_at:    string;
  escalated:      boolean;
  // Per-(firm_id, code) dedupe: any re-fires of the same code while
  // THIS alert is still unacknowledged bump seen_count + last_seen_at
  // and DO NOT trigger another email. The dashboard renders
  // "occurred N times" so the operator sees the condition is sticky.
  // Once acked, the open-alert key clears; the next fire is a new
  // incident with its own email.
  seen_count?:    number;
  last_seen_at?:  string;
}

// KV key indexing the currently-open (unacked) alert per (firm, code).
// Value is the alert_id of the open alert. Cleared on ack. Lets us
// answer "is there already an open alert for this code from this firm?"
// in O(1) without scanning all of ALERTS.
function openAlertKey(firm_id: string, code: string): string {
  return `open_alert:${firm_id}:${code}`;
}

// ── Handlers ─────────────────────────────────────────────────────────────────
async function handleHeartbeat(request: Request, env: Env): Promise<Response> {
  let snap: HealthSnapshot;
  try { snap = await request.json(); }
  catch { return jsonResponse({ ok: false, error: "invalid JSON" }, 400); }

  if (snap.schema_version !== 1) return jsonResponse({ ok: false, error: "unsupported schema" }, 400);
  if (!snap.firm_id || !/^[0-9a-f]{16}$/.test(snap.firm_id)) {
    return jsonResponse({ ok: false, error: "invalid firm_id" }, 400);
  }
  if (!await firmAuth(request, env, snap.firm_id)) {
    return jsonResponse({ ok: false, error: "unauthorized" }, 401);
  }

  // Persist current state. KV expiry 7 days — if a firm goes silent
  // for >7d, the dashboard surfaces "no recent heartbeat" instead of
  // displaying stale info indefinitely.
  const stored: StoredFirm = {
    firm_id:       snap.firm_id,
    last_seen:     new Date().toISOString(),
    last_snapshot: snap,
  };
  await env.FIRM_STATE.put(snap.firm_id, JSON.stringify(stored), { expirationTtl: 7 * 86400 });

  // Synthetic alert: firm is on a macOS major outside the supported
  // band. Severity warning (not critical) so it doesn't page the
  // on-call at 02:00, but it surfaces on the dashboard. Dedup via
  // alert_id derived from firm_id + version so we don't spam new
  // entries every 5-min heartbeat for the same offending OS.
  if (snap.macos_version && !isMacosSupported(snap.macos_version)) {
    const supportedAlert: AlertEntry = {
      code:        "supported_os_violation",
      severity:    "warning",
      message:     `Firm running macOS ${snap.macos_version} (build ${snap.macos_build || "unknown"}); not in supported band ${SUPPORTED_MACOS_MAJORS.join(", ")}. Per docs/sop/maintenance.md §macos-version-policy: firm must downgrade or wait until vendor approves the new version.`,
      auto_healed: false,
      timestamp:   snap.timestamp,
    };
    snap.pending_alerts = [supportedAlert, ...(snap.pending_alerts || [])];
  }

  // Persist + dispatch any new alerts in this heartbeat.
  //
  // Dedupe: for each pending alert, check whether THIS firm already has
  // an OPEN (unacked) alert with the same code. If so, bump
  // seen_count / last_seen_at on the existing record and skip the email
  // — operator was already paged once; spamming a second email per
  // sentinel tick (or per heartbeat) is what filled the mailbox. If the
  // open alert exists but was acked, treat this fire as a new incident.
  for (const a of (snap.pending_alerts || [])) {
    const openKey = openAlertKey(snap.firm_id, a.code);
    const openId = await env.ALERTS.get(openKey);
    if (openId) {
      const existingRaw = await env.ALERTS.get(`alert:${openId}`);
      if (existingRaw) {
        let existing: StoredAlert;
        try { existing = JSON.parse(existingRaw) as StoredAlert; }
        catch { existing = null as any; }
        if (existing && !existing.acknowledged) {
          // Bump the counter, persist, do NOT email.
          existing.seen_count = (existing.seen_count || 1) + 1;
          existing.last_seen_at = new Date().toISOString();
          await env.ALERTS.put(`alert:${openId}`, JSON.stringify(existing),
                               { expirationTtl: 30 * 86400 });
          continue;
        }
        // existing was acked or unparseable — fall through to create-new
      }
      // open-key pointed at nothing useful; clean it up before creating fresh
      await env.ALERTS.delete(openKey);
    }

    // No open alert for this (firm, code) → create + dispatch.
    const alert_id = await sha256Hex(`${snap.firm_id}:${a.code}:${a.timestamp}`);
    const stored_alert: StoredAlert = {
      ...a,
      firm_id:         snap.firm_id,
      alert_id:        alert_id.slice(0, 16),
      acknowledged:    a.auto_healed,            // auto-healed → already acked
      acknowledged_at: a.auto_healed ? new Date().toISOString() : null,
      ack_by:          a.auto_healed ? "auto_heal" : null,
      notified_at:     "",
      escalated:       false,
      seen_count:      1,
      last_seen_at:    new Date().toISOString(),
    };
    await env.ALERTS.put(`alert:${stored_alert.alert_id}`, JSON.stringify(stored_alert),
                         { expirationTtl: 30 * 86400 });
    if (!stored_alert.acknowledged) {
      // Only track as "open" if it's still unacked — auto-healed alerts
      // are already acked, no need to dedupe future fires.
      await env.ALERTS.put(openKey, stored_alert.alert_id,
                           { expirationTtl: 30 * 86400 });
    }
    if (a.severity === "critical" && !a.auto_healed) {
      await dispatchAlert(env, stored_alert, false);
      stored_alert.notified_at = new Date().toISOString();
      await env.ALERTS.put(`alert:${stored_alert.alert_id}`, JSON.stringify(stored_alert),
                           { expirationTtl: 30 * 86400 });
    }
  }

  return jsonResponse({ ok: true });
}

// Red-team finding 8.2: list firms that auto-issued a telemetry token
// but haven't been ack'd by the vendor yet. Dashboard renders these
// in a separate "Pending" section.
async function handleListPendingFirms(env: Env): Promise<Response> {
  const out: Array<{ firm_id: string; firm_name: string; registered_at: string; registered_ip?: string }> = [];
  let cursor: string | undefined = undefined;
  do {
    const list: KVNamespaceListResult<unknown, string> = await env.TELEMETRY_TOKENS.list({ prefix: "firm:", cursor });
    for (const k of list.keys) {
      const v = await env.TELEMETRY_TOKENS.get(k.name);
      if (!v) continue;
      try {
        const rec = JSON.parse(v) as { firm_name?: string; registered_at?: string; registered_ip?: string; acknowledged?: boolean };
        if (rec.acknowledged) continue;
        out.push({
          firm_id:       k.name.replace(/^firm:/, ""),
          firm_name:     rec.firm_name || "(unknown)",
          registered_at: rec.registered_at || "",
          registered_ip: rec.registered_ip,
        });
      } catch { /* skip corrupt */ }
    }
    cursor = list.list_complete ? undefined : list.cursor;
  } while (cursor);
  out.sort((a, b) => (a.registered_at < b.registered_at ? 1 : -1));
  return jsonResponse({ pending_firms: out });
}

// Admin ack of an auto-issued firm. POST body: {firm_id: "<16-hex>"}.
// Sets acknowledged: true on the TELEMETRY_TOKENS record so the firm
// stops appearing in the pending-firms list. Idempotent.
async function handleAckFirm(request: Request, env: Env): Promise<Response> {
  let body: { firm_id?: unknown };
  try { body = await request.json(); }
  catch { return jsonResponse({ ok: false, error: "invalid JSON body" }, 400); }
  const firm_id = body.firm_id;
  if (typeof firm_id !== "string" || !/^[a-f0-9]{16}$/.test(firm_id)) {
    return jsonResponse({ ok: false, error: "firm_id must be 16 hex chars" }, 400);
  }
  const key = `firm:${firm_id}`;
  const raw = await env.TELEMETRY_TOKENS.get(key);
  if (!raw) return jsonResponse({ ok: false, error: "firm not found" }, 404);
  let rec: Record<string, unknown>;
  try { rec = JSON.parse(raw); }
  catch { return jsonResponse({ ok: false, error: "corrupt firm record" }, 500); }
  rec.acknowledged = true;
  rec.acknowledged_at = new Date().toISOString();
  await env.TELEMETRY_TOKENS.put(key, JSON.stringify(rec), {
    expirationTtl: 5 * 365 * 86400,
  });
  return jsonResponse({ ok: true, firm_id });
}

async function handleListFirms(env: Env): Promise<Response> {
  const out: StoredFirm[] = [];
  let cursor: string | undefined = undefined;
  do {
    const list: KVNamespaceListResult<unknown, string> = await env.FIRM_STATE.list({ cursor });
    for (const k of list.keys) {
      const v = await env.FIRM_STATE.get(k.name);
      if (v) {
        try { out.push(JSON.parse(v)); } catch { /* skip corrupt */ }
      }
    }
    cursor = list.list_complete ? undefined : list.cursor;
  } while (cursor);
  // Join firm_name in from TELEMETRY_TOKENS so the dashboard can render
  // human-readable names alongside the hash. Auto-issued firms have a
  // record at `firm:<firm_id>`; legacy firms (FIRM_TOKENS env JSON) do
  // not, and surface as the hash only — that's the right behaviour
  // until the operator manually migrates them.
  await Promise.all(out.map(async (f) => {
    try {
      const raw = await env.TELEMETRY_TOKENS.get(`firm:${f.firm_id}`);
      if (raw) {
        const rec = JSON.parse(raw) as { firm_name?: string };
        if (rec.firm_name) f.firm_name = rec.firm_name;
      }
    } catch { /* leave firm_name undefined */ }
  }));
  out.sort((a, b) => (a.last_seen < b.last_seen ? 1 : -1));
  return jsonResponse({ firms: out });
}

async function handleListAlerts(env: Env, onlyOpen = false): Promise<Response> {
  const out: StoredAlert[] = [];
  let cursor: string | undefined = undefined;
  do {
    const list: KVNamespaceListResult<unknown, string> = await env.ALERTS.list({ prefix: "alert:", cursor });
    for (const k of list.keys) {
      const v = await env.ALERTS.get(k.name);
      if (v) {
        try {
          const a = JSON.parse(v) as StoredAlert;
          if (onlyOpen && a.acknowledged) continue;
          out.push(a);
        } catch { /* skip corrupt */ }
      }
    }
    cursor = list.list_complete ? undefined : list.cursor;
  } while (cursor);
  out.sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1));
  return jsonResponse({ alerts: out });
}

async function handleAck(request: Request, env: Env): Promise<Response> {
  let body: { alert_id?: string; note?: string };
  try { body = await request.json(); } catch { return jsonResponse({ ok: false, error: "invalid JSON" }, 400); }
  if (!body.alert_id) return jsonResponse({ ok: false, error: "missing alert_id" }, 400);
  const v = await env.ALERTS.get(`alert:${body.alert_id}`);
  if (!v) return jsonResponse({ ok: false, error: "not found" }, 404);
  const a = JSON.parse(v) as StoredAlert;
  a.acknowledged = true;
  a.acknowledged_at = new Date().toISOString();
  a.ack_by = "operator";
  await env.ALERTS.put(`alert:${body.alert_id}`, JSON.stringify(a), { expirationTtl: 30 * 86400 });
  // Clear the open-alert key so the next fire of this (firm, code) is
  // treated as a NEW incident (emails again) instead of bumping the
  // now-acked alert's seen_count silently.
  await env.ALERTS.delete(openAlertKey(a.firm_id, a.code));
  return jsonResponse({ ok: true, alert: a });
}

// ── Notification dispatch ────────────────────────────────────────────────────
async function dispatchAlert(env: Env, a: StoredAlert, isEscalation: boolean): Promise<void> {
  const subject = `${isEscalation ? "[SLA ESCALATION] " : ""}LocallyAI alert: ${a.code} (firm ${a.firm_id})`;
  const body = [
    `Firm:     ${a.firm_id}`,
    `Code:     ${a.code}`,
    `Severity: ${a.severity}`,
    `Time:     ${a.timestamp}`,
    `Message:  ${a.message || "(none)"}`,
    `Ack URL:  Open the dashboard, find alert ${a.alert_id}, click Acknowledge.`,
    "",
    `4-hour SLA started at ${a.timestamp}.`,
    isEscalation ? `>>> THIS IS THE ESCALATION — SLA window expires soon. <<<` : "",
  ].filter(Boolean).join("\n");

  // Email via Resend (free tier — 3000 emails/month).
  if (env.RESEND_API_KEY && env.ALERT_TO_EMAIL) {
    try {
      await fetch("https://api.resend.com/emails", {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.RESEND_API_KEY}`,
          "Content-Type":  "application/json",
        },
        body: JSON.stringify({
          from:    (env.RESEND_FROM || "LocallyAI Monitor <onboarding@resend.dev>"),
          to:      [env.ALERT_TO_EMAIL],
          subject,
          text:    body,
        }),
      });
    } catch (e) {
      console.error("Resend dispatch failed:", e);
    }
  }

  // Slack webhook (optional — channel-level echo for the on-call channel).
  if (env.SLACK_WEBHOOK_URL) {
    try {
      await fetch(env.SLACK_WEBHOOK_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: `*${subject}*\n\`\`\`${body}\`\`\`` }),
      });
    } catch (e) {
      console.error("Slack dispatch failed:", e);
    }
  }
}

// ── Cron: SLA escalation ─────────────────────────────────────────────────────
// Walks unacked critical alerts and re-emails after SLA_WARN_HOURS.
// Disabled by default (SLA_WARN_HOURS="0") because the "one email
// per (firm, code) until acked" model above is usually what an
// operator wants. To re-enable, set SLA_WARN_HOURS in wrangler.toml
// to a positive number (e.g. "3.5" = re-notify after 3.5h).
async function handleCron(env: Env): Promise<void> {
  const warnHours = Number(env.SLA_WARN_HOURS);
  if (!Number.isFinite(warnHours) || warnHours <= 0) return;
  const now = Date.now();
  let cursor: string | undefined = undefined;
  do {
    const list: KVNamespaceListResult<unknown, string> = await env.ALERTS.list({ prefix: "alert:", cursor });
    for (const k of list.keys) {
      const v = await env.ALERTS.get(k.name);
      if (!v) continue;
      let a: StoredAlert;
      try { a = JSON.parse(v); } catch { continue; }
      if (a.acknowledged || a.escalated || a.severity !== "critical") continue;
      const ageHours = (now - new Date(a.timestamp).getTime()) / 3_600_000;
      if (ageHours >= warnHours) {
        await dispatchAlert(env, a, true);
        a.escalated = true;
        await env.ALERTS.put(k.name, JSON.stringify(a), { expirationTtl: 30 * 86400 });
      }
    }
    cursor = list.list_complete ? undefined : list.cursor;
  } while (cursor);
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// ── One-time-use intake tokens ───────────────────────────────────────────────
//
// Threat: the form's generated install command is copy-pasted by firm IT into
// a Terminal. If that command is intercepted (shoulder-surfed, leaked email,
// scrollback on a shared Mac), an attacker could re-run the same command
// later to register a malicious office Mac under the firm's identity, OR
// just to consume vendor's CF resources.
//
// Mitigation: the intake blob is no longer inlined in the curl URL. The form
// POSTs the blob to /onboarding/mint-token, gets back a single-use token,
// and produces a curl command that fetches the blob via /onboarding/intake?t=
// — that endpoint atomically marks the token consumed and refuses any
// subsequent fetch. Tokens TTL after 7 days regardless.
//
// The bootstrap script itself stays unmodified (and stays GPG-signable):
// LOCALLYAI_INTAKE is set by the user's command line via curl substitution,
// not injected by the Worker.

interface IntakeTokenRecord {
  intake_blob: string;
  issued_at:   string;
  issued_ip?:  string;
  consumed_at?:string;
  consumer_ip?:string;
  // Populated by handleMintToken when the form sets firm_name; used
  // by /onboarding/deploy-key as the human-friendly title for the
  // GitHub deploy key, and to associate the issued key back to the
  // firm record. Both optional for backwards compat.
  firm_name?:  string;
  firm_id?:    string;
}

const INTAKE_TOKEN_TTL_SEC = 7 * 24 * 3600;
const INTAKE_BLOB_MAX_BYTES = 16384;

function generateToken(): string {
  // 32 bytes = 64 hex chars. URL-safe by virtue of being hex only.
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes).map(b => b.toString(16).padStart(2, "0")).join("");
}

// Compute the anonymised firm_id the same way the form does (and the same way
// scripts/onboard_firm.sh and telemetry.py do): SHA-256("locallyai-firm:<name>")[:16].
async function computeFirmId(firmName: string): Promise<string> {
  // Match what onboard_firm.sh and the form's JS do exactly: SHA-256 over
  // "locallyai-firm:<TRIMMED-NAME>", first 16 hex chars (= first 8 bytes).
  const data = new TextEncoder().encode(`locallyai-firm:${firmName.trim()}`);
  const buf = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(buf)).slice(0, 8)
    .map(b => b.toString(16).padStart(2, "0")).join("");
}

// Allowlist of LOCALLYAI_* keys that may appear in a form-submitted
// intake blob. Red-team finding 8.3: previously the regex accepted
// arbitrary KEY=VALUE pairs, so a malicious form submission could
// inject e.g. LOCALLYAI_KILL_SWITCH_URL=https://attacker.example/
// into the firm's .env via the bootstrap. Any key not in this set is
// silently dropped before storage. New legitimate keys must be added
// here explicitly + reviewed.
const ALLOWED_INTAKE_KEYS = new Set([
  "LOCALLYAI_FIRM_NAME",
  "LOCALLYAI_DATA_REGION",
  "LOCALLYAI_TELEMETRY",
  "LOCALLYAI_UPDATE_CHANNEL",
  "LOCALLYAI_OFFICE_SUBNET",
  "LOCALLYAI_TELEMETRY_TOKEN",
]);

// Decode a base64 .env-style intake blob into a key→value map. Keys
// outside ALLOWED_INTAKE_KEYS are dropped. Comments (#) and blanks
// are skipped.
function parseEnvBlob(b64: string): Record<string, string> {
  let text: string;
  try { text = atob(b64); } catch { return {}; }
  const out: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const m = line.match(/^([A-Z][A-Z0-9_]*)=(.*)$/);
    if (m && ALLOWED_INTAKE_KEYS.has(m[1])) {
      out[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
  }
  return out;
}

// Rebuild a sanitised .env blob containing ONLY the allowlisted keys.
// Used in handleMintToken so the blob stored in INTAKE_TOKENS (and later
// served to the office Mac's bootstrap) cannot smuggle attacker-chosen
// env vars even if parseEnvBlob's allowlist drift is incomplete.
function rebuildSanitisedBlob(parsed: Record<string, string>): string {
  const lines: string[] = ["# Generated by /onboarding/mint-token (allowlisted keys only)"];
  for (const k of ALLOWED_INTAKE_KEYS) {
    if (parsed[k] !== undefined) {
      // Quote the value so it round-trips intact even if it contains spaces.
      lines.push(`${k}=${JSON.stringify(parsed[k])}`);
    }
  }
  lines.push("");
  return btoa(unescape(encodeURIComponent(lines.join("\n"))));
}

// Per-IP rate limit on /onboarding/mint-token. Red-team finding 8.1:
// without this, anyone hitting the form's API can fill TELEMETRY_TOKENS
// KV with junk firm registrations, spam the vendor's alert email/Slack
// channels, and exhaust the INTAKE_TOKENS namespace. KV-backed sliding
// window: store {first_seen, count} under "ratelimit:mint:<ip>"; allow
// MINT_RATE_LIMIT_MAX requests per MINT_RATE_LIMIT_WINDOW_SEC seconds.
const MINT_RATE_LIMIT_MAX = 5;
const MINT_RATE_LIMIT_WINDOW_SEC = 3600;  // 1 hour

async function checkMintRateLimit(request: Request, env: Env): Promise<{ ok: boolean; retry_after?: number }> {
  const ip = request.headers.get("CF-Connecting-IP") || "anon";
  const key = `ratelimit:mint:${ip}`;
  const now = Math.floor(Date.now() / 1000);
  // Round-2 A3: rate-limit state lives in its own namespace so churn
  // doesn't pollute INTAKE_TOKENS write quota.
  const raw = await env.RATE_LIMITS.get(key);
  let rec: RateLimitRecord;
  if (raw) {
    try { rec = JSON.parse(raw); } catch { rec = { first_seen: now, count: 0 }; }
  } else {
    rec = { first_seen: now, count: 0 };
  }
  // If the window has expired, reset.
  if (now - rec.first_seen >= MINT_RATE_LIMIT_WINDOW_SEC) {
    rec = { first_seen: now, count: 0 };
  }
  if (rec.count >= MINT_RATE_LIMIT_MAX) {
    const retry_after = MINT_RATE_LIMIT_WINDOW_SEC - (now - rec.first_seen);
    return { ok: false, retry_after };
  }
  rec.count += 1;
  await env.RATE_LIMITS.put(key, JSON.stringify(rec), {
    expirationTtl: MINT_RATE_LIMIT_WINDOW_SEC + 60,
  });
  return { ok: true };
}

async function handleMintToken(request: Request, env: Env): Promise<Response> {
  const rl = await checkMintRateLimit(request, env);
  if (!rl.ok) {
    return new Response(
      `Too many mint-token requests from this IP. Try again in ${rl.retry_after}s.`,
      {
        status: 429,
        headers: {
          "Content-Type": "text/plain",
          "Retry-After":  String(rl.retry_after || MINT_RATE_LIMIT_WINDOW_SEC),
        },
      },
    );
  }

  let body: { intake_blob?: unknown; profile_md?: unknown };
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ ok: false, error: "invalid JSON body" }, 400);
  }
  if (typeof body.intake_blob !== "string" || body.intake_blob.length === 0) {
    return jsonResponse({ ok: false, error: "intake_blob (base64 string) required" }, 400);
  }
  let blob: string = body.intake_blob;
  // Optional firm-profile markdown (the .md the form generates). When
  // present, we email it to the vendor on-call inbox so the vendor gets
  // the full firm record automatically — no manual "please email this
  // back" step. Capped at 64 KB to bound the email size.
  const profileMd: string | undefined = (typeof body.profile_md === "string" && body.profile_md.length > 0 && body.profile_md.length <= 65536)
    ? body.profile_md
    : undefined;
  if (blob.length > INTAKE_BLOB_MAX_BYTES) {
    return jsonResponse({ ok: false, error: `intake_blob too large (max ${INTAKE_BLOB_MAX_BYTES} bytes)` }, 413);
  }
  if (!/^[A-Za-z0-9+/=]+$/.test(blob)) {
    return jsonResponse({ ok: false, error: "intake_blob must be base64" }, 400);
  }

  // ── Auto-issue telemetry token ────────────────────────────────────────────
  // If the blob opts the firm in to telemetry (LOCALLYAI_TELEMETRY=1 — the
  // default in the form), generate a 32-byte hex token, register it under the
  // firm_id derived from the firm name, and append a LOCALLYAI_TELEMETRY_TOKEN=
  // line to the blob. The bootstrap on the office Mac then writes the token
  // straight into .env without any vendor-side step.
  //
  // This replaces the prior workflow where firm IT downloaded the .md and
  // emailed it to the vendor, who ran scripts/onboard_firm.sh manually. The
  // CLI script still works (and is the path for re-issuance / rotation), but
  // it's no longer required to onboard a new firm.
  let auto_telemetry: { firm_id: string; firm_name: string } | null = null;
  let auto_telemetry_token: string | undefined;
  const env_vars = parseEnvBlob(blob);
  // Accept the same truthy set as telemetry.py's _enabled(): 1, on, true, yes, y, t.
  const TRUTHY = new Set(["1", "on", "true", "yes", "y", "t"]);
  const wantsTelemetry = TRUTHY.has((env_vars["LOCALLYAI_TELEMETRY"] || "").trim().toLowerCase());
  const firmName = env_vars["LOCALLYAI_FIRM_NAME"] || "";
  if (wantsTelemetry && firmName.length > 0 && firmName.length <= 200) {
    const firmId = await computeFirmId(firmName);
    // If firm_id already has a token, reissue (rotate). KV TTL = 5 years —
    // tokens persist across sessions (heartbeats need them indefinitely).
    auto_telemetry_token = generateToken();
    // Red-team finding 8.2: auto-issued firms are marked pending_ack
    // until the vendor explicitly acknowledges via POST /api/firms/ack.
    // The heartbeat still authenticates (firmAuth) so the firm can
    // come up and report; the dashboard renders pending firms with a
    // yellow border + Acknowledge button so the vendor can review
    // and approve before treating them as production fleet members.
    // Re-issuing a token for a firm that's already ack'd preserves
    // the ack state.
    const existing = await env.TELEMETRY_TOKENS.get(`firm:${firmId}`);
    let already_acked = false;
    if (existing) {
      try {
        const rec = JSON.parse(existing) as { acknowledged?: boolean };
        already_acked = !!rec.acknowledged;
      } catch { /* corrupt entry — treat as fresh */ }
    }
    await env.TELEMETRY_TOKENS.put(`firm:${firmId}`, JSON.stringify({
      token:         auto_telemetry_token,
      firm_name:     firmName,
      registered_at: new Date().toISOString(),
      registered_ip: request.headers.get("CF-Connecting-IP") || undefined,
      via:           "auto-mint",
      acknowledged:  already_acked,
    }), { expirationTtl: 5 * 365 * 86400 });
    // Append LOCALLYAI_TELEMETRY_TOKEN to the blob so the bootstrap writes it
    // to .env. We append a line, then re-base64 the whole thing.
    const newEnvText = atob(blob).replace(/\n*$/, "") +
      `\nLOCALLYAI_TELEMETRY_TOKEN=${auto_telemetry_token}\n`;
    blob = btoa(newEnvText);
    auto_telemetry = { firm_id: firmId, firm_name: firmName };
  }

  const token = generateToken();
  const record: IntakeTokenRecord = {
    intake_blob: blob,
    issued_at:   new Date().toISOString(),
    issued_ip:   request.headers.get("CF-Connecting-IP") || undefined,
  };
  await env.INTAKE_TOKENS.put(`intake:${token}`, JSON.stringify(record), {
    expirationTtl: INTAKE_TOKEN_TTL_SEC,
  });

  // ── Fan out the firm profile to vendor inbox + Slack ────────────────────
  // Three best-effort sinks; non-blocking so they don't slow down the
  // install-token response:
  //   1. Email via Resend (RESEND_API_KEY + ALERT_TO_EMAIL)
  //   2. Slack file attachment via Bot Token (SLACK_BOT_TOKEN + SLACK_CHANNEL_ID)
  //   3. Slack inline code block via incoming webhook (SLACK_WEBHOOK_URL)
  // Configure whichever sinks you want; missing config = silently skipped.

  if (profileMd) {
    const firmLabel = auto_telemetry?.firm_name || firmName || "(unknown firm)";
    const firmIdHint = auto_telemetry?.firm_id || "(no auto-issuance)";
    const tokenLine = auto_telemetry_token
      ? `Telemetry token (auto-issued): ${auto_telemetry_token}`
      : `Telemetry: not opted in`;
    const submittedFrom = request.headers.get("CF-Connecting-IP") || "(unknown IP)";
    const submittedAt = new Date().toISOString();
    const slug = firmLabel.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    const filename = `firm-profile-${slug || "unknown"}.md`;
    const summary = [
      `New LocallyAI intake submitted.`,
      `Firm:           ${firmLabel}`,
      `firm_id:        ${firmIdHint}`,
      `Submitted from: ${submittedFrom}`,
      `Submitted at:   ${submittedAt}`,
      tokenLine,
    ].join("\n");
    const fullBody = summary + "\n\n── Firm profile (paste into vendor-records/firms/) ──\n\n" + profileMd;

    // ── 1) Email via Resend ─────────────────────────────────────────────
    if (env.RESEND_API_KEY && env.ALERT_TO_EMAIL) {
      fetch("https://api.resend.com/emails", {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.RESEND_API_KEY}`,
          "Content-Type":  "application/json",
        },
        body: JSON.stringify({
          from:    (env.RESEND_FROM || "LocallyAI Onboarding <onboarding@resend.dev>"),
          to:      [env.ALERT_TO_EMAIL],
          subject: `LocallyAI intake: ${firmLabel}`,
          text:    fullBody,
        }),
      }).catch((e) => console.error("Resend intake email failed:", e));
    }

    // ── 2) Slack real .md attachment via Bot Token (preferred) ─────────
    if (env.SLACK_BOT_TOKEN && env.SLACK_CHANNEL_ID) {
      // Slack files.upload v2 flow: getUploadURLExternal → PUT bytes → completeUploadExternal.
      (async () => {
        try {
          const blobBytes = new TextEncoder().encode(profileMd);
          // Step A: ask Slack for an upload URL.
          const init = await fetch("https://slack.com/api/files.getUploadURLExternal", {
            method: "POST",
            headers: {
              "Authorization": `Bearer ${env.SLACK_BOT_TOKEN}`,
              "Content-Type":  "application/x-www-form-urlencoded",
            },
            body: new URLSearchParams({
              filename,
              length: String(blobBytes.length),
            }).toString(),
          }).then(r => r.json() as Promise<{ ok: boolean; upload_url?: string; file_id?: string; error?: string }>);
          if (!init.ok || !init.upload_url || !init.file_id) {
            console.error("Slack getUploadURLExternal failed:", init.error || init);
            return;
          }
          // Step B: PUT the bytes.
          await fetch(init.upload_url, { method: "POST", body: blobBytes });
          // Step C: complete + post into channel with the summary as the comment.
          await fetch("https://slack.com/api/files.completeUploadExternal", {
            method: "POST",
            headers: {
              "Authorization": `Bearer ${env.SLACK_BOT_TOKEN}`,
              "Content-Type":  "application/json",
            },
            body: JSON.stringify({
              files: [{ id: init.file_id, title: `LocallyAI intake: ${firmLabel}` }],
              channel_id:      env.SLACK_CHANNEL_ID,
              initial_comment: summary,
            }),
          });
        } catch (e) {
          console.error("Slack file upload failed:", e);
        }
      })();
    }
    // ── 3) Slack inline (fallback when only webhook is configured) ─────
    else if (env.SLACK_WEBHOOK_URL) {
      // Webhooks can't upload files; post the profile as a fenced code
      // block in the channel. Slack truncates messages over ~40 KB; for
      // long profiles, configure SLACK_BOT_TOKEN + SLACK_CHANNEL_ID
      // instead for real attachments.
      const inlineBody = summary + "\n\n```markdown\n" + profileMd.slice(0, 38000) + "\n```";
      fetch(env.SLACK_WEBHOOK_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ text: inlineBody }),
      }).catch((e) => console.error("Slack webhook intake post failed:", e));
    }
  }

  return jsonResponse({
    ok: true,
    token,
    expires_in_seconds: INTAKE_TOKEN_TTL_SEC,
    telemetry_auto_issued: auto_telemetry,
    profile_emailed: !!(profileMd && env.RESEND_API_KEY && env.ALERT_TO_EMAIL),
    // The actual telemetry token is included so the form can surface it
    // directly without making the operator parse it out of the install
    // command + intake URL. Same security profile as the install path
    // (mint-token is unauthenticated; abuse mitigated by per-IP rate
    // limit at the CF edge + monitor dashboard's "new firms" review).
    telemetry_token: auto_telemetry ? auto_telemetry_token : undefined,
  } as Record<string, unknown>);
}

async function handleIntake(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const token = url.searchParams.get("t");
  if (!token || !/^[a-f0-9]{64}$/.test(token)) {
    return new Response("missing or malformed token (?t=<64-hex>)\n", {
      status: 400, headers: { "Content-Type": "text/plain" },
    });
  }
  const key = `intake:${token}`;
  const raw = await env.INTAKE_TOKENS.get(key);
  if (!raw) {
    return new Response(
      "This install link has expired or never existed. Regenerate from the intake form.\n",
      { status: 410, headers: { "Content-Type": "text/plain" } }
    );
  }
  const record: IntakeTokenRecord = JSON.parse(raw);
  if (record.consumed_at) {
    return new Response(
      `This install link has already been used (at ${record.consumed_at}). ` +
      `Regenerate from the intake form to install again.\n`,
      { status: 410, headers: { "Content-Type": "text/plain" } }
    );
  }

  // Atomically mark consumed BEFORE returning the blob, so a network
  // glitch between us-and-them doesn't leave the token replayable.
  // (KV's eventual consistency means a second concurrent fetch in-flight
  // could still see the unconsumed record. For a copy-paste install
  // workflow that's acceptable; a full strict-once implementation would
  // need Durable Objects.)
  record.consumed_at = new Date().toISOString();
  record.consumer_ip = request.headers.get("CF-Connecting-IP") || undefined;
  await env.INTAKE_TOKENS.put(key, JSON.stringify(record), {
    expirationTtl: INTAKE_TOKEN_TTL_SEC,
  });

  // Return just the base64 blob; the bootstrap script reads stdin via
  // shell command substitution: LOCALLYAI_INTAKE="$(curl ...)".
  return new Response(record.intake_blob, {
    status: 200,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-store, no-cache, must-revalidate",
    },
  });
}

// ── Auto deploy-key creation ────────────────────────────────────────────────
// During install, the firm's IT generates a fresh SSH keypair on the
// office Mac and POSTs the public key here together with their valid
// install token. We validate the token then call GitHub to add the
// key as a deploy key on the per-firm vendor-records repo.
//
// Replaces the manual "email your .pub to the vendor laptop" step
// (which created a copy-paste error surface + delayed installs by
// hours when the vendor was on call). The bootstrap script now does
// the round-trip in <1 s and the firm's install proceeds.

interface DeployKeyReq {
  install_token?: string;
  pubkey?: string;
  firm_label?: string;  // optional human-friendly title for the key
}

async function handleDeployKey(request: Request, env: Env): Promise<Response> {
  let body: DeployKeyReq;
  try { body = await request.json(); }
  catch { return jsonResponse({ ok: false, error: "invalid JSON" }, 400); }

  const token = (body.install_token || "").trim();
  if (!/^[a-f0-9]{64}$/.test(token)) {
    return jsonResponse({ ok: false, error: "missing or malformed install_token" }, 400);
  }
  const pubkey = (body.pubkey || "").trim();
  // Accept the standard SSH public-key formats only.
  if (!/^(ssh-(ed25519|rsa)|ecdsa-sha2-\w+) [A-Za-z0-9+/=]+( .+)?$/.test(pubkey)) {
    return jsonResponse({ ok: false, error: "invalid public key format" }, 400);
  }

  // Validate the install token — must exist + not yet consumed.
  // We do NOT mark it consumed here (handleIntake does that on the
  // separate bootstrap fetch). Reusing a single token for both calls
  // is intentional — the firm's install is one logical operation.
  const tokenRaw = await env.INTAKE_TOKENS.get(`intake:${token}`);
  if (!tokenRaw) {
    return jsonResponse({ ok: false, error: "install token expired or unknown" }, 410);
  }
  let tokenRec: IntakeTokenRecord;
  try { tokenRec = JSON.parse(tokenRaw); }
  catch { return jsonResponse({ ok: false, error: "install token corrupted" }, 500); }

  // Resolve the GitHub config. Without a PAT we can't add deploy keys —
  // fall back to "manual" mode and tell the operator what to do.
  const pat = env.GITHUB_DEPLOY_KEYS_PAT;
  const repo = env.GITHUB_DEPLOY_KEY_REPO;
  if (!pat || !repo) {
    return jsonResponse({
      ok: false,
      manual_required: true,
      reason: "GITHUB_DEPLOY_KEYS_PAT or GITHUB_DEPLOY_KEY_REPO not configured on the worker",
      next_step: "vendor adds the key manually at https://github.com/" + (repo || "<repo>") + "/settings/keys",
      pubkey_received: true,
    }, 200);
  }

  const firmLabel = (body.firm_label || tokenRec.firm_name || "locallyai-deploy")
    .toString().slice(0, 64);
  const ghBody = {
    title: `${firmLabel} (auto-created ${new Date().toISOString().slice(0, 10)})`,
    key:   pubkey,
    read_only: true,
  };

  const ghRes = await fetch(`https://api.github.com/repos/${repo}/keys`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${pat}`,
      "Accept":        "application/vnd.github+json",
      "User-Agent":    "LocallyAI-monitor/deploy-key",
      "Content-Type":  "application/json",
    },
    body: JSON.stringify(ghBody),
  });
  const ghPayload = await ghRes.text();
  if (!ghRes.ok) {
    return jsonResponse({
      ok: false,
      github_status: ghRes.status,
      github_response: ghPayload.slice(0, 500),
      hint: ghRes.status === 422
        ? "Key may already exist on the repo. Safe to ignore if the firm previously installed."
        : "Check that GITHUB_DEPLOY_KEYS_PAT has admin:repo on the target repo.",
    }, ghRes.status === 422 ? 200 : 502);
  }
  let ghJson: { id?: number; title?: string } = {};
  try { ghJson = JSON.parse(ghPayload); } catch { /* ignore */ }
  return jsonResponse({
    ok: true,
    repo,
    key_id: ghJson.id,
    key_title: ghJson.title,
    firm_id: tokenRec.firm_id,
  }, 201);
}


// ── Worker entrypoint ────────────────────────────────────────────────────────
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    // POST /heartbeat (firm-authed)
    if (url.pathname === "/heartbeat" && request.method === "POST") {
      return handleHeartbeat(request, env);
    }
    // POST /onboarding/mint-token — public (form → token).
    // GET  /onboarding/intake?t=<64-hex> — public, single-use, atomic consume.
    if (url.pathname === "/onboarding/mint-token" && request.method === "POST") {
      return handleMintToken(request, env);
    }
    if (url.pathname === "/onboarding/intake" && request.method === "GET") {
      return handleIntake(request, env);
    }
    // POST /onboarding/deploy-key — bootstrap submits an SSH public
    // key + its install token; the worker validates the token, then
    // calls GitHub to add the key as a per-firm deploy key on the
    // vendor-records repo. Replaces the manual "send your .pub to
    // the vendor laptop" step with a one-shot in-script flow.
    if (url.pathname === "/onboarding/deploy-key" && request.method === "POST") {
      return handleDeployKey(request, env);
    }
    // Admin endpoints — TOTP-gated, session-tokened
    if (url.pathname.startsWith("/api/")) {
      const auth = await adminAuth(request, env);
      if (!auth.ok) return jsonResponse({ ok: false, error: "unauthorized" }, 401);
      let resp: Response;
      if (url.pathname === "/api/firms" && request.method === "GET") resp = await handleListFirms(env);
      else if (url.pathname === "/api/firms/ack" && request.method === "POST") resp = await handleAckFirm(request, env);
      else if (url.pathname === "/api/firms/pending" && request.method === "GET") resp = await handleListPendingFirms(env);
      else if (url.pathname === "/api/alerts" && request.method === "GET")
        resp = await handleListAlerts(env, url.searchParams.get("open") === "1");
      else if (url.pathname === "/api/ack" && request.method === "POST") resp = await handleAck(request, env);
      else resp = jsonResponse({ ok: false, error: "unknown endpoint" }, 404);
      return withSession(resp, auth);
    }
    // Static dashboard files via Assets binding (CF Workers v3+)
    return env.ASSETS.fetch(request);
  },

  async scheduled(_event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(handleCron(env));
  },
};

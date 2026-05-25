// Typed client for the LocallyAI backend used by the management console.
// The single bearer token used by this UI is the LOCALLYAI_ADMIN_KEY — the
// backend treats that key as a synthetic "admin" user, so it works for both
// /v1/* (chat, models, ingest) and admin-only routes (/monitor, /export,
// /diagnostician, /admin/users).

import { getAdminKey, clearAdminKey } from "./auth";

const BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ||
  "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const key = getAdminKey();
  if (!key) throw new ApiError(401, "Not signed in");
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${key}`);
  const res = await fetch(`${BASE_URL}${path}`, { ...init, headers });
  if (res.status === 401 || res.status === 403) {
    clearAdminKey();
    throw new ApiError(res.status, "Invalid or expired admin key");
  }
  return res;
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(res.status, body || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ── Firm identity (unauth — surfaces in TopBar + LoginGate) ──────────────────
export interface BrandingResponse {
  firm_name: string;
  office_host: string;
  deployment_id: string;
  data_region: string;
  node_id: string;
  isolation_statement: string;
}
export async function getBranding(): Promise<BrandingResponse> {
  const res = await fetch(`${BASE_URL}/v1/branding`);
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json();
}

// ── Health / identity ────────────────────────────────────────────────────────
export interface HealthResponse {
  ok: boolean;
  backend: string;
}

export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch(`${BASE_URL}/healthz`);
  return jsonOrThrow(res);
}

export interface MeResponse {
  user: string;
  is_admin: boolean;
}

export async function getMe(): Promise<MeResponse> {
  return jsonOrThrow(await authedFetch(`/v1/me`));
}

// ── Chat / models / ingest (user-tier) ───────────────────────────────────────
export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface SourceCitation {
  chunk_id: string;
  source: string;
  snippet: string;
  score: number;
}

export interface ChatCompletionResponse {
  id: string;
  object: string;
  model: string;
  backend: string;
  choices: Array<{ index: number; message: { role: string; content: string }; finish_reason: string }>;
  usage: { sources_retrieved: number };
  sources?: SourceCitation[];
  safe_mode?: boolean;
}

export interface ChatRequestPayload {
  messages: ChatMessage[];
  model?: string;
  max_tokens?: number;
  temperature?: number;
  matter_code?: string;
}

export async function chatCompletion(payload: ChatRequestPayload): Promise<ChatCompletionResponse> {
  return jsonOrThrow(
    await authedFetch(`/v1/chat/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export interface ModelInfo {
  id: string;
  object: string;
  owned_by: string;
}

export async function listModels(): Promise<ModelInfo[]> {
  const data = await jsonOrThrow<{ object: string; data: ModelInfo[] }>(
    await authedFetch(`/v1/models`),
  );
  return data.data;
}

export interface IngestResponse {
  status: string;
  stored_as: string;
  bytes: number;
  indexing: string;
}

export async function ingestDocument(file: File): Promise<IngestResponse> {
  const form = new FormData();
  form.append("file", file);
  return jsonOrThrow(await authedFetch(`/v1/ingest`, { method: "POST", body: form }));
}

// ── Documents listing (real corpus, not localStorage) ────────────────────────
export interface DocumentInfo {
  name: string;
  size_bytes: number;
  ingested_at: string;
  suffix: string;
  indexed: boolean;
}
export interface DocumentsResponse {
  object: string;
  data: DocumentInfo[];
  count: number;
}
export async function listDocuments(): Promise<DocumentsResponse> {
  return jsonOrThrow(await authedFetch(`/v1/documents`));
}

export interface DocumentDeleteResponse {
  status: string;
  filename: string;
  qdrant_points_removed: number;
  ingest_state_updated: boolean;
}
export async function deleteDocument(displayName: string): Promise<DocumentDeleteResponse> {
  return jsonOrThrow(
    await authedFetch(`/v1/documents/${encodeURIComponent(displayName)}`, { method: "DELETE" }),
  );
}

// ── Per-document ACL ────────────────────────────────────────────────────────
export interface DocAcl {
  allowed_users: string[];
  matter_code: string;
  ethical_wall: string[];
  set_at: string | null;
  set_by: string | null;
  version: number;
  default?: boolean;
}

export async function getDocumentAcl(displayName: string): Promise<DocAcl> {
  return jsonOrThrow(
    await authedFetch(`/v1/documents/${encodeURIComponent(displayName)}/acl`),
  );
}

export async function listDocumentAcls(): Promise<{ acls: Record<string, DocAcl> }> {
  return jsonOrThrow(await authedFetch(`/v1/documents/acls`));
}

export async function setDocumentAcl(displayName: string, input: {
  allowed_users: string[];
  matter_code?: string;
  ethical_wall?: string[];
}): Promise<{ ok: boolean; acl: DocAcl; chunks_updated: number }> {
  return jsonOrThrow(
    await authedFetch(`/v1/documents/${encodeURIComponent(displayName)}/acl`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}

export async function deleteDocumentAcl(displayName: string): Promise<{ ok: boolean; removed: boolean }> {
  return jsonOrThrow(
    await authedFetch(`/v1/documents/${encodeURIComponent(displayName)}/acl`, { method: "DELETE" }),
  );
}

// ── Chunked / resumable uploads — gigabyte-scale corpus loading ──────────────
export interface UploadInitResponse {
  upload_id: string;
  received_bytes: number;
  chunk_size_suggested: number;
  max_chunk_bytes: number;
}
export interface UploadStatusResponse {
  upload_id: string;
  filename: string;
  total_bytes: number;
  received_bytes: number;
  status: "open" | "complete" | "cancelled" | "failed";
}
export interface UploadCompleteResponse {
  stored_as: string;
  bytes: number;
  indexing: string;
}

export async function initUpload(filename: string, totalBytes: number, sha256?: string | null): Promise<UploadInitResponse> {
  return jsonOrThrow(await authedFetch(`/v1/uploads`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, total_bytes: totalBytes, sha256: sha256 ?? null }),
  }));
}

export async function getUploadStatus(uploadId: string): Promise<UploadStatusResponse> {
  return jsonOrThrow(await authedFetch(`/v1/uploads/${encodeURIComponent(uploadId)}`));
}

export async function patchUploadChunk(
  uploadId: string, start: number, end: number, total: number,
  body: Blob | ArrayBuffer | Uint8Array, signal?: AbortSignal,
): Promise<{ received_bytes: number; total_bytes: number }> {
  const key = getAdminKey();
  if (!key) throw new ApiError(401, "Not signed in");
  const res = await fetch(`${BASE_URL}/v1/uploads/${encodeURIComponent(uploadId)}`, {
    method: "PATCH",
    headers: {
      "Authorization": `Bearer ${key}`,
      "Content-Range": `bytes ${start}-${end}/${total}`,
      "Content-Type": "application/octet-stream",
    },
    body: body as BodyInit,
    signal,
  });
  if (res.status === 401 || res.status === 403) { clearAdminKey(); throw new ApiError(res.status, "Invalid or expired admin key"); }
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || `HTTP ${res.status}`);
  return res.json();
}

export async function completeUpload(uploadId: string, sha256?: string | null): Promise<UploadCompleteResponse> {
  return jsonOrThrow(await authedFetch(`/v1/uploads/${encodeURIComponent(uploadId)}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sha256: sha256 ?? null }),
  }));
}

export async function cancelUpload(uploadId: string): Promise<void> {
  await authedFetch(`/v1/uploads/${encodeURIComponent(uploadId)}`, { method: "DELETE" });
}

export interface IngestStatusResponse {
  in_flight: number;
  queued: number;
  completed_total: number;
  failed_total: number;
  bm25_pending: boolean;
  last_completed_at: number | null;
}
export async function getIngestStatus(): Promise<IngestStatusResponse> {
  return jsonOrThrow(await authedFetch(`/v1/ingest/status`));
}
export async function flushIngest(): Promise<void> {
  await authedFetch(`/v1/ingest/flush`, { method: "POST" });
}

export async function sha256OfFile(file: File): Promise<string> {
  if (file.size <= 8 * 1024 * 1024) {
    return _hex(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()));
  }
  try { return _hex(await crypto.subtle.digest("SHA-256", await file.arrayBuffer())); }
  catch { return ""; }
}
function _hex(buf: ArrayBuffer): string {
  const b = new Uint8Array(buf); let s = "";
  for (let i = 0; i < b.length; i++) s += b[i].toString(16).padStart(2, "0");
  return s;
}

// ── Admin: system updates (server code, server side) ────────────────────────
// Companion to client-installer distribution. THESE are updates to the
// server itself (Python source, UI, models). See system_updates.py for
// the verification model (GPG + manifest + soak + kill-switch).
export interface ReleaseManifest {
  version: string;
  channel: "stable" | "dev";
  tier: "A" | "B" | "C" | "?";
  released_at: string;
  changelog_summary: string;
  artifacts: Array<{ name: string; sha256: string; size: number }>;
  min_required_version: string;
  rollback_to_previous_if_failed: boolean;
}
export interface AvailableUpdate {
  tag: string;
  manifest: ReleaseManifest;
  gpg_verified: boolean;
  gpg_detail: string;
  manifest_verified: boolean;
  manifest_detail: string;
  blocked_by_kill_switch: boolean;
  blocked_reason: string;
  eligible_for_auto_apply: boolean;
}
export interface UpdatesResponse {
  channel_status: {
    channel: "stable" | "dev";
    current_version: string;
    auto_update_enabled: boolean;
    auto_update_tiers: string[];
    gpg_available: boolean;
    github_repo: string;
    dev_soak_hours: number;
  };
  kill_switch: {
    url: string; required: boolean; reachable: boolean;
    kill_switch_active: boolean;
    blocklisted_tags: string[];
    min_required_version: string | null;
    message: string | null;
    error: string | null;
  };
  available: AvailableUpdate[];
}
export async function listUpdates(): Promise<UpdatesResponse> {
  return jsonOrThrow(await authedFetch(`/admin/updates`));
}
export interface ApplyUpdateResponse {
  tag: string;
  ok: boolean;
  detail: string;
  rolled_back: boolean;
  previous_ref: string;
}
export async function applyUpdate(tag: string): Promise<ApplyUpdateResponse> {
  return jsonOrThrow(await authedFetch(`/admin/updates/apply/${encodeURIComponent(tag)}`, { method: "POST" }));
}

// ── Admin: LLM model picker ─────────────────────────────────────────────────
export interface CuratedModel {
  id: string; label: string; backend: string;
  approx_disk_gb: number; approx_ram_gb: number;
  languages: string[]; notes: string;
  active: boolean; downloaded: boolean;
}
export interface ModelsResponse {
  current: string;
  models: CuratedModel[];
  download: { in_flight: string | null; log_tail: string[] };
}
export async function listLlmModels(): Promise<ModelsResponse> {
  return jsonOrThrow(await authedFetch(`/admin/models`));
}
export async function selectLlmModel(model_id: string): Promise<{ accepted: boolean; detail: string }> {
  return jsonOrThrow(await authedFetch(`/admin/models/select`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_id }),
  }));
}

// ── Admin: client installer distribution ─────────────────────────────────────
// IT downloads .dmg / .msi from THIS server, not GitHub. The office Mac
// pulls from GitHub (via gh CLI + deploy key) on a daily schedule and
// caches under storage/installers/. See client_installers.py.
export interface InstallerFile {
  name: string;
  size_bytes: number;
  mtime_iso: string;
  platform: "macOS" | "Windows" | "unknown";
  app: "Worker" | "Manager" | "unknown";
}
export interface InstallerStatus {
  last_tag: string;
  last_pulled_at: number;
  last_pulled_iso: string | null;
  last_status: string;
  last_rebuilt_at: number;
  last_rebuilt_iso: string | null;
  last_rebuild_status: string;
  last_rebuild_detail: string;
  github_repo: string;
  gh_cli_available: boolean;
  swiftc_available: boolean;
}
export interface InstallersListResponse {
  files: InstallerFile[];
  status: InstallerStatus;
  refresh_in_flight: boolean;
  rebuild_in_flight: boolean;
}
export async function listInstallers(): Promise<InstallersListResponse> {
  return jsonOrThrow(await authedFetch(`/admin/installers`));
}
export async function refreshInstallers(): Promise<InstallerStatus & { ok: boolean; detail: string }> {
  return jsonOrThrow(await authedFetch(`/admin/installers/refresh`, { method: "POST" }));
}
export async function rebuildInstallers(): Promise<InstallerStatus & { ok: boolean; detail: string }> {
  return jsonOrThrow(await authedFetch(`/admin/installers/rebuild`, { method: "POST" }));
}
export function installerDownloadUrl(filename: string): string {
  // Returns the URL with admin-key in a query string would be insecure.
  // Use authedFetch via fetch+blob for downloads instead — see the
  // /downloads route's onDownload handler.
  return `${BASE_URL}/admin/installers/${encodeURIComponent(filename)}`;
}
export async function downloadInstaller(filename: string): Promise<Blob> {
  const res = await authedFetch(`/admin/installers/${encodeURIComponent(filename)}`);
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || `HTTP ${res.status}`);
  return res.blob();
}

// ── Admin: users ─────────────────────────────────────────────────────────────
// The API used to return string[] but now ships {name, created_at,
// expires_at} objects. Accept either shape for back-compat — the
// users.tsx panel only renders the name today; richer per-user info
// (TTL, last-rotated) is a follow-up.
export interface UserRecord {
  name: string;
  created_at: string | null;
  expires_at: string | null;
}
export interface UsersListResponse {
  users: Array<string | UserRecord>;
}

export async function listUsers(): Promise<string[]> {
  const data = await jsonOrThrow<UsersListResponse>(await authedFetch(`/admin/users`));
  return (data.users ?? []).map((u) =>
    typeof u === "string" ? u : (u && typeof u.name === "string" ? u.name : String(u))
  );
}

export interface UserKeyResponse {
  name: string;
  api_key: string;
  warning: string;
}

export async function createUser(name: string): Promise<UserKeyResponse> {
  return jsonOrThrow(
    await authedFetch(`/admin/users`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  );
}

export async function deleteUser(name: string): Promise<{ removed: string }> {
  return jsonOrThrow(
    await authedFetch(`/admin/users/${encodeURIComponent(name)}`, { method: "DELETE" }),
  );
}

export async function rotateUserKey(name: string): Promise<UserKeyResponse> {
  return jsonOrThrow(
    await authedFetch(`/admin/users/${encodeURIComponent(name)}/rotate`, { method: "POST" }),
  );
}

// ── Admin: monitor ───────────────────────────────────────────────────────────
export interface AuditEntry {
  timestamp?: string;
  user_hash?: string;
  model?: string;
  sources?: number;
  latency_ms?: number;
  backend?: string;
  query_hash?: string;
  matter_code?: string;
}

export interface DetailedHealth {
  timestamp: string;
  backend: { name: string; reachable: boolean; detail: unknown };
  disk_free_gb: number;
  audit_log: { line_count?: number; size_bytes?: number; last_5?: AuditEntry[]; error?: string };
  watchdog: Record<string, unknown>;
}

export async function getDetailedHealth(): Promise<DetailedHealth> {
  return jsonOrThrow(await authedFetch(`/monitor/health/detailed`));
}

export interface AlertItem {
  level: "info" | "warning" | "critical";
  message: string;
}

export interface AlertsResponse {
  alerts: AlertItem[];
  status: "ok" | "degraded" | "critical";
}

export async function getAlerts(): Promise<AlertsResponse> {
  return jsonOrThrow(await authedFetch(`/monitor/alerts`));
}

// ── Admin: audit export ──────────────────────────────────────────────────────
export interface AuditSummary {
  period: { from: string; to: string };
  total_queries: number;
  by_user: Record<
    string,
    {
      queries: number;
      total_sources: number;
      avg_latency_ms: number;
      matter_codes: string[];
    }
  >;
  generated_at: string;
}

export async function getAuditSummary(fromDate: string, toDate: string): Promise<AuditSummary> {
  const qs = new URLSearchParams({ from_date: fromDate, to_date: toDate });
  return jsonOrThrow(await authedFetch(`/export/summary?${qs}`));
}

export async function downloadAuditCsv(fromDate: string, toDate: string): Promise<Blob> {
  const qs = new URLSearchParams({ from_date: fromDate, to_date: toDate });
  const res = await authedFetch(`/export/?${qs}`);
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.blob();
}

// ── Admin: audit chain verification ──────────────────────────────────────────
// Walks the HMAC chain end-to-end and returns "ok" only if every entry's MAC
// matches its predecessor. "TAMPERED" pinpoints the first broken line so the
// DPO knows where to look. "HMAC_KEY_MISSING" means the verifier never had
// a key to check against — surface that as a setup issue, not a tamper event.
export interface AuditVerifyResult {
  status: "ok" | "TAMPERED" | "HMAC_KEY_MISSING";
  entries: number;
  node_id: string;
  broken_at_line?: number;
  reason?: string;
}

export async function verifyAuditChain(): Promise<AuditVerifyResult> {
  return jsonOrThrow(await authedFetch(`/admin/audit-verify`));
}

// ── Admin: diagnostician ─────────────────────────────────────────────────────
export interface DiagnosticianHistoryResponse {
  entries: Array<{ timestamp?: string; event?: string; detail?: string; raw?: string }>;
}

export async function getDiagnosticianHistory(limit = 50): Promise<DiagnosticianHistoryResponse> {
  return jsonOrThrow(await authedFetch(`/diagnostician/history?limit=${limit}`));
}

export interface PendingFixesResponse {
  pending: Array<{
    id: string;
    code: string;
    description: string;
    suggestion: string;
    error_preview: string;
    status: string;
    created_at: string;
  }>;
}

export async function getPendingFixes(): Promise<PendingFixesResponse> {
  return jsonOrThrow(await authedFetch(`/diagnostician/pending`));
}

// ── DPO compliance snapshot ──────────────────────────────────────────────────
export interface KeyMaterialFinding {
  code: string;
  level: "ok" | "warn" | "fail" | "info";
  message: string;
}

export interface ComplianceSubProcessor {
  name: string;
  role: string;
  observable: string;
  client_data_exposure: string;
  soc2_url?: string;
  soc2_last_reviewed?: string;
}

export interface ComplianceAuditEntry {
  timestamp?: string;
  user_hash?: string;
  model?: string;
  sources?: number;
  latency_ms?: number;
  query_hash?: string;
  matter_code?: string;
}

export interface ComplianceIncidentEntry {
  timestamp?: string;
  event?: string;
  code?: string;
  severity?: string;
  level?: string;
  message?: string;
  detail?: string;
}

export interface ComplianceTrainingSummary {
  total_records: number;
  users_trained: number;
  topics: Record<string, number>;
  last_recorded_at: string | null;
}

export interface ComplianceBackupAttestation {
  id: number;
  test_type: string;
  result: string;
  operator: string;
  tested_at: string;
  notes: string;
}

export interface ComplianceBackupSummary {
  total: number;
  last_5: ComplianceBackupAttestation[];
  last_test_at: string | null;
}

export interface ComplianceDpia {
  version: string;
  generated_at: string | null;
  regulation: string;
  necessity_and_proportionality: Record<string, string>;
  risks_to_rights_and_freedoms: Array<{
    risk: string;
    likelihood: string;
    severity: string;
    mitigations: string[];
  }>;
  controller_sign_off: {
    dpo_name: string;
    dpo_signature_date: string;
    consultation_with_data_subjects: string;
    supervisory_authority_consultation_required: boolean;
  };
}

export interface ComplianceRetentionStream {
  configured_days: number;
  exists: boolean;
  size_bytes?: number;
  oldest_entry_at?: string;
}

export interface ComplianceErasureEvent {
  timestamp?: string;
  pseudonym?: string;
  salt_era?: string;
  billing_redacted_lines?: number;
}

export interface ComplianceBreachBucket {
  severity_code: string;
  count: number;
}

export interface ComplianceSnapshot {
  version: string;
  generated_at: string;
  deployment: {
    deployment_id: string;
    firm_id: string;
    node_id: string;
    region: string;
    version: string;
  };
  ropa: Record<string, unknown>;
  audit_chain: { status: string; entries?: number; node_id?: string; reason?: string };
  key_material: KeyMaterialFinding[];
  sub_processors: ComplianceSubProcessor[];
  telemetry_disclosure: {
    version: string;
    fields: string[];
    never_carries: string[];
    active_allowlist: string[];
  };
  retention_status: Record<string, ComplianceRetentionStream>;
  erasure_log: { total_erasures: number; last_5: ComplianceErasureEvent[] };
  breach_events_30d: ComplianceBreachBucket[];
  snapshot_hmac: string;
  // Added in snapshot version 1.1
  dpia?: ComplianceDpia;
  audit_log_sample?: ComplianceAuditEntry[];
  incident_register_90d?: ComplianceIncidentEntry[];
  training_records?: ComplianceTrainingSummary;
  backup_attestations?: ComplianceBackupSummary;
}

export async function getComplianceSnapshot(): Promise<ComplianceSnapshot> {
  return jsonOrThrow(await authedFetch(`/admin/compliance/snapshot`));
}

export async function downloadComplianceSnapshotHtml(): Promise<Blob> {
  const res = await authedFetch(`/admin/compliance/snapshot?format=html`);
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.blob();
}

// ── Training records (ISO 27001 A.6.3) ──────────────────────────────────────
export interface TrainingRecord {
  id: number;
  user: string;
  topic: string;
  completed_at: string;
  notes: string;
}

export async function listTrainingRecords(): Promise<{ records: TrainingRecord[] }> {
  return jsonOrThrow(await authedFetch(`/admin/training-records`));
}

export async function addTrainingRecord(input: {
  user: string;
  topic: string;
  notes?: string;
  completed_at?: string;
}): Promise<{ record: TrainingRecord }> {
  return jsonOrThrow(
    await authedFetch(`/admin/training-records`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}

export async function deleteTrainingRecord(id: number): Promise<{ deleted: boolean; id: number }> {
  return jsonOrThrow(
    await authedFetch(`/admin/training-records/${id}`, { method: "DELETE" }),
  );
}

// ── Backup test attestations (ISO 27001 A.8.13 / A.8.14) ────────────────────
export interface BackupAttestation {
  id: number;
  test_type: string;
  result: string;
  operator: string;
  tested_at: string;
  notes: string;
}

export async function listBackupAttestations(): Promise<{ records: BackupAttestation[] }> {
  return jsonOrThrow(await authedFetch(`/admin/backup-attestations`));
}

export async function addBackupAttestation(input: {
  test_type: string;
  result: string;
  operator?: string;
  notes?: string;
  tested_at?: string;
}): Promise<{ record: BackupAttestation }> {
  return jsonOrThrow(
    await authedFetch(`/admin/backup-attestations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}

// ── Conflict checks ──────────────────────────────────────────────────────────
export type ConflictRole = "client" | "opposing" | "interested" | "opposing-counsel";
export type ConflictStatus = "clear" | "review" | "conflict";

export interface ConflictParty {
  role: ConflictRole;
  name: string;
}

export interface ConflictHit {
  source: string;
  matter_code: string;
  score: number;
  snippet: string;
  bucket: "strong" | "weak";
}

export interface ConflictCheckResult {
  status: ConflictStatus;
  summary: string;
  key_concerns: string[];
  recommended_action: string;
  hits: ConflictHit[];
  llm_assessment: unknown;
  checked_at: string;
  elapsed_ms: number;
  matter_id: string | null;
}

export interface ConflictCheckRequest {
  parties: ConflictParty[];
  description?: string;
  opposing_counsel?: string[];
  matter_id?: string;
}

export async function runConflictCheck(req: ConflictCheckRequest): Promise<ConflictCheckResult> {
  return jsonOrThrow(
    await authedFetch(`/v1/conflicts/check`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  );
}

export interface ConflictLogEntry {
  timestamp: string;
  matter_id: string | null;
  requester: string;
  parties_hashed: Array<{ role: string; hash: string }>;
  opposing_counsel_hashed: string[];
  status: ConflictStatus;
  summary: string;
  hit_count_strong: number;
  hit_count_weak: number;
  decision: string;
  decided_by: string | null;
  decided_at: string | null;
}

export async function listRecentConflictChecks(limit = 50): Promise<{ checks: ConflictLogEntry[] }> {
  return jsonOrThrow(await authedFetch(`/v1/conflicts/recent?limit=${limit}`));
}

// ── Document comparison ──────────────────────────────────────────────────────
export type CompareSignificance = "high" | "medium" | "low";
export type CompareChangeType = "added" | "removed" | "rewritten" | "whitespace-only";
export type CompareVerdict = "identical" | "minor-changes" | "material-changes";

export interface CompareSection {
  heading_a: string;
  heading_b: string;
  change_type: CompareChangeType;
  diff: string;
  commentary?: {
    summary: string;
    why_matters: string;
    significance: CompareSignificance;
    watch_for: string[];
  };
}

export interface CompareResult {
  label_a: string;
  label_b: string;
  verdict: CompareVerdict;
  summary: string;
  sections: CompareSection[];
  section_count_a: number;
  section_count_b: number;
  llm_calls: number;
  elapsed_ms: number;
}

export interface CompareRequest {
  doc_a?: string;
  doc_b?: string;
  text_a?: string;
  text_b?: string;
  label_a?: string;
  label_b?: string;
}

export async function compareDocuments(req: CompareRequest): Promise<CompareResult> {
  return jsonOrThrow(
    await authedFetch(`/v1/documents/compare`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  );
}

// ── Admin: plugin + MCP-server marketplace ──────────────────────────────────
// Plugins are git-cloned bundles under <install>/plugins/. Each declares a
// list of skills (chat-time tools) and a list of MCP servers it depends on.
// Enable/disable lives in a small JSON state file on the server so the same
// install can be reshaped per firm without touching code.
export interface MarketplacePluginSkill {
  name: string;
  description: string;
}

export interface MarketplacePlugin {
  name: string;
  version: string;
  description: string;
  enabled: boolean;
  skills: MarketplacePluginSkill[];
  mcp_servers: string[];
}

export interface MarketplaceMcpServer {
  name: string;
  enabled: boolean;
  tool_count: number;
}

export interface MarketplaceResponse {
  plugins: MarketplacePlugin[];
  mcp_servers: MarketplaceMcpServer[];
  state_file: string;
}

export async function getMarketplace(): Promise<MarketplaceResponse> {
  return jsonOrThrow(await authedFetch(`/admin/marketplace`));
}

export async function setPluginEnabled(name: string, enabled: boolean): Promise<void> {
  const action = enabled ? "enable" : "disable";
  const res = await authedFetch(`/admin/plugins/${encodeURIComponent(name)}/${action}`, {
    method: "POST",
  });
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || `HTTP ${res.status}`);
}

export async function setMcpServerEnabled(name: string, enabled: boolean): Promise<void> {
  const action = enabled ? "enable" : "disable";
  const res = await authedFetch(`/admin/mcp-servers/${encodeURIComponent(name)}/${action}`, {
    method: "POST",
  });
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || `HTTP ${res.status}`);
}

export { BASE_URL };

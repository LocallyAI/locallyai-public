// Fleet dashboard backend client. The dashboard talks to ONE node (the
// one its URL points at) and that node fans out to the rest. Single-page
// admin tool — keep it minimal, no router, no state library.

const ADMIN_KEY_STORAGE = "locallyai_fleet_admin_key";

const BASE_URL: string =
  ((import.meta as unknown as { env: { VITE_API_BASE_URL?: string } }).env.VITE_API_BASE_URL || "").replace(/\/$/, "") ||
  "https://localhost:8000";

export function getAdminKey(): string | null {
  return localStorage.getItem(ADMIN_KEY_STORAGE);
}
export function setAdminKey(k: string): void {
  localStorage.setItem(ADMIN_KEY_STORAGE, k);
}
export function clearAdminKey(): void {
  localStorage.removeItem(ADMIN_KEY_STORAGE);
}

async function adminGet<T>(path: string): Promise<T> {
  const key = getAdminKey();
  if (!key) throw new Error("Admin key not set");
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { Authorization: `Bearer ${key}` },
    cache: "no-store",
  });
  if (res.status === 401 || res.status === 403) {
    clearAdminKey();
    throw new Error(`Unauthorised (${res.status}) — admin key cleared`);
  }
  if (!res.ok) throw new Error(`HTTP ${res.status} ${path}`);
  return res.json();
}

export interface FleetNode {
  node_id: string;
  hostname: string;
  api_url: string;
  backend: string;
  started_at: string;
  last_seen: string;
  alive: boolean;
}
export interface FleetNodesResp {
  this_node: string;
  active_count: number;
  nodes: FleetNode[];
}
export const getFleetNodes = () => adminGet<FleetNodesResp>("/admin/fleet/nodes");

export interface FleetAuditNode {
  node_id: string;
  status: string;
  entries?: number;
  reason?: string;
  source?: string;
  broken_at_line?: number;
}
export interface FleetAuditResp {
  fleet_status: string;
  nodes: FleetAuditNode[];
}
export const getFleetAudit = () => adminGet<FleetAuditResp>("/admin/fleet/audit-verify");

export interface QdrantHealthResp {
  node_id: string;
  mode: string;
  raft_state?: string | null;
  peer_count?: number;
  peers?: Array<{ id: string; uri: string }>;
  reason?: string;
}
export const getQdrantHealth = () => adminGet<QdrantHealthResp>("/admin/fleet/qdrant-health");

export interface SyncConflict {
  name: string;
  size: number;
  mtime: string;
}
export interface SyncConflictsResp {
  shared_dir: string;
  conflicts: SyncConflict[];
}
export const getSyncConflicts = () => adminGet<SyncConflictsResp>("/admin/fleet/sync-conflicts");

export interface AlertNode {
  node_id: string;
  alerts: unknown;
  unreachable?: string;
}
export interface AlertsResp { nodes: AlertNode[] }
export const getFleetAlerts = () => adminGet<AlertsResp>("/admin/fleet/alerts");

export interface GateStats {
  max_inflight?: number;
  max_queue?: number;
  in_flight?: number;
  queued?: number;
  peak_queue?: number;
  total_admitted?: number;
  total_rejected?: number;
}
export interface GateNode {
  node_id: string;
  gate: GateStats;
  unreachable?: string;
}
export interface GateResp { nodes: GateNode[] }
export const getFleetGate = () => adminGet<GateResp>("/admin/fleet/gate");

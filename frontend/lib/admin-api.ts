import { fetchWithAccessToken } from "./authenticated-fetch.ts";

type Fetcher = typeof fetch;

type SessionLike = { accessToken: string; refreshToken: string };

export class AdminApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(message: string, status: number, code: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export type CurrentUser = {
  id: string;
  email: string;
  display_name: string;
  role: "SUPER_ADMIN" | "OPERATOR" | "CUSTOMER";
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
  organization_names?: string[];
};

export type OperationCenterAssignment = {
  assigned_to_id: string | null;
  assigned_to_name: string | null;
  assigned_at: string | null;
  acknowledged_by_id: string | null;
  acknowledged_at: string | null;
  resolved_by_id: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
  version: number;
};

export type OperationCenterAction =
  | "CANCEL"
  | "RETRY"
  | "ACKNOWLEDGE"
  | "ASSIGN"
  | "RESOLVE_MANUALLY";

export type OperationCenterItem = {
  id: string;
  resource_type: "OPERATION" | "PROVISIONING";
  operation_type: string;
  action: string;
  status: string;
  cluster_id: string;
  cluster_name: string;
  organization_id: string | null;
  organization_name: string | null;
  requested_by_id: string;
  requested_by_name: string;
  workload_id: string | null;
  workload_name: string | null;
  current_step: string | null;
  error_code: string | null;
  error_summary: string | null;
  retryable: boolean;
  retry_of_id: string | null;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at: string | null;
  is_stuck: boolean;
  available_actions: OperationCenterAction[];
  impact_summary: string;
  recommended_action: string;
  assignment: OperationCenterAssignment | null;
  version: number;
};

export type OperationCenterDetail = OperationCenterItem & {
  events: {
    id: number;
    event_type: string;
    status: string | null;
    step: string | null;
    message: string;
    details: Record<string, unknown>;
    actor_user_id: string | null;
    occurred_at: string;
  }[];
  pve_tasks: {
    step_name: string;
    status: string;
    upid_reference: string;
    pve_exit_status: string | null;
    poll_attempts: number;
    error_code: string | null;
    submitted_at: string;
    last_polled_at: string | null;
    completed_at: string | null;
  }[];
  provisioning_steps: {
    order: number;
    name: string;
    status: string;
    attempt_count: number;
    upid_reference: string | null;
    error_code: string | null;
    started_at: string | null;
    finished_at: string | null;
  }[];
  related_audit_count: number;
  related_backup_ids: string[];
};

export type OperationCenterFilters = {
  status?: string;
  operationType?: string;
  errorCode?: string;
};

export type UserCreateInput = {
  email: string;
  display_name: string;
  role: CurrentUser["role"];
  password: string;
};

export class OrganizationUserProvisionError extends Error {
  readonly createdUser: CurrentUser;
  readonly assignmentError: unknown;

  constructor(createdUser: CurrentUser, assignmentError: unknown) {
    super("The user was created, but could not be added to the organization.");
    this.name = "OrganizationUserProvisionError";
    this.createdUser = createdUser;
    this.assignmentError = assignmentError;
  }
}

export type Organization = {
  id: string;
  name: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  version: number;
};

export type OrganizationPage = {
  items: Organization[];
  total: number;
  limit: number | null;
  offset: number;
};

export type OrganizationSearchFilters = {
  q?: string;
  status?: "active" | "inactive" | "all";
  sort?: "newest" | "oldest" | "name";
  limit?: number;
  offset?: number;
};

export type OrganizationMember = {
  id: string;
  organization_id: string;
  user_id: string;
  email: string;
  display_name: string;
  role: CurrentUser["role"];
  is_active: boolean;
  created_at: string;
};

export type Workload = {
  id: string;
  cluster_id: string;
  cluster_name: string;
  vmid: number;
  node: string;
  kind: "QEMU" | "LXC";
  name: string | null;
  power_state: string;
  cpu_cores: number | null;
  memory_bytes: number | null;
  disk_bytes: number | null;
  is_template: boolean;
  is_present: boolean;
  sync_generation?: number;
  missing_since?: string | null;
  organization_id: string | null;
  organization_name: string | null;
  assigned_ip_addresses?: string[];
  observed_at: string;
  is_stale?: boolean;
  stale_reason?: string | null;
  version: number;
};

export type WorkloadAssignment = {
  id: string;
  workload_id: string;
  organization_id: string;
  organization_name: string;
  assigned_by_id: string;
  assigned_at: string;
  revoked_by_id: string | null;
  revoked_at: string | null;
  revoke_reason: string | null;
};

export type BackupStorageCandidate = {
  cluster_id: string;
  cluster_name: string;
  storage_id: string;
  datastore: string | null;
  namespace: string | null;
  available: boolean;
  enabled_in_pve: boolean;
  registered_target_id: string | null;
};

export type BackupTarget = {
  id: string;
  cluster_id: string;
  cluster_name: string;
  storage_id: string;
  datastore: string | null;
  namespace: string | null;
  is_enabled: boolean;
  available: boolean;
  last_checked_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
};

export type BackupRun = {
  id: string;
  operation_id: string;
  backup_target_id: string;
  cluster_id: string;
  cluster_name: string;
  storage_id: string;
  workload_id: string;
  workload_name: string | null;
  vmid: number;
  kind: "QEMU" | "LXC";
  source_node: string;
  organization_id: string | null;
  organization_name: string | null;
  mode: string;
  compression: string;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "TIMEOUT";
  snapshot_volume_id: string | null;
  snapshot_time: string | null;
  size_bytes: number | null;
  transferred_bytes: number | null;
  error_code: string | null;
  error_summary: string | null;
  retryable: boolean | null;
  pve_exit_status: string | null;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type RestoreRun = {
  id: string;
  operation_id: string;
  backup_run_id: string;
  cluster_id: string;
  cluster_name: string;
  source_workload_id: string;
  source_workload_name: string | null;
  kind: "QEMU" | "LXC";
  snapshot_volume_id: string;
  target_node: string;
  target_vmid: number;
  target_name: string;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "TIMEOUT";
  error_code: string | null;
  error_summary: string | null;
  retryable: boolean | null;
  pve_exit_status: string | null;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type Cluster = {
  id: string;
  name: string;
  api_base_url: string;
  is_active: boolean;
  ca_configured: boolean;
  last_connection_error_code: string | null;
  last_connected_at: string | null;
  last_sync_succeeded_at?: string | null;
  sync_interval_seconds?: number;
  inventory_stale?: boolean;
  credential: { token_identifier: string; configured: boolean; last_used_at: string | null };
  created_at: string;
  updated_at: string;
  version: number;
};

export type ClusterRemovalBlock = {
  code: string;
  count: number;
};

export type ClusterRemovalCheck = {
  cluster_id: string;
  can_remove: boolean;
  blocks: ClusterRemovalBlock[];
};

export type ClusterNode = {
  node: string;
  status: string | null;
  cpu: number | null;
  maxcpu: number | null;
  mem: number | null;
  maxmem: number | null;
  uptime: number | null;
};

export type ClusterGuest = {
  vmid: number;
  node: string | null;
  type: string;
  name: string | null;
  status: string | null;
  cpu: number | null;
  maxcpu: number | null;
  mem: number | null;
  maxmem: number | null;
  disk: number | null;
  maxdisk: number | null;
  uptime: number | null;
  template: number | boolean | null;
};

export type ClusterStorage = {
  storage: string;
  node: string | null;
  type: string | null;
  status: string | null;
  total: number | null;
  used: number | null;
  avail: number | null;
  shared: number | boolean | null;
  content: string | null;
};

export type ClusterResourceOverview = {
  cluster_id: string;
  name: string;
  connected: boolean;
  observed_at: string;
  error_code: string | null;
  node_count: number;
  guest_count: number;
  running_guest_count: number;
  qemu_count: number;
  lxc_count: number;
  storage_count: number;
  storage_used_bytes: number;
  storage_total_bytes: number;
  vm_storage_count: number;
  vm_storage_used_bytes: number;
  vm_storage_total_bytes: number;
  nodes: Array<{
    node: string;
    status: string | null;
    cpu: number | null;
    maxcpu: number | null;
    memory_used_bytes: number | null;
    memory_total_bytes: number | null;
    disk_used_bytes: number | null;
    disk_total_bytes: number | null;
    load_average: number[];
    uptime_seconds: number | null;
  }>;
};

export type NodeMetricRange = "hour" | "six_hours" | "day" | "week";

export type NodeMetricPoint = {
  time: number;
  cpu_usage: number | null;
  server_load: number | null;
  memory_used_bytes: number | null;
  memory_total_bytes: number | null;
  network_receive_bps: number | null;
  network_transmit_bps: number | null;
  cpu_pressure_some: number | null;
  io_pressure_some: number | null;
  io_pressure_full: number | null;
  memory_pressure_some: number | null;
  memory_pressure_full: number | null;
};

export type NodeMetricSeries = {
  cluster_id: string;
  node: string;
  range: NodeMetricRange;
  observed_at: string;
  items: NodeMetricPoint[];
};

export type OperationsStatus = {
  status: string;
  worker: { available: boolean; alive: boolean; workers: string[]; stale_after_seconds: number };
  queue: { available: boolean; total: number; queues: Record<string, number>; backlog_threshold: number };
  workloads: { total: number; assigned: number; unassigned: number };
  directory: {
    organizations: { total: number; active: number };
    users: { total: number; active: number };
  };
  clusters: Array<{
    cluster_id: string;
    name: string;
    enabled: boolean;
    connected: boolean;
    last_connected_at: string | null;
    error_code: string | null;
  }>;
  scheduler?: Array<{
    job_name: string;
    status: string;
    last_started_at: string;
    last_finished_at: string | null;
    last_success_at: string | null;
    processed_count: number;
    error_code: string | null;
  }>;
  open_reconciliation_findings?: number;
  stale_inventory_clusters?: number;
  alerts: Array<{
    code: string;
    severity: string;
    resource_type: string;
    resource_id: string | null;
    message: string;
    value: number | null;
    threshold: number | null;
  }>;
};

export type InventoryFreshness = {
  cluster_id: string;
  cluster_name: string;
  last_full_success_at: string | null;
  stale_after_seconds: number;
  is_stale: boolean;
  stale_reason: string | null;
  latest_status: string | null;
};

export type InventorySyncRun = {
  id: string;
  operation_id: string;
  cluster_id: string;
  cluster_name: string;
  generation: number;
  scope: string;
  status: string;
  partial_failure: boolean;
  triggered_by: string;
  requested_by_id: string | null;
  target_workload_id: string | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  error_code: string | null;
  resource_counts: Record<string, unknown>;
};

export type ReconciliationFinding = {
  id: string;
  kind: string;
  severity: "INFO" | "WARNING" | "CRITICAL";
  status: "OPEN" | "ACKNOWLEDGED" | "RESOLVED";
  cluster_id: string;
  cluster_name: string;
  workload_id: string | null;
  sync_run_id: string | null;
  target_type: string;
  target_id: string;
  summary: string;
  details: Record<string, unknown>;
  first_observed_at: string;
  last_observed_at: string;
  acknowledged_by_id: string | null;
  acknowledged_at: string | null;
  assigned_to_id: string | null;
  resolved_by_id: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
};

export type IpPool = {
  id: string;
  name: string;
  cluster_id: string | null;
  cidr: string;
  prefix_length: number;
  gateway: string | null;
  dns_servers: string[];
  bridge: string;
  vlan_tag: number | null;
  ip_family: number;
  allocation_strategy: string;
  quarantine_seconds: number;
  is_active: boolean;
  allocated_count: number;
  quarantined_count: number;
  availability_status: string;
  version: number;
};

export type Product = {
  id: string;
  name: string;
  cpu_cores: number;
  memory_bytes: number;
  disk_bytes: number;
  is_enabled: boolean;
};

export type Template = {
  id: string;
  name: string;
  source_workload_id: string;
  source_disk: string;
  default_storage: string;
  default_bridge: string;
  default_vlan_tag: number | null;
  cloud_init_enabled: boolean;
  linux_only: boolean;
  is_enabled: boolean;
};

export type ProvisionRequest = {
  id: string;
  job_id: string;
  status: string;
  current_step: string;
  target_name: string;
  target_vmid: number | null;
  ip_address: string | null;
  error_code: string | null;
  requested_at: string;
  steps: Array<{ name: string; status: string; attempt_count: number }>;
};

export type ProvisioningNode = {
  id: string;
  cluster_id: string;
  name: string;
  is_enabled: boolean;
  is_maintenance: boolean;
  available_memory_bytes: number;
  available_storage_bytes: number;
  last_selected_at: string | null;
};

export type AdminWorkloadJob = {
  id: string;
  job_id: string;
  vm_id: string;
  workload_id: string;
  action: "start" | "shutdown" | "stop" | "reboot" | "reset" | "update_spec" | "delete" | "backup";
  action_mode: "STANDARD" | "GRACEFUL" | "FORCED" | "CONFIGURATION" | "DESTRUCTIVE" | "BACKUP";
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "TIMEOUT";
  error_code: string | null;
  error_summary: string | null;
  requested_at: string;
  finished_at: string | null;
};

/** @deprecated Use AdminWorkloadJob. */
export type AdminVmJob = AdminWorkloadJob;

export type AuditLog = {
  id: string;
  actor_user_id: string | null;
  actor_role: string | null;
  actor_display_name: string | null;
  actor_email: string | null;
  organization_id: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  workload_name: string | null;
  workload_vmid: number | null;
  workload_kind: string | null;
  workload_node: string | null;
  workload_cluster_name: string | null;
  source_ip: string | null;
  request_id: string | null;
  result: string;
  error_code: string | null;
  created_at: string;
};

async function api<T>(
  apiBaseUrl: string,
  path: string,
  accessToken: string,
  init: RequestInit = {},
  fetcher: Fetcher = fetch,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}${path}`,
    accessToken,
    { ...init, headers },
    fetcher,
  );
  if (response.status === 204) return undefined as T;
  const body = (await response.json()) as T & { error?: { code?: string; message?: string } };
  if (!response.ok) {
    throw new AdminApiError(
      body.error?.message ?? "관리자 요청을 처리하지 못했습니다.",
      response.status,
      body.error?.code ?? "REQUEST_FAILED",
    );
  }
  return body;
}

export const getMe = (base: string, token: string, fetcher?: Fetcher) =>
  api<CurrentUser>(base, "/api/v1/auth/me", token, {}, fetcher);

export const getOperationsStatus = (base: string, token: string, fetcher?: Fetcher) =>
  api<OperationsStatus>(base, "/api/v1/admin/operations/status", token, {}, fetcher);

export async function listClusters(base: string, token: string, fetcher?: Fetcher) {
  const response = await api<{ items: Cluster[] }>(base, "/api/v1/admin/clusters", token, {}, fetcher);
  return response.items.filter((cluster) => cluster.is_active);
}

export async function getClusterResourceOverview(
  base: string,
  token: string,
  fetcher?: Fetcher,
) {
  const response = await api<{ items: ClusterResourceOverview[] }>(
    base,
    "/api/v1/admin/clusters/overview",
    token,
    {},
    fetcher,
  );
  return response.items;
}

export const getNodeMetrics = (
  base: string,
  token: string,
  clusterId: string,
  node: string,
  range: NodeMetricRange,
  signal?: AbortSignal,
  fetcher?: Fetcher,
) => api<NodeMetricSeries>(
  base,
  `/api/v1/admin/clusters/${encodeURIComponent(clusterId)}/nodes/${encodeURIComponent(node)}/metrics?range=${range}`,
  token,
  { signal },
  fetcher,
);

export const createCluster = (
  base: string,
  token: string,
  payload: Record<string, unknown>,
  fetcher?: Fetcher,
) => api<Cluster>(base, "/api/v1/admin/clusters", token, { method: "POST", body: JSON.stringify(payload) }, fetcher);

export const deleteCluster = (base: string, token: string, id: string, fetcher?: Fetcher) =>
  api<void>(base, `/api/v1/admin/clusters/${encodeURIComponent(id)}`, token, { method: "DELETE" }, fetcher);

export const getClusterRemovalCheck = (base: string, token: string, id: string, fetcher?: Fetcher) =>
  api<ClusterRemovalCheck>(base, `/api/v1/admin/clusters/${encodeURIComponent(id)}/removal-check`, token, {}, fetcher);

export const testCluster = (base: string, token: string, id: string, fetcher?: Fetcher) =>
  api<{ reachable: boolean; tls_valid: boolean; authenticated: boolean; version: string | null }>(
    base, `/api/v1/admin/clusters/${encodeURIComponent(id)}/test`, token, { method: "POST" }, fetcher,
  );

export const importClusterWorkloads = (
  base: string,
  token: string,
  id: string,
  fetcher?: Fetcher,
) => api<{ cluster_id: string; discovered: number; created: number; updated: number }>(
  base,
  `/api/v1/admin/clusters/${encodeURIComponent(id)}/workloads/import`,
  token,
  { method: "POST" },
  fetcher,
);

export const requestClusterSync = (
  base: string,
  token: string,
  id: string,
  fetcher?: Fetcher,
) => api<{ operation_id: string; status: string }>(
  base,
  `/api/v1/admin/clusters/${encodeURIComponent(id)}/sync`,
  token,
  { method: "POST" },
  fetcher,
);

export async function listInventoryFreshness(base: string, token: string, fetcher?: Fetcher) {
  return (await api<{ items: InventoryFreshness[] }>(
    base,
    "/api/v1/admin/inventory/freshness",
    token,
    {},
    fetcher,
  )).items;
}

export async function listInventorySyncRuns(base: string, token: string, fetcher?: Fetcher) {
  return (await api<{ items: InventorySyncRun[] }>(
    base,
    "/api/v1/admin/inventory/sync-runs?limit=50",
    token,
    {},
    fetcher,
  )).items;
}

export async function listReconciliationFindings(
  base: string,
  token: string,
  fetcher?: Fetcher,
) {
  return (await api<{ items: ReconciliationFinding[] }>(
    base,
    "/api/v1/admin/inventory/reconciliation/findings?limit=100",
    token,
    {},
    fetcher,
  )).items;
}

export const acknowledgeReconciliationFinding = (
  base: string,
  token: string,
  findingId: string,
  assignedToId: string | null = null,
  fetcher?: Fetcher,
) => api<ReconciliationFinding>(
  base,
  `/api/v1/admin/inventory/reconciliation/findings/${encodeURIComponent(findingId)}/acknowledge`,
  token,
  { method: "POST", body: JSON.stringify({ assigned_to_id: assignedToId }) },
  fetcher,
);

export const resolveReconciliationFinding = (
  base: string,
  token: string,
  findingId: string,
  resolutionNote: string,
  fetcher?: Fetcher,
) => api<ReconciliationFinding>(
  base,
  `/api/v1/admin/inventory/reconciliation/findings/${encodeURIComponent(findingId)}/resolve`,
  token,
  { method: "POST", body: JSON.stringify({ resolution_note: resolutionNote }) },
  fetcher,
);

async function clusterItems<T>(base: string, token: string, id: string, kind: string, fetcher?: Fetcher) {
  return (await api<{ items: T[] }>(base, `/api/v1/admin/clusters/${encodeURIComponent(id)}/${kind}`, token, {}, fetcher)).items;
}

export const getClusterInventory = async (base: string, token: string, id: string, fetcher?: Fetcher) => {
  const [nodes, guests, storages] = await Promise.all([
    clusterItems<ClusterNode>(base, token, id, "nodes", fetcher),
    clusterItems<ClusterGuest>(base, token, id, "guests", fetcher),
    clusterItems<ClusterStorage>(base, token, id, "storages", fetcher),
  ]);
  return { nodes, guests, storages };
};

export async function listUsers(base: string, token: string, fetcher?: Fetcher) {
  return (await api<{ items: CurrentUser[] }>(base, "/api/v1/admin/users", token, {}, fetcher)).items;
}

export const createUser = (base: string, token: string, payload: UserCreateInput, fetcher?: Fetcher) =>
  api<CurrentUser>(base, "/api/v1/admin/users", token, { method: "POST", body: JSON.stringify(payload) }, fetcher);

export const resetUserPassword = (
  base: string,
  token: string,
  userId: string,
  newPassword: string,
  fetcher?: Fetcher,
) => api<void>(
  base,
  `/api/v1/admin/users/${encodeURIComponent(userId)}/reset-password`,
  token,
  { method: "POST", body: JSON.stringify({ new_password: newPassword }) },
  fetcher,
);

export const updateUserStatus = (
  base: string,
  token: string,
  userId: string,
  isActive: boolean,
  version: number,
  fetcher?: Fetcher,
) => api<CurrentUser>(
  base,
  `/api/v1/admin/users/${encodeURIComponent(userId)}`,
  token,
  { method: "PATCH", body: JSON.stringify({ is_active: isActive, version }) },
  fetcher,
);

export const deleteUser = (
  base: string,
  token: string,
  userId: string,
  version: number,
  fetcher?: Fetcher,
) => api<void>(
  base,
  `/api/v1/admin/users/${encodeURIComponent(userId)}?version=${version}`,
  token,
  { method: "DELETE" },
  fetcher,
);

export async function listOrganizations(base: string, token: string, fetcher?: Fetcher) {
  return (await api<{ items: Organization[] }>(base, "/api/v1/admin/organizations", token, {}, fetcher)).items;
}

export async function searchOrganizations(
  base: string,
  token: string,
  filters: OrganizationSearchFilters = {},
  fetcher?: Fetcher,
) {
  const query = new URLSearchParams();
  const normalizedQuery = filters.q?.trim();
  if (normalizedQuery) query.set("q", normalizedQuery);
  if (filters.status) query.set("status", filters.status);
  if (filters.sort) query.set("sort", filters.sort);
  if (filters.limit !== undefined) query.set("limit", String(filters.limit));
  if (filters.offset !== undefined) query.set("offset", String(filters.offset));
  const suffix = query.size ? `?${query.toString()}` : "";
  return api<OrganizationPage>(
    base,
    `/api/v1/admin/organizations${suffix}`,
    token,
    {},
    fetcher,
  );
}

export const createOrganization = (base: string, token: string, name: string, fetcher?: Fetcher) =>
  api<Organization>(base, "/api/v1/admin/organizations", token, { method: "POST", body: JSON.stringify({ name }) }, fetcher);

export const updateOrganization = (base: string, token: string, organizationId: string, name: string, version: number, fetcher?: Fetcher) =>
  api<Organization>(base, `/api/v1/admin/organizations/${encodeURIComponent(organizationId)}`, token, { method: "PATCH", body: JSON.stringify({ name, version }) }, fetcher);

export const activateOrganization = (base: string, token: string, organizationId: string, version: number, fetcher?: Fetcher) =>
  api<Organization>(base, `/api/v1/admin/organizations/${encodeURIComponent(organizationId)}`, token, { method: "PATCH", body: JSON.stringify({ is_active: true, version }) }, fetcher);

export const deleteOrganization = (base: string, token: string, organizationId: string, version: number, fetcher?: Fetcher) =>
  api<void>(base, `/api/v1/admin/organizations/${encodeURIComponent(organizationId)}?version=${version}`, token, { method: "DELETE" }, fetcher);

export async function listOrganizationMembers(
  base: string,
  token: string,
  organizationId: string,
  fetcher?: Fetcher,
) {
  return (await api<{ items: OrganizationMember[] }>(
    base,
    `/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/members`,
    token,
    {},
    fetcher,
  )).items;
}

export const addOrganizationMember = (
  base: string,
  token: string,
  organizationId: string,
  userId: string,
  fetcher?: Fetcher,
) => api<{ id: string }>(
  base,
  `/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/members`,
  token,
  { method: "POST", body: JSON.stringify({ user_id: userId }) },
  fetcher,
);

export async function createOrganizationUser(
  base: string,
  token: string,
  organizationId: string,
  payload: UserCreateInput,
  fetcher?: Fetcher,
) {
  const createdUser = await createUser(base, token, payload, fetcher);
  try {
    await addOrganizationMember(base, token, organizationId, createdUser.id, fetcher);
  } catch (error) {
    throw new OrganizationUserProvisionError(createdUser, error);
  }
  return createdUser;
}

export const removeOrganizationMember = (
  base: string,
  token: string,
  organizationId: string,
  userId: string,
  fetcher?: Fetcher,
) => api<void>(
  base,
  `/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/members/${encodeURIComponent(userId)}`,
  token,
  { method: "DELETE" },
  fetcher,
);

export async function listWorkloads(
  base: string,
  token: string,
  filters: { organizationId?: string; clusterId?: string } = {},
  fetcher?: Fetcher,
) {
  const query = new URLSearchParams();
  if (filters.organizationId) query.set("organization_id", filters.organizationId);
  if (filters.clusterId) query.set("cluster_id", filters.clusterId);
  const suffix = query.size ? `?${query}` : "";
  return (await api<{ items: Workload[] }>(
    base,
    `/api/v1/admin/workloads${suffix}`,
    token,
    {},
    fetcher,
  )).items;
}

export const getWorkload = (
  base: string,
  token: string,
  workloadId: string,
  fetcher?: Fetcher,
) => api<Workload>(
  base,
  `/api/v1/admin/workloads/${encodeURIComponent(workloadId)}`,
  token,
  {},
  fetcher,
);

export const assignWorkload = (
  base: string,
  token: string,
  workloadId: string,
  organizationId: string,
  fetcher?: Fetcher,
) => api<WorkloadAssignment>(
  base,
  `/api/v1/admin/workloads/${encodeURIComponent(workloadId)}/assign`,
  token,
  { method: "POST", body: JSON.stringify({ organization_id: organizationId }) },
  fetcher,
);

export const unassignWorkload = (
  base: string,
  token: string,
  workloadId: string,
  fetcher?: Fetcher,
) => api<void>(
  base,
  `/api/v1/admin/workloads/${encodeURIComponent(workloadId)}/assignment`,
  token,
  { method: "DELETE" },
  fetcher,
);

export async function discoverBackupStorages(
  base: string,
  token: string,
  clusterId: string,
  fetcher?: Fetcher,
) {
  return (await api<{ items: BackupStorageCandidate[] }>(
    base,
    `/api/v1/admin/clusters/${encodeURIComponent(clusterId)}/backup-storages`,
    token,
    {},
    fetcher,
  )).items;
}

export async function listBackupTargets(base: string, token: string, fetcher?: Fetcher) {
  return (await api<{ items: BackupTarget[] }>(
    base,
    "/api/v1/admin/backup-targets",
    token,
    {},
    fetcher,
  )).items;
}

export const createBackupTarget = (
  base: string,
  token: string,
  clusterId: string,
  storageId: string,
  fetcher?: Fetcher,
) => api<BackupTarget>(base, "/api/v1/admin/backup-targets", token, {
  method: "POST",
  body: JSON.stringify({ cluster_id: clusterId, storage_id: storageId }),
}, fetcher);

export const updateBackupTarget = (
  base: string,
  token: string,
  target: BackupTarget,
  isEnabled: boolean,
  fetcher?: Fetcher,
) => api<BackupTarget>(
  base,
  `/api/v1/admin/backup-targets/${encodeURIComponent(target.id)}`,
  token,
  {
    method: "PATCH",
    body: JSON.stringify({ is_enabled: isEnabled, version: target.version }),
  },
  fetcher,
);

export async function listBackups(base: string, token: string, fetcher?: Fetcher) {
  return (await api<{ items: BackupRun[] }>(
    base,
    "/api/v1/admin/backups",
    token,
    {},
    fetcher,
  )).items;
}

export const requestWorkloadBackup = (
  base: string,
  token: string,
  workloadId: string,
  backupTargetId: string,
  idempotencyKey: string,
  fetcher?: Fetcher,
) => api<BackupRun>(
  base,
  `/api/v1/admin/workloads/${encodeURIComponent(workloadId)}/backups`,
  token,
  {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ backup_target_id: backupTargetId }),
  },
  fetcher,
);

export const getBackup = (
  base: string,
  token: string,
  runId: string,
  fetcher?: Fetcher,
) => api<BackupRun>(
  base,
  `/api/v1/admin/backups/${encodeURIComponent(runId)}`,
  token,
  {},
  fetcher,
);

export const requestBackupRestore = (
  base: string,
  token: string,
  runId: string,
  payload: { target_node: string; target_vmid: number; target_name: string },
  idempotencyKey: string,
  fetcher?: Fetcher,
) => api<RestoreRun>(
  base,
  `/api/v1/admin/backups/${encodeURIComponent(runId)}/restores`,
  token,
  {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(payload),
  },
  fetcher,
);

export const getRestore = (
  base: string,
  token: string,
  restoreId: string,
  fetcher?: Fetcher,
) => api<RestoreRun>(
  base,
  `/api/v1/admin/restores/${encodeURIComponent(restoreId)}`,
  token,
  {},
  fetcher,
);

export async function listIpPools(base: string, token: string, fetcher?: Fetcher) {
  return (await api<{ items: IpPool[] }>(base, "/api/v1/admin/ip-pools", token, {}, fetcher)).items;
}

export const createIpPool = (base: string, token: string, payload: Record<string, unknown>, fetcher?: Fetcher) =>
  api<IpPool>(base, "/api/v1/admin/ip-pools", token, { method: "POST", body: JSON.stringify(payload) }, fetcher);

export const updateIpPool = (base: string, token: string, poolId: string, payload: Record<string, unknown>, fetcher?: Fetcher) =>
  api<IpPool>(base, `/api/v1/admin/ip-pools/${encodeURIComponent(poolId)}`, token, { method: "PATCH", body: JSON.stringify(payload) }, fetcher);

export const deleteIpPool = (base: string, token: string, poolId: string, version: number, fetcher?: Fetcher) =>
  api<void>(base, `/api/v1/admin/ip-pools/${encodeURIComponent(poolId)}?version=${version}`, token, { method: "DELETE" }, fetcher);

export async function listProducts(base: string, token: string, fetcher?: Fetcher) {
  return (await api<{ items: Product[] }>(base, "/api/v1/admin/products", token, {}, fetcher)).items;
}

export const createProduct = (base: string, token: string, payload: Record<string, unknown>, fetcher?: Fetcher) =>
  api<Product>(base, "/api/v1/admin/products", token, { method: "POST", body: JSON.stringify(payload) }, fetcher);

export const updateProduct = (base: string, token: string, productId: string, payload: Record<string, unknown>, fetcher?: Fetcher) =>
  api<Product>(base, `/api/v1/admin/products/${productId}`, token, { method: "PATCH", body: JSON.stringify(payload) }, fetcher);

export const deleteProduct = (base: string, token: string, productId: string, fetcher?: Fetcher) =>
  api<void>(base, `/api/v1/admin/products/${productId}`, token, { method: "DELETE" }, fetcher);

export async function listTemplates(base: string, token: string, fetcher?: Fetcher) {
  return (await api<{ items: Template[] }>(base, "/api/v1/admin/templates", token, {}, fetcher)).items;
}

export const createTemplate = (base: string, token: string, payload: Record<string, unknown>, fetcher?: Fetcher) =>
  api<Template>(base, "/api/v1/admin/templates", token, { method: "POST", body: JSON.stringify(payload) }, fetcher);

export const updateTemplate = (base: string, token: string, templateId: string, payload: Record<string, unknown>, fetcher?: Fetcher) =>
  api<Template>(base, `/api/v1/admin/templates/${templateId}`, token, { method: "PATCH", body: JSON.stringify(payload) }, fetcher);

export const deleteTemplate = (base: string, token: string, templateId: string, fetcher?: Fetcher) =>
  api<void>(base, `/api/v1/admin/templates/${templateId}`, token, { method: "DELETE" }, fetcher);

export async function listProvisioningNodes(base: string, token: string, fetcher?: Fetcher) {
  return (await api<{ items: ProvisioningNode[] }>(base, "/api/v1/admin/provisioning-nodes", token, {}, fetcher)).items;
}

export const upsertProvisioningNode = (base: string, token: string, payload: Record<string, unknown>, fetcher?: Fetcher) =>
  api<ProvisioningNode>(base, "/api/v1/admin/provisioning-nodes", token, { method: "PUT", body: JSON.stringify(payload) }, fetcher);

export const createProvisionRequest = (
  base: string,
  token: string,
  payload: Record<string, unknown>,
  idempotencyKey: string,
  fetcher?: Fetcher,
) => api<ProvisionRequest>(base, "/api/v1/admin/provision-requests", token, {
  method: "POST",
  headers: { "Idempotency-Key": idempotencyKey },
  body: JSON.stringify(payload),
}, fetcher);

export async function listProvisionRequests(base: string, token: string, fetcher?: Fetcher) {
  return (await api<{ items: ProvisionRequest[] }>(base, "/api/v1/admin/provision-requests", token, {}, fetcher)).items;
}

export async function listOperationCenter(
  base: string,
  token: string,
  filters: OperationCenterFilters = {},
  fetcher?: Fetcher,
) {
  const query = new URLSearchParams();
  if (filters.status) query.set("status", filters.status);
  if (filters.operationType) query.set("operation_type", filters.operationType);
  if (filters.errorCode) query.set("error_code", filters.errorCode);
  const suffix = query.size ? `?${query.toString()}` : "";
  return api<{ items: OperationCenterItem[]; total: number; limit: number; offset: number }>(
    base,
    `/api/v1/admin/operations${suffix}`,
    token,
    {},
    fetcher,
  );
}

export const getOperationCenterDetail = (
  base: string,
  token: string,
  operationId: string,
  fetcher?: Fetcher,
) => api<OperationCenterDetail>(
  base,
  `/api/v1/admin/operations/${encodeURIComponent(operationId)}`,
  token,
  {},
  fetcher,
);

export async function runOperationCenterAction(
  base: string,
  token: string,
  operation: OperationCenterItem,
  action: OperationCenterAction,
  options: { assignedToId?: string; resolutionNote?: string } = {},
  fetcher?: Fetcher,
) {
  const paths: Record<OperationCenterAction, string> = {
    CANCEL: "cancel",
    RETRY: "retry",
    ACKNOWLEDGE: "acknowledge",
    ASSIGN: "assign",
    RESOLVE_MANUALLY: "resolve-manually",
  };
  const payload: Record<string, unknown> = { version: operation.version };
  if (options.assignedToId) payload.assigned_to_id = options.assignedToId;
  if (options.resolutionNote) payload.resolution_note = options.resolutionNote;
  return api<OperationCenterItem | {
    operation: OperationCenterItem;
    created_operation_id: string;
  }>(
    base,
    `/api/v1/admin/operations/${encodeURIComponent(operation.id)}/${paths[action]}`,
    token,
    { method: "POST", body: JSON.stringify(payload) },
    fetcher,
  );
}

export const requestAdminWorkloadAction = (
  base: string,
  token: string,
  workloadId: string,
  action: AdminWorkloadJob["action"],
  idempotencyKey: string,
  fetcher?: Fetcher,
) => api<AdminWorkloadJob>(
  base,
  `/api/v1/admin/workloads/${encodeURIComponent(workloadId)}/actions/${action}`,
  token,
  {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({}),
  },
  fetcher,
);

/** @deprecated Use requestAdminWorkloadAction. */
export const requestAdminVmAction = requestAdminWorkloadAction;

export const updateAdminVmSpec = (
  base: string,
  token: string,
  vmId: string,
  payload: Record<string, unknown>,
  idempotencyKey: string,
  fetcher?: Fetcher,
) => api<AdminVmJob>(base, `/api/v1/admin/vms/${encodeURIComponent(vmId)}/spec`, token, {
  method: "PATCH",
  headers: { "Idempotency-Key": idempotencyKey },
  body: JSON.stringify(payload),
}, fetcher);

export const deleteAdminVm = (
  base: string,
  token: string,
  vmId: string,
  payload: Record<string, unknown>,
  idempotencyKey: string,
  fetcher?: Fetcher,
) => api<AdminVmJob>(base, `/api/v1/admin/vms/${encodeURIComponent(vmId)}`, token, {
  method: "DELETE",
  headers: { "Idempotency-Key": idempotencyKey },
  body: JSON.stringify(payload),
}, fetcher);

export const getAdminVmJob = (base: string, token: string, jobId: string, fetcher?: Fetcher) =>
  api<AdminVmJob>(base, `/api/v1/jobs/${encodeURIComponent(jobId)}`, token, {}, fetcher);

export const getAdminWorkloadJob = (
  base: string,
  token: string,
  jobId: string,
  fetcher?: Fetcher,
) => api<AdminWorkloadJob>(base, `/api/v1/jobs/${encodeURIComponent(jobId)}`, token, {}, fetcher);

export async function listAuditLogs(
  base: string,
  token: string,
  fetcher?: Fetcher,
  page: { limit?: number; offset?: number } = {},
) {
  const limit = page.limit ?? 100;
  const offset = page.offset ?? 0;
  return await api<{ items: AuditLog[]; total: number; limit: number; offset: number }>(
    base, `/api/v1/admin/audit-logs?limit=${limit}&offset=${offset}`, token, {}, fetcher,
  );
}

export async function endSession(base: string, session: SessionLike, fetcher?: Fetcher) {
  await (fetcher ?? fetch)(`${base}/api/v1/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: session.refreshToken }),
  });
}

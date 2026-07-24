import type { Page, Route } from "@playwright/test";

export const ids = {
  cluster: "92a1b74d-0611-4bc5-a20f-3375a45f6a20",
  organization: "d8c83325-968c-4cd4-a20f-17194d812d80",
  workload: "9927945d-515b-4ef3-9d3c-f0e6892ef069",
  foreignWorkload: "e8aaaf4d-312e-47f6-b182-3b2e59b75822",
  formerWorkload: "257328ea-917e-488c-bde0-e85ee7bda641",
  inactiveWorkload: "d8d452fe-1bf5-41bb-b5c9-abd715c68ab1",
  job: "57aec936-c0c3-4e39-b262-733512911f65",
  syncRun: "8d8c145f-c7f5-4b65-a85c-aa6c32360271",
  finding: "66c1d306-e05f-4890-8e1e-c30fa3137f9c",
  operation: "2ab92d25-95ed-4af4-aefe-d86902305795",
} as const;

type MockOptions = {
  initialClusters?: boolean;
  delayCustomerListMs?: number;
  failCustomerListOnce?: boolean;
  staleCustomerInventory?: boolean;
};

type MockState = {
  role: "SUPER_ADMIN" | "CUSTOMER";
  clustersRegistered: boolean;
  imported: boolean;
  assigned: boolean;
  powerState: "STOPPED" | "RUNNING";
  jobPolls: number;
  customerListCalls: number;
  findingStatus: "OPEN" | "ACKNOWLEDGED" | "RESOLVED";
  requests: Array<{ method: string; path: string }>;
};

const observedAt = "2026-07-24T01:00:00Z";

function cluster() {
  return {
    id: ids.cluster,
    name: "staging-pve",
    api_base_url: "https://pve.example.test:8006",
    is_active: true,
    ca_configured: false,
    last_connection_error_code: null,
    last_connected_at: observedAt,
    last_sync_succeeded_at: observedAt,
    sync_interval_seconds: 60,
    inventory_stale: false,
    credential: {
      token_identifier: "svc@pve!portal",
      configured: true,
      last_used_at: null,
    },
    created_at: observedAt,
    updated_at: observedAt,
    version: 1,
  };
}

function workload(state: MockState) {
  return {
    id: ids.workload,
    cluster_id: ids.cluster,
    cluster_name: "staging-pve",
    vmid: 101,
    node: "pve-a",
    kind: "QEMU",
    name: "customer-web-01",
    power_state: state.powerState,
    cpu_cores: 4,
    memory_bytes: 8_589_934_592,
    disk_bytes: 107_374_182_400,
    is_template: false,
    is_present: true,
    organization_id: state.assigned ? ids.organization : null,
    organization_name: state.assigned ? "Acme Korea" : null,
    assigned_ip_addresses: ["192.0.2.24"],
    observed_at: observedAt,
    version: 1,
  };
}

function organization() {
  return {
    id: ids.organization,
    name: "Acme Korea",
    is_active: true,
    created_at: observedAt,
    updated_at: observedAt,
    version: 1,
  };
}

function customerVm(state: MockState, stale = false) {
  const item = workload(state);
  return {
    id: item.id,
    name: item.name,
    organization_name: "Acme Korea",
    power_state: item.power_state,
    cpu_cores: item.cpu_cores,
    memory_bytes: item.memory_bytes,
    disk_bytes: item.disk_bytes,
    assigned_ip_addresses: item.assigned_ip_addresses,
    observed_at: item.observed_at,
    is_stale: stale,
    stale_reason: stale ? "마지막 전체 동기화가 최신성 기준을 초과했습니다." : null,
  };
}

function operationCenterItem() {
  return {
    id: ids.operation,
    resource_type: "OPERATION",
    operation_type: "POWER_REBOOT",
    action: "reboot",
    status: "FAILED",
    cluster_id: ids.cluster,
    cluster_name: "staging-pve",
    organization_id: ids.organization,
    organization_name: "Acme Korea",
    requested_by_id: "admin-id",
    requested_by_name: "Admin",
    workload_id: ids.workload,
    workload_name: "customer-web-01",
    current_step: null,
    error_code: "CLUSTER_UNREACHABLE",
    error_summary: "Cluster temporarily unavailable.",
    retryable: true,
    retry_of_id: null,
    requested_at: observedAt,
    started_at: observedAt,
    finished_at: observedAt,
    heartbeat_at: observedAt,
    is_stuck: false,
    available_actions: ["ASSIGN", "ACKNOWLEDGE", "RETRY", "RESOLVE_MANUALLY"],
    impact_summary: "customer-web-01 assigned to Acme Korea is affected.",
    recommended_action: "Verify cluster health and use the safe retry action.",
    assignment: null,
    version: 1,
  };
}

function reconciliationFinding(state: MockState) {
  return {
    id: ids.finding,
    kind: "SPEC_DRIFT",
    severity: "WARNING",
    status: state.findingStatus,
    cluster_id: ids.cluster,
    cluster_name: "staging-pve",
    workload_id: ids.workload,
    sync_run_id: ids.syncRun,
    target_type: "workload",
    target_id: ids.workload,
    summary: "customer-web-01의 메모리 사양이 변경되었습니다.",
    details: { changed_fields: ["memory_bytes"] },
    first_observed_at: observedAt,
    last_observed_at: observedAt,
    acknowledged_by_id: state.findingStatus === "OPEN" ? null : "admin-id",
    acknowledged_at: state.findingStatus === "OPEN" ? null : observedAt,
    assigned_to_id: state.findingStatus === "OPEN" ? null : "admin-id",
    resolved_by_id: state.findingStatus === "RESOLVED" ? "admin-id" : null,
    resolved_at: state.findingStatus === "RESOLVED" ? observedAt : null,
    resolution_note: state.findingStatus === "RESOLVED" ? "브라우저 검증 완료" : null,
  };
}

function operationStatus() {
  return {
    status: "ok",
    worker: { available: true, alive: true, workers: ["worker-1"], stale_after_seconds: 60 },
    queue: { available: true, total: 0, queues: { operations: 0 }, backlog_threshold: 100 },
    workloads: { total: 1, assigned: 1, unassigned: 0 },
    directory: {
      organizations: { total: 1, active: 1 },
      users: { total: 2, active: 2 },
    },
    clusters: [],
    alerts: [],
  };
}

const corsHeaders = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "authorization,content-type,idempotency-key",
  "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
  "content-type": "application/json",
};

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, headers: corsHeaders, body: JSON.stringify(body) });
}

function apiError(code: string, message: string) {
  return { error: { code, message } };
}

export async function installApiMock(
  page: Page,
  options: MockOptions = {},
): Promise<MockState> {
  const state: MockState = {
    role: "CUSTOMER",
    clustersRegistered: options.initialClusters ?? true,
    imported: true,
    assigned: true,
    powerState: "STOPPED",
    jobPolls: 0,
    customerListCalls: 0,
    findingStatus: "OPEN",
    requests: [],
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname;
    state.requests.push({ method, path });

    if (method === "OPTIONS") {
      await route.fulfill({ status: 204, headers: corsHeaders });
      return;
    }
    if (path === "/api/auth/session" && method === "PUT") {
      await json(route, apiError("SESSION_NOT_FOUND", "저장된 세션이 없습니다."), 401);
      return;
    }
    if (path === "/api/auth/session" && method === "POST") {
      await route.fulfill({ status: 204, headers: corsHeaders });
      return;
    }
    if (path === "/api/auth/session" && method === "DELETE") {
      await route.fulfill({ status: 204, headers: corsHeaders });
      return;
    }
    if (path === "/api/v1/auth/login" && method === "POST") {
      const body = request.postDataJSON() as { email: string };
      state.role = body.email.startsWith("admin") ? "SUPER_ADMIN" : "CUSTOMER";
      await json(route, {
        access_token: `${state.role.toLowerCase()}-access`,
        refresh_token: "browser-only-refresh",
      });
      return;
    }
    if (path === "/api/v1/auth/me") {
      await json(route, {
        id: state.role === "SUPER_ADMIN" ? "admin-id" : "customer-id",
        email: state.role === "SUPER_ADMIN"
          ? "admin@example.test"
          : "customer@example.test",
        display_name: state.role === "SUPER_ADMIN" ? "Admin" : "Customer",
        role: state.role,
        is_active: true,
        last_login_at: observedAt,
        created_at: observedAt,
        updated_at: observedAt,
        version: 1,
      });
      return;
    }

    if (path === "/api/v1/admin/operations/status") {
      await json(route, operationStatus());
      return;
    }
    if (path === "/api/v1/admin/clusters/overview") {
      await json(route, { items: [] });
      return;
    }
    if (path === "/api/v1/admin/clusters" && method === "POST") {
      state.clustersRegistered = true;
      await json(route, cluster(), 201);
      return;
    }
    if (path === "/api/v1/admin/clusters") {
      await json(route, { items: state.clustersRegistered ? [cluster()] : [] });
      return;
    }
    if (path === `/api/v1/admin/clusters/${ids.cluster}/sync` && method === "POST") {
      await json(route, { operation_id: ids.syncRun, status: "QUEUED" }, 202);
      return;
    }
    if (path === "/api/v1/admin/inventory/freshness") {
      await json(route, {
        items: [{
          cluster_id: ids.cluster,
          cluster_name: "staging-pve",
          last_full_success_at: observedAt,
          stale_after_seconds: 180,
          is_stale: false,
          stale_reason: null,
          latest_status: "SUCCEEDED",
        }],
      });
      return;
    }
    if (path === "/api/v1/admin/inventory/sync-runs") {
      await json(route, {
        items: [{
          id: ids.syncRun,
          operation_id: ids.syncRun,
          cluster_id: ids.cluster,
          cluster_name: "staging-pve",
          generation: 7,
          scope: "FULL",
          status: "SUCCEEDED",
          partial_failure: false,
          triggered_by: "SCHEDULED",
          requested_by_id: null,
          target_workload_id: null,
          started_at: observedAt,
          finished_at: observedAt,
          duration_ms: 420,
          error_code: null,
          resource_counts: { created: 0, updated: 1, missing: 0 },
        }],
      });
      return;
    }
    if (path === "/api/v1/admin/inventory/reconciliation/findings") {
      await json(route, { items: [reconciliationFinding(state)] });
      return;
    }
    if (
      path === `/api/v1/admin/inventory/reconciliation/findings/${ids.finding}/acknowledge`
      && method === "POST"
    ) {
      state.findingStatus = "ACKNOWLEDGED";
      await json(route, reconciliationFinding(state));
      return;
    }
    if (
      path === `/api/v1/admin/inventory/reconciliation/findings/${ids.finding}/resolve`
      && method === "POST"
    ) {
      state.findingStatus = "RESOLVED";
      await json(route, reconciliationFinding(state));
      return;
    }
    if (path === `/api/v1/admin/clusters/${ids.cluster}/test`) {
      await json(route, {
        reachable: true,
        tls_valid: true,
        authenticated: true,
        version: "9.0",
        release: "1",
        capabilities: {},
      });
      return;
    }
    if (path === `/api/v1/admin/clusters/${ids.cluster}/nodes`) {
      await json(route, {
        items: [{ node: "pve-a", status: "online", maxcpu: 16, mem: 1024, maxmem: 2048 }],
      });
      return;
    }
    if (path === `/api/v1/admin/clusters/${ids.cluster}/guests`) {
      await json(route, {
        items: [{
          vmid: 101,
          node: "pve-a",
          type: "qemu",
          name: "customer-web-01",
          status: state.powerState.toLowerCase(),
          cpu: 0.125,
          maxcpu: 4,
          mem: 2_147_483_648,
          maxmem: 8_589_934_592,
          disk: 26_843_545_600,
          maxdisk: 107_374_182_400,
          uptime: 3_600,
        }],
      });
      return;
    }
    if (path === `/api/v1/admin/clusters/${ids.cluster}/storages`) {
      await json(route, {
        items: [{
          storage: "local-lvm",
          node: "pve-a",
          type: "lvmthin",
          status: "available",
          used: 25,
          total: 100,
        }],
      });
      return;
    }
    if (path === `/api/v1/admin/clusters/${ids.cluster}/workloads/import`) {
      state.imported = true;
      state.assigned = false;
      await json(route, { cluster_id: ids.cluster, discovered: 1, created: 1, updated: 0 });
      return;
    }
    if (path === "/api/v1/admin/workloads") {
      await json(route, { items: state.imported ? [workload(state)] : [] });
      return;
    }
    if (path === `/api/v1/admin/workloads/${ids.workload}/assign`) {
      state.assigned = true;
      await json(route, {
        id: "assignment-id",
        workload_id: ids.workload,
        organization_id: ids.organization,
        organization_name: "Acme Korea",
      });
      return;
    }
    if (path === "/api/v1/admin/users") {
      await json(route, {
        items: [{
          id: "admin-id",
          email: "admin@example.test",
          display_name: "Admin",
          role: "SUPER_ADMIN",
          is_active: true,
          last_login_at: observedAt,
          created_at: observedAt,
          updated_at: observedAt,
          version: 1,
        }],
      });
      return;
    }
    if (path === "/api/v1/admin/operations") {
      await json(route, { items: [operationCenterItem()], total: 1, limit: 50, offset: 0 });
      return;
    }
    if (path === `/api/v1/admin/operations/${ids.operation}`) {
      await json(route, {
        ...operationCenterItem(),
        events: [{
          id: 1,
          event_type: "STATUS_CHANGED",
          status: "FAILED",
          step: null,
          message: "Operation failed",
          details: { error_code: "CLUSTER_UNREACHABLE" },
          actor_user_id: null,
          occurred_at: observedAt,
        }],
        pve_tasks: [],
        provisioning_steps: [],
        related_audit_count: 1,
        related_backup_ids: [],
      });
      return;
    }
    if (path === `/api/v1/admin/operations/${ids.operation}/acknowledge`) {
      await json(route, {
        ...operationCenterItem(),
        version: 2,
        assignment: {
          assigned_to_id: null,
          assigned_to_name: null,
          assigned_at: null,
          acknowledged_by_id: "admin-id",
          acknowledged_at: observedAt,
          resolved_by_id: null,
          resolved_at: null,
          resolution_note: null,
          version: 2,
        },
      });
      return;
    }
    if (path === "/api/v1/admin/organizations") {
      const item = organization();
      await json(route, url.search ? {
        items: [item],
        total: 1,
        limit: Number(url.searchParams.get("limit") ?? 25),
        offset: Number(url.searchParams.get("offset") ?? 0),
      } : { items: [item] });
      return;
    }
    if (path === `/api/v1/admin/organizations/${ids.organization}/members`) {
      await json(route, { items: [] });
      return;
    }

    if (path === "/api/v1/customer/vms") {
      state.customerListCalls += 1;
      if (options.delayCustomerListMs) {
        await new Promise((resolve) => setTimeout(resolve, options.delayCustomerListMs));
      }
      if (options.failCustomerListOnce && state.customerListCalls === 1) {
        await json(route, apiError("PVE_TIMEOUT", "가상 머신 목록 조회 시간이 초과되었습니다."), 504);
        return;
      }
      await json(route, { items: [customerVm(state, options.staleCustomerInventory)] });
      return;
    }
    if (path === "/api/v1/customer/jobs") {
      await json(route, {
        items: state.jobPolls > 0 ? [{
          id: ids.job,
          job_id: ids.job,
          vm_id: ids.workload,
          action: "start",
          action_mode: "STANDARD",
          status: "SUCCEEDED",
          result: { final_power_state: "RUNNING" },
          error_code: null,
          error_summary: null,
          retryable: false,
          requested_at: observedAt,
          started_at: observedAt,
          finished_at: observedAt,
        }] : [],
      });
      return;
    }
    if (
      path === `/api/v1/customer/vms/${ids.foreignWorkload}`
      || path === `/api/v1/customer/vms/${ids.formerWorkload}`
    ) {
      await json(route, apiError("RESOURCE_NOT_FOUND", "가상 머신을 찾을 수 없습니다."), 404);
      return;
    }
    if (path === `/api/v1/customer/vms/${ids.inactiveWorkload}`) {
      await json(route, apiError("ORGANIZATION_INACTIVE", "비활성 조직은 접근할 수 없습니다."), 403);
      return;
    }
    if (path === `/api/v1/customer/vms/${ids.workload}/actions/start`) {
      state.jobPolls = 0;
      await json(route, {
        id: ids.job,
        job_id: ids.job,
        vm_id: ids.workload,
        action: "start",
        action_mode: "STANDARD",
        status: "QUEUED",
        result: {},
        error_code: null,
        error_summary: null,
        retryable: null,
        requested_at: observedAt,
        started_at: null,
        finished_at: null,
      }, 202);
      return;
    }
    if (path === `/api/v1/customer/jobs/${ids.job}`) {
      state.jobPolls += 1;
      const done = state.jobPolls >= 1;
      if (done) state.powerState = "RUNNING";
      await json(route, {
        id: ids.job,
        job_id: ids.job,
        vm_id: ids.workload,
        action: "start",
        action_mode: "STANDARD",
        status: done ? "SUCCEEDED" : "RUNNING",
        result: done ? { final_power_state: "RUNNING" } : {},
        error_code: null,
        error_summary: null,
        retryable: false,
        requested_at: observedAt,
        started_at: observedAt,
        finished_at: done ? observedAt : null,
      });
      return;
    }

    const emptyItemEndpoints = new Set([
      "/api/v1/admin/ip-pools",
      "/api/v1/admin/products",
      "/api/v1/admin/templates",
      "/api/v1/admin/provisioning-nodes",
      "/api/v1/admin/provision-requests",
      "/api/v1/admin/backup-targets",
      "/api/v1/admin/backups",
      "/api/v1/admin/backups/storage-candidates",
    ]);
    if (emptyItemEndpoints.has(path)) {
      await json(route, { items: [] });
      return;
    }
    if (path === "/api/v1/admin/audit-logs") {
      await json(route, { items: [], total: 0, limit: 25, offset: 0 });
      return;
    }

    await json(route, apiError("NOT_FOUND", `Unhandled browser fixture: ${method} ${path}`), 404);
  });
  return state;
}

export async function loginAs(page: Page, role: "admin" | "customer") {
  await page.goto("/");
  await page.getByLabel("이메일").fill(`${role}@example.test`);
  await page.getByLabel("비밀번호").fill("browser-test-password");
  await page.getByRole("button", { name: "로그인" }).click();
}

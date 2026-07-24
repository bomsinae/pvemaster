import type { Page, Route } from "@playwright/test";

export const ids = {
  cluster: "92a1b74d-0611-4bc5-a20f-3375a45f6a20",
  organization: "d8c83325-968c-4cd4-a20f-17194d812d80",
  workload: "9927945d-515b-4ef3-9d3c-f0e6892ef069",
  foreignWorkload: "e8aaaf4d-312e-47f6-b182-3b2e59b75822",
  formerWorkload: "257328ea-917e-488c-bde0-e85ee7bda641",
  inactiveWorkload: "d8d452fe-1bf5-41bb-b5c9-abd715c68ab1",
  job: "57aec936-c0c3-4e39-b262-733512911f65",
} as const;

type MockOptions = {
  initialClusters?: boolean;
  delayCustomerListMs?: number;
  failCustomerListOnce?: boolean;
};

type MockState = {
  role: "SUPER_ADMIN" | "CUSTOMER";
  clustersRegistered: boolean;
  imported: boolean;
  assigned: boolean;
  powerState: "STOPPED" | "RUNNING";
  jobPolls: number;
  customerListCalls: number;
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

function customerVm(state: MockState) {
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
      await json(route, { items: [] });
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
      await json(route, { items: [customerVm(state)] });
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

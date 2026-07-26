import assert from "node:assert/strict";
import test from "node:test";

import {
  changePassword,
  createCustomerServiceRequest,
  createCustomerSshKey,
  getCustomerJob,
  getCustomerNotificationPreferences,
  getCustomerVm,
  getCustomerVmMetrics,
  listCustomerJobs,
  listCustomerServiceRequests,
  listCustomerVms,
  login,
  requestPowerAction,
  updateCustomerNotificationPreference,
} from "../lib/customer-api.ts";
import { filterCustomerVms, upsertCustomerJob } from "../lib/customer-portal-state.ts";
import {
  endBrowserSession,
  persistBrowserSession,
  restoreBrowserSession,
} from "../lib/browser-session.ts";

const vm = {
  id: "8fd1d8d7-5a27-4de4-982f-a7aef685fe00",
  name: "고객 웹 VM",
  organization_name: "젤란다",
  power_state: "STOPPED",
  cpu_cores: 4,
  memory_bytes: 8_589_934_592,
  disk_bytes: 107_374_182_400,
  assigned_ip_addresses: ["192.0.2.24"],
  observed_at: "2026-07-14T12:00:00Z",
};

function response(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("mock login to customer power operation flow", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  let jobPolls = 0;
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    requests.push({ url, init });
    if (url.endsWith("/auth/login")) {
      return response({ access_token: "mock-access-token", refresh_token: "mock-refresh-token" });
    }
    if (url.endsWith("/customer/vms")) return response({ items: [vm] });
    if (url.endsWith("/customer/jobs")) return response({ items: [] });
    if (url.endsWith(`/customer/vms/${vm.id}/metrics?range=day`)) {
      return response({
        vm_id: vm.id,
        range: "day",
        resolution_seconds: 60,
        assignment_started_at: "2026-07-14T11:00:00Z",
        observed_at: "2026-07-14T12:00:00Z",
        partial: true,
        items: [{
          time: "2026-07-14T12:00:00Z",
          sample_count: 1,
          cpu_avg: 0.2,
          cpu_max: 0.3,
          memory_used_avg: null,
          memory_used_max: null,
          disk_read_avg: null,
          disk_read_max: null,
          disk_write_avg: null,
          disk_write_max: null,
          network_receive_avg: null,
          network_receive_max: null,
          network_transmit_avg: null,
          network_transmit_max: null,
        }],
      });
    }
    if (url.endsWith(`/customer/vms/${vm.id}`)) {
      return response({
        ...vm,
        recent_jobs: [],
        recent_state_changes: [],
        recent_backup: null,
        upcoming_maintenance: [],
      });
    }
    if (url.endsWith(`/customer/vms/${vm.id}/actions/start`)) {
      assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer mock-access-token");
      assert.equal(new Headers(init?.headers).get("Idempotency-Key"), "e2e-idempotency-key");
      return response({
        id: "57aec936-c0c3-4e39-b262-733512911f65",
        job_id: "57aec936-c0c3-4e39-b262-733512911f65",
        vm_id: vm.id,
        action: "start",
        action_mode: "STANDARD",
        status: "QUEUED",
        result: {},
        error_code: null,
        error_summary: null,
        retryable: null,
        requested_at: "2026-07-14T12:01:00Z",
        started_at: null,
        finished_at: null,
      }, 202);
    }
    if (url.endsWith("/customer/jobs/57aec936-c0c3-4e39-b262-733512911f65")) {
      jobPolls += 1;
      return response({
        id: "57aec936-c0c3-4e39-b262-733512911f65",
        job_id: "57aec936-c0c3-4e39-b262-733512911f65",
        vm_id: vm.id,
        action: "start",
        action_mode: "STANDARD",
        status: jobPolls === 1 ? "RUNNING" : "SUCCEEDED",
        result: jobPolls === 1 ? {} : { final_power_state: "RUNNING" },
        error_code: null,
        error_summary: null,
        retryable: false,
        requested_at: "2026-07-14T12:01:00Z",
        started_at: "2026-07-14T12:01:01Z",
        finished_at: jobPolls === 1 ? null : "2026-07-14T12:01:02Z",
      });
    }
    return response({ error: { code: "NOT_FOUND", message: "unexpected request" } }, 404);
  };

  const session = await login("http://api.test", "customer@example.test", "test-password", fetcher);
  const listing = await listCustomerVms("http://api.test", session.accessToken, fetcher);
  const persistedJobs = await listCustomerJobs(
    "http://api.test",
    session.accessToken,
    fetcher,
  );
  const detail = await getCustomerVm("http://api.test", session.accessToken, listing[0].id, fetcher);
  const metrics = await getCustomerVmMetrics(
    "http://api.test",
    session.accessToken,
    listing[0].id,
    "day",
    fetcher,
  );
  const accepted = await requestPowerAction(
    "http://api.test",
    session.accessToken,
    detail.id,
    "start",
    "e2e-idempotency-key",
    fetcher,
  );
  const running = await getCustomerJob("http://api.test", session.accessToken, accepted.id, fetcher);
  const finished = await getCustomerJob("http://api.test", session.accessToken, accepted.id, fetcher);

  assert.equal(session.refreshToken, "mock-refresh-token");
  assert.deepEqual(listing, [vm]);
  assert.deepEqual(persistedJobs, []);
  assert.equal(detail.id, vm.id);
  assert.equal(detail.cpu_cores, 4);
  assert.equal(detail.memory_bytes, 8_589_934_592);
  assert.equal(detail.disk_bytes, 107_374_182_400);
  assert.deepEqual(detail.assigned_ip_addresses, ["192.0.2.24"]);
  assert.equal(metrics.partial, true);
  assert.equal(metrics.items[0].cpu_avg, 0.2);
  assert.equal(metrics.items[0].memory_used_avg, null);
  assert.equal(running.status, "RUNNING");
  assert.equal(finished.status, "SUCCEEDED");
  assert.equal(finished.result.final_power_state, "RUNNING");
  assert.equal(requests.length, 8);
});

test("customer VM inventory searches names and IP addresses and filters power state", () => {
  const running = { ...vm, id: "running-vm", name: "web-primary", organization_name: "젤란다", power_state: "RUNNING" };
  const stopped = {
    ...vm,
    id: "stopped-vm",
    name: "database",
    organization_name: "신규고객",
    power_state: "STOPPED",
    assigned_ip_addresses: ["198.51.100.18"],
  };

  assert.deepEqual(
    filterCustomerVms([stopped, running], { query: "web", power: "ALL" }).map(
      (item) => item.id,
    ),
    ["running-vm"],
  );
  assert.deepEqual(
    filterCustomerVms([running, stopped], { query: "198.51.100", power: "STOPPED" }).map(
      (item) => item.id,
    ),
    ["stopped-vm"],
  );
  assert.deepEqual(
    filterCustomerVms([running, stopped], { query: "", power: "ALL", organization: "젤란다" }).map(
      (item) => item.id,
    ),
    ["running-vm"],
  );
  assert.deepEqual(
    filterCustomerVms([running, stopped], { query: "신규고객", power: "ALL" }).map(
      (item) => item.id,
    ),
    ["stopped-vm"],
  );
});

test("customer VM operation statuses are retained independently for each VM", () => {
  const firstJob = {
    id: "57aec936-c0c3-4e39-b262-733512911f65",
    job_id: "57aec936-c0c3-4e39-b262-733512911f65",
    vm_id: "first-vm",
    action: "shutdown" as const,
    action_mode: "GRACEFUL" as const,
    status: "SUCCEEDED" as const,
    result: { final_power_state: "STOPPED" },
    error_code: null,
    error_summary: null,
    retryable: false,
    requested_at: "2026-07-22T01:00:00Z",
    started_at: "2026-07-22T01:00:01Z",
    finished_at: "2026-07-22T01:00:02Z",
  };
  const secondJob = {
    ...firstJob,
    id: "9198fe5c-ced4-4590-8307-24c570ee7241",
    job_id: "9198fe5c-ced4-4590-8307-24c570ee7241",
    vm_id: "second-vm",
    action: "reboot" as const,
    status: "RUNNING" as const,
    result: {},
    finished_at: null,
  };

  const jobsByVmId = upsertCustomerJob(upsertCustomerJob({}, firstJob), secondJob);

  assert.equal(jobsByVmId["first-vm"], firstJob);
  assert.equal(jobsByVmId["second-vm"], secondJob);
});

test("customer forced stop sends explicit server confirmation", async () => {
  let requestBody: unknown;
  const fetcher: typeof fetch = async (_input, init) => {
    requestBody = JSON.parse(String(init?.body));
    return response({
      id: "9198fe5c-ced4-4590-8307-24c570ee7241",
      job_id: "9198fe5c-ced4-4590-8307-24c570ee7241",
      vm_id: vm.id,
      action: "stop",
      action_mode: "FORCED",
      status: "QUEUED",
      result: { action_mode: "FORCED" },
      error_code: null,
      error_summary: null,
      retryable: null,
      requested_at: "2026-07-14T12:01:00Z",
      started_at: null,
      finished_at: null,
    }, 202);
  };

  const job = await requestPowerAction(
    "http://api.test",
    "mock-access-token",
    vm.id,
    "stop",
    "forced-stop-idempotency-key",
    { confirmForced: true, fetcher },
  );

  assert.deepEqual(requestBody, { confirm_forced: true });
  assert.equal(job.action, "stop");
  assert.equal(job.action_mode, "FORCED");
});

test("customer password change sends the current and new password without returning a token", async () => {
  let request: { url: string; init?: RequestInit } | undefined;
  const fetcher: typeof fetch = async (input, init) => {
    request = { url: String(input), init };
    return new Response(null, { status: 204 });
  };

  await changePassword(
    "http://api.test",
    "customer-access",
    "current-password",
    "new-password-at-least-12",
    fetcher,
  );

  assert.equal(request?.url, "http://api.test/api/v1/auth/change-password");
  assert.equal(new Headers(request?.init?.headers).get("Authorization"), "Bearer customer-access");
  assert.deepEqual(JSON.parse(String(request?.init?.body)), {
    current_password: "current-password",
    new_password: "new-password-at-least-12",
    revoke_all_sessions: true,
  });
});

test("customer notification preference uses optimistic versioning", async () => {
  const requests: Array<{ url: string; body?: unknown }> = [];
  const preference = {
    organization_id: "d8c83325-968c-4cd4-a20f-17194d812d80",
    organization_name: "Acme Korea",
    event_type: "VM_DOWN" as const,
    email_enabled: true,
    required_by_organization: false,
    version: 0,
  };
  const fetcher: typeof fetch = async (input, init) => {
    requests.push({
      url: String(input),
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (init?.method === "PUT") {
      return response({ ...preference, email_enabled: false, version: 1 });
    }
    return response({
      channel: "EMAIL",
      destination: "c*******@example.test",
      items: [preference],
    });
  };

  const listed = await getCustomerNotificationPreferences(
    "http://api.test",
    "customer-access",
    fetcher,
  );
  const updated = await updateCustomerNotificationPreference(
    "http://api.test",
    "customer-access",
    {
      organization_id: preference.organization_id,
      event_type: preference.event_type,
      email_enabled: false,
      version: preference.version,
    },
    fetcher,
  );

  assert.equal(listed.destination, "c*******@example.test");
  assert.equal(updated.version, 1);
  assert.deepEqual(requests[1].body, {
    organization_id: preference.organization_id,
    event_type: "VM_DOWN",
    email_enabled: false,
    version: 0,
  });
});

test("customer self-service keeps public keys and approval requests VM-scoped", async () => {
  const requests: Array<{ url: string; method?: string; body?: unknown; key?: string | null }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    requests.push({
      url: String(input),
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
      key: new Headers(init?.headers).get("Idempotency-Key"),
    });
    if (String(input).endsWith("/ssh-keys")) {
      return response({
        id: "key-1",
        label: "Laptop",
        fingerprint: "SHA256:test",
        public_key: "ssh-ed25519 AAAA",
        created_at: "2026-07-26T00:00:00Z",
      }, 201);
    }
    if (String(input).endsWith("/service-requests") && init?.method === "POST") {
      return response({
        id: "request-1",
        request_type: "RESIZE",
        vm_id: vm.id,
        vm_name: vm.name,
        organization_name: vm.organization_name,
        input: { cpu_cores: 6 },
        impact: { messages: ["approval"] },
        status: "PENDING_APPROVAL",
        operation_id: null,
        error_code: null,
        result_summary: null,
        requested_at: "2026-07-26T00:00:00Z",
        started_at: null,
        finished_at: null,
        version: 1,
        approvals: [],
      }, 202);
    }
    return response({ items: [] });
  };

  await createCustomerSshKey(
    "http://api.test",
    "customer-access",
    vm.id,
    "Laptop",
    "ssh-ed25519 AAAA",
    fetcher,
  );
  await createCustomerServiceRequest(
    "http://api.test",
    "customer-access",
    vm.id,
    "RESIZE",
    { cpu_cores: 6 },
    "self-service-idempotency",
    fetcher,
  );
  assert.deepEqual(await listCustomerServiceRequests(
    "http://api.test",
    "customer-access",
    fetcher,
  ), []);
  assert.equal(requests[0].url.endsWith(`/customer/vms/${vm.id}/ssh-keys`), true);
  assert.deepEqual(requests[1].body, {
    request_type: "RESIZE",
    input: { cpu_cores: 6 },
  });
  assert.equal(requests[1].key, "self-service-idempotency");
});

test("customer password change exposes a safe API error code", async () => {
  const fetcher: typeof fetch = async () => response({
    error: { code: "CURRENT_PASSWORD_INVALID", message: "The current password is invalid." },
  }, 400);

  await assert.rejects(
    changePassword("http://api.test", "customer-access", "wrong", "new-password-at-least-12", fetcher),
    (error: unknown) => error instanceof Error && "code" in error && error.code === "CURRENT_PASSWORD_INVALID",
  );
});

test("browser session is persisted, restored, and cleared through the same-origin BFF", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    requests.push({ url: String(input), init });
    if (init?.method === "PUT") return response({ access_token: "rotated-access" });
    return new Response(null, { status: 204 });
  };

  await persistBrowserSession("one-time-refresh", fetcher);
  const restored = await restoreBrowserSession(fetcher);
  await endBrowserSession(fetcher);

  assert.deepEqual(restored, { accessToken: "rotated-access", refreshToken: "" });
  assert.deepEqual(requests.map((item) => item.init?.method), ["POST", "PUT", "DELETE"]);
  assert.ok(String(requests[0].init?.body).includes("one-time-refresh"));
  assert.equal(requests.every((item) => item.url === "/api/auth/session"), true);
  assert.equal(requests.every((item) => item.init?.credentials === "same-origin"), true);
});

import assert from "node:assert/strict";
import test from "node:test";

import {
  changePassword,
  getCustomerJob,
  getCustomerVm,
  listCustomerVms,
  login,
  requestPowerAction,
} from "../lib/customer-api.ts";
import { filterCustomerVms } from "../lib/customer-portal-state.ts";
import {
  endBrowserSession,
  persistBrowserSession,
  restoreBrowserSession,
} from "../lib/browser-session.ts";

const vm = {
  id: "8fd1d8d7-5a27-4de4-982f-a7aef685fe00",
  name: "고객 웹 VM",
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
    if (url.endsWith(`/customer/vms/${vm.id}`)) {
      return response({ ...vm, recent_jobs: [] });
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
  const detail = await getCustomerVm("http://api.test", session.accessToken, listing[0].id, fetcher);
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
  assert.equal(detail.id, vm.id);
  assert.equal(detail.cpu_cores, 4);
  assert.equal(detail.memory_bytes, 8_589_934_592);
  assert.equal(detail.disk_bytes, 107_374_182_400);
  assert.deepEqual(detail.assigned_ip_addresses, ["192.0.2.24"]);
  assert.equal(running.status, "RUNNING");
  assert.equal(finished.status, "SUCCEEDED");
  assert.equal(finished.result.final_power_state, "RUNNING");
  assert.equal(requests.length, 6);
});

test("customer VM inventory searches names and IP addresses and filters power state", () => {
  const running = { ...vm, id: "running-vm", name: "web-primary", power_state: "RUNNING" };
  const stopped = {
    ...vm,
    id: "stopped-vm",
    name: "database",
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
  });
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

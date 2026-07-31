import assert from "node:assert/strict";
import test from "node:test";

import {
  addOrganizationMember,
  activateOrganization,
  AdminApiError,
  assignWorkload,
  createCluster,
  createOrganizationUser,
  createProvisionRequest,
  createTemplate,
  deleteProduct,
  deleteIpPool,
  deleteOrganization,
  deleteUser,
  deleteCluster,
  deleteTemplate,
  getAdminVmJob,
  getClusterInventory,
  getClusterResourceOverview,
  getClusterRemovalCheck,
  getNodeMetrics,
  getMe,
  getOperationsStatus,
  importClusterWorkloads,
  listAuditLogs,
  listClusters,
  listIpPools,
  listOrganizations,
  listOrganizationMembers,
  listProducts,
  listProvisioningNodes,
  listProvisionRequests,
  listTemplates,
  listUsers,
  listWorkloads,
  OrganizationUserProvisionError,
  removeOrganizationMember,
  resetUserPassword,
  requestAdminWorkloadAction,
  searchOrganizations,
  testCluster,
  unassignWorkload,
  updateProduct,
  updateIpPool,
  updateOrganization,
  updateUserStatus,
  updateTemplate,
  upsertProvisioningNode,
} from "../lib/admin-api.ts";
import { login } from "../lib/customer-api.ts";

function response(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("admin login, operations, cluster registration and inventory flow", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  const cluster = {
    id: "92a1b74d-0611-4bc5-a20f-3375a45f6a20",
    name: "staging-pve",
    api_base_url: "https://pve.example.test:8006",
    is_active: true,
    ca_configured: false,
    last_connection_error_code: null,
    last_connected_at: "2026-07-14T12:00:00Z",
    credential: { token_identifier: "svc@pve!portal", configured: true, last_used_at: null },
    created_at: "2026-07-14T12:00:00Z",
    updated_at: "2026-07-14T12:00:00Z",
    version: 1,
  };
  const organizationId = "d8c83325-968c-4cd4-a20f-17194d812d80";
  const customerId = "90900b84-3b1c-47fd-a155-12723af9eb6c";
  const workload = {
    id: "9927945d-515b-4ef3-9d3c-f0e6892ef069",
    cluster_id: cluster.id,
    cluster_name: cluster.name,
    vmid: 101,
    node: "pve-a",
    kind: "QEMU",
    name: "vm-101",
    power_state: "RUNNING",
    cpu_cores: 4,
    memory_bytes: 8_589_934_592,
    disk_bytes: 107_374_182_400,
    is_template: false,
    is_present: true,
    organization_id: null,
    organization_name: null,
    observed_at: "2026-07-14T12:00:00Z",
    version: 1,
  };
  const template = {
    id: "8b7da9a7-e486-4fcb-bb95-c77cbe6e5690", name: "ubuntu-2404",
    source_workload_id: workload.id, source_disk: "scsi0", default_storage: "local-lvm",
    default_bridge: "vmbr0", default_vlan_tag: null, cloud_init_enabled: true,
    linux_only: true, os_type: "LINUX", is_enabled: true,
  };
  const node = {
    id: "8f63769b-f060-488c-b80d-e8b4084e16a9", cluster_id: cluster.id, name: "pve-a",
    is_enabled: true, is_maintenance: false, available_memory_bytes: 68_719_476_736,
    available_storage_bytes: 1_099_511_627_776,
  };
  const job = {
    id: "2d53be6d-6080-4f91-b249-2bb68e4f696c", job_id: "2d53be6d-6080-4f91-b249-2bb68e4f696c",
    vm_id: workload.id, workload_id: workload.id, action: "start", action_mode: "STANDARD", status: "QUEUED",
    error_code: null, error_summary: null, requested_at: "2026-07-14T12:00:00Z", finished_at: null,
  };

  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    requests.push({ url, init });
    if (!url.endsWith("/auth/login")) {
      assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer admin-access");
    }
    if (url.endsWith("/auth/login")) {
      return response({ access_token: "admin-access", refresh_token: "admin-refresh" });
    }
    if (url.endsWith("/auth/me")) return response({ id: "admin-id", email: "admin@example.test", display_name: "Admin", role: "SUPER_ADMIN", is_active: true, last_login_at: null, created_at: "2026-07-14T12:00:00Z", updated_at: "2026-07-14T12:00:00Z", version: 1 });
    if (url.endsWith("/admin/operations/status")) return response({ status: "ok", worker: { available: true, alive: true, workers: ["worker-1"], stale_after_seconds: 60 }, queue: { available: true, total: 0, queues: { operations: 0 }, backlog_threshold: 100 }, workloads: { total: 3, assigned: 2, unassigned: 1 }, directory: { organizations: { total: 3, active: 2 }, users: { total: 5, active: 4 } }, clusters: [], alerts: [] });
    if (url.endsWith("/admin/clusters") && init?.method === "POST") return response(cluster, 201);
    if (url.endsWith("/admin/clusters/overview")) return response({ items: [{ cluster_id: cluster.id, name: cluster.name, connected: true, observed_at: "2026-07-14T12:00:00Z", error_code: null, node_count: 1, guest_count: 1, running_guest_count: 1, qemu_count: 1, lxc_count: 0, storage_count: 2, storage_used_bytes: 60, storage_total_bytes: 200, vm_storage_count: 1, vm_storage_used_bytes: 25, vm_storage_total_bytes: 100, nodes: [{ node: "pve-a", status: "online", cpu: 0.25, maxcpu: 16, memory_used_bytes: 1024, memory_total_bytes: 2048, disk_used_bytes: 35, disk_total_bytes: 100, load_average: [1.1, 0.9, 0.7], uptime_seconds: 3600 }] }] });
    if (url.endsWith(`/admin/clusters/${cluster.id}`) && init?.method === "DELETE") return new Response(null, { status: 204 });
    if (url.endsWith(`/admin/clusters/${cluster.id}/removal-check`)) return response({ cluster_id: cluster.id, can_remove: false, blocks: [{ code: "ASSIGNED_WORKLOADS", count: 1 }] });
    if (url.endsWith("/admin/clusters")) return response({ items: [cluster] });
    if (url.endsWith(`/admin/clusters/${cluster.id}/test`)) return response({ reachable: true, tls_valid: true, authenticated: true, version: "9.0", release: "1", capabilities: {} });
    if (url.endsWith(`/admin/clusters/${cluster.id}/nodes/pve-a/metrics?range=hour`)) return response({ cluster_id: cluster.id, node: "pve-a", range: "hour", observed_at: "2026-07-14T12:00:00Z", items: [{ time: 1720000000, cpu_usage: 0.25, server_load: 1.1, memory_used_bytes: 1024, memory_total_bytes: 2048, network_receive_bps: 4096, network_transmit_bps: 2048, cpu_pressure_some: null, io_pressure_some: null, io_pressure_full: null, memory_pressure_some: null, memory_pressure_full: null }] });
    if (url.endsWith(`/admin/clusters/${cluster.id}/nodes`)) return response({ items: [{ node: "pve-a", status: "online", maxcpu: 16, mem: 1024, maxmem: 2048 }] });
    if (url.endsWith(`/admin/clusters/${cluster.id}/guests`)) return response({ items: [{ vmid: 101, node: "pve-a", type: "qemu", name: "vm-101", status: "running", cpu: 0.125, maxcpu: 4, mem: 2_147_483_648, maxmem: 8_589_934_592, disk: 26_843_545_600, maxdisk: 107_374_182_400, uptime: 90_061 }] });
    if (url.endsWith(`/admin/clusters/${cluster.id}/storages`)) return response({ items: [{ storage: "local-lvm", node: "pve-a", type: "lvmthin", status: "available" }] });
    if (url.endsWith(`/admin/clusters/${cluster.id}/workloads/import`)) return response({ cluster_id: cluster.id, discovered: 1, created: 1, updated: 0 });
    if (url.endsWith(`/admin/organizations/${organizationId}/members`) && init?.method === "POST") return response({ id: "membership-id" }, 201);
    if (url.endsWith(`/admin/organizations/${organizationId}/members`)) return response({ items: [] });
    if (url.endsWith(`/admin/organizations/${organizationId}/members/${customerId}`)) return response({});
    if (url.endsWith(`/admin/workloads/${workload.id}/assign`)) return response({ id: "assignment-id", workload_id: workload.id, organization_id: organizationId });
    if (url.endsWith(`/admin/workloads/${workload.id}/assignment`)) return response({});
    if (url.endsWith("/admin/workloads")) return response({ items: [workload] });
    if (url.endsWith("/admin/users")) return response({ items: [] });
    if (url.endsWith("/admin/organizations")) return response({ items: [] });
    if (url.endsWith("/admin/ip-pools")) return response({ items: [] });
    if (url.endsWith("/admin/products/product-id") && init?.method === "PATCH") return response({ id: "product-id", name: "standard-2", cpu_cores: 4, memory_bytes: 4_294_967_296, disk_bytes: 42_949_672_960, is_enabled: false });
    if (url.endsWith("/admin/products/product-id") && init?.method === "DELETE") return new Response(null, { status: 204 });
    if (url.endsWith("/admin/products")) return response({ items: [] });
    if (url.endsWith(`/admin/templates/${template.id}`) && init?.method === "PATCH") return response({ ...template, name: "ubuntu-2404-v2", is_enabled: false });
    if (url.endsWith(`/admin/templates/${template.id}`) && init?.method === "DELETE") return new Response(null, { status: 204 });
    if (url.endsWith("/admin/templates") && init?.method === "POST") return response(template, 201);
    if (url.endsWith("/admin/templates")) return response({ items: [] });
    if (url.endsWith("/admin/provisioning-nodes") && init?.method === "PUT") return response(node);
    if (url.endsWith("/admin/provisioning-nodes")) return response({ items: [node] });
    if (url.endsWith("/admin/provision-requests") && init?.method === "POST") {
      assert.equal(new Headers(init.headers).get("Idempotency-Key"), "provision-key-123");
      return response({ id: "request-id", job_id: "provision-job-id", status: "QUEUED", current_step: "VALIDATE_REQUEST", os_type: "LINUX", target_name: "web-01", target_vmid: null, ip_address: null, error_code: null, initial_password: null, requested_at: "2026-07-14T12:00:00Z", steps: [] }, 202);
    }
    if (url.endsWith("/admin/provision-requests")) return response({ items: [] });
    if (url.endsWith(`/admin/workloads/${workload.id}/actions/start`)) {
      assert.equal(new Headers(init?.headers).get("Idempotency-Key"), "power-key-123");
      return response(job, 202);
    }
    if (url.endsWith(`/api/v1/jobs/${job.id}`)) return response(job);
    if (url.includes("/admin/audit-logs")) return response({ items: [], total: 0, limit: 100, offset: 0 });
    return response({ error: { code: "NOT_FOUND", message: "unexpected request" } }, 404);
  };

  const session = await login("http://api.test", "admin@example.test", "password", fetcher);
  const me = await getMe("http://api.test", session.accessToken, fetcher);
  const operations = await getOperationsStatus("http://api.test", session.accessToken, fetcher);
  const initialClusters = await listClusters("http://api.test", session.accessToken, fetcher);
  const resourceOverview = await getClusterResourceOverview("http://api.test", session.accessToken, fetcher);
  const nodeMetrics = await getNodeMetrics("http://api.test", session.accessToken, cluster.id, "pve-a", "hour", undefined, fetcher);
  const created = await createCluster("http://api.test", session.accessToken, {
    name: "staging-pve",
    api_base_url: "https://pve.example.test:8006",
    token_identifier: "svc@pve!portal",
    token_secret: "write-only-secret",
  }, fetcher);
  const probe = await testCluster("http://api.test", session.accessToken, created.id, fetcher);
  const inventory = await getClusterInventory("http://api.test", session.accessToken, created.id, fetcher);
  const imported = await importClusterWorkloads("http://api.test", session.accessToken, created.id, fetcher);
  const removalCheck = await getClusterRemovalCheck("http://api.test", session.accessToken, created.id, fetcher);
  await deleteCluster("http://api.test", session.accessToken, created.id, fetcher);
  const importedWorkloads = await listWorkloads("http://api.test", session.accessToken, {}, fetcher);
  await listOrganizationMembers("http://api.test", session.accessToken, organizationId, fetcher);
  await addOrganizationMember("http://api.test", session.accessToken, organizationId, customerId, fetcher);
  await assignWorkload("http://api.test", session.accessToken, workload.id, organizationId, fetcher);
  await unassignWorkload("http://api.test", session.accessToken, workload.id, fetcher);
  await removeOrganizationMember("http://api.test", session.accessToken, organizationId, customerId, fetcher);
  await createTemplate("http://api.test", session.accessToken, {
    name: template.name, source_workload_id: workload.id, source_disk: "scsi0",
    default_storage: "local-lvm", default_bridge: "vmbr0", default_vlan_tag: null, os_type: "WINDOWS",
  }, fetcher);
  const updatedProduct = await updateProduct("http://api.test", session.accessToken, "product-id", {
    name: "standard-2", cpu_cores: 4, memory_bytes: 4_294_967_296,
    disk_bytes: 42_949_672_960, is_enabled: false,
  }, fetcher);
  const updatedTemplate = await updateTemplate("http://api.test", session.accessToken, template.id, {
    name: "ubuntu-2404-v2", is_enabled: false,
  }, fetcher);
  await deleteProduct("http://api.test", session.accessToken, "product-id", fetcher);
  await deleteTemplate("http://api.test", session.accessToken, template.id, fetcher);
  await upsertProvisioningNode("http://api.test", session.accessToken, {
    cluster_id: cluster.id, name: "pve-a", is_enabled: true, is_maintenance: false,
    available_memory_bytes: node.available_memory_bytes, available_storage_bytes: node.available_storage_bytes,
  }, fetcher);
  const listedNodes = await listProvisioningNodes("http://api.test", session.accessToken, fetcher);
  await createProvisionRequest("http://api.test", session.accessToken, {
    product_id: "product-id", template_id: template.id, organization_id: organizationId,
    target_cluster_id: cluster.id, target_node_id: node.id, target_name: "web-01",
    ip_pool_id: "pool-id", cloud_init: { username: "ubuntu", ssh_public_keys: ["ssh-ed25519 AAAA"] },
    start_after_create: true,
  }, "provision-key-123", fetcher);
  const queuedJob = await requestAdminWorkloadAction("http://api.test", session.accessToken, workload.id, "start", "power-key-123", fetcher);
  await getAdminVmJob("http://api.test", session.accessToken, queuedJob.id, fetcher);
  await Promise.all([
    listUsers("http://api.test", session.accessToken, fetcher),
    listOrganizations("http://api.test", session.accessToken, fetcher),
    listIpPools("http://api.test", session.accessToken, fetcher),
    listProducts("http://api.test", session.accessToken, fetcher),
    listTemplates("http://api.test", session.accessToken, fetcher),
    listProvisionRequests("http://api.test", session.accessToken, fetcher),
    listAuditLogs("http://api.test", session.accessToken, fetcher),
  ]);

  assert.equal(me.role, "SUPER_ADMIN");
  assert.equal(operations.worker.alive, true);
  assert.equal(operations.directory.organizations.active, 2);
  assert.equal(operations.directory.users.total, 5);
  assert.equal(initialClusters[0].id, cluster.id);
  assert.equal(resourceOverview[0].nodes[0].load_average[0], 1.1);
  assert.equal(nodeMetrics.items[0].network_receive_bps, 4096);
  assert.equal(nodeMetrics.items[0].cpu_pressure_some, null);
  assert.equal(probe.authenticated, true);
  assert.equal(inventory.nodes[0].node, "pve-a");
  assert.equal(inventory.guests[0].vmid, 101);
  assert.equal(inventory.guests[0].cpu, 0.125);
  assert.equal(inventory.guests[0].mem, 2_147_483_648);
  assert.equal(inventory.guests[0].disk, 26_843_545_600);
  assert.equal(inventory.guests[0].uptime, 90_061);
  assert.equal(inventory.guests[0].maxcpu, 4);
  assert.equal(inventory.storages[0].storage, "local-lvm");
  assert.equal(imported.created, 1);
  assert.equal(removalCheck.can_remove, false);
  assert.deepEqual(removalCheck.blocks, [{ code: "ASSIGNED_WORKLOADS", count: 1 }]);
  assert.ok(requests.some((request) => request.url.endsWith(`/admin/clusters/${cluster.id}`) && request.init?.method === "DELETE"));
  assert.equal(importedWorkloads[0].id, workload.id);
  assert.equal(listedNodes[0].name, "pve-a");
  assert.equal(updatedProduct.is_enabled, false);
  assert.equal(updatedTemplate.name, "ubuntu-2404-v2");
  const templateCreateRequest = requests.find((request) =>
    request.url.endsWith("/admin/templates") && request.init?.method === "POST"
  );
  assert.equal(
    JSON.parse(String(templateCreateRequest?.init?.body)).os_type,
    "WINDOWS",
  );
  assert.ok(requests.some((request) => request.url.endsWith("/admin/audit-logs?limit=100&offset=0")));
  assert.equal(requests.length, 37);
});

test("super administrator resets a user password without exposing it in a response", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    requests.push({ url: String(input), init });
    return new Response(null, { status: 204 });
  };

  await resetUserPassword(
    "http://api.test",
    "admin-access",
    "user/id",
    "new-password-at-least-12",
    fetcher,
  );

  assert.equal(requests[0].url, "http://api.test/api/v1/admin/users/user%2Fid/reset-password");
  assert.equal(new Headers(requests[0].init?.headers).get("Authorization"), "Bearer admin-access");
  assert.deepEqual(JSON.parse(String(requests[0].init?.body)), {
    new_password: "new-password-at-least-12",
  });
});

test("super administrator changes user status and deletes an account with version checks", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  const updatedUser = {
    id: "user-id",
    email: "customer@example.test",
    display_name: "Customer",
    role: "CUSTOMER",
    is_active: false,
    last_login_at: null,
    created_at: "2026-07-21T00:00:00Z",
    updated_at: "2026-07-21T00:01:00Z",
    version: 3,
  };
  const fetcher: typeof fetch = async (input, init) => {
    requests.push({ url: String(input), init });
    return init?.method === "DELETE" ? new Response(null, { status: 204 }) : response(updatedUser);
  };

  await updateUserStatus("http://api.test", "admin-access", "user/id", false, 2, fetcher);
  await deleteUser("http://api.test", "admin-access", "user/id", 3, fetcher);

  assert.equal(requests[0].url, "http://api.test/api/v1/admin/users/user%2Fid");
  assert.deepEqual(JSON.parse(String(requests[0].init?.body)), { is_active: false, version: 2 });
  assert.equal(requests[1].url, "http://api.test/api/v1/admin/users/user%2Fid?version=3");
  assert.equal(requests[1].init?.method, "DELETE");
});

test("administrator updates and deletes an IP pool with optimistic versioning", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  const updatedPool = {
    id: "pool-id",
    name: "private-vlan",
    cluster_id: null,
    cidr: "10.20.0.0/24",
    prefix_length: 24,
    gateway: "10.20.0.1",
    dns_servers: ["10.20.0.2"],
    bridge: "vmbr20",
    vlan_tag: 20,
    ip_family: 4,
    allocation_strategy: "SEQUENTIAL",
    quarantine_seconds: 600,
    is_active: true,
    allocated_count: 0,
    quarantined_count: 0,
    availability_status: "AVAILABLE",
    version: 4,
  };
  const fetcher: typeof fetch = async (input, init) => {
    requests.push({ url: String(input), init });
    return init?.method === "DELETE"
      ? new Response(null, { status: 204 })
      : response(updatedPool);
  };

  await updateIpPool("http://api.test", "admin-access", "pool/id", {
    name: "private-vlan",
    version: 3,
  }, fetcher);
  await deleteIpPool("http://api.test", "admin-access", "pool/id", 4, fetcher);

  assert.equal(requests[0].url, "http://api.test/api/v1/admin/ip-pools/pool%2Fid");
  assert.equal(requests[0].init?.method, "PATCH");
  assert.deepEqual(JSON.parse(String(requests[0].init?.body)), {
    name: "private-vlan",
    version: 3,
  });
  assert.equal(requests[1].url, "http://api.test/api/v1/admin/ip-pools/pool%2Fid?version=4");
  assert.equal(requests[1].init?.method, "DELETE");
});

test("administrator updates and deletes an organization with optimistic versioning", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  const updatedOrganization = {
    id: "organization-id",
    name: "Renamed tenant",
    is_active: true,
    created_at: "2026-07-17T00:00:00Z",
    updated_at: "2026-07-17T00:01:00Z",
    version: 3,
  };
  const fetcher: typeof fetch = async (input, init) => {
    requests.push({ url: String(input), init });
    return init?.method === "DELETE"
      ? new Response(null, { status: 204 })
      : response(updatedOrganization);
  };

  await updateOrganization(
    "http://api.test",
    "admin-access",
    "organization/id",
    "Renamed tenant",
    2,
    fetcher,
  );
  await deleteOrganization(
    "http://api.test",
    "admin-access",
    "organization/id",
    3,
    fetcher,
  );
  await activateOrganization(
    "http://api.test",
    "admin-access",
    "organization/id",
    4,
    fetcher,
  );

  assert.equal(requests[0].url, "http://api.test/api/v1/admin/organizations/organization%2Fid");
  assert.equal(requests[0].init?.method, "PATCH");
  assert.deepEqual(JSON.parse(String(requests[0].init?.body)), {
    name: "Renamed tenant",
    version: 2,
  });
  assert.equal(requests[1].url, "http://api.test/api/v1/admin/organizations/organization%2Fid?version=3");
  assert.equal(requests[1].init?.method, "DELETE");
  assert.deepEqual(JSON.parse(String(requests[2].init?.body)), {
    is_active: true,
    version: 4,
  });
});

test("removed clusters are excluded from administrator cluster options", async () => {
  const baseCluster = {
    api_base_url: "https://pve.example.test:8006",
    ca_configured: false,
    last_connection_error_code: null,
    last_connected_at: "2026-07-17T00:00:00Z",
    credential: { token_identifier: "svc@pve!portal", configured: true, last_used_at: null },
    created_at: "2026-07-17T00:00:00Z",
    updated_at: "2026-07-17T00:00:00Z",
    version: 1,
  };
  const active = {
    ...baseCluster,
    id: "active-cluster",
    name: "active-pve",
    is_active: true,
  };
  const removed = {
    ...baseCluster,
    id: "removed-cluster",
    name: "removed-pve",
    is_active: false,
  };
  const fetcher: typeof fetch = async () => response({ items: [removed, active] });

  const listed = await listClusters("http://api.test", "admin-access", fetcher);

  assert.deepEqual(listed.map((item) => item.id), [active.id]);
});

test("creates a customer and immediately adds it to the selected organization", async () => {
  const organizationId = "d8c83325-968c-4cd4-a20f-17194d812d80";
  const createdUser = {
    id: "90900b84-3b1c-47fd-a155-12723af9eb6c",
    email: "new-customer@example.test",
    display_name: "New Customer",
    role: "CUSTOMER" as const,
    is_active: true,
    last_login_at: null,
    created_at: "2026-07-16T12:00:00Z",
    updated_at: "2026-07-16T12:00:00Z",
    version: 1,
  };
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    requests.push({ url, init });
    if (url.endsWith("/admin/users")) return response(createdUser, 201);
    if (url.endsWith(`/admin/organizations/${organizationId}/members`)) {
      return response({ id: "membership-id" }, 201);
    }
    return response({ error: { code: "NOT_FOUND", message: "unexpected request" } }, 404);
  };

  const result = await createOrganizationUser(
    "http://api.test",
    "admin-access",
    organizationId,
    {
      email: createdUser.email,
      display_name: createdUser.display_name,
      role: "CUSTOMER",
      password: "long-test-password",
    },
    fetcher,
  );

  assert.equal(result.id, createdUser.id);
  assert.equal(requests.length, 2);
  assert.deepEqual(JSON.parse(String(requests[1].init?.body)), { user_id: createdUser.id });
});

test("reports a recoverable partial result when organization assignment fails", async () => {
  const organizationId = "d8c83325-968c-4cd4-a20f-17194d812d80";
  const createdUser = {
    id: "90900b84-3b1c-47fd-a155-12723af9eb6c",
    email: "new-customer@example.test",
    display_name: "New Customer",
    role: "CUSTOMER" as const,
    is_active: true,
    last_login_at: null,
    created_at: "2026-07-16T12:00:00Z",
    updated_at: "2026-07-16T12:00:00Z",
    version: 1,
  };
  const fetcher: typeof fetch = async (input) => {
    const url = String(input);
    if (url.endsWith("/admin/users")) return response(createdUser, 201);
    return response({ error: { code: "ORGANIZATION_INACTIVE", message: "Organization is inactive." } }, 409);
  };

  await assert.rejects(
    createOrganizationUser(
      "http://api.test",
      "admin-access",
      organizationId,
      {
        email: createdUser.email,
        display_name: createdUser.display_name,
        role: "CUSTOMER",
        password: "long-test-password",
      },
      fetcher,
    ),
    (error: unknown) => {
      assert.ok(error instanceof OrganizationUserProvisionError);
      assert.equal(error.createdUser.id, createdUser.id);
      assert.equal((error.assignmentError as { code: string }).code, "ORGANIZATION_INACTIVE");
      return true;
    },
  );
});

test("searches a limited organization directory and preserves API failures", async () => {
  const requests: string[] = [];
  const organization = {
    id: "d8c83325-968c-4cd4-a20f-17194d812d80",
    name: "Integration organization",
    is_active: true,
    created_at: "2026-07-16T12:00:00Z",
    updated_at: "2026-07-16T12:00:00Z",
    version: 1,
  };
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    requests.push(url);
    assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer admin-access");
    return response({ items: [organization], total: 24, limit: 10, offset: 0 });
  };

  const result = await searchOrganizations(
    "http://api.test",
    "admin-access",
    { q: "Integration & Seoul", status: "all", sort: "name", limit: 10, offset: 0 },
    fetcher,
  );

  assert.equal(result.total, 24);
  assert.equal(result.items[0].id, organization.id);
  assert.ok(requests[0].endsWith("/admin/organizations?q=Integration+%26+Seoul&status=all&sort=name&limit=10&offset=0"));

  const deniedFetcher: typeof fetch = async () => response({
    error: { code: "ROLE_FORBIDDEN", message: "Role is not allowed." },
  }, 403);
  await assert.rejects(
    searchOrganizations("http://api.test", "customer-access", { q: "Integration", limit: 10 }, deniedFetcher),
    (error: unknown) => error instanceof AdminApiError && error.status === 403 && error.code === "ROLE_FORBIDDEN",
  );
});

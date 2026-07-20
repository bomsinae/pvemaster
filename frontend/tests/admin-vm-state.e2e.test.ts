import assert from "node:assert/strict";
import test from "node:test";

import {
  filterAdminWorkloads,
  getAdminWorkloadCapabilities,
  supportsAdminPowerAction,
} from "../lib/admin-vm-state.ts";
import type { Workload } from "../lib/admin-api.ts";

function workload(values: Partial<Workload>): Workload {
  return {
    id: crypto.randomUUID(), cluster_id: crypto.randomUUID(), cluster_name: "ty10",
    vmid: 100, node: "pve-a", kind: "QEMU", name: "web-01", power_state: "RUNNING",
    cpu_cores: 2, memory_bytes: 1024, disk_bytes: 2048, is_template: false,
    is_present: true, organization_id: null, organization_name: null,
    observed_at: "2026-07-15T00:00:00Z", version: 1, ...values,
  };
}

test("LXC supports safe power actions but not reset", () => {
  for (const action of ["start", "shutdown", "stop", "reboot"] as const) {
    assert.equal(supportsAdminPowerAction("LXC", action), true);
  }
  assert.equal(supportsAdminPowerAction("LXC", "reset"), false);
  assert.equal(supportsAdminPowerAction("QEMU", "reset"), true);
});

test("VM and CT expose distinct console and power capability sets", () => {
  assert.deepEqual(getAdminWorkloadCapabilities("QEMU"), {
    consoleAction: "novnc",
    powerActions: ["start", "shutdown", "reboot", "stop", "reset"],
    canUpdateSpec: true,
    canDelete: true,
  });
  assert.deepEqual(getAdminWorkloadCapabilities("LXC"), {
    consoleAction: "terminal",
    powerActions: ["start", "shutdown", "reboot", "stop"],
    canUpdateSpec: true,
    canDelete: true,
  });
});

test("admin VM inventory searches and filters large resource lists", () => {
  const workloads = [
    workload({ vmid: 102, name: "web-02", power_state: "STOPPED" }),
    workload({ vmid: 101, name: "web-01" }),
    workload({ vmid: 201, name: "database", node: "pve-b", kind: "LXC" }),
    workload({ vmid: 9000, name: "ubuntu-template", is_template: true }),
    workload({ vmid: 301, name: "removed", is_present: false }),
  ];

  assert.deepEqual(
    filterAdminWorkloads(workloads, { query: "web", kind: "QEMU", power: "ALL", node: "ALL" })
      .map((item) => item.vmid),
    [101, 102],
  );
  assert.deepEqual(
    filterAdminWorkloads(workloads, { query: "pve-b", kind: "ALL", power: "RUNNING", node: "ALL" })
      .map((item) => item.vmid),
    [201],
  );
  assert.equal(
    filterAdminWorkloads(workloads, { query: "", kind: "ALL", power: "ALL", node: "ALL" }).length,
    3,
  );
  assert.deepEqual(
    filterAdminWorkloads(workloads, {
      query: "",
      kind: "ALL",
      power: "ALL",
      node: "pve-a",
    }).map((item) => item.vmid),
    [101, 102],
  );
});

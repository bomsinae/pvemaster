import type { Workload } from "./admin-api";

export type VmKindFilter = "ALL" | Workload["kind"];
export type VmPowerFilter = "ALL" | "RUNNING" | "STOPPED";
export type AdminPowerAction = "start" | "shutdown" | "stop" | "reboot" | "reset";
export type AdminConsoleAction = "novnc" | "terminal";

export type AdminWorkloadCapabilities = {
  consoleAction: AdminConsoleAction;
  powerActions: readonly AdminPowerAction[];
  canUpdateSpec: boolean;
  canDelete: boolean;
};

const ADMIN_WORKLOAD_CAPABILITIES: Record<
  Workload["kind"],
  AdminWorkloadCapabilities
> = {
  QEMU: {
    consoleAction: "novnc",
    powerActions: ["start", "shutdown", "reboot", "stop", "reset"],
    canUpdateSpec: true,
    canDelete: true,
  },
  LXC: {
    consoleAction: "terminal",
    powerActions: ["start", "shutdown", "reboot", "stop"],
    canUpdateSpec: true,
    canDelete: true,
  },
};

export function getAdminWorkloadCapabilities(
  kind: Workload["kind"],
): AdminWorkloadCapabilities {
  return ADMIN_WORKLOAD_CAPABILITIES[kind];
}

export function supportsAdminPowerAction(
  kind: Workload["kind"],
  action: AdminPowerAction,
): boolean {
  return getAdminWorkloadCapabilities(kind).powerActions.includes(action);
}

export function filterAdminWorkloads(
  workloads: Workload[],
  filters: { query: string; kind: VmKindFilter; power: VmPowerFilter; node: string },
) {
  const query = filters.query.trim().toLocaleLowerCase();
  return workloads
    .filter((item) => item.is_present && !item.is_template)
    .filter((item) => filters.kind === "ALL" || item.kind === filters.kind)
    .filter((item) => filters.node === "ALL" || item.node === filters.node)
    .filter(
      (item) => filters.power === "ALL" || item.power_state.toUpperCase() === filters.power,
    )
    .filter((item) => {
      if (!query) return true;
      return [
        item.name,
        String(item.vmid),
        item.node,
        item.cluster_name,
        item.organization_name,
      ].some((value) => value?.toLocaleLowerCase().includes(query));
    })
    .sort((left, right) => {
      const byName = (left.name ?? "").localeCompare(right.name ?? "", "ko");
      return byName || left.vmid - right.vmid;
    });
}

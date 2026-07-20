import type { CustomerVm } from "./customer-api";

export type CustomerPowerFilter = "ALL" | "RUNNING" | "STOPPED";

export function filterCustomerVms(
  vms: CustomerVm[],
  filters: { query: string; power: CustomerPowerFilter },
): CustomerVm[] {
  const query = filters.query.trim().toLocaleLowerCase();
  return vms
    .filter(
      (vm) => filters.power === "ALL" || vm.power_state.toUpperCase() === filters.power,
    )
    .filter((vm) => {
      if (!query) return true;
      return [vm.name, ...vm.assigned_ip_addresses].some((value) =>
        value.toLocaleLowerCase().includes(query),
      );
    })
    .sort((left, right) => left.name.localeCompare(right.name, "ko"));
}

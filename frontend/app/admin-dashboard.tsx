"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  AuditLog,
  BackupRun,
  BackupStorageCandidate,
  BackupTarget,
  RestoreRun,
  AdminWorkloadJob,
  AdminApiError,
  Cluster,
  ClusterResourceOverview,
  ClusterRemovalCheck,
  ClusterGuest,
  ClusterNode,
  ClusterStorage,
  CurrentUser,
  IpPool,
  OperationsStatus,
  Organization,
  OrganizationPage,
  OrganizationSearchFilters,
  OrganizationMember,
  OrganizationUserProvisionError,
  Product,
  ProvisioningNode,
  ProvisionRequest,
  Template,
  Workload,
  addOrganizationMember,
  activateOrganization,
  assignWorkload,
  createCluster,
  createBackupTarget,
  createIpPool,
  createOrganization,
  createOrganizationUser,
  createProduct,
  createProvisionRequest,
  createTemplate,
  createUser,
  deleteIpPool,
  deleteUser,
  deleteOrganization,
  deleteCluster,
  deleteProduct,
  deleteTemplate,
  deleteAdminVm,
  getClusterInventory,
  getBackup,
  getRestore,
  getClusterResourceOverview,
  getClusterRemovalCheck,
  getOperationsStatus,
  getAdminWorkloadJob,
  importClusterWorkloads,
  listAuditLogs,
  listBackups,
  listBackupTargets,
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
  removeOrganizationMember,
  resetUserPassword,
  requestAdminWorkloadAction,
  requestWorkloadBackup,
  requestBackupRestore,
  discoverBackupStorages,
  searchOrganizations,
  testCluster,
  unassignWorkload,
  updateAdminVmSpec,
  updateBackupTarget,
  updateProduct,
  updateIpPool,
  updateOrganization,
  updateUserStatus,
  updateTemplate,
  upsertProvisioningNode,
} from "@/lib/admin-api";
import { endBrowserSession } from "@/lib/browser-session";
import { AuthSession, CustomerApiError } from "@/lib/customer-api";
import { openConsoleWindow } from "@/lib/console-window";
import {
  AdminSection,
  adminSections,
  hrefForSection,
  sectionFromSearch,
} from "@/lib/admin-navigation";
import {
  filterAdminWorkloads,
  getAdminWorkloadCapabilities,
  supportsAdminPowerAction,
  AdminPowerAction,
  VmKindFilter,
  VmPowerFilter,
} from "@/lib/admin-vm-state";
import { validateSshPublicKeys } from "@/lib/ssh-public-key";
import { generateSshRsaKeyPair } from "@/lib/ssh-keypair";
import { VmConsoleModal } from "./vm-console-modal";
import { ClusterMetricsPanel } from "./cluster-metrics";
import { useDialogFocus } from "./use-dialog-focus";

type Section = AdminSection;

const DEFAULT_AUDIT_PAGE_SIZE = 25;
const AUDIT_PAGE_SIZES = [25, 50, 100] as const;
const OVERVIEW_REFRESH_INTERVAL_MS = 10_000;
const CLUSTER_INVENTORY_REFRESH_INTERVAL_MS = 10_000;

const sectionLabels: Record<Section, string> = {
  overview: "운영 개요",
  clusters: "클러스터",
  vms: "가상 머신",
  backups: "백업",
  access: "사용자와 조직",
  networks: "IP 주소 관리",
  provisioning: "프로비저닝",
  audit: "감사 로그",
};

function readableError(error: unknown) {
  return error instanceof CustomerApiError || error instanceof AdminApiError
    ? `${error.message} · ${error.code}`
    : "관리자 API에 연결하지 못했습니다.";
}

function formatTime(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatBytes(value: number | null) {
  if (value === null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let result = value;
  let unit = 0;
  while (result >= 1024 && unit < units.length - 1) {
    result /= 1024;
    unit += 1;
  }
  return `${result.toFixed(result >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function formatTransferredBytes(value: number | null) {
  if (value === null) return "측정 정보 없음";
  if (value === 0) return "신규 데이터 없음";
  return formatBytes(value);
}

function formatDuration(startedAt: string | null, finishedAt: string | null) {
  if (!startedAt || !finishedAt) return "—";
  const seconds = Math.max(0, Math.round((Date.parse(finishedAt) - Date.parse(startedAt)) / 1000));
  if (seconds < 60) return `${seconds}초`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}분 ${seconds % 60}초`;
}

function formatPercent(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "—";
  return `${Math.round(Math.max(0, value) * 100)}%`;
}

function formatUptime(value: number | null) {
  if (value === null) return "—";
  const days = Math.floor(value / 86400);
  const hours = Math.floor((value % 86400) / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  if (days > 0) return `${days}일 ${hours}시간`;
  if (hours > 0) return `${hours}시간 ${minutes}분`;
  return `${minutes}분`;
}

function formatGuestCapacity(used: number | null, total: number | null) {
  if (used === null || !total) return "—";
  return `${formatPercent(used / total)} · ${formatBytes(used)} / ${formatBytes(total)}`;
}

function wholeGib(value: number) {
  return Math.floor(Math.max(0, value) / 1024 ** 3);
}

function availableStorageBytes(storages: ClusterStorage[], nodeName: string) {
  return storages
    .filter((item) => item.node === nodeName || item.node === null)
    .reduce((total, item) => {
      const available = item.avail ?? (
        item.total !== null && item.used !== null
          ? Math.max(0, item.total - item.used)
          : 0
      );
      return total + available;
    }, 0);
}

function StatusMark({ ok, label }: { ok: boolean | null; label: string }) {
  const tone = ok === null ? "neutral" : ok ? "ok" : "failed";
  return <span className={`admin-status ${tone}`}><i />{label}</span>;
}

export function AdminDashboard({
  apiBaseUrl,
  session,
  user,
  onSessionEnded,
}: {
  apiBaseUrl: string;
  session: AuthSession;
  user: CurrentUser;
  onSessionEnded: () => void;
}) {
  const [section, setSection] = useState<Section>("overview");
  const [status, setStatus] = useState<OperationsStatus | null>(null);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [clusterResources, setClusterResources] = useState<ClusterResourceOverview[]>([]);
  const [overviewRefreshing, setOverviewRefreshing] = useState(false);
  const [overviewStale, setOverviewStale] = useState(false);
  const [overviewLastUpdatedAt, setOverviewLastUpdatedAt] = useState<string | null>(null);
  const [overviewSecondsUntilRefresh, setOverviewSecondsUntilRefresh] = useState<number | null>(null);
  const overviewRequestRef = useRef<AbortController | null>(null);
  const overviewRefreshInFlightRef = useRef(false);
  const [selectedCluster, setSelectedCluster] = useState<string | null>(null);
  const selectedClusterRef = useRef<string | null>(null);
  const inventoryRequestIdRef = useRef(0);
  const inventoryLoadingRequestIdRef = useRef(0);
  const [clusterRemovalCheck, setClusterRemovalCheck] = useState<ClusterRemovalCheck | null>(null);
  const [checkingClusterRemoval, setCheckingClusterRemoval] = useState(false);
  const [nodes, setNodes] = useState<ClusterNode[]>([]);
  const [guests, setGuests] = useState<ClusterGuest[]>([]);
  const [storages, setStorages] = useState<ClusterStorage[]>([]);
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [selectedOrganization, setSelectedOrganization] = useState<Organization | null>(null);
  const [editingOrganization, setEditingOrganization] = useState<Organization | null>(null);
  const [organizationTotal, setOrganizationTotal] = useState(0);
  const [organizationMembers, setOrganizationMembers] = useState<OrganizationMember[]>([]);
  const [workloads, setWorkloads] = useState<Workload[]>([]);
  const [backupTargets, setBackupTargets] = useState<BackupTarget[]>([]);
  const [backupCandidates, setBackupCandidates] = useState<BackupStorageCandidate[]>([]);
  const [backupRuns, setBackupRuns] = useState<BackupRun[]>([]);
  const [activeBackupRun, setActiveBackupRun] = useState<BackupRun | null>(null);
  const [activeRestoreRun, setActiveRestoreRun] = useState<RestoreRun | null>(null);
  const [pools, setPools] = useState<IpPool[]>([]);
  const [editingPool, setEditingPool] = useState<IpPool | null>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [requests, setRequests] = useState<ProvisionRequest[]>([]);
  const [provisioningNodes, setProvisioningNodes] = useState<ProvisioningNode[]>([]);
  const [editingProvisioningNode, setEditingProvisioningNode] = useState<ProvisioningNode | null>(null);
  const [editingProduct, setEditingProduct] = useState<Product | null>(null);
  const [editingTemplate, setEditingTemplate] = useState<Template | null>(null);
  const [passwordResetUser, setPasswordResetUser] = useState<CurrentUser | null>(null);
  const [managedUser, setManagedUser] = useState<CurrentUser | null>(null);
  const [selectedWorkload, setSelectedWorkload] = useState<string | null>(null);
  const [backupFocusWorkload, setBackupFocusWorkload] = useState<string | null>(null);
  const [activeVmJob, setActiveVmJob] = useState<AdminWorkloadJob | null>(null);
  const [consoleWorkload, setConsoleWorkload] = useState<Workload | null>(null);
  const [audits, setAudits] = useState<AuditLog[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditOffset, setAuditOffset] = useState(0);
  const [auditPageSize, setAuditPageSize] = useState(DEFAULT_AUDIT_PAGE_SIZE);
  const auditOffsetRef = useRef(0);
  const auditPageSizeRef = useRef(DEFAULT_AUDIT_PAGE_SIZE);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [form, setForm] = useState<"cluster" | "cluster-delete" | "user" | "user-password-reset" | "user-status" | "user-delete" | "organization-user" | "organization" | "organization-delete" | "pool" | "pool-delete" | "product" | "product-delete" | "template" | "template-delete" | "node" | "vm" | "vm-spec" | "vm-delete" | null>(null);
  const drawerRef = useRef<HTMLElement>(null);

  useDialogFocus(Boolean(form), drawerRef, () => setForm(null));

  const isSuperAdmin = user.role === "SUPER_ADMIN";
  const navigation = useMemo<Section[]>(
    () => isSuperAdmin
      ? [...adminSections]
      : ["overview", "clusters", "vms", "backups", "access"],
    [isSuperAdmin],
  );

  const token = session.accessToken;

  useEffect(() => {
    selectedClusterRef.current = selectedCluster;
  }, [selectedCluster]);

  function navigateToSection(next: Section) {
    setForm(null);
    setNotice("");
    setMobileNavOpen(false);
    if (next === section) return;
    window.history.pushState(
      { ...window.history.state, pveMasterSection: next },
      "",
      hrefForSection(window.location.href, next),
    );
    setSection(next);
  }

  useEffect(() => {
    const restoreSection = () => {
      const next = sectionFromSearch(window.location.search, navigation);
      setForm(null);
      setNotice("");
      setSection(next);
    };

    const initial = sectionFromSearch(window.location.search, navigation);
    window.history.replaceState(
      { ...window.history.state, pveMasterSection: initial },
      "",
      hrefForSection(window.location.href, initial),
    );
    const initialRestore = window.setTimeout(restoreSection, 0);
    window.addEventListener("popstate", restoreSection);
    return () => {
      window.clearTimeout(initialRestore);
      window.removeEventListener("popstate", restoreSection);
    };
  }, [navigation]);

  function openConsole(workload: Workload) {
    if (!openConsoleWindow(workload.id)) {
      setConsoleWorkload(workload);
      setNotice("브라우저가 새 콘솔 창을 차단해 현재 화면에서 열었습니다.");
    }
  }

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(""), 6000);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  const refreshOverview = useCallback(async ({ showLoading = false }: { showLoading?: boolean } = {}) => {
    if (overviewRefreshInFlightRef.current) return;
    overviewRefreshInFlightRef.current = true;
    const controller = new AbortController();
    overviewRequestRef.current = controller;
    if (showLoading) setLoading(true);
    else setOverviewRefreshing(true);
    setError("");
    const abortableFetch: typeof fetch = (input, init) => fetch(input, { ...init, signal: controller.signal });
    try {
      const [nextStatus, nextClusters, nextClusterResources] = await Promise.all([
        getOperationsStatus(apiBaseUrl, token, abortableFetch),
        listClusters(apiBaseUrl, token, abortableFetch),
        getClusterResourceOverview(apiBaseUrl, token, abortableFetch),
      ]);
      setStatus(nextStatus);
      setClusters(nextClusters);
      setClusterResources(nextClusterResources);
      setOverviewLastUpdatedAt(new Date().toISOString());
      setOverviewStale(false);
    } catch (caught) {
      if (!(caught instanceof Error && caught.name === "AbortError")) {
        setOverviewStale(true);
        setError(readableError(caught));
      }
    } finally {
      if (overviewRequestRef.current === controller) overviewRequestRef.current = null;
      overviewRefreshInFlightRef.current = false;
      if (showLoading) setLoading(false);
      else setOverviewRefreshing(false);
    }
  }, [apiBaseUrl, token]);

  const loadSection = useCallback(async (
    next: Section,
    requestedAuditOffset?: number,
    requestedAuditPageSize?: number,
  ) => {
    if (next === "overview") {
      await refreshOverview({ showLoading: true });
      return;
    }
    setLoading(true);
    setError("");
    try {
      if (next === "clusters") {
        const nextClusters = await listClusters(apiBaseUrl, token);
        setClusters(nextClusters);
        setSelectedCluster((current) =>
          current && nextClusters.some((cluster) => cluster.id === current)
            ? current
            : (nextClusters[0]?.id ?? null),
        );
      } else if (next === "access") {
        const [nextUsers, organizationPage, nextWorkloads] = await Promise.all([
          listUsers(apiBaseUrl, token),
          searchOrganizations(apiBaseUrl, token, { limit: 10, offset: 0 }),
          listWorkloads(apiBaseUrl, token),
        ]);
        setUsers(nextUsers);
        setOrganizations(organizationPage.items);
        setOrganizationTotal(organizationPage.total);
        setWorkloads(nextWorkloads);
        if (!organizationPage.total) setOrganizationMembers([]);
        setSelectedOrganization((current) => current ?? organizationPage.items[0] ?? null);
      } else if (next === "networks") {
        const [nextPools, nextClusters] = await Promise.all([
          listIpPools(apiBaseUrl, token),
          listClusters(apiBaseUrl, token),
        ]);
        setPools(nextPools);
        setClusters(nextClusters);
      } else if (next === "vms") {
        const [nextWorkloads, nextRuns] = await Promise.all([
          listWorkloads(apiBaseUrl, token),
          listBackups(apiBaseUrl, token),
        ]);
        setWorkloads(nextWorkloads);
        setBackupRuns(nextRuns);
        if (isSuperAdmin) {
          const [nextClusters, nextOrganizations, nextPools, nextProducts, nextTemplates, nextNodes] = await Promise.all([
            listClusters(apiBaseUrl, token), listOrganizations(apiBaseUrl, token), listIpPools(apiBaseUrl, token),
            listProducts(apiBaseUrl, token), listTemplates(apiBaseUrl, token), listProvisioningNodes(apiBaseUrl, token),
          ]);
          setClusters(nextClusters); setOrganizations(nextOrganizations); setPools(nextPools);
          setProducts(nextProducts); setTemplates(nextTemplates); setProvisioningNodes(nextNodes);
        }
        const visible = nextWorkloads.filter((item) => item.is_present && !item.is_template);
        setSelectedWorkload((current) => current && visible.some((item) => item.id === current) ? current : (visible[0]?.id ?? null));
      } else if (next === "backups") {
        const [nextTargets, nextRuns, nextWorkloads, nextClusters] = await Promise.all([
          listBackupTargets(apiBaseUrl, token),
          listBackups(apiBaseUrl, token),
          listWorkloads(apiBaseUrl, token),
          listClusters(apiBaseUrl, token),
        ]);
        setBackupTargets(nextTargets);
        setBackupRuns(nextRuns);
        setWorkloads(nextWorkloads);
        setClusters(nextClusters);
      } else if (next === "provisioning") {
        const [nextProducts, nextTemplates, nextRequests, nextNodes, nextWorkloads, nextClusters] = await Promise.all([
          listProducts(apiBaseUrl, token),
          listTemplates(apiBaseUrl, token),
          listProvisionRequests(apiBaseUrl, token),
          listProvisioningNodes(apiBaseUrl, token),
          listWorkloads(apiBaseUrl, token),
          listClusters(apiBaseUrl, token),
        ]);
        setProducts(nextProducts);
        setTemplates(nextTemplates);
        setRequests(nextRequests);
        setProvisioningNodes(nextNodes);
        setWorkloads(nextWorkloads);
        setClusters(nextClusters);
      } else if (next === "audit") {
        const offset = requestedAuditOffset ?? auditOffsetRef.current;
        const limit = requestedAuditPageSize ?? auditPageSizeRef.current;
        const result = await listAuditLogs(apiBaseUrl, token, undefined, {
          limit,
          offset,
        });
        setAudits(result.items);
        setAuditTotal(result.total);
        setAuditOffset(result.offset);
        setAuditPageSize(result.limit);
        auditOffsetRef.current = result.offset;
        auditPageSizeRef.current = result.limit;
      }
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl, isSuperAdmin, refreshOverview, token]);

  useEffect(() => {
    if (section !== "overview") return;

    let stopped = false;
    let refreshTimer: number | undefined;
    let nextRefreshAt: number | null = null;

    const updateCountdown = () => {
      setOverviewSecondsUntilRefresh(
        nextRefreshAt === null
          ? null
          : Math.max(0, Math.ceil((nextRefreshAt - Date.now()) / 1000)),
      );
    };
    const schedule = () => {
      if (stopped || document.visibilityState === "hidden") {
        nextRefreshAt = null;
        updateCountdown();
        return;
      }
      nextRefreshAt = Date.now() + OVERVIEW_REFRESH_INTERVAL_MS;
      updateCountdown();
      refreshTimer = window.setTimeout(async () => {
        await refreshOverview();
        schedule();
      }, OVERVIEW_REFRESH_INTERVAL_MS);
    };
    const handleVisibility = () => {
      if (refreshTimer !== undefined) window.clearTimeout(refreshTimer);
      if (document.visibilityState === "hidden") {
        overviewRequestRef.current?.abort();
        nextRefreshAt = null;
        updateCountdown();
        return;
      }
      refreshTimer = window.setTimeout(async () => {
        await refreshOverview();
        schedule();
      }, 0);
    };

    schedule();
    const countdownTimer = window.setInterval(updateCountdown, 1000);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      stopped = true;
      if (refreshTimer !== undefined) window.clearTimeout(refreshTimer);
      window.clearInterval(countdownTimer);
      document.removeEventListener("visibilitychange", handleVisibility);
      overviewRequestRef.current?.abort();
    };
  }, [refreshOverview, section]);

  useEffect(() => {
    if (!activeVmJob || ["SUCCEEDED", "FAILED", "TIMEOUT"].includes(activeVmJob.status)) return;
    const timer = window.setTimeout(async () => {
      try {
        const job = await getAdminWorkloadJob(apiBaseUrl, token, activeVmJob.id);
        setActiveVmJob(job);
        if (["SUCCEEDED", "FAILED", "TIMEOUT"].includes(job.status)) {
          if (job.status === "SUCCEEDED") setNotice(`${job.action.toUpperCase()} 작업이 완료되었습니다.`);
          else setError(`${job.error_summary ?? "전원 작업이 실패했습니다."} · ${job.error_code ?? job.status}`);
          await loadSection("vms");
        }
      } catch (caught) { setError(readableError(caught)); }
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [activeVmJob, apiBaseUrl, loadSection, token]);

  useEffect(() => {
    if (!activeBackupRun || ["SUCCEEDED", "FAILED", "TIMEOUT"].includes(activeBackupRun.status)) return;
    const timer = window.setTimeout(async () => {
      try {
        const run = await getBackup(apiBaseUrl, token, activeBackupRun.id);
        setActiveBackupRun(run);
        setBackupRuns((current) => [run, ...current.filter((item) => item.id !== run.id)]);
        if (["SUCCEEDED", "FAILED", "TIMEOUT"].includes(run.status)) {
          if (run.status === "SUCCEEDED") setNotice("백업이 완료되었습니다.");
          else setError(`${run.error_summary ?? "백업이 실패했습니다."} · ${run.error_code ?? run.status}`);
          await loadSection("backups");
        }
      } catch (caught) { setError(readableError(caught)); }
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [activeBackupRun, apiBaseUrl, loadSection, token]);

  useEffect(() => {
    if (!activeRestoreRun || ["SUCCEEDED", "FAILED", "TIMEOUT"].includes(activeRestoreRun.status)) return;
    const timer = window.setTimeout(async () => {
      try {
        const run = await getRestore(apiBaseUrl, token, activeRestoreRun.id);
        setActiveRestoreRun(run);
        if (["SUCCEEDED", "FAILED", "TIMEOUT"].includes(run.status)) {
          if (run.status === "SUCCEEDED") {
            setNotice(`복구가 완료되었습니다 · VMID ${run.target_vmid}`);
            await loadSection("vms");
          } else {
            setError(`${run.error_summary ?? "복구가 실패했습니다."} · ${run.error_code ?? run.status}`);
          }
        }
      } catch (caught) { setError(readableError(caught)); }
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [activeRestoreRun, apiBaseUrl, loadSection, token]);

  useEffect(() => {
    const timer = window.setTimeout(() => { void loadSection(section); }, 0);
    return () => window.clearTimeout(timer);
  }, [loadSection, section]);

  const loadInventory = useCallback(async (clusterId: string, background = false) => {
    const requestId = inventoryRequestIdRef.current + 1;
    inventoryRequestIdRef.current = requestId;
    if (!background) {
      inventoryLoadingRequestIdRef.current = requestId;
      setLoading(true);
      setNodes([]);
      setGuests([]);
      setStorages([]);
    }
    setError("");
    try {
      const inventory = await getClusterInventory(apiBaseUrl, token, clusterId);
      if (
        requestId !== inventoryRequestIdRef.current
        || clusterId !== selectedClusterRef.current
      ) return;
      setNodes(inventory.nodes);
      setGuests(inventory.guests);
      setStorages(inventory.storages);
    } catch (caught) {
      if (
        requestId !== inventoryRequestIdRef.current
        || clusterId !== selectedClusterRef.current
      ) return;
      setError(readableError(caught));
      if (!background) {
        setNodes([]);
        setGuests([]);
        setStorages([]);
      }
    } finally {
      if (!background && requestId === inventoryLoadingRequestIdRef.current) {
        setLoading(false);
      }
    }
  }, [apiBaseUrl, token]);

  useEffect(() => {
    if (section !== "clusters" || !selectedCluster) return;

    const clusterId = selectedCluster;
    let stopped = false;
    let refreshing = false;
    let refreshTimer: number | undefined;

    const schedule = () => {
      if (refreshTimer !== undefined) window.clearTimeout(refreshTimer);
      if (stopped || document.visibilityState === "hidden") return;
      refreshTimer = window.setTimeout(async () => {
        if (!refreshing) {
          refreshing = true;
          await loadInventory(clusterId, true);
          refreshing = false;
        }
        schedule();
      }, CLUSTER_INVENTORY_REFRESH_INTERVAL_MS);
    };

    const start = async () => {
      refreshing = true;
      await loadInventory(clusterId);
      refreshing = false;
      schedule();
    };

    const handleVisibility = () => {
      if (document.visibilityState === "hidden") {
        if (refreshTimer !== undefined) window.clearTimeout(refreshTimer);
        refreshTimer = undefined;
        return;
      }
      if (!refreshing) {
        refreshing = true;
        void loadInventory(clusterId, true).finally(() => {
          refreshing = false;
          schedule();
        });
      } else {
        schedule();
      }
    };

    void start();
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      stopped = true;
      if (refreshTimer !== undefined) window.clearTimeout(refreshTimer);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [loadInventory, section, selectedCluster]);

  const loadOrganizationMembers = useCallback(async (organizationId: string) => {
    try {
      setOrganizationMembers(
        await listOrganizationMembers(apiBaseUrl, token, organizationId),
      );
    } catch (caught) {
      setOrganizationMembers([]);
      setError(readableError(caught));
    }
  }, [apiBaseUrl, token]);

  const searchOrganizationOptions = useCallback(
    (filters: OrganizationSearchFilters) => searchOrganizations(apiBaseUrl, token, filters),
    [apiBaseUrl, token],
  );

  useEffect(() => {
    if (section !== "access" || !selectedOrganization) return;
    const timer = window.setTimeout(() => {
      void loadOrganizationMembers(selectedOrganization.id);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadOrganizationMembers, section, selectedOrganization]);

  async function runClusterTest(clusterId: string) {
    setSaving(true);
    setError("");
    try {
      const result = await testCluster(apiBaseUrl, token, clusterId);
      setNotice(`연결 확인 완료 · PVE ${result.version ?? "version unknown"}`);
      await loadSection("clusters");
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setSaving(false);
    }
  }

  async function runWorkloadImport(clusterId: string) {
    setSaving(true);
    setError("");
    try {
      const result = await importClusterWorkloads(apiBaseUrl, token, clusterId);
      setNotice(
        `VM/CT ${result.discovered}개 확인 · ${result.created}개 가져옴 · ${result.updated}개 갱신`,
      );
      await loadInventory(clusterId);
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setSaving(false);
    }
  }

  async function discoverClusterBackups(clusterId: string) {
    setSaving(true); setError("");
    try {
      setBackupCandidates(await discoverBackupStorages(apiBaseUrl, token, clusterId));
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function registerBackupTarget(candidate: BackupStorageCandidate) {
    setSaving(true); setError("");
    try {
      await createBackupTarget(apiBaseUrl, token, candidate.cluster_id, candidate.storage_id);
      setNotice(`${candidate.storage_id} 백업 대상을 등록했습니다.`);
      await loadSection("backups");
      await discoverClusterBackups(candidate.cluster_id);
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function toggleBackupTarget(target: BackupTarget) {
    setSaving(true); setError("");
    try {
      await updateBackupTarget(apiBaseUrl, token, target, !target.is_enabled);
      await loadSection("backups");
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function runBackup(workloadId: string, targetId: string) {
    setSaving(true); setError("");
    try {
      const run = await requestWorkloadBackup(
        apiBaseUrl,
        token,
        workloadId,
        targetId,
        crypto.randomUUID(),
      );
      setActiveBackupRun(run);
      setBackupRuns((current) => [run, ...current.filter((item) => item.id !== run.id)]);
      setNotice(`백업 작업을 접수했습니다 · ${run.id.slice(0, 8)}`);
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function runRestore(
    backupRunId: string,
    payload: { target_node: string; target_vmid: number; target_name: string },
  ) {
    setSaving(true); setError("");
    try {
      const run = await requestBackupRestore(
        apiBaseUrl,
        token,
        backupRunId,
        payload,
        crypto.randomUUID(),
      );
      setActiveRestoreRun(run);
      setNotice(`복구 작업을 접수했습니다 · VMID ${run.target_vmid}`);
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  function openBackupForWorkload(workloadId: string) {
    setSelectedWorkload(workloadId);
    setBackupFocusWorkload(workloadId);
    navigateToSection("backups");
  }

  async function addMember(userId: string) {
    if (!selectedOrganization) return;
    setSaving(true);
    setError("");
    try {
      await addOrganizationMember(apiBaseUrl, token, selectedOrganization.id, userId);
      await loadOrganizationMembers(selectedOrganization.id);
      setUsers(await listUsers(apiBaseUrl, token));
      setNotice("조직 구성원을 추가했습니다.");
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setSaving(false);
    }
  }

  async function removeMember(userId: string) {
    if (!selectedOrganization) return;
    setSaving(true);
    setError("");
    try {
      await removeOrganizationMember(apiBaseUrl, token, selectedOrganization.id, userId);
      await loadOrganizationMembers(selectedOrganization.id);
      setUsers(await listUsers(apiBaseUrl, token));
      setNotice("구성원을 제거하고 고객 접근을 회수했습니다.");
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setSaving(false);
    }
  }

  async function assignToOrganization(workloadId: string, organizationId = selectedOrganization?.id ?? null) {
    if (!organizationId) return;
    setSaving(true);
    setError("");
    try {
      await assignWorkload(apiBaseUrl, token, workloadId, organizationId);
      setWorkloads(await listWorkloads(apiBaseUrl, token));
      setNotice("워크로드를 조직에 할당했습니다.");
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setSaving(false);
    }
  }

  async function unassignFromOrganization(workloadId: string) {
    setSaving(true);
    setError("");
    try {
      await unassignWorkload(apiBaseUrl, token, workloadId);
      setWorkloads(await listWorkloads(apiBaseUrl, token));
      setNotice("워크로드 할당을 회수했습니다.");
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setSaving(false);
    }
  }

  async function submitCluster(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setSaving(true);
    try {
      await createCluster(apiBaseUrl, token, {
        name: String(data.get("name")),
        api_base_url: String(data.get("api_base_url")),
        token_identifier: String(data.get("token_identifier")),
        token_secret: String(data.get("token_secret")),
        ca_bundle_pem: String(data.get("ca_bundle_pem") || "") || null,
      });
      setForm(null);
      setNotice("클러스터를 등록하고 최소 권한 연결 시험을 완료했습니다.");
      await loadSection("clusters");
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function submitClusterDelete(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!currentCluster) return;
    const confirmation = String(new FormData(event.currentTarget).get("confirmation") ?? "");
    if (confirmation !== currentCluster.name) {
      setError("클러스터 이름이 일치하지 않습니다.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await deleteCluster(apiBaseUrl, token, currentCluster.id);
      const remaining = await listClusters(apiBaseUrl, token);
      setClusters(remaining);
      setSelectedCluster(remaining[0]?.id ?? null);
      setNodes([]);
      setGuests([]);
      setStorages([]);
      setClusterRemovalCheck(null);
      setForm(null);
      setNotice(`${currentCluster.name} 클러스터 등록을 해제했습니다.`);
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function openClusterRemoval() {
    if (!currentCluster) return;
    setForm("cluster-delete");
    setClusterRemovalCheck(null);
    setCheckingClusterRemoval(true);
    setError("");
    try {
      setClusterRemovalCheck(await getClusterRemovalCheck(apiBaseUrl, token, currentCluster.id));
    } catch (caught) { setError(readableError(caught)); }
    finally { setCheckingClusterRemoval(false); }
  }

  async function submitUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const payload = {
      email: String(data.get("email")),
      display_name: String(data.get("display_name")),
      role: String(data.get("role")) as CurrentUser["role"],
      password: String(data.get("password")),
    };
    const organizationId = form === "organization-user" ? selectedOrganization?.id ?? null : null;
    setSaving(true);
    setError("");
    try {
      if (organizationId) {
        const organization = organizations.find((item) => item.id === organizationId);
        await createOrganizationUser(apiBaseUrl, token, organizationId, payload);
        setForm(null);
        setNotice(`${payload.display_name} 사용자를 ${organization?.name ?? "선택한 조직"}에 추가했습니다.`);
        await loadSection("access");
        await loadOrganizationMembers(organizationId);
      } else {
        await createUser(apiBaseUrl, token, payload);
        setForm(null);
        setNotice("사용자를 생성했습니다.");
        await loadSection("access");
      }
    } catch (caught) {
      if (caught instanceof OrganizationUserProvisionError) {
        await loadSection("access");
        setForm(null);
        setError(`${caught.createdUser.display_name} 계정은 생성했지만 조직 배정에 실패했습니다. 기존 사용자 선택에서 다시 추가해 주세요. · ${readableError(caught.assignmentError)}`);
      } else {
        setError(readableError(caught));
      }
    }
    finally { setSaving(false); }
  }

  async function submitUserPasswordReset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!passwordResetUser) return;
    const data = new FormData(event.currentTarget);
    const newPassword = String(data.get("new_password"));
    const confirmation = String(data.get("password_confirmation"));
    if (newPassword !== confirmation) {
      setError("새 비밀번호와 확인 값이 일치하지 않습니다.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await resetUserPassword(apiBaseUrl, token, passwordResetUser.id, newPassword);
      if (passwordResetUser.id === user.id) {
        try { await endBrowserSession(); } finally { onSessionEnded(); }
        return;
      }
      setForm(null);
      setNotice(`${passwordResetUser.display_name} 사용자의 비밀번호를 초기화하고 기존 세션을 종료했습니다.`);
      setPasswordResetUser(null);
      await loadSection("access");
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function submitUserStatus(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!managedUser) return;
    setSaving(true);
    setError("");
    try {
      const nextActive = !managedUser.is_active;
      await updateUserStatus(apiBaseUrl, token, managedUser.id, nextActive, managedUser.version);
      setUsers(await listUsers(apiBaseUrl, token));
      setNotice(`${managedUser.display_name} 사용자를 ${nextActive ? "활성화" : "비활성화"}했습니다.`);
      setManagedUser(null);
      setForm(null);
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function submitUserDelete(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!managedUser) return;
    setSaving(true);
    setError("");
    try {
      await deleteUser(apiBaseUrl, token, managedUser.id, managedUser.version);
      setUsers(await listUsers(apiBaseUrl, token));
      if (selectedOrganization) await loadOrganizationMembers(selectedOrganization.id);
      setNotice(`${managedUser.display_name} 사용자를 삭제했습니다.`);
      setManagedUser(null);
      setForm(null);
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function submitOrganization(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setSaving(true);
    setError("");
    try {
      const name = String(data.get("name"));
      if (editingOrganization) {
        const updated = await updateOrganization(apiBaseUrl, token, editingOrganization.id, name, editingOrganization.version);
        setForm(null);
        setEditingOrganization(null);
        await loadSection("access");
        setSelectedOrganization(updated);
        setNotice(`${updated.name} 조직을 수정했습니다.`);
      } else {
        const created = await createOrganization(apiBaseUrl, token, name);
        setForm(null);
        await loadSection("access");
        setSelectedOrganization(created);
        setNotice("조직을 생성했습니다.");
      }
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function submitOrganizationDelete(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingOrganization) return;
    setSaving(true);
    setError("");
    try {
      await deleteOrganization(apiBaseUrl, token, editingOrganization.id, editingOrganization.version);
      setForm(null);
      setNotice(`${editingOrganization.name} 조직을 비활성화했습니다.`);
      setEditingOrganization(null);
      setSelectedOrganization(null);
      setOrganizationMembers([]);
      await loadSection("access");
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function reactivateOrganization(organization: Organization) {
    setSaving(true);
    setError("");
    try {
      const updated = await activateOrganization(apiBaseUrl, token, organization.id, organization.version);
      setSelectedOrganization(updated);
      setOrganizations((current) => current.map((item) => item.id === updated.id ? updated : item));
      setNotice(`${updated.name} 조직을 활성화했습니다.`);
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function submitPool(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const clusterId = String(data.get("cluster_id") || "");
    setSaving(true);
    try {
      const payload = {
        name: String(data.get("name")), cidr: String(data.get("cidr")),
        gateway: String(data.get("gateway") || "") || null,
        dns_servers: String(data.get("dns_servers") || "").split(",").map((item) => item.trim()).filter(Boolean),
        bridge: String(data.get("bridge")), cluster_id: clusterId || null,
        vlan_tag: String(data.get("vlan_tag") || "") ? Number(data.get("vlan_tag")) : null,
        allocation_strategy: String(data.get("allocation_strategy")), quarantine_seconds: Number(data.get("quarantine_seconds")),
      };
      if (editingPool) {
        await updateIpPool(apiBaseUrl, token, editingPool.id, { ...payload, version: editingPool.version });
        setNotice(`${payload.name} IP 풀을 수정했습니다.`);
      } else {
        await createIpPool(apiBaseUrl, token, { ...payload, excluded_ranges: [] });
        setNotice("IP 풀을 생성했습니다.");
      }
      setForm(null); setEditingPool(null); await loadSection("networks");
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function submitPoolDelete(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingPool) return;
    setSaving(true);
    setError("");
    try {
      await deleteIpPool(apiBaseUrl, token, editingPool.id, editingPool.version);
      setForm(null);
      setNotice(`${editingPool.name} IP 풀을 삭제했습니다.`);
      setEditingPool(null);
      await loadSection("networks");
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function submitProduct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setSaving(true);
    try {
      const payload = {
        name: String(data.get("name")), cpu_cores: Number(data.get("cpu_cores")),
        memory_bytes: Number(data.get("memory_gib")) * 1024 ** 3,
        disk_bytes: Number(data.get("disk_gib")) * 1024 ** 3,
      };
      if (editingProduct) await updateProduct(apiBaseUrl, token, editingProduct.id, { ...payload, is_enabled: data.get("is_enabled") === "on" });
      else await createProduct(apiBaseUrl, token, payload);
      setForm(null); setEditingProduct(null); setNotice(editingProduct ? "상품 사양을 수정했습니다." : "상품 사양을 생성했습니다."); await loadSection("provisioning");
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function submitTemplate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setSaving(true); setError("");
    try {
      const payload: Record<string, unknown> = {
        name: String(data.get("name")),
        source_disk: String(data.get("source_disk")), default_storage: String(data.get("default_storage")),
        default_bridge: String(data.get("default_bridge")),
        default_vlan_tag: data.get("default_vlan_tag") ? Number(data.get("default_vlan_tag")) : null,
      };
      const sourceWorkloadId = String(data.get("source_workload_id"));
      if (editingTemplate) {
        if (sourceWorkloadId !== editingTemplate.source_workload_id) payload.source_workload_id = sourceWorkloadId;
        payload.is_enabled = data.get("is_enabled") === "on";
        await updateTemplate(apiBaseUrl, token, editingTemplate.id, payload);
      } else {
        await createTemplate(apiBaseUrl, token, { ...payload, source_workload_id: sourceWorkloadId });
      }
      setForm(null); setEditingTemplate(null); setNotice(editingTemplate ? "프로비저닝 템플릿을 수정했습니다." : "QEMU 템플릿을 프로비저닝 카탈로그에 등록했습니다."); await loadSection(section);
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function submitCatalogDelete(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const expected = editingProduct?.name ?? editingTemplate?.name ?? "";
    if (String(data.get("confirmation")) !== expected) {
      setError(`확인 문자열 '${expected}'을 정확히 입력하세요.`);
      return;
    }
    setSaving(true); setError("");
    try {
      if (editingProduct) await deleteProduct(apiBaseUrl, token, editingProduct.id);
      else if (editingTemplate) await deleteTemplate(apiBaseUrl, token, editingTemplate.id);
      setForm(null); setEditingProduct(null); setEditingTemplate(null);
      setNotice(editingProduct ? "상품을 삭제했습니다." : "템플릿 등록을 삭제했습니다. Proxmox 원본은 변경되지 않았습니다.");
      await loadSection("provisioning");
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function submitNode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setSaving(true); setError("");
    try {
      await upsertProvisioningNode(apiBaseUrl, token, {
        cluster_id: String(data.get("cluster_id")), name: String(data.get("name")),
        is_enabled: data.get("is_enabled") === "on",
        is_maintenance: data.get("is_maintenance") === "on",
        available_memory_bytes: Number(data.get("memory_gib")) * 1024 ** 3,
        available_storage_bytes: Number(data.get("storage_gib")) * 1024 ** 3,
      });
      setForm(null); setEditingProvisioningNode(null);
      setNotice(editingProvisioningNode ? "노드 배치 정책을 수정했습니다." : "프로비저닝 노드를 등록했습니다.");
      await loadSection(section);
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function submitVm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const selectedTemplate = templates.find((item) => item.id === String(data.get("template_id")));
    const source = workloads.find((item) => item.id === selectedTemplate?.source_workload_id);
    const sshPublicKeys = validateSshPublicKeys(String(data.get("ssh_public_keys")));
    if (sshPublicKeys.error) {
      setError(sshPublicKeys.error);
      return;
    }
    setSaving(true); setError("");
    try {
      const request = await createProvisionRequest(apiBaseUrl, token, {
        product_id: String(data.get("product_id")), template_id: String(data.get("template_id")),
        organization_id: String(data.get("organization_id")), target_cluster_id: source?.cluster_id,
        target_node_id: String(data.get("target_node_id") || "") || null,
        target_vmid: data.get("target_vmid") ? Number(data.get("target_vmid")) : null,
        target_name: String(data.get("target_name")), ip_pool_id: String(data.get("ip_pool_id")), ip_address: null,
        cloud_init: {
          username: String(data.get("username")),
          ssh_public_keys: sshPublicKeys.keys,
        },
        start_after_create: data.get("start_after_create") === "on",
      }, crypto.randomUUID());
      setForm(null); setNotice(`VM 생성 요청을 접수했습니다 · ${request.job_id.slice(0, 8)}`); await loadSection("vms");
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function runVmAction(workload: Workload, action: AdminPowerAction) {
    if (!supportsAdminPowerAction(workload.kind, action)) return;
    const warnings: Partial<Record<AdminWorkloadJob["action"], string>> = {
      shutdown: "게스트 OS에 정상 종료를 요청할까요?",
      reboot: "게스트 OS를 재부팅할까요?",
      stop: "강제 중지는 전원 차단(SIGKILL)에 해당합니다. 계속할까요?",
      reset: "강제 재설정은 정상 종료 절차를 거치지 않습니다. 계속할까요?",
    };
    if (warnings[action] && !window.confirm(warnings[action])) return;
    setSaving(true); setError("");
    try {
      const job = await requestAdminWorkloadAction(apiBaseUrl, token, workload.id, action, crypto.randomUUID());
      setActiveVmJob(job); setNotice(`${action.toUpperCase()} 작업을 접수했습니다 · ${job.job_id.slice(0, 8)}`);
    } catch (caught) { setError(readableError(caught)); }
    finally { setSaving(false); }
  }

  async function submitVmSpec(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const workload = workloads.find((item) => item.id === selectedWorkload);
    if (!workload) return;
    const data = new FormData(event.currentTarget);
    setSaving(true); setError("");
    try {
      const job = await updateAdminVmSpec(apiBaseUrl, token, workload.id, {
        cpu_cores: Number(data.get("cpu_cores")), memory_gib: Number(data.get("memory_gib")),
        disk_gib: data.get("disk_gib") ? Number(data.get("disk_gib")) : null,
        version: workload.version, reason: String(data.get("reason") || "") || null,
      }, crypto.randomUUID());
      setActiveVmJob(job); setForm(null); setNotice(`사양 변경을 접수했습니다 · ${job.job_id.slice(0, 8)}`);
    } catch (caught) { setError(readableError(caught)); } finally { setSaving(false); }
  }

  async function submitVmDelete(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const workload = workloads.find((item) => item.id === selectedWorkload);
    if (!workload) return;
    const data = new FormData(event.currentTarget);
    setSaving(true); setError("");
    try {
      const job = await deleteAdminVm(apiBaseUrl, token, workload.id, {
        confirmation: String(data.get("confirmation")), reason: String(data.get("reason") || "") || null,
      }, crypto.randomUUID());
      setActiveVmJob(job); setForm(null); setNotice(`삭제 작업을 접수했습니다 · ${job.job_id.slice(0, 8)}`);
    } catch (caught) { setError(readableError(caught)); } finally { setSaving(false); }
  }

  async function logout() {
    try { await endBrowserSession(); } finally { onSessionEnded(); }
  }

  const currentCluster = clusters.find((cluster) => cluster.id === selectedCluster) ?? null;

  return (
    <main className="admin-shell">
      <aside className="admin-nav">
        <div className="admin-brand"><span className="brand-mark">PM</span><div><strong>PVE Master</strong><small>Control plane</small></div></div>
        <button
          type="button"
          className="admin-menu-toggle"
          aria-expanded={mobileNavOpen}
          aria-controls="admin-primary-navigation"
          onClick={() => setMobileNavOpen((open) => !open)}
        >
          <span>메뉴</span>
          <strong>{sectionLabels[section]}</strong>
          <span aria-hidden="true">{mobileNavOpen ? "−" : "+"}</span>
        </button>
        <nav id="admin-primary-navigation" className={mobileNavOpen ? "mobile-open" : ""} aria-label="관리자 메뉴">
          {navigation.map((item) => (
            <button key={item} type="button" aria-current={section === item ? "page" : undefined} className={section === item ? "active" : ""} onClick={() => { if (item === "backups") setBackupFocusWorkload(null); navigateToSection(item); }}>
              <span>{sectionLabels[item]}</span>
            </button>
          ))}
        </nav>
        <div className="admin-identity">
          <span>{user.display_name.slice(0, 1).toUpperCase()}</span>
          <div><strong>{user.display_name}</strong><small>{user.role}</small></div>
          <button onClick={logout} aria-label="로그아웃">↗</button>
        </div>
      </aside>

      <section className="admin-workspace">
        <header className="admin-topbar">
          <div><p className="eyebrow">{section}</p><h1>{sectionLabels[section]}</h1></div>
          <div className="admin-top-actions"><span>{section === "overview" ? (loading || overviewRefreshing ? "자원 갱신 중" : overviewStale ? "이전 데이터 표시 중" : overviewSecondsUntilRefresh === null ? "자동 갱신 일시 중지" : `자동 갱신 ${overviewSecondsUntilRefresh}초`) : section === "clusters" ? (loading ? "인벤토리 갱신 중" : "10초 자동 갱신") : loading ? "동기화 중" : "방금 갱신됨"}</span><button onClick={() => loadSection(section)} disabled={loading || (section === "overview" && overviewRefreshing)}>새로고침</button></div>
        </header>

        {error && !form && <div className="admin-message error" role="alert"><span>{error}</span><button onClick={() => setError("")}>×</button></div>}
        {notice && <div className="admin-message notice" role="status" aria-live="polite"><span>{notice}</span><button onClick={() => setNotice("")} aria-label="알림 닫기">×</button></div>}

        {section === "overview" && <Overview apiBaseUrl={apiBaseUrl} token={token} status={status} clusters={clusters} resources={clusterResources} loading={loading} refreshing={overviewRefreshing} stale={overviewStale} lastUpdatedAt={overviewLastUpdatedAt} secondsUntilRefresh={overviewSecondsUntilRefresh} />}
        {section === "clusters" && (
          <ClustersView clusters={clusters} current={currentCluster} nodes={nodes} guests={guests} storages={storages}
            selectedId={selectedCluster} onSelect={setSelectedCluster} onTest={runClusterTest} onImport={runWorkloadImport} onCreate={() => setForm("cluster")} onDelete={openClusterRemoval} saving={saving || loading} canDelete={isSuperAdmin} />
        )}
        {section === "vms" && <VmOperationsView workloads={workloads} backupRuns={backupRuns} onSelect={setSelectedWorkload} onCreate={() => setForm("vm")} onEdit={() => setForm("vm-spec")} onDelete={() => setForm("vm-delete")} onBackup={openBackupForWorkload} onAction={runVmAction} onConsole={openConsole} activeJob={activeVmJob} saving={saving} canManage={isSuperAdmin} />}
        {section === "backups" && <BackupsView clusters={clusters} workloads={workloads} targets={backupTargets} candidates={backupCandidates} runs={backupRuns} preferredWorkloadId={backupFocusWorkload} activeRun={activeBackupRun} activeRestore={activeRestoreRun} saving={saving} canConfigure={isSuperAdmin} onClearPreferredWorkload={() => setBackupFocusWorkload(null)} onDiscover={discoverClusterBackups} onRegister={registerBackupTarget} onToggle={toggleBackupTarget} onBackup={runBackup} onRestore={runRestore} />}
        {section === "access" && <AccessView currentUserId={user.id} users={users} organizations={organizations} organizationTotal={organizationTotal} members={organizationMembers} workloads={workloads} selectedOrganization={selectedOrganization} canWrite={isSuperAdmin} saving={saving} onSelectOrganization={(organization) => { setSelectedOrganization(organization); setOrganizations((current) => current.some((item) => item.id === organization.id) ? current : [organization, ...current]); }} onSearchOrganizations={searchOrganizationOptions} onAddMember={addMember} onRemoveMember={removeMember} onAssign={assignToOrganization} onUnassign={unassignFromOrganization} onUser={() => setForm("user")} onResetPassword={(targetUser) => { setPasswordResetUser(targetUser); setForm("user-password-reset"); }} onUserStatus={(targetUser) => { setManagedUser(targetUser); setForm("user-status"); }} onDeleteUser={(targetUser) => { setManagedUser(targetUser); setForm("user-delete"); }} onCreateMember={() => setForm("organization-user")} onOrganization={() => { setEditingOrganization(null); setForm("organization"); }} onEditOrganization={(organization) => { setEditingOrganization(organization); setForm("organization"); }} onActivateOrganization={reactivateOrganization} onDeleteOrganization={(organization) => { setEditingOrganization(organization); setForm("organization-delete"); }} />}
        {section === "networks" && <NetworksView pools={pools} clusters={clusters} onCreate={() => { setEditingPool(null); setForm("pool"); }} onEdit={(pool) => { setEditingPool(pool); setForm("pool"); }} onDelete={(pool) => { setEditingPool(pool); setForm("pool-delete"); }} />}
        {section === "provisioning" && <ProvisioningView products={products} templates={templates} workloads={workloads} nodes={provisioningNodes} clusters={clusters} requests={requests} onCreateProduct={() => { setEditingProduct(null); setForm("product"); }} onEditProduct={(product) => { setEditingProduct(product); setForm("product"); }} onDeleteProduct={(product) => { setEditingProduct(product); setEditingTemplate(null); setForm("product-delete"); }} onCreateTemplate={() => { setEditingTemplate(null); setForm("template"); }} onEditTemplate={(template) => { setEditingTemplate(template); setForm("template"); }} onDeleteTemplate={(template) => { setEditingTemplate(template); setEditingProduct(null); setForm("template-delete"); }} onCreateNode={() => { setEditingProvisioningNode(null); setForm("node"); }} onEditNode={(node) => { setEditingProvisioningNode(node); setForm("node"); }} />}
        {section === "audit" && <AuditView audits={audits} total={auditTotal} offset={auditOffset} pageSize={auditPageSize} loading={loading} onPageChange={(offset) => { void loadSection("audit", offset); }} onPageSizeChange={(limit) => { void loadSection("audit", 0, limit); }} />}
      </section>

      {form && (
        <div className="admin-drawer-backdrop" onMouseDown={() => setForm(null)}>
          <aside ref={drawerRef} tabIndex={-1} className="admin-drawer" role="dialog" aria-modal="true" aria-label="관리 작업" onMouseDown={(event) => event.stopPropagation()}>
            <button className="drawer-close" onClick={() => setForm(null)} aria-label="관리 작업 닫기">×</button>
            {error && <div className="drawer-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError("")} aria-label="오류 메시지 닫기">×</button></div>}
            {form === "cluster" && <ClusterForm onSubmit={submitCluster} saving={saving} />}
            {form === "cluster-delete" && currentCluster && <ClusterDeleteForm cluster={currentCluster} check={clusterRemovalCheck} checking={checkingClusterRemoval} onSubmit={submitClusterDelete} saving={saving} />}
            {(form === "user" || form === "organization-user") && <UserForm onSubmit={submitUser} saving={saving} organizationName={form === "organization-user" ? selectedOrganization?.name ?? null : null} />}
            {form === "user-password-reset" && passwordResetUser && <UserPasswordResetForm user={passwordResetUser} onSubmit={submitUserPasswordReset} saving={saving} />}
            {form === "user-status" && managedUser && <UserStatusForm user={managedUser} onSubmit={submitUserStatus} saving={saving} />}
            {form === "user-delete" && managedUser && <UserDeleteForm user={managedUser} onSubmit={submitUserDelete} saving={saving} />}
            {form === "organization" && <OrganizationForm onSubmit={submitOrganization} saving={saving} existing={editingOrganization} />}
            {form === "organization-delete" && editingOrganization && <OrganizationDeleteForm organization={editingOrganization} memberCount={organizationMembers.length} workloadCount={workloads.filter((item) => item.organization_id === editingOrganization.id).length} onSubmit={submitOrganizationDelete} saving={saving} />}
            {form === "pool" && <PoolForm onSubmit={submitPool} saving={saving} clusters={clusters} existing={editingPool} />}
            {form === "pool-delete" && editingPool && <PoolDeleteForm pool={editingPool} onSubmit={submitPoolDelete} saving={saving} />}
            {form === "product" && <ProductForm onSubmit={submitProduct} saving={saving} existing={editingProduct} />}
            {form === "product-delete" && editingProduct && <CatalogDeleteForm kind="상품" name={editingProduct.name} copy="프로비저닝 이력이 참조하는 상품은 삭제할 수 없습니다. 새 요청에서만 제외하려면 상품을 수정해 비활성화하세요." onSubmit={submitCatalogDelete} saving={saving} />}
            {form === "template" && <TemplateForm onSubmit={submitTemplate} saving={saving} workloads={workloads} existing={editingTemplate} />}
            {form === "template-delete" && editingTemplate && <CatalogDeleteForm kind="템플릿" name={editingTemplate.name} copy="플랫폼의 등록만 삭제하며 Proxmox 원본 템플릿은 삭제하지 않습니다. 프로비저닝 이력이 참조하면 삭제할 수 없습니다." onSubmit={submitCatalogDelete} saving={saving} />}
            {form === "node" && <ProvisioningNodeForm onSubmit={submitNode} saving={saving} clusters={clusters} existing={editingProvisioningNode} apiBaseUrl={apiBaseUrl} token={token} />}
            {form === "vm" && <VmCreateForm onSubmit={submitVm} saving={saving} products={products} templates={templates} organizations={organizations} pools={pools} nodes={provisioningNodes} workloads={workloads} />}
            {form === "vm-spec" && selectedWorkload && <VmSpecForm workload={workloads.find((item) => item.id === selectedWorkload)!} onSubmit={submitVmSpec} saving={saving} />}
            {form === "vm-delete" && selectedWorkload && <VmDeleteForm workload={workloads.find((item) => item.id === selectedWorkload)!} onSubmit={submitVmDelete} saving={saving} />}
          </aside>
        </div>
      )}
      {consoleWorkload && (
        <VmConsoleModal
          apiBaseUrl={apiBaseUrl}
          accessToken={token}
          workloadId={consoleWorkload.id}
          workloadName={consoleWorkload.name ?? `VMID ${consoleWorkload.vmid}`}
          workloadKind={consoleWorkload.kind}
          onClose={() => setConsoleWorkload(null)}
        />
      )}
    </main>
  );
}

function Overview({ apiBaseUrl, token, status, clusters, resources, loading, refreshing, stale, lastUpdatedAt, secondsUntilRefresh }: {
  apiBaseUrl: string;
  token: string;
  status: OperationsStatus | null;
  clusters: Cluster[];
  resources: ClusterResourceOverview[];
  loading: boolean;
  refreshing: boolean;
  stale: boolean;
  lastUpdatedAt: string | null;
  secondsUntilRefresh: number | null;
}) {
  const connected = resources.length
    ? resources.filter((cluster) => cluster.connected).length
    : status?.clusters.filter((cluster) => cluster.connected).length ?? 0;
  return <div className="admin-content enter-admin">
    <section className="signal-strip">
      <div><span>할당 VM/CT</span><strong>{status ? `${status.workloads.assigned}/${status.workloads.total}` : "—"}</strong><small>organization assigned</small></div>
      <div><span>미할당 VM/CT</span><strong>{status?.workloads.unassigned ?? "—"}</strong><small>available inventory</small></div>
      <div><span>조직</span><strong>{status ? `${status.directory.organizations.active}/${status.directory.organizations.total}` : "—"}</strong><small>active organizations</small></div>
      <div><span>사용자</span><strong>{status ? `${status.directory.users.active}/${status.directory.users.total}` : "—"}</strong><small>active users</small></div>
      <div><span>클러스터</span><strong>{connected}/{clusters.length}</strong><small>connected</small></div>
    </section>
    <section className="admin-section"><div className="admin-section-title"><div><p className="eyebrow">Current state</p><h2>운영 신호</h2></div><span>실시간 운영 API 기준</span></div>
      {status?.alerts.length ? <div className="alert-list">{status.alerts.map((alert) => <div key={`${alert.code}-${alert.resource_id}`}><StatusMark ok={false} label={alert.severity} /><strong>{alert.code}</strong><p>{alert.message}</p></div>)}</div> : <div className="calm-state"><span>✓</span><div><strong>활성 경보가 없습니다.</strong><p>클러스터 연결, 작업 처리, 프로비저닝과 IP 풀 가용성이 정상 범위입니다.</p></div></div>}
      {status?.scheduler?.length ? <div className="scheduler-status-grid" aria-label="정기 작업 최근 실행 상태">{status.scheduler.map((job) => <article key={job.job_name}><header><code>{job.job_name}</code><StatusMark ok={job.status === "SUCCEEDED"} label={job.status} /></header><strong>{job.last_success_at ? `최근 성공 ${formatTime(job.last_success_at)}` : "성공 기록 없음"}</strong><small>{job.error_code ? `오류 ${job.error_code}` : `최근 처리 ${job.processed_count}건`}</small></article>)}</div> : null}
    </section>
    <section className="admin-section cluster-fleet-section"><div className="admin-section-title"><div><p className="eyebrow">Resource fleet</p><h2>클러스터 자원 현황</h2></div><div className={`overview-refresh-status${stale ? " stale" : ""}`} aria-live="polite"><span className={refreshing ? "refreshing" : ""}><i />{refreshing ? "갱신 중" : stale ? "갱신 실패 · 이전 데이터" : secondsUntilRefresh === null ? "일시 중지" : `${secondsUntilRefresh}초 후 갱신`}</span><time>{lastUpdatedAt ? `최근 갱신 ${formatTime(lastUpdatedAt)}` : "첫 상태 확인 중"}</time></div></div>
      {loading && !resources.length ? <ClusterResourceSkeleton /> : resources.length ? <div className="cluster-resource-grid">{resources.map((cluster) => <ClusterResourceCard key={cluster.cluster_id} cluster={cluster} apiBaseUrl={apiBaseUrl} token={token} />)}</div> : <p className="empty-state">등록된 클러스터가 없습니다.</p>}
    </section>
  </div>;
}

function ClusterResourceSkeleton() {
  return <div className="cluster-resource-grid" aria-label="클러스터 자원 정보를 불러오는 중">
    {[0, 1].map((item) => <div className="cluster-resource-card loading" key={item}><span /><span /><span /><span /></div>)}
  </div>;
}

function ClusterResourceCard({ cluster, apiBaseUrl, token }: { cluster: ClusterResourceOverview; apiBaseUrl: string; token: string }) {
  if (!cluster.connected) {
    return <article className="cluster-resource-card failed">
      <header><div><p className="eyebrow">Cluster offline</p><h3>{cluster.name}</h3></div><StatusMark ok={false} label="조회 실패" /></header>
      <div className="cluster-resource-error"><strong>실시간 자원을 읽지 못했습니다.</strong><p>{cluster.error_code ?? "PVE_REQUEST_FAILED"}</p><small>{formatTime(cluster.observed_at)} 관측</small></div>
    </article>;
  }

  const cpuCores = cluster.nodes.reduce((total, node) => total + (node.maxcpu ?? 0), 0);
  const usedCpuCores = cluster.nodes.reduce((total, node) => total + ((node.cpu ?? 0) * (node.maxcpu ?? 0)), 0);
  const cpuRatio = cpuCores > 0 ? usedCpuCores / cpuCores : null;
  const memoryUsed = cluster.nodes.reduce((total, node) => total + (node.memory_used_bytes ?? 0), 0);
  const memoryTotal = cluster.nodes.reduce((total, node) => total + (node.memory_total_bytes ?? 0), 0);
  const diskUsed = cluster.vm_storage_used_bytes;
  const diskTotal = cluster.vm_storage_total_bytes;
  const loadValues = cluster.nodes.map((node) => node.load_average[0]).filter((value): value is number => value !== undefined);
  const averageLoad = loadValues.length ? loadValues.reduce((sum, value) => sum + value, 0) / loadValues.length : null;
  const totalLoad = loadValues.reduce((sum, value) => sum + value, 0);

  return <article className="cluster-resource-card">
    <header><div><p className="eyebrow">Live cluster</p><h3>{cluster.name}</h3></div><StatusMark ok label="연결됨" /></header>
    <div className="cluster-resource-meta"><span>{cluster.node_count} nodes</span><span>{cluster.running_guest_count}/{cluster.guest_count} running</span><span>{cluster.vm_storage_count} VM storages</span><time>{formatTime(cluster.observed_at)}</time></div>
    <div className="cluster-metric-grid">
      <ResourceMetric label="CPU" value={formatPercent(cpuRatio)} detail={cpuCores ? `${usedCpuCores.toFixed(1)} / ${cpuCores} cores` : "측정값 없음"} ratio={cpuRatio} />
      <ResourceMetric label="RAM" value={memoryTotal ? formatPercent(memoryUsed / memoryTotal) : "—"} detail={`${formatBytes(memoryUsed)} / ${formatBytes(memoryTotal || null)}`} ratio={memoryTotal ? memoryUsed / memoryTotal : null} />
      <ResourceMetric label="VM/CT DISK" value={diskTotal ? formatPercent(diskUsed / diskTotal) : "—"} detail={diskTotal ? `${formatBytes(diskUsed)} / ${formatBytes(diskTotal)}` : "VM 스토리지 없음"} ratio={diskTotal ? diskUsed / diskTotal : null} />
      <ResourceMetric label="LOAD 1M" value={averageLoad === null ? "—" : averageLoad.toFixed(2)} detail={`${cluster.qemu_count} VM · ${cluster.lxc_count} CT`} ratio={cpuCores && averageLoad !== null ? totalLoad / cpuCores : null} />
    </div>
    <div className="cluster-node-table"><div className="cluster-node-head"><span>노드</span><span>CPU</span><span>RAM</span><span>루트 디스크</span><span>Load 1/5/15</span><span>가동시간</span></div>
      {cluster.nodes.map((node) => {
        const ramRatio = node.memory_used_bytes !== null && node.memory_total_bytes ? node.memory_used_bytes / node.memory_total_bytes : null;
        const diskRatio = node.disk_used_bytes !== null && node.disk_total_bytes ? node.disk_used_bytes / node.disk_total_bytes : null;
        return <div className="cluster-node-row" key={node.node}><span><StatusMark ok={(node.status ?? "").toLowerCase() === "online"} label={node.node} /></span><strong>{formatPercent(node.cpu)}</strong><strong>{formatPercent(ramRatio)}</strong><strong>{formatPercent(diskRatio)}</strong><code>{node.load_average.length ? node.load_average.map((value) => value.toFixed(2)).join(" / ") : "—"}</code><span>{formatUptime(node.uptime_seconds)}</span></div>;
      })}
    </div>
    <ClusterMetricsPanel apiBaseUrl={apiBaseUrl} token={token} clusterId={cluster.cluster_id} nodes={cluster.nodes} />
  </article>;
}

function ResourceMetric({ label, value, detail, ratio }: { label: string; value: string; detail: string; ratio: number | null }) {
  const width = `${Math.min(100, Math.max(0, (ratio ?? 0) * 100))}%`;
  const level = ratio !== null && ratio >= 0.9 ? "critical" : ratio !== null && ratio >= 0.75 ? "warning" : "normal";
  return <div className={`cluster-metric ${level}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small><i aria-hidden="true"><b style={{ width }} /></i></div>;
}

function ClustersView({ clusters, current, nodes, guests, storages, selectedId, onSelect, onTest, onImport, onCreate, onDelete, saving, canDelete }: {
  clusters: Cluster[]; current: Cluster | null; nodes: ClusterNode[]; guests: ClusterGuest[]; storages: ClusterStorage[];
  selectedId: string | null; onSelect: (id: string) => void; onTest: (id: string) => void; onImport: (id: string) => void; onCreate: () => void; onDelete: () => void; saving: boolean; canDelete: boolean;
}) {
  return <div className="admin-content cluster-layout enter-admin">
    <section className="cluster-switcher">
      <div className="cluster-switcher-head">
        <div><p className="eyebrow">Cluster scope</p><h2>운영 대상</h2><span>{clusters.length} registered</span></div>
        <button className="accent-button" onClick={onCreate}>클러스터 등록</button>
      </div>
      {clusters.length ? <div className="cluster-switcher-track" role="group" aria-label="관리할 클러스터 선택">
        {clusters.map((cluster) => {
          const selected = selectedId === cluster.id;
          const connected = !cluster.last_connection_error_code && cluster.is_active;
          return <button
            key={cluster.id}
            type="button"
            aria-pressed={selected}
            className={selected ? "cluster-switch active" : "cluster-switch"}
            onClick={() => onSelect(cluster.id)}
          >
            <i className={connected ? "connected" : "failed"} aria-hidden="true" />
            <span><strong>{cluster.name}</strong><small>{cluster.api_base_url}</small></span>
            <em>{connected ? "연결됨" : "확인 필요"}</em>
          </button>;
        })}
      </div> : <p className="cluster-switcher-empty">등록된 클러스터가 없습니다. 첫 연결을 등록해 인벤토리를 불러오세요.</p>}
    </section>
    <section
      id="cluster-inventory-panel"
      className="resource-detail cluster-inventory-panel"
    >{current ? <>
      <div className="resource-title"><div><p className="eyebrow">Live inventory</p><h2>{current.name}</h2><p>{current.api_base_url}</p></div><div className="resource-actions"><button onClick={() => onTest(current.id)} disabled={saving}>연결 시험</button><button className="accent-button" onClick={() => onImport(current.id)} disabled={saving}>VM/CT 가져오기</button>{canDelete && <button className="danger" onClick={onDelete} disabled={saving}>등록 해제</button>}</div></div>
      <div className="inventory-totals"><span><strong>{nodes.length}</strong> Nodes</span><span><strong>{guests.length}</strong> VM / CT</span><span><strong>{storages.length}</strong> Storages</span></div>
      <InventoryTable title="노드" columns={["이름", "상태", "CPU", "메모리"]} rows={nodes.map((node) => [node.node, node.status ?? "—", node.maxcpu ? `${node.maxcpu} cores` : "—", node.mem && node.maxmem ? `${formatBytes(node.mem)} / ${formatBytes(node.maxmem)}` : "—"])} />
      <InventoryTable
        className="guest-inventory-table"
        title="VM과 CT"
        columns={["VMID", "이름", "종류", "노드", "CPU 사용량", "메모리 사용량", "디스크 사용량", "가동시간", "상태"]}
        rows={guests.map((guest) => {
          const powerState = guest.status?.toLowerCase() ?? "unknown";
          const running = powerState === "running";
          const stopped = powerState === "stopped";
          return [
            String(guest.vmid),
            guest.name ?? "Unnamed",
            guest.type.toUpperCase(),
            guest.node ?? "—",
            running && guest.cpu !== null
              ? `${formatPercent(guest.cpu)}${guest.maxcpu === null ? "" : ` · ${guest.maxcpu} vCPU`}`
              : "—",
            running ? formatGuestCapacity(guest.mem, guest.maxmem) : "—",
            running ? formatGuestCapacity(guest.disk, guest.maxdisk) : "—",
            running ? formatUptime(guest.uptime) : "—",
            <StatusMark
              key={`${guest.vmid}-power-state`}
              ok={running ? true : stopped ? false : null}
              label={powerState.toUpperCase()}
            />,
          ];
        })}
      />
      <InventoryTable title="스토리지" columns={["이름", "노드", "종류", "사용량"]} rows={storages.map((storage) => [storage.storage, storage.node ?? "shared", storage.type ?? "—", storage.used !== null && storage.total ? `${formatBytes(storage.used)} / ${formatBytes(storage.total)}` : "—"])} />
    </> : <p className="empty-state">클러스터를 등록하거나 운영 대상을 선택하세요.</p>}</section>
  </div>;
}

function InventoryTable({ title, columns, rows, className = "" }: { title: string; columns: string[]; rows: React.ReactNode[][]; className?: string }) {
  return <section className={`inventory-block ${className}`.trim()}><div><h3>{title}</h3><span>{rows.length}</span></div><div className="dynamic-table" style={{ "--columns": columns.length } as React.CSSProperties}><div className="table-head">{columns.map((column) => <span key={column}>{column}</span>)}</div>{rows.map((row, index) => <div className="table-row" key={`${String(row[0] ?? "row")}-${index}`}>{row.map((value, cell) => <span key={cell} data-label={columns[cell]}>{value}</span>)}</div>)}</div>{!rows.length && <p className="empty-state">조회된 항목이 없습니다.</p>}</section>;
}

function BackupsView({
  clusters,
  workloads,
  targets,
  candidates,
  runs,
  preferredWorkloadId,
  activeRun,
  activeRestore,
  saving,
  canConfigure,
  onClearPreferredWorkload,
  onDiscover,
  onRegister,
  onToggle,
  onBackup,
  onRestore,
}: {
  clusters: Cluster[];
  workloads: Workload[];
  targets: BackupTarget[];
  candidates: BackupStorageCandidate[];
  runs: BackupRun[];
  preferredWorkloadId: string | null;
  activeRun: BackupRun | null;
  activeRestore: RestoreRun | null;
  saving: boolean;
  canConfigure: boolean;
  onClearPreferredWorkload: () => void;
  onDiscover: (clusterId: string) => void;
  onRegister: (candidate: BackupStorageCandidate) => void;
  onToggle: (target: BackupTarget) => void;
  onBackup: (workloadId: string, targetId: string) => void;
  onRestore: (
    backupRunId: string,
    payload: { target_node: string; target_vmid: number; target_name: string },
  ) => void;
}) {
  const visibleWorkloads = useMemo(
    () => workloads.filter((item) => item.is_present && !item.is_template),
    [workloads],
  );
  const [clusterId, setClusterId] = useState(clusters[0]?.id ?? "");
  const [workloadId, setWorkloadId] = useState(preferredWorkloadId ?? visibleWorkloads[0]?.id ?? "");
  const effectiveClusterId = clusters.some((item) => item.id === clusterId)
    ? clusterId
    : (clusters[0]?.id ?? "");
  const effectiveWorkloadId = visibleWorkloads.some((item) => item.id === workloadId)
    ? workloadId
    : preferredWorkloadId && visibleWorkloads.some((item) => item.id === preferredWorkloadId)
      ? preferredWorkloadId
      : (visibleWorkloads[0]?.id ?? "");
  const selectedWorkload = visibleWorkloads.find((item) => item.id === effectiveWorkloadId) ?? null;
  const matchingTargets = useMemo(
    () => targets.filter(
      (item) => item.is_enabled && item.cluster_id === selectedWorkload?.cluster_id,
    ),
    [selectedWorkload?.cluster_id, targets],
  );
  const [targetId, setTargetId] = useState(matchingTargets[0]?.id ?? "");
  const effectiveTargetId = matchingTargets.some((item) => item.id === targetId)
    ? targetId
    : (matchingTargets[0]?.id ?? "");
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyStatus, setHistoryStatus] = useState("ALL");
  const [historyClusterId, setHistoryClusterId] = useState("ALL");
  const [historyWorkloadId, setHistoryWorkloadId] = useState(preferredWorkloadId ?? "ALL");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [restoreFormOpen, setRestoreFormOpen] = useState(false);
  const [restoreNode, setRestoreNode] = useState("");
  const [restoreVmid, setRestoreVmid] = useState("");
  const [restoreName, setRestoreName] = useState("");
  const filteredRuns = useMemo(() => {
    const normalizedQuery = historyQuery.trim().toLocaleLowerCase("ko");
    return runs.filter((item) => {
      const searchable = [
        item.workload_name,
        String(item.vmid),
        item.organization_name,
        item.cluster_name,
        item.storage_id,
        item.snapshot_volume_id,
      ].filter(Boolean).join(" ").toLocaleLowerCase("ko");
      return (!normalizedQuery || searchable.includes(normalizedQuery))
        && (historyStatus === "ALL" || item.status === historyStatus)
        && (historyClusterId === "ALL" || item.cluster_id === historyClusterId)
        && (historyWorkloadId === "ALL" || item.workload_id === historyWorkloadId);
    });
  }, [historyClusterId, historyQuery, historyStatus, historyWorkloadId, runs]);
  const selectedRun = selectedRunId
    ? filteredRuns.find((item) => item.id === selectedRunId) ?? null
    : null;
  const restoreNodes = selectedRun
    ? [...new Set([
      selectedRun.source_node,
      ...visibleWorkloads
        .filter((item) => item.cluster_id === selectedRun.cluster_id)
        .map((item) => item.node),
    ])].sort((left, right) => left.localeCompare(right, "ko"))
    : [];
  const resetHistoryFilters = () => {
    setHistoryQuery("");
    setHistoryStatus("ALL");
    setHistoryClusterId("ALL");
    setHistoryWorkloadId("ALL");
    onClearPreferredWorkload();
  };
  const openRunDetail = (run: BackupRun) => {
    const source = visibleWorkloads.find((item) => item.id === run.workload_id);
    const clusterWorkloads = visibleWorkloads.filter((item) => item.cluster_id === run.cluster_id);
    const maxVmid = clusterWorkloads.reduce(
      (maximum, item) => Math.max(maximum, item.vmid),
      99,
    );
    setRestoreNode(source?.node ?? run.source_node);
    setRestoreVmid(String(maxVmid + 1));
    const safeName = `${run.workload_name ?? `vm-${run.vmid}`}-restored`
      .replace(/[^A-Za-z0-9-]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 63);
    setRestoreName(safeName || `vm-${run.vmid}-restored`);
    setRestoreFormOpen(false);
    setSelectedRunId(run.id);
  };

  useEffect(() => {
    if (!selectedRun) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setSelectedRunId(null);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [selectedRun]);

  const successfulWorkloadIds = new Set(
    runs.filter((item) => item.status === "SUCCEEDED").map((item) => item.workload_id),
  );
  const unprotectedCount = visibleWorkloads.filter(
    (item) => !successfulWorkloadIds.has(item.id),
  ).length;
  const terminal = activeRun === null || ["SUCCEEDED", "FAILED", "TIMEOUT"].includes(activeRun.status);

  return <div className="admin-content backup-workspace enter-admin">
    <section className="backup-summary-grid" aria-label="백업 요약">
      <article><small>활성 대상</small><strong>{targets.filter((item) => item.is_enabled).length}</strong><span>PBS storages</span></article>
      <article><small>성공</small><strong>{runs.filter((item) => item.status === "SUCCEEDED").length}</strong><span>최근 실행 내역</span></article>
      <article><small>실패</small><strong>{runs.filter((item) => ["FAILED", "TIMEOUT"].includes(item.status)).length}</strong><span>확인 필요</span></article>
      <article><small>백업 없음</small><strong>{unprotectedCount}</strong><span>VM / CT</span></article>
    </section>

    <section className="backup-command-grid">
      <div className="backup-panel">
        <div className="admin-section-title"><div><p className="eyebrow">Manual backup</p><h2>지금 백업</h2><p>VM/CT와 같은 클러스터의 PBS 대상만 선택할 수 있습니다.</p></div></div>
        <form className="backup-run-form" onSubmit={(event) => { event.preventDefault(); if (effectiveWorkloadId && effectiveTargetId) onBackup(effectiveWorkloadId, effectiveTargetId); }}>
          <label><span>VM / CT</span><select value={effectiveWorkloadId} onChange={(event) => setWorkloadId(event.target.value)}>{visibleWorkloads.map((item) => <option value={item.id} key={item.id}>{item.name ?? `VMID ${item.vmid}`} · {item.cluster_name}</option>)}</select></label>
          <label><span>PBS 대상</span><select value={effectiveTargetId} onChange={(event) => setTargetId(event.target.value)} disabled={!matchingTargets.length}><option value="">{matchingTargets.length ? "대상 선택" : "사용 가능한 대상 없음"}</option>{matchingTargets.map((item) => <option value={item.id} key={item.id}>{item.storage_id} · {item.cluster_name}</option>)}</select></label>
          <div className="backup-run-options"><span>SNAPSHOT</span><span>ZSTD</span><span>{selectedWorkload?.kind ?? "—"}</span></div>
          <button className="accent-button" type="submit" disabled={saving || !effectiveWorkloadId || !effectiveTargetId || !terminal}>{activeRun && !terminal ? `백업 ${activeRun.status}` : "백업 실행"}</button>
        </form>
      </div>

      <div className="backup-panel">
        <div className="admin-section-title"><div><p className="eyebrow">PVE storage</p><h2>백업 대상 검색</h2><p>PVE에 미리 등록된 PBS 스토리지만 가져옵니다.</p></div></div>
        <div className="backup-discovery-tools"><select aria-label="PBS 검색 클러스터" value={effectiveClusterId} onChange={(event) => setClusterId(event.target.value)}>{clusters.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select><button onClick={() => onDiscover(effectiveClusterId)} disabled={saving || !effectiveClusterId}>스토리지 검색</button></div>
        <div className="backup-candidate-list">
          {candidates.filter((item) => item.cluster_id === effectiveClusterId).map((item) => <div key={`${item.cluster_id}-${item.storage_id}`}><span><StatusMark ok={item.available} label={item.available ? "사용 가능" : "확인 필요"} /><strong>{item.storage_id}</strong><small>{item.datastore ?? "datastore 미표시"}{item.namespace ? ` · ${item.namespace}` : ""}</small></span>{item.registered_target_id ? <em>등록됨</em> : canConfigure ? <button onClick={() => onRegister(item)} disabled={saving || !item.enabled_in_pve}>대상 등록</button> : <em>미등록</em>}</div>)}
          {!candidates.some((item) => item.cluster_id === effectiveClusterId) && <p className="empty-state">클러스터를 선택하고 스토리지를 검색하세요.</p>}
        </div>
      </div>
    </section>

    <section className="backup-panel">
      <div className="admin-section-title"><div><p className="eyebrow">Backup targets</p><h2>등록된 대상</h2></div></div>
      <div className="backup-target-list">
        {targets.map((item) => <div key={item.id}><span><StatusMark ok={item.available && item.is_enabled} label={item.is_enabled ? item.available ? "사용 가능" : "연결 확인 필요" : "비활성"} /><strong>{item.storage_id}</strong><small>{item.cluster_name} · {item.datastore ?? "datastore 미표시"}{item.namespace ? ` / ${item.namespace}` : ""}</small></span>{canConfigure && <button onClick={() => onToggle(item)} disabled={saving}>{item.is_enabled ? "비활성화" : "활성화"}</button>}</div>)}
        {!targets.length && <p className="empty-state">등록된 PBS 백업 대상이 없습니다.</p>}
      </div>
    </section>

    <section className="backup-panel backup-history-panel">
      <div className="admin-section-title"><div><p className="eyebrow">Backup history</p><h2>백업 내역 관리</h2><p>전체 이력을 검색하고 VM별 실행 결과와 저장 정보를 확인합니다.</p></div><span>{filteredRuns.length} / {runs.length} runs</span></div>
      {preferredWorkloadId && historyWorkloadId !== "ALL" && <div className="backup-history-context"><span>VM에서 이동한 내역만 표시 중</span><button type="button" onClick={resetHistoryFilters}>전체 내역 보기</button></div>}
      <div className="backup-history-tools">
        <input type="search" value={historyQuery} onChange={(event) => setHistoryQuery(event.target.value)} placeholder="VM, 조직, VMID, 스냅샷 검색" aria-label="백업 내역 검색" />
        <select value={historyStatus} onChange={(event) => setHistoryStatus(event.target.value)} aria-label="백업 상태"><option value="ALL">전체 상태</option><option value="SUCCEEDED">성공</option><option value="FAILED">실패</option><option value="TIMEOUT">시간 초과</option><option value="RUNNING">진행 중</option><option value="QUEUED">대기 중</option></select>
        <select value={historyClusterId} onChange={(event) => setHistoryClusterId(event.target.value)} aria-label="백업 클러스터"><option value="ALL">전체 클러스터</option>{clusters.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
        <select value={historyWorkloadId} onChange={(event) => { setHistoryWorkloadId(event.target.value); if (event.target.value === "ALL") onClearPreferredWorkload(); }} aria-label="백업 VM과 CT"><option value="ALL">전체 VM / CT</option>{visibleWorkloads.map((item) => <option key={item.id} value={item.id}>{item.name ?? `VMID ${item.vmid}`} · {item.vmid}</option>)}</select>
        <button type="button" onClick={resetHistoryFilters}>초기화</button>
      </div>
      <div className="backup-table">
        <div className="backup-table-head"><span>VM / CT · 조직</span><span>클러스터 / 대상</span><span>상태</span><span>백업 시각</span><span>크기</span></div>
        {filteredRuns.map((item) => <button type="button" className="backup-table-row" key={item.id} onClick={() => openRunDetail(item)} aria-label={`${item.workload_name ?? `VMID ${item.vmid}`} 백업 상세 보기`}><span data-label="VM / CT · 조직"><strong>{item.workload_name ?? `VMID ${item.vmid}`}</strong><small>{item.kind} {item.vmid} · {item.organization_name ?? "미할당"}</small></span><span data-label="클러스터 / 대상"><strong>{item.cluster_name}</strong><small>{item.storage_id}</small></span><span data-label="상태"><StatusMark ok={item.status === "SUCCEEDED"} label={item.status} />{item.error_code && <small>{item.error_code}</small>}</span><span data-label="백업 시각">{formatTime(item.snapshot_time ?? item.requested_at)}</span><span data-label="크기"><strong>{formatBytes(item.size_bytes)}</strong><small>{item.transferred_bytes === 0 ? "기존 데이터 100% 재사용" : `신규 전송 ${formatTransferredBytes(item.transferred_bytes)}`}</small></span></button>)}
        {!filteredRuns.length && <p className="empty-state">조건에 맞는 백업 내역이 없습니다.</p>}
      </div>
    </section>
    {selectedRun && typeof document !== "undefined" && createPortal(<div className="admin-drawer-backdrop" onMouseDown={() => setSelectedRunId(null)}>
      <aside className="admin-drawer backup-run-detail" role="dialog" aria-modal="true" aria-label="선택한 백업 상세" onMouseDown={(event) => event.stopPropagation()}>
        <button className="drawer-close" type="button" onClick={() => setSelectedRunId(null)} aria-label="백업 상세 닫기">×</button>
        <div><p className="eyebrow">Run detail</p><h3>{selectedRun.workload_name ?? `VMID ${selectedRun.vmid}`}</h3><StatusMark ok={selectedRun.status === "SUCCEEDED"} label={selectedRun.status} /></div>
        <dl><div><dt>실행 ID</dt><dd>{selectedRun.id.slice(0, 8)}</dd></div><div><dt>조직</dt><dd>{selectedRun.organization_name ?? "미할당"}</dd></div><div><dt>대상</dt><dd>{selectedRun.cluster_name} / {selectedRun.storage_id}</dd></div><div><dt>백업 방식</dt><dd>{selectedRun.mode.toUpperCase()} · {selectedRun.compression.toUpperCase()}</dd></div><div><dt>논리 크기</dt><dd>{formatBytes(selectedRun.size_bytes)}</dd></div><div className="backup-transfer-metric"><dt>신규 전송 데이터</dt><dd>{formatTransferredBytes(selectedRun.transferred_bytes)}</dd></div><div><dt>소요 시간</dt><dd>{formatDuration(selectedRun.started_at, selectedRun.finished_at)}</dd></div><div><dt>스냅샷 시각</dt><dd>{formatTime(selectedRun.snapshot_time)}</dd></div></dl>
        <div className="backup-volume-id"><span>스냅샷 ID</span><code>{selectedRun.snapshot_volume_id ?? "아직 연결된 스냅샷 정보가 없습니다."}</code></div>
        {(selectedRun.error_code || selectedRun.error_summary) && <div className="backup-run-error"><strong>{selectedRun.error_code ?? "BACKUP_FAILED"}</strong><span>{selectedRun.error_summary ?? "백업 작업이 완료되지 않았습니다."}</span></div>}
        <p className={`backup-size-note ${selectedRun.transferred_bytes === 0 ? "fully-reused" : ""}`}>{selectedRun.transferred_bytes === 0 ? "정상 백업입니다. PBS가 기존 청크를 100% 재사용해 이번 실행에서 새로 전송할 데이터가 없었습니다." : "논리 크기는 백업 데이터 전체 크기이며, 신규 전송 데이터는 PBS 중복제거로 재사용된 데이터를 제외하고 이번 실행에서 새로 전송된 양입니다."}</p>
        <div className="backup-detail-actions">
          <button className="accent-button" type="button" disabled={saving || !targets.some((item) => item.id === selectedRun.backup_target_id && item.is_enabled)} onClick={() => onBackup(selectedRun.workload_id, selectedRun.backup_target_id)}>같은 대상으로 다시 백업</button>
          {canConfigure && selectedRun.status === "SUCCEEDED" && selectedRun.snapshot_volume_id && <button className="restore-button" type="button" disabled={saving || Boolean(activeRestore && !["SUCCEEDED", "FAILED", "TIMEOUT"].includes(activeRestore.status))} onClick={() => setRestoreFormOpen((current) => !current)}>{restoreFormOpen ? "복구 입력 닫기" : "새 VM/CT로 복구"}</button>}
        </div>
        {activeRestore?.backup_run_id === selectedRun.id && <div className={`restore-progress ${activeRestore.status.toLowerCase()}`} role="status"><span>복구 작업</span><strong>VMID {activeRestore.target_vmid} · {activeRestore.status}</strong>{activeRestore.error_code && <small>{activeRestore.error_code}</small>}</div>}
        {restoreFormOpen && <form className="restore-run-form" onSubmit={(event) => {
          event.preventDefault();
          const targetVmid = Number(restoreVmid);
          if (!restoreNode || !restoreName || !Number.isInteger(targetVmid)) return;
          onRestore(selectedRun.id, { target_node: restoreNode, target_vmid: targetVmid, target_name: restoreName });
        }}>
          <div className="restore-safety-note"><strong>새 VM/CT로만 복구됩니다.</strong><span>기존 VMID는 덮어쓰지 않으며 복구 후 전원은 꺼진 상태로 유지됩니다.</span></div>
          <label><span>대상 노드</span><select value={restoreNode} onChange={(event) => setRestoreNode(event.target.value)} required><option value="">노드 선택</option>{restoreNodes.map((node) => <option key={node} value={node}>{node}</option>)}</select></label>
          <div className="restore-target-grid"><label><span>새 VMID</span><input type="number" min="100" max="999999999" value={restoreVmid} onChange={(event) => setRestoreVmid(event.target.value)} required /></label><label><span>새 이름</span><input type="text" minLength={1} maxLength={63} pattern="[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?" value={restoreName} onChange={(event) => setRestoreName(event.target.value)} required /></label></div>
          <button className="accent-button" type="submit" disabled={saving || !restoreNode || !restoreName || Number(restoreVmid) < 100}>{saving ? "복구 요청 중…" : "복구 시작"}</button>
        </form>}
      </aside>
    </div>, document.body)}
  </div>;
}

function VmOperationsView({ workloads, backupRuns, onSelect, onCreate, onEdit, onDelete, onBackup, onAction, onConsole, activeJob, saving, canManage }: {
  workloads: Workload[]; backupRuns: BackupRun[]; onSelect: (id: string) => void; onCreate: () => void;
  onEdit: () => void; onDelete: () => void;
  onBackup: (workloadId: string) => void;
  onAction: (workload: Workload, action: AdminPowerAction) => void;
  onConsole: (workload: Workload) => void;
  activeJob: AdminWorkloadJob | null; saving: boolean; canManage: boolean;
}) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<VmKindFilter>("ALL");
  const [power, setPower] = useState<VmPowerFilter>("ALL");
  const [node, setNode] = useState("ALL");
  const visible = workloads.filter((item) => item.is_present && !item.is_template);
  const nodeOptions = [...new Set(visible.map((item) => item.node))].sort((left, right) => left.localeCompare(right, "ko"));
  const filtered = filterAdminWorkloads(workloads, { query, kind, power, node });
  return <div className="admin-content vm-operations enter-admin">
    <section className="vm-inventory-table"><div className="admin-section-title"><div><p className="eyebrow">Managed inventory</p><h2>VM과 CT</h2><p>상태, 사양, 소유 조직과 운영 작업을 한 행에서 관리합니다.</p></div>{canManage && <button className="accent-button" onClick={onCreate}>VM 생성</button>}</div>
      <div className="vm-list-tools">
        <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="이름, VMID, 노드 검색" aria-label="VM과 CT 검색" />
        <div><select value={kind} onChange={(event) => setKind(event.target.value as VmKindFilter)} aria-label="가상화 종류"><option value="ALL">전체 종류</option><option value="QEMU">QEMU</option><option value="LXC">LXC</option></select><select value={power} onChange={(event) => setPower(event.target.value as VmPowerFilter)} aria-label="전원 상태"><option value="ALL">전체 상태</option><option value="RUNNING">실행 중</option><option value="STOPPED">중지됨</option></select><select value={node} onChange={(event) => setNode(event.target.value)} aria-label="Proxmox 노드"><option value="ALL">전체 노드</option>{nodeOptions.map((nodeName) => <option key={nodeName} value={nodeName}>{nodeName}</option>)}</select></div>
        <span>{filtered.length} / {visible.length} resources</span>
      </div>
      <div className="vm-table-scroll" aria-label="VM과 CT 목록"><div className="vm-table">
        <div className="vm-table-head"><span>리소스</span><span>상태</span><span>vCPU</span><span>메모리</span><span>디스크</span><span>IP 주소</span><span>조직</span><span>최근 백업</span><span>관리 작업</span><span>전원 작업</span></div>
        {filtered.map((item) => {
          const running = item.power_state.toUpperCase() === "RUNNING";
          const actionPending = activeJob?.workload_id === item.id && !["SUCCEEDED", "FAILED", "TIMEOUT"].includes(activeJob.status);
          const capabilities = getAdminWorkloadCapabilities(item.kind);
          const hasPowerAction = (action: AdminPowerAction) => capabilities.powerActions.includes(action);
          const latestBackup = backupRuns.find((run) => run.workload_id === item.id) ?? null;
          return <div key={item.id} className="vm-table-row">
            <span className="vm-resource-cell" data-label="리소스"><strong>{item.name ?? `VMID ${item.vmid}`}</strong><small>{item.kind} {item.vmid} · {item.cluster_name} / {item.node}</small>{actionPending && <small className="vm-inline-job">작업: {activeJob.action.toUpperCase()} · {activeJob.status}</small>}</span>
            <span className="vm-status-cell" data-label="상태"><StatusMark ok={running} label={item.power_state} /></span>
            <strong data-label="vCPU">{item.cpu_cores ?? "—"}</strong><strong data-label="메모리">{formatBytes(item.memory_bytes)}</strong><strong data-label="디스크">{formatBytes(item.disk_bytes)}</strong>
            <span className="vm-cell-muted" data-label="IP 주소">{item.assigned_ip_addresses?.length ? item.assigned_ip_addresses.join(", ") : "—"}</span>
            <span className="vm-ownership-cell" data-label="조직">{item.organization_id && item.organization_name ? <strong className="vm-ownership-badge assigned" aria-label={`${item.organization_name} 조직에 할당됨`} title={`${item.organization_name} 조직에 할당됨`}><i aria-hidden="true">✓</i><span>{item.organization_name}</span></strong> : <span className="vm-ownership-badge unassigned">미할당</span>}</span>
            <button type="button" className="vm-backup-summary" data-label="최근 백업" onClick={() => onBackup(item.id)}>{latestBackup ? <><StatusMark ok={latestBackup.status === "SUCCEEDED"} label={latestBackup.status} /><small>{formatTime(latestBackup.snapshot_time ?? latestBackup.requested_at)}</small><em>{latestBackup.transferred_bytes === 0 ? "기존 데이터 100% 재사용" : `신규 전송 ${formatTransferredBytes(latestBackup.transferred_bytes)}`}</em></> : <><strong>백업 없음</strong><small>보호를 시작하세요</small></>}</button>
            <span className="vm-row-actions" data-label="관리 작업"><button disabled={saving || Boolean(actionPending)} onClick={() => onBackup(item.id)}>백업 관리</button>{canManage && capabilities.canUpdateSpec && <button disabled={saving || Boolean(actionPending)} onClick={() => { onSelect(item.id); onEdit(); }}>사양</button>}{canManage && capabilities.canDelete && <button className="danger" disabled={saving || Boolean(actionPending) || running || item.organization_id !== null} onClick={() => { onSelect(item.id); onDelete(); }}>삭제</button>}</span>
            <span className="vm-row-actions vm-power-actions" data-label="전원 작업"><span className="vm-standard-actions"><button className="console-row-button" disabled={!running} onClick={() => onConsole(item)}>콘솔</button>{hasPowerAction("start") && <button disabled={saving || Boolean(actionPending) || running} onClick={() => onAction(item, "start")}>시작</button>}{hasPowerAction("shutdown") && <button disabled={saving || Boolean(actionPending) || !running} onClick={() => onAction(item, "shutdown")}>종료</button>}{hasPowerAction("reboot") && <button disabled={saving || Boolean(actionPending) || !running} onClick={() => onAction(item, "reboot")}>재부팅</button>}</span><span className="vm-forced-actions">{hasPowerAction("stop") && <button className="danger" disabled={saving || Boolean(actionPending) || !running} onClick={() => onAction(item, "stop")}>강제 중지</button>}{hasPowerAction("reset") && <button className="danger" disabled={saving || Boolean(actionPending) || !running} onClick={() => onAction(item, "reset")}>강제 재설정</button>}</span></span>
          </div>;
        })}
        {!visible.length && <p className="empty-state">가져온 VM/CT가 없습니다. 클러스터 화면에서 먼저 가져오세요.</p>}
        {visible.length > 0 && !filtered.length && <p className="empty-state">검색 조건에 맞는 리소스가 없습니다.</p>}
      </div></div>
      <p className="section-note">정상 종료·재부팅은 게스트 OS에 요청합니다. 강제 재설정은 QEMU VM만 지원하며, 삭제하려면 워크로드를 중지하고 조직 할당을 해제해야 합니다.</p>
    </section>
  </div>;
}

function OrganizationSearchSelect({
  label,
  value,
  initialOptions,
  total,
  onSearch,
  onSelect,
  onlyActive = false,
}: {
  label: string;
  value: Organization | null;
  initialOptions: Organization[];
  total: number;
  onSearch: (filters: OrganizationSearchFilters) => Promise<OrganizationPage>;
  onSelect: (organization: Organization) => void;
  onlyActive?: boolean;
}) {
  const listboxId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState(initialOptions);
  const [resultTotal, setResultTotal] = useState(total);
  const [activeIndex, setActiveIndex] = useState(0);
  const [searching, setSearching] = useState(false);
  const [searchFailed, setSearchFailed] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setSearching(true);
      setSearchFailed(false);
      try {
        const result = await onSearch({ q: query, status: onlyActive ? "active" : "all", sort: "name", limit: 10, offset: 0 });
        if (cancelled) return;
        setOptions(result.items);
        setResultTotal(result.total);
        setActiveIndex(0);
      } catch {
        if (!cancelled) setSearchFailed(true);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, query ? 220 : 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [onSearch, onlyActive, open, query]);

  useEffect(() => {
    if (!open) return;
    const closeOnOutsidePress = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsidePress);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePress);
  }, [open]);

  function choose(organization: Organization) {
    if (onlyActive && !organization.is_active) return;
    onSelect(organization);
    setQuery("");
    setOpen(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => Math.min(current + 1, options.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((current) => Math.max(current - 1, 0));
    } else if (event.key === "Enter" && open && options[activeIndex]) {
      event.preventDefault();
      choose(options[activeIndex]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  return <div className="organization-combobox" ref={rootRef}>
    <label><span>{label}</span><input type="search" role="combobox" aria-expanded={open} aria-controls={listboxId} aria-autocomplete="list" value={open ? query : value?.name ?? ""} placeholder="조직 이름 또는 ID 검색" onFocus={() => { setOpen(true); setQuery(""); }} onChange={(event) => { setQuery(event.target.value); setOpen(true); }} onKeyDown={onKeyDown} /></label>
    <span className="organization-combobox-mark" aria-hidden="true">⌄</span>
    {open && <div className="organization-options" id={listboxId} role="listbox" aria-label={`${label} 검색 결과`}>
      <div className="organization-options-meta"><span>{query ? "검색 결과" : "최근 생성된 조직"}</span><strong>{resultTotal}</strong></div>
      {searching && <p role="status">검색 중…</p>}
      {searchFailed && <p role="alert">조직을 불러오지 못했습니다.</p>}
      {!searching && !searchFailed && options.map((organization, index) => <button type="button" role="option" aria-selected={organization.id === value?.id} className={`${index === activeIndex ? "active" : ""} ${organization.id === value?.id ? "selected" : ""}`} disabled={onlyActive && !organization.is_active} key={organization.id} onMouseEnter={() => setActiveIndex(index)} onClick={() => choose(organization)}><span><strong>{organization.name}</strong><small>{organization.id.slice(0, 8)}</small></span><em>{organization.is_active ? "활성" : "비활성"}</em></button>)}
      {!searching && !searchFailed && !options.length && <p>일치하는 조직이 없습니다.</p>}
      {!searching && options.length > 0 && resultTotal > options.length && <small className="organization-options-limit">상위 {options.length}개 표시 · 검색어를 더 입력하세요.</small>}
    </div>}
  </div>;
}

function AccessView({
  currentUserId,
  users,
  organizations,
  organizationTotal,
  members,
  workloads,
  selectedOrganization,
  canWrite,
  saving,
  onSelectOrganization,
  onSearchOrganizations,
  onAddMember,
  onRemoveMember,
  onAssign,
  onUnassign,
  onUser,
  onResetPassword,
  onUserStatus,
  onDeleteUser,
  onCreateMember,
  onOrganization,
  onEditOrganization,
  onActivateOrganization,
  onDeleteOrganization,
}: {
  currentUserId: string;
  users: CurrentUser[];
  organizations: Organization[];
  organizationTotal: number;
  members: OrganizationMember[];
  workloads: Workload[];
  selectedOrganization: Organization | null;
  canWrite: boolean;
  saving: boolean;
  onSelectOrganization: (organization: Organization) => void;
  onSearchOrganizations: (filters: OrganizationSearchFilters) => Promise<OrganizationPage>;
  onAddMember: (userId: string) => void;
  onRemoveMember: (userId: string) => void;
  onAssign: (workloadId: string, organizationId?: string) => void;
  onUnassign: (workloadId: string) => void;
  onUser: () => void;
  onResetPassword: (user: CurrentUser) => void;
  onUserStatus: (user: CurrentUser) => void;
  onDeleteUser: (user: CurrentUser) => void;
  onCreateMember: () => void;
  onOrganization: () => void;
  onEditOrganization: (organization: Organization) => void;
  onActivateOrganization: (organization: Organization) => void;
  onDeleteOrganization: (organization: Organization) => void;
}) {
  const [memberCandidate, setMemberCandidate] = useState("");
  const [memberQuery, setMemberQuery] = useState("");
  const [workloadQuery, setWorkloadQuery] = useState("");
  const [userQuery, setUserQuery] = useState("");
  const [onlyUnassignedUsers, setOnlyUnassignedUsers] = useState(false);
  const [activePane, setActivePane] = useState<"members" | "workloads">("members");
  const [accessScope, setAccessScope] = useState<"organizations" | "users" | "assignments">("organizations");
  const [organizationView, setOrganizationView] = useState<"list" | "detail">("list");
  const [directoryQuery, setDirectoryQuery] = useState("");
  const [directoryStatus, setDirectoryStatus] = useState<"active" | "inactive" | "all">("active");
  const [directorySort, setDirectorySort] = useState<"newest" | "oldest" | "name">("newest");
  const [directoryPage, setDirectoryPage] = useState<OrganizationPage>({ items: organizations, total: organizationTotal, limit: 25, offset: 0 });
  const [directoryLoading, setDirectoryLoading] = useState(false);
  const [directoryError, setDirectoryError] = useState("");
  const [assignmentTarget, setAssignmentTarget] = useState<Organization | null>(selectedOrganization);
  const current = selectedOrganization;
  const memberIds = new Set(members.map((member) => member.user_id));
  const availableCustomers = users.filter(
    (item) => item.role === "CUSTOMER" && item.is_active && !memberIds.has(item.id),
  );
  const assigned = workloads.filter((item) => item.organization_id === current?.id);
  const available = workloads.filter(
    (item) => item.organization_id === null && item.is_present && !item.is_template,
  );
  const query = (value: string) => value.trim().toLocaleLowerCase();
  const memberNeedle = query(memberQuery);
  const workloadNeedle = query(workloadQuery);
  const userNeedle = query(userQuery);
  const filteredMembers = members.filter((item) =>
    !memberNeedle || [item.display_name, item.email, item.role].some((value) => value.toLocaleLowerCase().includes(memberNeedle)),
  );
  const matchesWorkload = (item: Workload) => !workloadNeedle || [item.name ?? "", item.vmid.toString(), item.node, item.cluster_name, ...(item.assigned_ip_addresses ?? [])]
    .some((value) => value.toLocaleLowerCase().includes(workloadNeedle));
  const filteredAssigned = assigned.filter(matchesWorkload);
  const filteredAvailable = available.filter(matchesWorkload);
  const organizationNames = (user: CurrentUser) => user.organization_names ?? [];
  const matchesUser = (user: CurrentUser) => !userNeedle || [user.display_name, user.email, user.role, ...organizationNames(user)]
    .some((value) => value.toLocaleLowerCase().includes(userNeedle));
  const filteredUsers = users.filter(
    (user) => matchesUser(user) && (!onlyUnassignedUsers || organizationNames(user).length === 0),
  );
  const unassignedUserCount = users.filter((user) => organizationNames(user).length === 0).length;

  useEffect(() => {
    if (accessScope !== "organizations" || organizationView !== "list") return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setDirectoryLoading(true);
      setDirectoryError("");
      try {
        const result = await onSearchOrganizations({
          q: directoryQuery,
          status: directoryStatus,
          sort: directorySort,
          limit: 25,
          offset: directoryPage.offset,
        });
        if (!cancelled) setDirectoryPage(result);
      } catch {
        if (!cancelled) setDirectoryError("조직 목록을 불러오지 못했습니다.");
      } finally {
        if (!cancelled) setDirectoryLoading(false);
      }
    }, directoryQuery ? 220 : 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [accessScope, directoryPage.offset, directoryQuery, directorySort, directoryStatus, onSearchOrganizations, organizationTotal, organizationView, organizations]);

  function openOrganization(organization: Organization) {
    onSelectOrganization(organization);
    setAssignmentTarget(organization);
    setActivePane("members");
    setOrganizationView("detail");
  }

  const directoryLimit = directoryPage.limit ?? 25;
  const directoryStart = directoryPage.total ? directoryPage.offset + 1 : 0;
  const directoryEnd = Math.min(directoryPage.offset + directoryPage.items.length, directoryPage.total);

  return <div className="admin-content organization-workspace enter-admin">
    <nav className="access-scope-tabs" aria-label="사용자와 조직 관리 영역">
      <button className={accessScope === "organizations" ? "active" : ""} onClick={() => { setAccessScope("organizations"); setOrganizationView("list"); }}>조직 <span>{organizationTotal}</span></button>
      <button className={accessScope === "users" ? "active" : ""} onClick={() => setAccessScope("users")}>사용자 <span>{users.length}</span></button>
      <button className={accessScope === "assignments" ? "active" : ""} onClick={() => setAccessScope("assignments")}>리소스 할당 <span>{available.length}</span></button>
    </nav>

    {accessScope === "organizations" && organizationView === "list" && <section className="organization-directory-page">
      <div className="admin-section-title organization-directory-title"><div><p className="eyebrow">Organization directory</p><h2>조직 목록</h2><p>조직을 검색하거나 상태별로 확인하고, 행을 선택해 상세 관리로 이동합니다.</p></div>{canWrite && <button className="accent-button" onClick={onOrganization}>새 조직</button>}</div>
      <div className="organization-directory-controls">
        <label className="organization-directory-search"><span className="sr-only">조직 검색</span><input type="search" value={directoryQuery} onChange={(event) => { setDirectoryQuery(event.target.value); setDirectoryPage((page) => ({ ...page, offset: 0 })); }} placeholder="조직 이름 또는 ID 검색" /></label>
        <label><span className="sr-only">조직 상태</span><select value={directoryStatus} onChange={(event) => { setDirectoryStatus(event.target.value as "active" | "inactive" | "all"); setDirectoryPage((page) => ({ ...page, offset: 0 })); }}><option value="active">활성 조직</option><option value="inactive">비활성 조직</option><option value="all">전체 상태</option></select></label>
        <label><span className="sr-only">조직 정렬</span><select value={directorySort} onChange={(event) => { setDirectorySort(event.target.value as "newest" | "oldest" | "name"); setDirectoryPage((page) => ({ ...page, offset: 0 })); }}><option value="newest">최근 생성순</option><option value="oldest">오래된 순</option><option value="name">이름순</option></select></label>
      </div>
      <div className={`organization-directory-table ${directoryLoading ? "loading" : ""}`} aria-busy={directoryLoading}>
        <div className="organization-directory-head"><span>조직</span><span>상태</span><span>생성일</span><span>최근 변경</span><span aria-hidden="true"></span></div>
        {directoryPage.items.map((organization) => <button type="button" className={organization.id === current?.id ? "selected" : ""} key={organization.id} onClick={() => openOrganization(organization)}><span><strong>{organization.name}</strong><small>{organization.id.slice(0, 8)}</small></span><StatusMark ok={organization.is_active} label={organization.is_active ? "활성" : "비활성"} /><time>{formatTime(organization.created_at)}</time><time>{formatTime(organization.updated_at)}</time><b aria-hidden="true">›</b></button>)}
      </div>
      {directoryError && <p className="empty-state" role="alert">{directoryError}</p>}
      {!directoryLoading && !directoryError && !directoryPage.items.length && <p className="empty-state">조건에 맞는 조직이 없습니다.</p>}
      <div className="organization-directory-pagination"><span>{directoryStart}–{directoryEnd} / {directoryPage.total}</span><div><button disabled={directoryLoading || directoryPage.offset === 0} onClick={() => setDirectoryPage((page) => ({ ...page, offset: Math.max(0, page.offset - directoryLimit) }))}>이전</button><button disabled={directoryLoading || directoryEnd >= directoryPage.total} onClick={() => setDirectoryPage((page) => ({ ...page, offset: page.offset + directoryLimit }))}>다음</button></div></div>
    </section>}

    {accessScope === "organizations" && organizationView === "detail" && <section className="organization-switcher organization-detail-toolbar">
      <button className="organization-back-button" onClick={() => setOrganizationView("list")}><span aria-hidden="true">←</span> 조직 목록</button>
      <div className="organization-switcher-actions"><OrganizationSearchSelect label="다른 조직 빠른 전환" value={current} initialOptions={organizations} total={organizationTotal} onSearch={onSearchOrganizations} onSelect={openOrganization} /></div>
    </section>}

    <section className="organization-detail access-global-detail">
      {accessScope === "organizations" && organizationView === "detail" && current && <>
        <div className="resource-title"><div><p className="eyebrow">Organization workspace</p><h2>{current.name}</h2><p>구성원 접근과 VM/CT 소유권을 한 곳에서 관리합니다.</p></div><div className="organization-title-actions"><StatusMark ok={current.is_active} label={current.is_active ? "활성" : "비활성"} />{canWrite && <>{current.is_active && <button disabled={saving} onClick={() => onEditOrganization(current)}>수정</button>}{current.is_active ? <button className="danger" disabled={saving} onClick={() => onDeleteOrganization(current)}>비활성화</button> : <button disabled={saving} onClick={() => onActivateOrganization(current)}>활성화</button>}</>}</div></div>
        <div className="organization-summary organization-summary-compact"><div><strong>{members.length}</strong><span>구성원</span></div><div><strong>{assigned.length}</strong><span>할당 리소스</span></div></div>
      </>}
      {accessScope === "organizations" && organizationView === "detail" && <div className="organization-tabs" role="tablist" aria-label="조직 상세 관리 영역"><button className={activePane === "members" ? "active" : ""} disabled={!current} onClick={() => setActivePane("members")}>구성원 <span>{members.length}</span></button><button className={activePane === "workloads" ? "active" : ""} disabled={!current} onClick={() => setActivePane("workloads")}>할당 VM/CT <span>{assigned.length}</span></button></div>}

      {accessScope === "organizations" && organizationView === "detail" && activePane === "members" && current && <section className="organization-block organization-tab-panel">
        <div className="admin-section-title"><div><p className="eyebrow">Membership</p><h2>구성원</h2><p>기존 고객을 연결하거나 새 고객 계정을 바로 만들어 추가합니다.</p></div>{canWrite && current.is_active && <div className="membership-actions"><div className="inline-control"><select aria-label="추가할 기존 고객" value={memberCandidate} onChange={(event) => setMemberCandidate(event.target.value)}><option value="">기존 고객 선택</option>{availableCustomers.map((item) => <option key={item.id} value={item.id}>{item.display_name} · {item.email}</option>)}</select><button disabled={!memberCandidate || saving} onClick={() => { onAddMember(memberCandidate); setMemberCandidate(""); }}>추가</button></div><button className="accent-button" disabled={saving} onClick={onCreateMember}>새 사용자 추가</button></div>}</div>
        <label className="list-search"><span className="sr-only">구성원 검색</span><input type="search" value={memberQuery} onChange={(event) => setMemberQuery(event.target.value)} placeholder="이름, 이메일 또는 역할 검색" /></label>
        <div className="management-list management-list-scroll">{filteredMembers.map((member) => <div key={member.id}><span><strong>{member.display_name}</strong><small>{member.email} · {member.role}</small></span><StatusMark ok={member.is_active} label={member.is_active ? "활성" : "중지"} />{canWrite && <button className="text-danger" disabled={saving} onClick={() => { if (window.confirm(`${member.display_name} 사용자를 조직에서 제거할까요?`)) onRemoveMember(member.user_id); }}>제거</button>}</div>)}</div>
        {!members.length && <p className="empty-state">연결된 고객 사용자가 없습니다.</p>}
        {Boolean(members.length && !filteredMembers.length) && <p className="empty-state">검색 결과가 없습니다.</p>}
      </section>}

      {accessScope === "organizations" && organizationView === "detail" && activePane === "workloads" && current && <section className="organization-block organization-tab-panel">
        <div className="admin-section-title"><div><p className="eyebrow">Owned inventory</p><h2>할당된 VM과 CT</h2></div></div>
        <label className="list-search"><span className="sr-only">할당된 VM 또는 CT 검색</span><input type="search" value={workloadQuery} onChange={(event) => setWorkloadQuery(event.target.value)} placeholder="이름, VMID, 노드 또는 IP 검색" /></label>
        <div className="management-list management-list-scroll workload-lines">{filteredAssigned.map((item) => <div key={item.id}><code>{item.kind} · {item.vmid}</code><span><strong>{item.name ?? `VMID ${item.vmid}`}</strong><small>{item.cluster_name} / {item.node} · {item.cpu_cores ?? "—"} vCPU · {formatBytes(item.memory_bytes)} RAM · {formatBytes(item.disk_bytes)} Disk · IP {item.assigned_ip_addresses?.length ? item.assigned_ip_addresses.join(", ") : "미할당"}</small></span><StatusMark ok={item.is_present} label={item.power_state} /><button className="text-danger" disabled={saving} onClick={() => { if (window.confirm(`${item.name ?? item.vmid} 할당을 회수할까요?`)) onUnassign(item.id); }}>할당 해제</button></div>)}</div>
        {!assigned.length && <p className="empty-state">이 조직에 할당된 리소스가 없습니다.</p>}
        {Boolean(assigned.length && !filteredAssigned.length) && <p className="empty-state">검색 결과가 없습니다.</p>}
      </section>}

      {accessScope === "assignments" && <section className="organization-block organization-tab-panel available-workloads">
        <div className="admin-section-title"><div><p className="eyebrow">Available inventory</p><h2>미할당 리소스</h2><p>할당할 조직을 검색한 뒤 VM/CT 소유권을 연결합니다.</p></div></div>
        <div className="assignment-target-control"><OrganizationSearchSelect label="할당 대상 조직" value={assignmentTarget} initialOptions={organizations} total={organizationTotal} onSearch={onSearchOrganizations} onSelect={setAssignmentTarget} onlyActive /></div>
        <label className="list-search"><span className="sr-only">미할당 VM 또는 CT 검색</span><input type="search" value={workloadQuery} onChange={(event) => setWorkloadQuery(event.target.value)} placeholder="이름, VMID, 노드 또는 IP 검색" /></label>
        <div className="management-list management-list-scroll workload-lines">{filteredAvailable.map((item) => <div key={item.id}><code>{item.kind} · {item.vmid}</code><span><strong>{item.name ?? `VMID ${item.vmid}`}</strong><small>{item.cluster_name} / {item.node} · {item.cpu_cores ?? "—"} vCPU · {formatBytes(item.memory_bytes)} RAM · {formatBytes(item.disk_bytes)} Disk · IP {item.assigned_ip_addresses?.length ? item.assigned_ip_addresses.join(", ") : "미할당"}</small></span><StatusMark ok={item.is_present} label={item.power_state} /><button className="accent-button" disabled={saving || !assignmentTarget} onClick={() => assignmentTarget && onAssign(item.id, assignmentTarget.id)}>조직에 할당</button></div>)}</div>
        {!available.length && <p className="empty-state">미할당 리소스가 없습니다. 클러스터에서 VM/CT 가져오기를 실행하세요.</p>}
        {Boolean(available.length && !filteredAvailable.length) && <p className="empty-state">검색 결과가 없습니다.</p>}
      </section>}

      {accessScope === "users" && <section className="organization-block organization-tab-panel">
        <div className="admin-section-title"><div><p className="eyebrow">Identity directory</p><h2>전체 사용자</h2><p>조직 소속 여부를 포함한 전체 계정 목록입니다.</p></div>{canWrite && <button onClick={onUser}>사용자 추가</button>}</div>
        <div className="user-directory-controls"><label className="list-search"><span className="sr-only">사용자 검색</span><input type="search" value={userQuery} onChange={(event) => setUserQuery(event.target.value)} placeholder="이름, 이메일, 역할 또는 조직 검색" /></label><label className="unassigned-user-filter"><input type="checkbox" checked={onlyUnassignedUsers} onChange={(event) => setOnlyUnassignedUsers(event.target.checked)} /> <span>조직 미할당만</span><strong>{unassignedUserCount}</strong></label></div>
        <div className="admin-table users-table table-scroll"><div className="table-head"><span>사용자</span><span>역할</span><span>조직</span><span>상태</span><span>최근 로그인</span><span>계정 관리</span></div>{filteredUsers.map((item) => <div className="table-row" key={item.id}><span data-label="사용자"><strong>{item.display_name}</strong><small>{item.email}</small></span><code data-label="역할">{item.role}</code><span data-label="조직" className={organizationNames(item).length ? "user-organizations" : "user-unassigned"}>{organizationNames(item).length ? organizationNames(item).join(", ") : "미할당"}</span><span className="user-status-cell" data-label="상태"><StatusMark ok={item.is_active} label={item.is_active ? "활성" : "비활성"} /></span><span data-label="최근 로그인">{formatTime(item.last_login_at)}</span><span className="user-row-actions" data-label="계정 관리">{canWrite && <><button type="button" disabled={saving || !item.is_active} onClick={() => onResetPassword(item)}>비밀번호 초기화</button><button type="button" disabled={saving || item.id === currentUserId} onClick={() => onUserStatus(item)}>{item.is_active ? "비활성화" : "활성화"}</button><button type="button" className="danger" disabled={saving || item.id === currentUserId} onClick={() => onDeleteUser(item)}>삭제</button></>}</span></div>)}</div>
        {users.length > 0 && !filteredUsers.length && <p className="empty-state">검색 조건에 맞는 사용자가 없습니다.</p>}
      </section>}
      {accessScope === "organizations" && organizationView === "detail" && !current && <p className="empty-state">조직 목록에서 관리할 조직을 선택하세요.</p>}
    </section>
  </div>;
}

function NetworksView({ pools, clusters, onCreate, onEdit, onDelete }: { pools: IpPool[]; clusters: Cluster[]; onCreate: () => void; onEdit: (pool: IpPool) => void; onDelete: (pool: IpPool) => void }) {
  const clusterNames = new Map(clusters.map((cluster) => [cluster.id, cluster.name]));
  return <div className="admin-content enter-admin"><section className="admin-section"><div className="admin-section-title"><div><p className="eyebrow">Address space</p><h2>IP 풀</h2></div><button className="accent-button" onClick={onCreate}>풀 생성</button></div><div className="admin-table pool-table"><div className="table-head"><span>풀</span><span>클러스터</span><span>네트워크</span><span>Gateway</span><span>할당</span><span>격리</span><span>상태</span><span>관리</span></div>{pools.map((pool) => <div className="table-row" key={pool.id}><span data-label="풀"><strong>{pool.name}</strong><small>{pool.bridge}{pool.vlan_tag ? ` · VLAN ${pool.vlan_tag}` : ""}</small></span><span data-label="클러스터" className={pool.cluster_id ? "pool-cluster-scope" : "pool-cluster-scope shared"}><strong>{pool.cluster_id ? clusterNames.get(pool.cluster_id) ?? "미확인 클러스터" : "공유 정책"}</strong>{pool.cluster_id && !clusterNames.has(pool.cluster_id) && <small>{pool.cluster_id.slice(0, 8)}</small>}</span><code data-label="네트워크">{pool.cidr}</code><code data-label="Gateway">{pool.gateway ?? "—"}</code><strong data-label="할당">{pool.allocated_count}</strong><span data-label="격리">{pool.quarantined_count}</span><span className="responsive-table-field" data-label="상태"><StatusMark ok={pool.availability_status !== "EXHAUSTED"} label={pool.availability_status} /></span><span className="pool-row-actions" data-label="관리"><button onClick={() => onEdit(pool)}>수정</button><button className="danger" onClick={() => onDelete(pool)}>삭제</button></span></div>)}</div>{!pools.length && <p className="empty-state">등록된 IP 풀이 없습니다.</p>}</section></div>;
}

function ProvisioningView({ products, templates, workloads, nodes, clusters, requests, onCreateProduct, onEditProduct, onDeleteProduct, onCreateTemplate, onEditTemplate, onDeleteTemplate, onCreateNode, onEditNode }: {
  products: Product[]; templates: Template[]; workloads: Workload[]; nodes: ProvisioningNode[]; clusters: Cluster[];
  requests: ProvisionRequest[]; onCreateProduct: () => void; onCreateTemplate: () => void;
  onEditProduct: (product: Product) => void; onDeleteProduct: (product: Product) => void;
  onEditTemplate: (template: Template) => void; onDeleteTemplate: (template: Template) => void;
  onCreateNode: () => void; onEditNode: (node: ProvisioningNode) => void;
}) {
  const clusterNames = new Map(clusters.map((cluster) => [cluster.id, cluster.name]));
  const sourceWorkloads = new Map(workloads.map((workload) => [workload.id, workload]));
  const eligible = nodes.filter((item) => item.is_enabled && !item.is_maintenance).length;
  return <div className="admin-content enter-admin">
    <section className="admin-section provisioning-catalog"><div className="admin-section-title"><div><p className="eyebrow">Catalog</p><h2>생성 카탈로그</h2></div><div className="setup-actions"><button onClick={onCreateTemplate}>템플릿 등록</button><button className="accent-button" onClick={onCreateProduct}>상품 생성</button></div></div>
      <div className="catalog-group-title"><h3>상품</h3><span>{products.length}</span></div>
      <div className="catalog-lines product-catalog-lines">{products.map((product) => <div key={product.id}><strong>{product.name}</strong><span>{product.cpu_cores} vCPU</span><span>{formatBytes(product.memory_bytes)} RAM</span><span>{formatBytes(product.disk_bytes)} Disk</span><StatusMark ok={product.is_enabled} label={product.is_enabled ? "사용" : "중지"} /><CatalogRowActions onEdit={() => onEditProduct(product)} onDelete={() => onDeleteProduct(product)} /></div>)}</div>
      {!products.length && <p className="empty-state">등록된 상품이 없습니다.</p>}
      <div className="catalog-group-title template-group-title"><h3>Linux QEMU 템플릿</h3><span>{templates.length}</span></div>
      <div className="catalog-lines template-catalog-lines">{templates.map((template) => { const source = sourceWorkloads.get(template.source_workload_id); return <div key={template.id}><strong>{template.name}</strong><span>{source ? `${source.cluster_name} / ${source.node} · VMID ${source.vmid}` : "원본 정보 없음"}</span><code>{template.source_disk}</code><span>{template.default_storage}</span><span>{template.default_bridge}{template.default_vlan_tag ? ` · VLAN ${template.default_vlan_tag}` : ""}</span><StatusMark ok={template.is_enabled} label={template.is_enabled ? "사용" : "중지"} /><CatalogRowActions onEdit={() => onEditTemplate(template)} onDelete={() => onDeleteTemplate(template)} /></div>; })}</div>
      {!templates.length && <p className="empty-state">등록된 템플릿이 없습니다. Proxmox 템플릿을 가져온 뒤 등록하세요.</p>}
    </section>
    <section className="admin-section provisioning-nodes"><div className="admin-section-title"><div><p className="eyebrow">Placement policy</p><h2>프로비저닝 노드</h2><p className="section-note">활성·유지보수·예약 가능 용량을 기준으로 VM 배치 대상을 결정합니다.</p></div><div className="setup-actions"><span>{eligible} / {nodes.length} eligible</span><button onClick={onCreateNode}>노드 추가</button></div></div>
      <div className="admin-table provisioning-node-table"><div className="table-head"><span>노드</span><span>클러스터</span><span>가용 RAM</span><span>가용 스토리지</span><span>배치 상태</span><span>최근 선택</span><span>관리</span></div>{nodes.map((node) => <div className="table-row" key={node.id}><span data-label="노드"><strong>{node.name}</strong><small>{node.id.slice(0, 8)}</small></span><strong data-label="클러스터">{clusterNames.get(node.cluster_id) ?? node.cluster_id.slice(0, 8)}</strong><code data-label="가용 RAM">{formatBytes(node.available_memory_bytes)}</code><code data-label="가용 스토리지">{formatBytes(node.available_storage_bytes)}</code><span className="responsive-table-field" data-label="배치 상태"><StatusMark ok={node.is_enabled && !node.is_maintenance} label={!node.is_enabled ? "중지" : node.is_maintenance ? "유지보수" : "자동 배치"} /></span><span data-label="최근 선택">{formatTime(node.last_selected_at)}</span><span className="responsive-table-field" data-label="관리"><button className="row-action" onClick={() => onEditNode(node)}>정책 수정</button></span></div>)}</div>
      {!nodes.length && <p className="empty-state">등록된 노드 정책이 없습니다. 클러스터 인벤토리에서 노드를 선택해 추가하세요.</p>}
    </section>
    <section className="admin-section"><div className="admin-section-title"><div><p className="eyebrow">Execution</p><h2>최근 프로비저닝</h2></div><span>{requests.length} requests</span></div><div className="admin-table request-table"><div className="table-head"><span>대상</span><span>VMID / IP</span><span>현재 단계</span><span>상태</span><span>요청 시각</span></div>{requests.map((request) => <div className="table-row" key={request.id}><span className="request-target"><strong>{request.target_name}</strong>{request.error_code && <small>{request.error_code}</small>}</span><code>{request.target_vmid ?? "auto"} · {request.ip_address ?? "reserved"}</code><span>{request.current_step}</span><StatusMark ok={request.status === "SUCCEEDED"} label={request.status} /><span>{formatTime(request.requested_at)}</span></div>)}</div></section>
  </div>;
}

function CatalogRowActions({ onEdit, onDelete }: { onEdit: () => void; onDelete: () => void }) {
  return <div className="catalog-row-actions"><button onClick={onEdit}>수정</button><button className="danger" onClick={onDelete}>삭제</button></div>;
}

function AuditView({ audits, total, offset, pageSize, loading, onPageChange, onPageSizeChange }: { audits: AuditLog[]; total: number; offset: number; pageSize: number; loading: boolean; onPageChange: (offset: number) => void; onPageSizeChange: (limit: number) => void }) {
  const start = total ? offset + 1 : 0;
  const end = Math.min(offset + audits.length, total);
  const hasNext = offset + audits.length < total;
  return <div className="admin-content enter-admin"><section className="admin-section"><div className="admin-section-title"><div><p className="eyebrow">Append-only trail</p><h2>최근 감사 사건</h2></div><span>{total} total</span></div><div className="admin-table audit-table"><div className="table-head"><span>시각</span><span>작업</span><span>행위자</span><span>리소스</span><span>결과</span><span>Request ID</span></div>{audits.map((audit) => { const actor = audit.actor_display_name ?? (audit.actor_user_id ? "삭제된 사용자" : "시스템"); const resource = audit.workload_name ?? audit.resource_type ?? "—"; const resourceDetail = audit.workload_vmid === null ? audit.resource_id?.slice(0, 12) : `${audit.workload_kind ?? "VM"} ${audit.workload_vmid} · ${audit.workload_cluster_name ?? audit.workload_node ?? ""}`; return <div className="table-row" key={audit.id}><span>{formatTime(audit.created_at)}</span><strong>{audit.action}</strong><span className="audit-actor"><strong>{actor}</strong><small>{audit.actor_email ?? audit.actor_role ?? "SYSTEM"}</small></span><span className="audit-resource"><strong>{resource}</strong><small>{resourceDetail ?? "—"}</small></span><StatusMark ok={audit.result === "SUCCEEDED"} label={audit.result} /><code>{audit.request_id?.slice(0, 12) ?? "—"}</code></div>; })}</div><div className="audit-pagination" aria-label="감사 로그 페이지"><span>{start}–{end} / 전체 {total}건</span><div><button disabled={loading || offset === 0} onClick={() => onPageChange(Math.max(0, offset - pageSize))}>이전</button><button disabled={loading || !hasNext} onClick={() => onPageChange(offset + pageSize)}>다음</button></div><label>페이지당 <select value={pageSize} disabled={loading} onChange={(event) => onPageSizeChange(Number(event.target.value))}>{AUDIT_PAGE_SIZES.map((size) => <option key={size} value={size}>{size}건</option>)}</select></label></div></section></div>;
}

function DrawerIntro({ eyebrow, title, copy }: { eyebrow: string; title: string; copy: string }) { return <div className="drawer-intro"><p className="eyebrow">{eyebrow}</p><h2>{title}</h2><p>{copy}</p></div>; }
function SubmitButton({ saving, label, disabled = false }: { saving: boolean; label: string; disabled?: boolean }) { return <button className="drawer-submit" type="submit" disabled={saving || disabled}>{saving ? "검증 중…" : label}<span>↗</span></button>; }
function ClusterForm({ onSubmit, saving }: { onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean }) { return <form className="admin-form" onSubmit={onSubmit}><DrawerIntro eyebrow="New cluster" title="Proxmox 연결" copy="등록 전에 TLS, 인증, 최소 권한을 실제 endpoint에서 확인합니다." /><label>표시 이름<input name="name" required placeholder="seoul-pve-1" /></label><label>API endpoint<input name="api_base_url" type="url" required placeholder="https://pve.example.internal:8006" /></label><label>Token identifier<input name="token_identifier" required placeholder="svc@pve!pvemaster" /></label><label>Token secret<input name="token_secret" type="password" required autoComplete="off" /></label><label>사설 CA PEM <small>선택</small><textarea name="ca_bundle_pem" rows={6} spellCheck={false} /></label><p className="form-security">Secret은 응답과 로그에 표시되지 않으며 암호화되어 저장됩니다.</p><SubmitButton saving={saving} label="검증 후 등록" /></form>; }
function ClusterDeleteForm({ cluster, check, checking, onSubmit, saving }: { cluster: Cluster; check: ClusterRemovalCheck | null; checking: boolean; onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean }) {
  const pattern = cluster.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const blockLabels: Record<string, string> = { ASSIGNED_WORKLOADS: "조직 할당 VM/CT", ACTIVE_OPERATIONS: "진행 중 VM 작업", ACTIVE_PROVISIONING_REQUESTS: "진행 중 프로비저닝", PROVISIONING_NODES: "프로비저닝 노드 정책", TEMPLATES: "등록 템플릿", CLUSTER_IP_POOLS: "클러스터 전용 IP 풀" };
  return <form className="admin-form" onSubmit={onSubmit}><DrawerIntro eyebrow="Cluster removal" title={`${cluster.name} 등록 해제`} copy="PVE의 VM/CT는 변경하지 않고, 이 서비스의 연결과 저장된 API token을 비활성화합니다." />{checking && <p className="form-security">연결된 리소스를 검사하고 있습니다…</p>}{check && !check.can_remove && <div className="removal-blocks" role="alert"><strong>먼저 정리해야 하는 리소스</strong>{check.blocks.map((block) => <span key={block.code}>{blockLabels[block.code] ?? block.code}<b>{block.count}건</b></span>)}</div>}{check?.can_remove && <p className="form-security">연결된 운영 리소스가 없습니다. 등록 해제할 수 있습니다.</p>}<label>확인 문자열 <small>{cluster.name} 입력</small><input name="confirmation" required autoComplete="off" pattern={pattern} disabled={!check?.can_remove || checking} /></label><p className="form-security node-inventory-error">등록 해제 후에는 이 클러스터의 인벤토리 조회와 관리 작업이 중단됩니다. 다시 사용하려면 새 연결을 등록해야 합니다.</p><SubmitButton saving={saving} disabled={!check?.can_remove || checking} label="클러스터 등록 해제" /></form>;
}
function UserForm({ onSubmit, saving, organizationName = null }: { onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean; organizationName?: string | null }) {
  const assignsToOrganization = Boolean(organizationName);
  return <form className="admin-form" onSubmit={onSubmit}>
    <DrawerIntro eyebrow={assignsToOrganization ? "New member" : "New identity"} title={assignsToOrganization ? `${organizationName}에 사용자 추가` : "사용자 생성"} copy={assignsToOrganization ? "고객 계정을 생성하고 현재 조직의 구성원으로 바로 연결합니다." : "역할은 서버에서 다시 검증되며 초기 비밀번호는 응답에 노출되지 않습니다."} />
    <label>이메일<input name="email" type="email" required autoFocus /></label>
    <label>표시 이름<input name="display_name" required /></label>
    {assignsToOrganization ? <><input name="role" type="hidden" value="CUSTOMER" /><div className="form-fixed-value"><span>역할</span><strong>CUSTOMER</strong><small>조직 구성원은 고객 역할로 생성됩니다.</small></div></> : <label>역할<select name="role" defaultValue="CUSTOMER"><option>CUSTOMER</option><option>OPERATOR</option><option>SUPER_ADMIN</option></select></label>}
    <label>초기 비밀번호<input name="password" type="password" minLength={12} required autoComplete="new-password" /></label>
    <SubmitButton saving={saving} label={assignsToOrganization ? "생성하고 조직에 추가" : "사용자 생성"} />
  </form>;
}
function UserPasswordResetForm({ user, onSubmit, saving }: { user: CurrentUser; onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean }) {
  return <form className="admin-form" onSubmit={onSubmit}>
    <DrawerIntro eyebrow="Credential reset" title={`${user.display_name} 비밀번호 초기화`} copy={`${user.email} 계정에 관리자가 지정한 새 비밀번호를 설정합니다.`} />
    <label>새 비밀번호 <small>12자 이상</small><input name="new_password" type="password" minLength={12} maxLength={1024} required autoFocus autoComplete="new-password" /></label>
    <label>새 비밀번호 확인<input name="password_confirmation" type="password" minLength={12} maxLength={1024} required autoComplete="new-password" /></label>
    <p className="form-security node-inventory-error">초기화 즉시 이 사용자의 모든 로그인 세션과 갱신 토큰이 폐기됩니다. 새 비밀번호는 별도의 안전한 채널로 전달하세요.</p>
    <SubmitButton saving={saving} label="비밀번호 초기화" />
  </form>;
}
function UserStatusForm({ user, onSubmit, saving }: { user: CurrentUser; onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean }) {
  const nextLabel = user.is_active ? "비활성화" : "활성화";
  return <form className="admin-form" onSubmit={onSubmit}>
    <DrawerIntro eyebrow="Account status" title={`${user.display_name} ${nextLabel}`} copy={user.is_active ? "로그인을 즉시 차단하고 현재 세션을 모두 종료합니다. 조직 소속과 감사 이력은 유지됩니다." : "사용자가 다시 로그인하고 소속 조직의 VM에 접근할 수 있도록 계정을 활성화합니다."} />
    <div className="form-fixed-value"><span>대상 계정</span><strong>{user.email}</strong><small>{user.role} · {user.organization_names?.join(", ") || "조직 미할당"}</small></div>
    {user.is_active && <p className="form-security node-inventory-error">실행 즉시 현재 기기를 포함한 모든 로그인 세션이 종료됩니다.</p>}
    <SubmitButton saving={saving} label={`사용자 ${nextLabel}`} />
  </form>;
}
function UserDeleteForm({ user, onSubmit, saving }: { user: CurrentUser; onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean }) {
  const pattern = user.email.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return <form className="admin-form" onSubmit={onSubmit}>
    <DrawerIntro eyebrow="Account removal" title={`${user.display_name} 삭제`} copy="로그인 계정과 조직 소속을 제거합니다. 운영 작업과 감사 이력의 참조는 익명화된 상태로 보존됩니다." />
    <label>확인 문자열 <small>{user.email} 입력</small><input name="confirmation" required autoComplete="off" pattern={pattern} autoFocus /></label>
    <p className="form-security node-inventory-error">삭제 후에는 계정을 복구할 수 없습니다. 일시적으로 접근만 막으려면 비활성화를 사용하세요.</p>
    <SubmitButton saving={saving} label="사용자 삭제" />
  </form>;
}
function OrganizationForm({ onSubmit, saving, existing }: { onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean; existing: Organization | null }) { return <form className="admin-form" onSubmit={onSubmit}><DrawerIntro eyebrow={existing ? "Tenant settings" : "New tenant"} title={existing ? `${existing.name} 수정` : "조직 생성"} copy={existing ? "조직 이름 변경은 구성원 권한과 VM/CT 소유권을 유지합니다." : "VM 소유권과 고객 접근 경계를 구성하는 기본 단위입니다."} /><label>조직 이름<input name="name" required maxLength={160} defaultValue={existing?.name ?? ""} autoFocus /></label><SubmitButton saving={saving} label={existing ? "조직 수정" : "조직 생성"} /></form>; }
function OrganizationDeleteForm({ organization, memberCount, workloadCount, onSubmit, saving }: { organization: Organization; memberCount: number; workloadCount: number; onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean }) {
  const blocked = memberCount > 0 || workloadCount > 0;
  const pattern = organization.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return <form className="admin-form" onSubmit={onSubmit}><DrawerIntro eyebrow="Tenant status" title={`${organization.name} 비활성화`} copy="신규 접근과 할당 대상에서 조직을 제외합니다. 조직 정보와 감사 이력은 보존되며 나중에 다시 활성화할 수 있습니다." /><div className="removal-blocks"><strong>비활성화 전 정리 상태</strong><span>구성원<b>{memberCount}명</b></span><span>할당 VM/CT<b>{workloadCount}개</b></span></div>{blocked ? <p className="form-security node-inventory-error">구성원과 VM/CT 할당을 모두 제거한 뒤 비활성화할 수 있습니다.</p> : <><label>확인 문자열 <small>{organization.name} 입력</small><input name="confirmation" required autoComplete="off" pattern={pattern} /></label><p className="form-security node-inventory-error">진행 중인 프로비저닝 요청이 있으면 서버에서 비활성화를 거부합니다.</p></>}<SubmitButton saving={saving} disabled={blocked} label="조직 비활성화" /></form>;
}
function PoolForm({ onSubmit, saving, clusters, existing }: { onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean; clusters: Cluster[]; existing: IpPool | null }) { return <form className="admin-form" onSubmit={onSubmit}><DrawerIntro eyebrow="Address space" title={existing ? `${existing.name} 수정` : "IP 풀 생성"} copy={existing ? "주소가 사용된 풀은 CIDR, 클러스터와 Gateway를 변경할 수 없습니다." : "네트워크·브로드캐스트·gateway는 자동 할당에서 제외됩니다."} /><label>풀 이름<input name="name" required defaultValue={existing?.name ?? ""} /></label><label>클러스터<select name="cluster_id" defaultValue={existing?.cluster_id ?? ""}><option value="">공유 정책</option>{clusters.map((cluster) => <option key={cluster.id} value={cluster.id}>{cluster.name}</option>)}</select></label><label>CIDR<input name="cidr" required placeholder="192.0.2.0/24" defaultValue={existing?.cidr ?? ""} /></label><label>Gateway<input name="gateway" placeholder="192.0.2.1" defaultValue={existing?.gateway ?? ""} /></label><label>DNS <small>쉼표 구분</small><input name="dns_servers" placeholder="1.1.1.1, 8.8.8.8" defaultValue={existing?.dns_servers.join(", ") ?? ""} /></label><label>Bridge<input name="bridge" defaultValue={existing?.bridge ?? "vmbr0"} required /></label><label>VLAN tag <small>선택</small><input name="vlan_tag" type="number" min="1" max="4094" defaultValue={existing?.vlan_tag ?? ""} /></label><label>할당 방식<select name="allocation_strategy" defaultValue={existing?.allocation_strategy ?? "SEQUENTIAL"}><option value="SEQUENTIAL">순차 할당</option><option value="RANDOM">무작위 할당</option></select></label><label>격리 시간(초)<input name="quarantine_seconds" type="number" min="0" max="2592000" defaultValue={existing?.quarantine_seconds ?? 600} required /></label><SubmitButton saving={saving} label={existing ? "IP 풀 수정" : "IP 풀 생성"} /></form>; }
function PoolDeleteForm({ pool, onSubmit, saving }: { pool: IpPool; onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean }) {
  const pattern = pool.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return <form className="admin-form" onSubmit={onSubmit}><DrawerIntro eyebrow="Address space removal" title={`${pool.name} 삭제`} copy="풀을 신규 할당 대상에서 제거하되 기존 주소와 할당 이력은 보존합니다." /><div className="form-fixed-value"><span>네트워크</span><strong>{pool.cidr}</strong><small>할당 {pool.allocated_count} · 격리 {pool.quarantined_count}</small></div><label>확인 문자열 <small>{pool.name} 입력</small><input name="confirmation" required autoComplete="off" pattern={pattern} /></label><p className="form-security node-inventory-error">예약·할당·격리 주소 또는 진행 중인 프로비저닝 요청이 있으면 삭제할 수 없습니다. 먼저 해당 주소를 모두 해제하세요.</p><SubmitButton saving={saving} label="IP 풀 삭제" /></form>;
}
function ProductForm({ onSubmit, saving, existing }: { onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean; existing: Product | null }) {
  return <form className="admin-form" onSubmit={onSubmit}><DrawerIntro eyebrow="Catalog item" title={existing ? `${existing.name} 수정` : "상품 사양 생성"} copy="변경된 사양은 새 프로비저닝 요청부터 적용되며 기존 요청의 snapshot은 유지됩니다." /><label>상품 이름<input name="name" required defaultValue={existing?.name ?? ""} /></label><label>vCPU<input name="cpu_cores" type="number" min="1" max="128" defaultValue={existing?.cpu_cores ?? 2} required /></label><label>RAM (GiB)<input name="memory_gib" type="number" min="0.25" step="0.25" defaultValue={existing ? existing.memory_bytes / 1024 ** 3 : 2} required /></label><label>Disk (GiB)<input name="disk_gib" type="number" min="1" defaultValue={existing ? existing.disk_bytes / 1024 ** 3 : 20} required /></label>{existing && <label className="form-checkbox"><input name="is_enabled" type="checkbox" defaultChecked={existing.is_enabled} /> 새 프로비저닝에서 사용</label>}<SubmitButton saving={saving} label={existing ? "상품 수정" : "상품 생성"} /></form>;
}

function CatalogDeleteForm({ kind, name, copy, onSubmit, saving }: { kind: string; name: string; copy: string; onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean }) {
  const pattern = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return <form className="admin-form" onSubmit={onSubmit}><DrawerIntro eyebrow="Catalog removal" title={`${name} 삭제`} copy={copy} /><label>확인 문자열 <small>{name} 입력</small><input name="confirmation" required autoComplete="off" pattern={pattern} /></label><p className="form-security node-inventory-error">{kind} 삭제는 되돌릴 수 없습니다. 사용 중이라면 삭제 대신 비활성화를 권장합니다.</p><SubmitButton saving={saving} label={`${kind} 삭제`} /></form>;
}

function VmSpecForm({ workload, onSubmit, saving }: { workload: Workload; onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean }) {
  return <form className="admin-form" onSubmit={onSubmit}><DrawerIntro eyebrow="Configuration" title={`${workload.name ?? workload.vmid} 사양 변경`} copy="CPU와 RAM을 변경하고 단일 디스크 구성인 경우 디스크를 증설합니다." /><label>vCPU<input name="cpu_cores" type="number" min="1" max="128" defaultValue={workload.cpu_cores ?? 1} required /></label><label>RAM (GiB)<input name="memory_gib" type="number" min="1" max="4096" defaultValue={Math.max(1, Math.ceil((workload.memory_bytes ?? 0) / 1024 ** 3))} required /></label><label>Disk (GiB) <small>비우면 유지 · 현재 {formatBytes(workload.disk_bytes)}</small><input name="disk_gib" type="number" min={Math.max(1, Math.ceil((workload.disk_bytes ?? 0) / 1024 ** 3))} placeholder="증설할 때만 입력" /></label><label>변경 사유 <small>선택</small><textarea name="reason" rows={3} maxLength={500} /></label><p className="form-security">실행 중인 게스트는 설정에 따라 재시작 후 CPU·RAM 변경이 완전히 반영될 수 있습니다. 여러 디스크가 연결된 QEMU VM의 디스크 변경은 안전을 위해 거부됩니다.</p><SubmitButton saving={saving} label="사양 변경 요청" /></form>;
}

function VmDeleteForm({ workload, onSubmit, saving }: { workload: Workload; onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean }) {
  const expected = workload.name ?? String(workload.vmid);
  return <form className="admin-form" onSubmit={onSubmit}><DrawerIntro eyebrow="Danger zone" title={`${expected} 삭제`} copy="Proxmox에서 VM/CT를 영구 삭제합니다. 이 작업은 되돌릴 수 없습니다." /><label>확인 문자열 <small>{expected} 입력</small><input name="confirmation" required autoComplete="off" pattern={expected.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} /></label><label>삭제 사유 <small>선택</small><textarea name="reason" rows={3} maxLength={500} /></label><p className="form-security node-inventory-error">삭제 전에 전원이 중지되어 있고 조직 할당이 해제되어 있어야 합니다. 연결된 IP는 자동 회수되지 않습니다.</p><SubmitButton saving={saving} label="영구 삭제" /></form>;
}

function TemplateForm({ onSubmit, saving, workloads, existing }: { onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean; workloads: Workload[]; existing: Template | null }) {
  const sources = workloads.filter((item) => item.kind === "QEMU" && item.is_template && item.is_present);
  const sourceAvailable = !existing || sources.some((item) => item.id === existing.source_workload_id);
  return <form className="admin-form" onSubmit={onSubmit}><DrawerIntro eyebrow="Template catalog" title={existing ? `${existing.name} 수정` : "QEMU 템플릿 등록"} copy="플랫폼 등록 정보만 관리하며 Proxmox 원본 템플릿 자체는 변경하지 않습니다." />{!sources.length && <p className="form-security">사용 가능한 QEMU 템플릿이 없습니다. Proxmox에서 템플릿을 만든 뒤 클러스터 화면의 VM/CT 가져오기를 실행하세요.</p>}{existing && !sourceAvailable && <p className="form-security node-inventory-error">현재 원본이 인벤토리에 없습니다. 다른 QEMU 템플릿을 선택하거나 항목을 비활성화하세요.</p>}<label>표시 이름<input name="name" required defaultValue={existing?.name ?? ""} /></label><label>원본 템플릿<select name="source_workload_id" required defaultValue={existing?.source_workload_id ?? ""}><option value="" disabled>템플릿 선택</option>{existing && !sourceAvailable && <option value={existing.source_workload_id}>현재 원본 · 인벤토리 없음</option>}{sources.map((item) => <option key={item.id} value={item.id}>{item.cluster_name} · {item.node} · {item.vmid} {item.name}</option>)}</select></label><label>원본 디스크<input name="source_disk" defaultValue={existing?.source_disk ?? "scsi0"} pattern="(scsi|virtio|sata)[0-9]+" required /></label><label>대상 스토리지<input name="default_storage" defaultValue={existing?.default_storage ?? ""} placeholder="local-lvm" pattern="[A-Za-z0-9_.-]+" required /></label><label>네트워크 브리지<input name="default_bridge" defaultValue={existing?.default_bridge ?? "vmbr0"} pattern="[A-Za-z0-9_.-]+" required /></label><label>VLAN tag <small>선택</small><input name="default_vlan_tag" type="number" min="1" max="4094" defaultValue={existing?.default_vlan_tag ?? ""} /></label>{existing && <label className="form-checkbox"><input name="is_enabled" type="checkbox" defaultChecked={existing.is_enabled} /> 새 프로비저닝에서 사용</label>}<SubmitButton saving={saving} disabled={!sources.length && !existing} label={existing ? "템플릿 수정" : "템플릿 등록"} /></form>;
}

function ProvisioningNodeForm({ onSubmit, saving, clusters, existing, apiBaseUrl, token }: {
  onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean; clusters: Cluster[];
  existing: ProvisioningNode | null; apiBaseUrl: string; token: string;
}) {
  const [clusterId, setClusterId] = useState(existing?.cluster_id ?? "");
  const [name, setName] = useState(existing?.name ?? "");
  const [memoryGib, setMemoryGib] = useState(existing ? String(wholeGib(existing.available_memory_bytes)) : "");
  const [storageGib, setStorageGib] = useState(existing ? String(wholeGib(existing.available_storage_bytes)) : "");
  const [liveNodes, setLiveNodes] = useState<ClusterNode[]>([]);
  const [liveStorages, setLiveStorages] = useState<ClusterStorage[]>([]);
  const [inventoryLoading, setInventoryLoading] = useState(false);
  const [inventoryError, setInventoryError] = useState("");

  useEffect(() => {
    if (!clusterId) return;
    let active = true;
    const timer = window.setTimeout(() => {
      setInventoryLoading(true); setInventoryError("");
      void getClusterInventory(apiBaseUrl, token, clusterId).then((inventory) => {
        if (!active) return;
        setLiveNodes(inventory.nodes); setLiveStorages(inventory.storages);
        if (!existing && inventory.nodes[0]) {
          const first = inventory.nodes[0];
          setName(first.node);
          setMemoryGib(String(wholeGib((first.maxmem ?? 0) - (first.mem ?? 0))));
          setStorageGib(String(wholeGib(availableStorageBytes(inventory.storages, first.node))));
        }
      }).catch((caught) => { if (active) setInventoryError(readableError(caught)); })
        .finally(() => { if (active) setInventoryLoading(false); });
    }, 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [apiBaseUrl, clusterId, existing, token]);

  function selectLiveNode(nodeName: string) {
    setName(nodeName);
    const node = liveNodes.find((item) => item.node === nodeName);
    if (node) setMemoryGib(String(wholeGib((node.maxmem ?? 0) - (node.mem ?? 0))));
    setStorageGib(String(wholeGib(availableStorageBytes(liveStorages, nodeName))));
  }

  const selectedLiveNode = liveNodes.find((item) => item.node === name);
  return <form className="admin-form" onSubmit={onSubmit}>
    <DrawerIntro eyebrow="Placement policy" title={existing ? `${existing.name} 정책 수정` : "프로비저닝 노드 추가"} copy="클러스터의 실제 노드를 선택하고 자동 배치 허용 여부와 예약 가능 용량을 관리합니다." />
    {existing ? <><input type="hidden" name="cluster_id" value={existing.cluster_id} /><label>클러스터<input value={clusters.find((item) => item.id === existing.cluster_id)?.name ?? existing.cluster_id} readOnly /></label></> : <label>클러스터<select name="cluster_id" required value={clusterId} onChange={(event) => { setClusterId(event.target.value); setName(""); setLiveNodes([]); setLiveStorages([]); }}><option value="" disabled>클러스터 선택</option>{clusters.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
    <label>Proxmox 노드{existing ? <input name="name" value={name} readOnly /> : <select name="name" required value={name} disabled={!clusterId || inventoryLoading} onChange={(event) => selectLiveNode(event.target.value)}><option value="">{inventoryLoading ? "인벤토리 불러오는 중…" : "노드 선택"}</option>{liveNodes.map((item) => <option key={item.node} value={item.node}>{item.node} · {item.status ?? "unknown"}</option>)}</select>}</label>
    {inventoryError && <p className="form-security node-inventory-error">{inventoryError}</p>}
    {selectedLiveNode && <p className="node-live-capacity">현재 PVE 인벤토리 · RAM {formatBytes(Math.max(0, (selectedLiveNode.maxmem ?? 0) - (selectedLiveNode.mem ?? 0)))} 가용 · 스토리지 {formatBytes(availableStorageBytes(liveStorages, selectedLiveNode.node))} 가용</p>}
    {selectedLiveNode && !inventoryLoading && availableStorageBytes(liveStorages, selectedLiveNode.node) === 0 && <p className="form-security node-inventory-error">스토리지 가용량을 확인하지 못했습니다. Proxmox 토큰의 스토리지 조회 권한과 스토리지 상태를 확인하세요.</p>}
    <label>예약 가능 RAM (GiB)<input name="memory_gib" type="number" min="0" step="1" value={memoryGib} onChange={(event) => setMemoryGib(event.target.value)} required /></label>
    <label>예약 가능 스토리지 (GiB)<input name="storage_gib" type="number" min="0" step="1" value={storageGib} onChange={(event) => setStorageGib(event.target.value)} required /></label>
    <div className="form-switches"><label className="form-checkbox"><input name="is_enabled" type="checkbox" defaultChecked={existing?.is_enabled ?? true} /> 자동 배치 허용</label><label className="form-checkbox"><input name="is_maintenance" type="checkbox" defaultChecked={existing?.is_maintenance ?? false} /> 유지보수 상태</label></div>
    <p className="form-security">용량은 동시 프로비저닝 예약에 사용됩니다. 유지보수 중이거나 비활성인 노드는 자동 선택에서 제외됩니다.</p>
    <SubmitButton saving={saving || inventoryLoading} disabled={!clusters.length || !name} label={existing ? "정책 저장" : "노드 추가"} />
  </form>;
}

function VmCreateForm({ onSubmit, saving, products, templates, organizations, pools, nodes, workloads }: {
  onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean; products: Product[]; templates: Template[];
  organizations: Organization[]; pools: IpPool[]; nodes: ProvisioningNode[]; workloads: Workload[];
}) {
  const [templateId, setTemplateId] = useState(templates[0]?.id ?? "");
  const [keyMode, setKeyMode] = useState<"existing" | "generate">("existing");
  const [keyName, setKeyName] = useState("pvemaster-key");
  const [sshPublicKeys, setSshPublicKeys] = useState("");
  const [keyGenerating, setKeyGenerating] = useState(false);
  const [keyGenerationError, setKeyGenerationError] = useState("");
  const [generatedKey, setGeneratedKey] = useState<{ filename: string; fingerprint: string } | null>(null);
  const selectedTemplate = templates.find((item) => item.id === templateId);
  const source = workloads.find((item) => item.id === selectedTemplate?.source_workload_id);
  const eligibleNodes = nodes.filter((item) => item.cluster_id === source?.cluster_id && item.is_enabled && !item.is_maintenance);
  const eligiblePools = pools.filter((item) => item.is_active && (item.cluster_id === null || item.cluster_id === source?.cluster_id) && item.ip_family === 4 && item.availability_status !== "EXHAUSTED");
  const ready = products.some((item) => item.is_enabled) && templates.some((item) => item.is_enabled) && organizations.some((item) => item.is_active) && eligibleNodes.length > 0 && eligiblePools.length > 0;

  async function generateKey() {
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(keyName)) {
      setKeyGenerationError("키 이름은 영문, 숫자, 점, 밑줄, 하이픈으로 1~64자까지 입력하세요.");
      return;
    }
    setKeyGenerating(true);
    setKeyGenerationError("");
    try {
      const keyPair = await generateSshRsaKeyPair(keyName);
      const url = URL.createObjectURL(new Blob([keyPair.privateKeyPem], { type: "application/x-pem-file" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = keyPair.filename;
      anchor.rel = "noopener";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
      setSshPublicKeys(keyPair.publicKey);
      setGeneratedKey({ filename: keyPair.filename, fingerprint: keyPair.fingerprint });
    } catch (caught) {
      setKeyGenerationError(caught instanceof Error ? caught.message : "SSH 키를 생성하지 못했습니다.");
    } finally {
      setKeyGenerating(false);
    }
  }

  return <form className="admin-form" onSubmit={onSubmit}>
    <DrawerIntro eyebrow="Full clone workflow" title="Linux VM 생성" copy="QEMU template full clone과 Cloud-Init으로 VM을 비동기 생성합니다. 각 단계와 실패 지점은 기록됩니다." />
    {!ready && <p className="form-security">생성 준비가 필요합니다. 활성 상품·템플릿·조직·IPv4 풀과 템플릿 클러스터의 프로비저닝 노드를 확인하세요.</p>}
    <label>상품<select name="product_id" required defaultValue=""><option value="" disabled>상품 선택</option>{products.filter((item) => item.is_enabled).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.cpu_cores} vCPU · {formatBytes(item.memory_bytes)}</option>)}</select></label>
    <label>템플릿<select name="template_id" required value={templateId} onChange={(event) => setTemplateId(event.target.value)}><option value="" disabled>템플릿 선택</option>{templates.filter((item) => item.is_enabled).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label>조직<select name="organization_id" required defaultValue=""><option value="" disabled>할당 조직 선택</option>{organizations.filter((item) => item.is_active).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label>대상 노드 <small>기본값은 자동 배치</small><select name="target_node_id" defaultValue=""><option value="">자동 선택 (권장)</option>{eligibleNodes.map((item) => <option key={item.id} value={item.id}>{item.name} · RAM {formatBytes(item.available_memory_bytes)} · Disk {formatBytes(item.available_storage_bytes)}</option>)}</select></label>
    <label>IPv4 풀<select name="ip_pool_id" required defaultValue=""><option value="" disabled>IP 풀 선택</option>{eligiblePools.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.cidr}</option>)}</select></label>
    <label>VM 이름<input name="target_name" required maxLength={63} pattern="[A-Za-z0-9][A-Za-z0-9.-]{0,62}" placeholder="web-01" /></label>
    <label>VMID <small>비우면 자동 예약</small><input name="target_vmid" type="number" min="100" max="999999999" /></label>
    <label>Cloud-Init 사용자<input name="username" required pattern="[a-z_][a-z0-9_-]{0,31}" defaultValue="ubuntu" /></label>
    <fieldset className="ssh-key-fieldset">
      <legend>SSH 인증 키</legend>
      <div className="ssh-key-mode" role="radiogroup" aria-label="SSH 키 입력 방식">
        <button type="button" role="radio" aria-checked={keyMode === "existing"} className={keyMode === "existing" ? "active" : ""} onClick={() => setKeyMode("existing")}>기존 공개키</button>
        <button type="button" role="radio" aria-checked={keyMode === "generate"} className={keyMode === "generate" ? "active" : ""} onClick={() => setKeyMode("generate")}>새 키 생성</button>
      </div>
      {keyMode === "generate" && <div className="ssh-key-generator">
        <label>키 이름<input value={keyName} onChange={(event) => { setKeyName(event.target.value); setKeyGenerationError(""); }} maxLength={64} autoComplete="off" /></label>
        <button className="key-generate-button" type="button" onClick={generateKey} disabled={keyGenerating}>{keyGenerating ? "RSA 3072 키 생성 중…" : generatedKey ? "새 키 다시 생성" : "키 생성 및 개인키 다운로드"}</button>
        {keyGenerationError && <p className="key-generation-error" role="alert">{keyGenerationError}</p>}
        {generatedKey && <div className="generated-key-status" role="status"><strong>개인키 다운로드를 시작했습니다</strong><span>{generatedKey.filename}</span><code>{generatedKey.fingerprint}</code><small>개인키는 다시 받을 수 없습니다. 다운로드를 확인하고 안전하게 보관한 뒤 <code>chmod 600 {generatedKey.filename}</code>을 실행하세요.</small></div>}
      </div>}
    </fieldset>
    <label>SSH 공개키 <small>필수 · 한 줄에 하나</small><textarea name="ssh_public_keys" rows={5} required spellCheck={false} aria-describedby="ssh-public-key-help" placeholder="ssh-ed25519 AAAA... admin@example" value={sshPublicKeys} readOnly={keyMode === "generate" && Boolean(generatedKey)} onChange={(event) => { setSshPublicKeys(event.target.value); setGeneratedKey(null); }} /></label>
    <p id="ssh-public-key-help" className="field-help">{keyMode === "generate" ? "개인키는 서버로 전송하거나 저장하지 않습니다. 공개키만 VM의 Cloud-Init 설정에 사용됩니다." : <>로컬의 <code>~/.ssh/id_ed25519.pub</code> 파일에서 공개키 한 줄 전체를 붙여 넣으세요.</>}</p>
    <label className="form-checkbox"><input name="start_after_create" type="checkbox" defaultChecked /> 생성 완료 후 VM 시작</label>
    <p className="form-security">노드를 지정하지 않으면 활성·비유지보수·충분한 용량 조건을 만족하는 노드를 round-robin으로 선택합니다.</p>
    <SubmitButton saving={saving || keyGenerating} disabled={!ready || keyGenerating} label="VM 생성 요청" />
  </form>;
}

"use client";

import { useCallback, useEffect, useState } from "react";

import {
  AuthSession,
  CustomerPowerAction,
  CustomerApiError,
  CustomerJob,
  CustomerVm,
  getCustomerJob,
  listCustomerVms,
  requestPowerAction,
} from "@/lib/customer-api";
import { endBrowserSession } from "@/lib/browser-session";
import { openConsoleWindow } from "@/lib/console-window";
import { filterCustomerVms } from "@/lib/customer-portal-state";
import type { CustomerPowerFilter } from "@/lib/customer-portal-state";

import { LoginPanel } from "./login-panel";
import { PasswordChangeDialog } from "./password-change-dialog";
import { VmConsoleModal } from "./vm-console-modal";

const actionLabels: Record<CustomerPowerAction, string> = {
  start: "시작",
  shutdown: "정상 종료",
  stop: "강제 중지",
  reboot: "재부팅",
};

const terminalStatuses = new Set(["SUCCEEDED", "FAILED", "TIMEOUT"]);

function errorMessage(error: unknown): string {
  return error instanceof CustomerApiError
    ? error.message
    : "요청을 처리하는 중 연결 오류가 발생했습니다.";
}

function formatTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatBytes(value: number | null): string {
  if (value === null) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let result = value;
  let unit = 0;
  while (result >= 1024 && unit < units.length - 1) {
    result /= 1024;
    unit += 1;
  }
  return `${result.toFixed(result >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function LoginView({
  apiBaseUrl,
  onAuthenticated,
}: {
  apiBaseUrl: string;
  onAuthenticated: (session: AuthSession) => void;
}) {
  return (
    <main className="login-shell">
      <div className="ambient-grid" aria-hidden="true" />
      <header className="brand-bar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">PM</span>
          <span>PVE Master</span>
        </div>
        <div className="system-state">
          <span className="state-dot" aria-hidden="true" /> Customer portal
        </div>
      </header>
      <section className="login-stage" aria-labelledby="login-title">
        <div className="context-copy">
          <p className="eyebrow">Secure workspace</p>
          <h1 id="login-title">내 가상 머신을 제어합니다.</h1>
          <p className="supporting-copy">
            조직에 할당된 VM 상태를 확인하고 안전한 전원 작업을 요청하세요.
          </p>
        </div>
        <LoginPanel apiBaseUrl={apiBaseUrl} onAuthenticated={onAuthenticated} />
      </section>
      <footer className="login-footer">
        <span>Protected session</span><span className="footer-rule" aria-hidden="true" />
        <span>API v1</span>
      </footer>
    </main>
  );
}

export function CustomerPortal({
  apiBaseUrl,
  initialSession = null,
  onSessionEnded,
}: {
  apiBaseUrl: string;
  initialSession?: AuthSession | null;
  onSessionEnded?: () => void;
}) {
  const [session, setSession] = useState<AuthSession | null>(initialSession);
  const [vms, setVms] = useState<CustomerVm[]>([]);
  const [query, setQuery] = useState("");
  const [powerFilter, setPowerFilter] = useState<CustomerPowerFilter>("ALL");
  const [pendingAction, setPendingAction] = useState<CustomerPowerAction | null>(null);
  const [pendingVm, setPendingVm] = useState<CustomerVm | null>(null);
  const [forcedAcknowledged, setForcedAcknowledged] = useState(false);
  const [activeJob, setActiveJob] = useState<CustomerJob | null>(null);
  const [consoleVm, setConsoleVm] = useState<CustomerVm | null>(null);
  const [passwordDialogOpen, setPasswordDialogOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refreshList = useCallback(async (activeSession: AuthSession) => {
    const items = await listCustomerVms(apiBaseUrl, activeSession.accessToken);
    setVms(items);
  }, [apiBaseUrl]);

  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => {
      void refreshList(session)
        .catch((caught) => setError(errorMessage(caught)))
        .finally(() => setLoading(false));
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refreshList, session]);

  useEffect(() => {
    if (!session || !activeJob || terminalStatuses.has(activeJob.status)) return;
    const timer = window.setTimeout(async () => {
      try {
        const next = await getCustomerJob(apiBaseUrl, session.accessToken, activeJob.id);
        setActiveJob(next);
        if (terminalStatuses.has(next.status)) {
          await refreshList(session);
        }
      } catch (caught) {
        setError(errorMessage(caught));
      }
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [activeJob, apiBaseUrl, refreshList, session]);

  async function runAction() {
    if (!session || !pendingVm || !pendingAction) return;
    const action = pendingAction;
    const vmId = pendingVm.id;
    if (action === "stop" && !forcedAcknowledged) return;
    setPendingAction(null);
    setPendingVm(null);
    setForcedAcknowledged(false);
    setError("");
    try {
      const job = await requestPowerAction(
        apiBaseUrl,
        session.accessToken,
        vmId,
        action,
        crypto.randomUUID(),
        { confirmForced: action === "stop" },
      );
      setActiveJob(job);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  function closeActionDialog() {
    setPendingAction(null);
    setPendingVm(null);
    setForcedAcknowledged(false);
  }

  function openActionDialog(vm: CustomerVm, action: CustomerPowerAction) {
    setForcedAcknowledged(false);
    setPendingVm(vm);
    setPendingAction(action);
  }

  function openConsole(vm: CustomerVm) {
    if (!openConsoleWindow(vm.id)) setConsoleVm(vm);
  }

  async function endSession() {
    if (!session) return;
    try {
      await endBrowserSession();
    } finally {
      setSession(null);
      setVms([]);
      setPendingVm(null);
      setConsoleVm(null);
      setPasswordDialogOpen(false);
      setActiveJob(null);
      onSessionEnded?.();
    }
  }

  function beginSession(nextSession: AuthSession) {
    setLoading(true);
    setError("");
    setSession(nextSession);
  }

  if (!session) {
    return <LoginView apiBaseUrl={apiBaseUrl} onAuthenticated={beginSession} />;
  }

  const visibleVms = filterCustomerVms(vms, { query, power: powerFilter });

  return (
    <main className="portal-shell">
      <header className="portal-header">
        <div className="brand"><span className="brand-mark" aria-hidden="true">PM</span><span>PVE Master</span></div>
        <div className="portal-session"><span className="state-dot" aria-hidden="true" /> 고객 워크스페이스 <button onClick={() => setPasswordDialogOpen(true)}>비밀번호 변경</button><button onClick={endSession}>로그아웃</button></div>
      </header>

      {error && <div className="error-banner" role="alert"><span>{error}</span><button onClick={() => setError("")} aria-label="오류 닫기">×</button></div>}

      <div className="customer-workspace">
        <section className="customer-inventory" aria-labelledby="customer-inventory-title">
          <div className="customer-inventory-heading">
            <div>
              <p className="eyebrow">Assigned resources</p>
              <h1 id="customer-inventory-title">가상 머신</h1>
              <p>할당된 VM의 상태, IP 주소와 사양을 한 화면에서 확인합니다.</p>
            </div>
            <span><strong>{vms.length}</strong> resources</span>
          </div>

          <div className="customer-inventory-tools">
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="이름 또는 IP 주소 검색"
              aria-label="가상 머신 검색"
            />
            <select
              value={powerFilter}
              onChange={(event) => setPowerFilter(event.target.value as CustomerPowerFilter)}
              aria-label="전원 상태 필터"
            >
              <option value="ALL">전체 상태</option>
              <option value="RUNNING">실행 중</option>
              <option value="STOPPED">중지됨</option>
            </select>
            <span>{visibleVms.length} / {vms.length}</span>
          </div>

          <div className="customer-table-scroll" aria-label="가상 머신 목록">
            <div className="customer-vm-table">
              <div className="customer-vm-table-head" aria-hidden="true">
                <span>가상 머신</span><span>상태</span><span>IP 주소</span><span>vCPU</span><span>메모리</span><span>디스크</span><span>마지막 확인</span><span>전원 제어</span>
              </div>
              {visibleVms.map((vm) => {
                const running = vm.power_state.toUpperCase() === "RUNNING";
                const vmJob = activeJob?.vm_id === vm.id ? activeJob : null;
                const jobPending = vmJob !== null && !terminalStatuses.has(vmJob.status);
                return (
                  <div
                    key={vm.id}
                    className="customer-vm-row"
                  >
                    <span className="customer-vm-identity" data-label="가상 머신"><strong>{vm.name}</strong><small>{vm.id.slice(0, 8)}</small></span>
                    <span className="customer-vm-status" data-label="상태"><span className={`status-pip ${vm.power_state.toLowerCase()}`} aria-hidden="true" />{running ? "실행 중" : "중지됨"}</span>
                    <span className={vm.assigned_ip_addresses.length ? "customer-vm-ip" : "customer-vm-muted"} data-label="IP 주소">{vm.assigned_ip_addresses.length ? vm.assigned_ip_addresses.join(", ") : "미할당"}</span>
                    <strong data-label="vCPU">{vm.cpu_cores ?? "—"}</strong>
                    <span data-label="메모리">{formatBytes(vm.memory_bytes)}</span>
                    <span data-label="디스크">{formatBytes(vm.disk_bytes)}</span>
                    <span className="customer-vm-muted" data-label="마지막 확인">{formatTime(vm.observed_at)}</span>
                    <span className="customer-row-actions" data-label="전원 제어">
                      <span className="customer-row-action-buttons">
                        <button className="console-row-button" disabled={!running} onClick={() => openConsole(vm)}>콘솔</button>
                        <button disabled={running || jobPending} onClick={() => openActionDialog(vm, "start")}>시작</button>
                        <button disabled={!running || jobPending} onClick={() => openActionDialog(vm, "shutdown")}>정상 종료</button>
                        <button disabled={!running || jobPending} onClick={() => openActionDialog(vm, "reboot")}>재부팅</button>
                        <button className="customer-danger-action" disabled={!running || jobPending} onClick={() => openActionDialog(vm, "stop")}>강제 중지</button>
                      </span>
                      {vmJob && (
                        <small className={`customer-row-job ${vmJob.status.toLowerCase()}`}>
                          <span className={!terminalStatuses.has(vmJob.status) ? "progress-pulse" : "status-pip"} aria-hidden="true" />
                          {actionLabels[vmJob.action]} · {vmJob.status}
                        </small>
                      )}
                    </span>
                  </div>
                );
              })}
              {!loading && vms.length === 0 && <p className="empty-state customer-table-empty">조직에 할당된 VM이 없습니다.</p>}
              {vms.length > 0 && visibleVms.length === 0 && <p className="empty-state customer-table-empty">검색 조건에 맞는 VM이 없습니다.</p>}
            </div>
          </div>
        </section>

      </div>

      {pendingAction && pendingVm && (
        <div className="dialog-backdrop" role="presentation" onMouseDown={closeActionDialog}>
          <section className={`confirm-dialog${pendingAction === "stop" ? " forced-confirm-dialog" : ""}`} role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" onMouseDown={(event) => event.stopPropagation()}>
            <p className="eyebrow">Confirm operation</p>
            <h2 id="confirm-title">{pendingVm.name} {actionLabels[pendingAction]}</h2>
            {pendingAction === "stop" ? <><p>게스트 운영체제의 종료 절차를 건너뛰고 전원을 즉시 차단합니다. 저장되지 않은 데이터나 파일시스템이 손상될 수 있습니다.</p><label className="forced-confirm-check"><input type="checkbox" checked={forcedAcknowledged} onChange={(event) => setForcedAcknowledged(event.target.checked)} /><span>위험을 이해했으며 강제 중지를 요청합니다.</span></label></> : <p>이 전원 작업을 요청하시겠습니까? 진행 상태는 VM 목록의 전원 제어 영역에서 확인할 수 있습니다.</p>}
            <div><button className="secondary" onClick={closeActionDialog}>취소</button><button className={pendingAction === "stop" ? "danger-confirm" : ""} disabled={pendingAction === "stop" && !forcedAcknowledged} onClick={runAction}>작업 요청</button></div>
          </section>
        </div>
      )}
      {consoleVm && (
        <VmConsoleModal
          apiBaseUrl={apiBaseUrl}
          accessToken={session.accessToken}
          workloadId={consoleVm.id}
          workloadName={consoleVm.name}
          workloadKind="QEMU"
          consoleScope="customer"
          onClose={() => setConsoleVm(null)}
        />
      )}
      {passwordDialogOpen && (
        <PasswordChangeDialog
          apiBaseUrl={apiBaseUrl}
          accessToken={session.accessToken}
          onClose={() => setPasswordDialogOpen(false)}
          onChanged={endSession}
        />
      )}
    </main>
  );
}

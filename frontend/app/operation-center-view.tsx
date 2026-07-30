"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  AdminApiError,
  CurrentUser,
  OperationCenterAction,
  OperationCenterDetail,
  OperationCenterItem,
  getOperationCenterDetail,
  listOperationCenter,
  listUsers,
  runOperationCenterAction,
} from "@/lib/admin-api";

const activeStatuses = new Set(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]);
const statusOptions = [
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "TIMEOUT",
  "CANCELLED",
  "NEEDS_ATTENTION",
  "MANUAL_REVIEW",
];

function displayTime(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function actionLabel(action: OperationCenterAction) {
  return {
    CANCEL: "취소",
    RETRY: "안전 재시도",
    ACKNOWLEDGE: "확인",
    ASSIGN: "담당자 지정",
    RESOLVE_MANUALLY: "수동 해결",
  }[action];
}

function tone(status: string) {
  if (status === "SUCCEEDED") return "ok";
  if (activeStatuses.has(status)) return "neutral";
  return "failed";
}

export function OperationCenterView({
  apiBaseUrl,
  token,
}: {
  apiBaseUrl: string;
  token: string;
}) {
  const [items, setItems] = useState<OperationCenterItem[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<OperationCenterDetail | null>(null);
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [status, setStatus] = useState("");
  const [operationType, setOperationType] = useState("");
  const [errorCode, setErrorCode] = useState("");
  const [assigneeId, setAssigneeId] = useState("");
  const [resolutionNote, setResolutionNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const filters = useMemo(
    () => ({ status, operationType, errorCode }),
    [errorCode, operationType, status],
  );

  const refresh = useCallback(async () => {
    try {
      const result = await listOperationCenter(apiBaseUrl, token, filters);
      setItems(result.items);
      setTotal(result.total);
      setSelectedId((current) =>
        current && result.items.some((item) => item.id === current)
          ? current
          : result.items[0]?.id ?? null,
      );
      setError("");
    } catch (caught) {
      setError(
        caught instanceof AdminApiError
          ? `${caught.message} · ${caught.code}`
          : "작업 목록을 불러오지 못했습니다.",
      );
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl, filters, token]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void Promise.all([
        refresh(),
        listUsers(apiBaseUrl, token).then((result) =>
          setUsers(
            result.filter(
              (user) =>
                user.is_active && (user.role === "SUPER_ADMIN" || user.role === "OPERATOR"),
            ),
          ),
        ),
      ]).catch((caught) =>
        setError(
          caught instanceof AdminApiError
            ? `${caught.message} · ${caught.code}`
            : "담당자 목록을 불러오지 못했습니다.",
        ),
      );
    }, 0);
    return () => window.clearTimeout(timer);
  }, [apiBaseUrl, refresh, token]);

  useEffect(() => {
    if (!selectedId) return;
    void getOperationCenterDetail(apiBaseUrl, token, selectedId)
      .then(setDetail)
      .catch((caught) =>
        setError(
          caught instanceof AdminApiError
            ? `${caught.message} · ${caught.code}`
            : "작업 상세를 불러오지 못했습니다.",
        ),
      );
  }, [apiBaseUrl, selectedId, token, items]);

  useEffect(() => {
    if (!items.some((item) => activeStatuses.has(item.status))) return;
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(timer);
  }, [items, refresh]);

  async function execute(action: OperationCenterAction) {
    if (!detail) return;
    if (
      (action === "CANCEL" || action === "RETRY") &&
      !window.confirm(`${actionLabel(action)} 작업을 실행하시겠습니까?`)
    ) return;
    if (action === "ASSIGN" && !assigneeId) {
      setError("담당자를 선택해 주세요.");
      return;
    }
    if (action === "RESOLVE_MANUALLY" && resolutionNote.trim().length < 3) {
      setError("수동 해결 근거를 3자 이상 입력해 주세요.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const result = await runOperationCenterAction(
        apiBaseUrl,
        token,
        detail,
        action,
        {
          assignedToId: action === "ASSIGN" ? assigneeId : undefined,
          resolutionNote: action === "RESOLVE_MANUALLY" ? resolutionNote.trim() : undefined,
        },
      );
      const nextId = "created_operation_id" in result
        ? result.created_operation_id
        : result.id;
      setResolutionNote("");
      await refresh();
      setSelectedId(nextId);
    } catch (caught) {
      setError(
        caught instanceof AdminApiError
          ? `${caught.message} · ${caught.code}`
          : "복구 작업을 실행하지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="operation-center" aria-label="Operation 센터">
      <div className="operation-toolbar">
        <label>
          <span>상태</span>
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">전체 상태</option>
            {statusOptions.map((value) => <option key={value}>{value}</option>)}
          </select>
        </label>
        <label>
          <span>종류</span>
          <input
            value={operationType}
            onChange={(event) => setOperationType(event.target.value)}
            placeholder="POWER_START"
          />
        </label>
        <label>
          <span>오류 코드</span>
          <input
            value={errorCode}
            onChange={(event) => setErrorCode(event.target.value)}
            placeholder="PVE_TIMEOUT"
          />
        </label>
        <span className="operation-total">{total} operations</span>
      </div>

      {error && <div className="admin-message error" role="alert">{error}</div>}

      <div className="operation-layout">
        <div className="operation-queue" aria-label="작업 대기열">
          {items.map((item) => (
            <button
              type="button"
              key={item.id}
              className={item.id === selectedId ? "selected" : ""}
              onClick={() => setSelectedId(item.id)}
            >
              <span className={`admin-status ${tone(item.status)}`}><i />{item.status}</span>
              <strong>{item.workload_name ?? item.operation_type}</strong>
              <small>{item.operation_type} · {item.organization_name ?? "미할당"}</small>
              <time>{displayTime(item.requested_at)}</time>
              {item.is_stuck && <b>HEARTBEAT STALE</b>}
            </button>
          ))}
          {loading && items.length === 0 && <p className="empty-state">작업을 불러오는 중입니다…</p>}
          {!loading && items.length === 0 && <p className="empty-state">조건에 맞는 작업이 없습니다.</p>}
        </div>

        <article className="operation-detail" aria-live="polite">
          {(!detail || !selectedId) && <p className="empty-state">작업을 선택하면 복구 정보를 확인할 수 있습니다.</p>}
          {detail && selectedId && (
            <>
              <header>
                <div>
                  <p className="eyebrow">{detail.resource_type} / {detail.operation_type}</p>
                  <h2>{detail.workload_name ?? detail.operation_type}</h2>
                </div>
                <span className={`admin-status ${tone(detail.status)}`}><i />{detail.status}</span>
              </header>
              <div className="operation-facts">
                <dl><dt>영향</dt><dd>{detail.impact_summary}</dd></dl>
                <dl><dt>권장 조치</dt><dd>{detail.recommended_action}</dd></dl>
                <dl><dt>오류</dt><dd>{detail.error_code ?? "없음"}{detail.error_summary ? ` · ${detail.error_summary}` : ""}</dd></dl>
                <dl><dt>담당</dt><dd>{detail.assignment?.assigned_to_name ?? "미지정"}</dd></dl>
              </div>

              <div className="operation-actions">
                {detail.available_actions.includes("ASSIGN") && (
                  <label>
                    <span>담당자</span>
                    <select value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)}>
                      <option value="">선택</option>
                      {users.map((user) => <option key={user.id} value={user.id}>{user.display_name}</option>)}
                    </select>
                  </label>
                )}
                {detail.available_actions.includes("RESOLVE_MANUALLY") && (
                  <label className="resolution-field">
                    <span>수동 해결 근거</span>
                    <textarea
                      value={resolutionNote}
                      onChange={(event) => setResolutionNote(event.target.value)}
                      placeholder="확인한 대상 상태와 조치 결과를 기록하세요."
                    />
                  </label>
                )}
                <div>
                  {detail.available_actions.map((action) => (
                    <button
                      type="button"
                      key={action}
                      disabled={
                        saving ||
                        (action === "ASSIGN" && !assigneeId) ||
                        (action === "RESOLVE_MANUALLY" && resolutionNote.trim().length < 3)
                      }
                      className={action === "CANCEL" ? "danger-quiet" : ""}
                      onClick={() => void execute(action)}
                    >
                      {actionLabel(action)}
                    </button>
                  ))}
                </div>
              </div>

              <nav className="operation-related" aria-label="관련 자원">
                {detail.workload_id && <a href="?section=vms">Workload</a>}
                {detail.organization_id && <a href="?section=access">Organization</a>}
                <a href="?section=audit">Audit ({detail.related_audit_count})</a>
                {detail.related_backup_ids.length > 0 && <a href="?section=backups">Backup</a>}
              </nav>

              <section className="operation-timeline">
                <h3>Event timeline</h3>
                {[...detail.events].reverse().map((event) => (
                  <div key={event.id}>
                    <span aria-hidden="true" />
                    <p><strong>{event.event_type}</strong><small>{event.message}</small></p>
                    <time>{displayTime(event.occurred_at)}</time>
                  </div>
                ))}
                {detail.pve_tasks.map((task) => (
                  <div key={`${task.step_name}-${task.upid_reference}`}>
                    <span aria-hidden="true" />
                    <p><strong>{task.step_name}</strong><small>{task.upid_reference} · poll {task.poll_attempts}</small></p>
                    <time>{task.status}</time>
                  </div>
                ))}
                {detail.provisioning_steps.map((step) => (
                  <div key={step.order}>
                    <span aria-hidden="true" />
                    <p><strong>{step.order}. {step.name}</strong><small>attempt {step.attempt_count}</small></p>
                    <time>{step.status}</time>
                  </div>
                ))}
              </section>
            </>
          )}
        </article>
      </div>
    </section>
  );
}

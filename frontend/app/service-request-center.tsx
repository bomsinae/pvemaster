"use client";

import { useCallback, useEffect, useState } from "react";

import {
  AdminApiError,
  AdminServiceRequest,
  decideAdminServiceRequest,
  listAdminServiceRequests,
  updateAdminServiceRequestExecution,
} from "@/lib/admin-api";

function safeValue(value: unknown) {
  if (typeof value === "string" || typeof value === "number") return String(value);
  return "—";
}

export function ServiceRequestCenter({
  apiBaseUrl,
  token,
  canApprove,
}: {
  apiBaseUrl: string;
  token: string;
  canApprove: boolean;
}) {
  const [items, setItems] = useState<AdminServiceRequest[]>([]);
  const [message, setMessage] = useState("");
  const [savingId, setSavingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await listAdminServiceRequests(apiBaseUrl, token));
      setMessage("");
    } catch (error) {
      setMessage(error instanceof AdminApiError ? `${error.message} · ${error.code}` : "요청 목록을 불러오지 못했습니다.");
    }
  }, [apiBaseUrl, token]);

  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function decide(item: AdminServiceRequest, decision: "approve" | "reject") {
    const reason = window.prompt(decision === "approve" ? "승인 근거" : "거부 사유");
    if (!reason || reason.trim().length < 3) return;
    setSavingId(item.id);
    try {
      await decideAdminServiceRequest(apiBaseUrl, token, item, decision, reason.trim());
      await load();
    } catch (error) {
      setMessage(error instanceof AdminApiError ? `${error.message} · ${error.code}` : "결정을 저장하지 못했습니다.");
    } finally {
      setSavingId(null);
    }
  }

  async function execute(
    item: AdminServiceRequest,
    outcome: "START" | "SUCCEEDED" | "FAILED",
  ) {
    const summary = window.prompt(outcome === "START" ? "실행 계획 또는 변경 창" : "검증 결과 요약");
    if (!summary || summary.trim().length < 3) return;
    setSavingId(item.id);
    try {
      await updateAdminServiceRequestExecution(
        apiBaseUrl,
        token,
        item,
        outcome,
        summary.trim(),
      );
      await load();
    } catch (error) {
      setMessage(error instanceof AdminApiError ? `${error.message} · ${error.code}` : "실행 상태를 저장하지 못했습니다.");
    } finally {
      setSavingId(null);
    }
  }

  return (
    <div className="admin-content enter-admin">
      <section className="admin-section service-request-center">
        <div className="admin-section-title">
          <div><p className="eyebrow">Approval queue</p><h2>고객 변경 요청</h2><p>현재 소유권과 quota를 다시 확인한 뒤 승인하고, operation과 함께 실행 결과를 남깁니다.</p></div>
          <button type="button" onClick={load}>새로고침</button>
        </div>
        {message && <p role="alert">{message}</p>}
        <div className="service-request-list">
          {items.map((item) => (
            <article key={item.id}>
              <header><div><strong>{item.vm_name}</strong><small>{item.organization_name} · {item.request_type}</small></div><span>{item.status}</span></header>
              <ul>{item.impact.messages?.map((impact) => <li key={impact}>{impact}</li>)}</ul>
              <dl>{Object.entries(item.input).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{safeValue(value)}</dd></div>)}</dl>
              {item.result_summary && <p>{item.result_summary}</p>}
              <footer>
                {canApprove && item.status === "PENDING_APPROVAL" && <><button disabled={savingId === item.id} onClick={() => decide(item, "approve")}>승인</button><button className="danger" disabled={savingId === item.id} onClick={() => decide(item, "reject")}>거부</button></>}
                {canApprove && (item.status === "APPROVED" || item.status === "NEEDS_ATTENTION") && <button disabled={savingId === item.id} onClick={() => execute(item, "START")}>실행 시작</button>}
                {canApprove && item.status === "IN_PROGRESS" && <><button disabled={savingId === item.id} onClick={() => execute(item, "SUCCEEDED")}>성공 완료</button><button className="danger" disabled={savingId === item.id} onClick={() => execute(item, "FAILED")}>실패 · 관리자 처리</button></>}
                <time>{new Date(item.requested_at).toLocaleString("ko-KR")}</time>
              </footer>
            </article>
          ))}
          {!items.length && <p className="empty-state">접수된 고객 변경 요청이 없습니다.</p>}
        </div>
      </section>
    </div>
  );
}

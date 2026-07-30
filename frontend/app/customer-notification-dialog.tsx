"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  CustomerApiError,
  CustomerNotificationEvent,
  CustomerNotificationPreference,
  getCustomerNotificationPreferences,
  updateCustomerNotificationPreference,
} from "@/lib/customer-api";

import { useDialogFocus } from "./use-dialog-focus";

const labels: Record<CustomerNotificationEvent, { title: string; description: string }> = {
  VM_DOWN: {
    title: "VM 장기 비가용",
    description: "할당된 VM이 장시간 응답하지 않을 때 알려드립니다.",
  },
  OPERATION_COMPLETED: {
    title: "전원 작업 완료·실패",
    description: "직접 요청한 시작·종료·재부팅 결과를 알려드립니다.",
  },
  BACKUP_FAILED: {
    title: "백업 실패",
    description: "운영 백업에서 확인이 필요한 실패가 발생하면 알려드립니다.",
  },
  MAINTENANCE: {
    title: "예정된 유지보수",
    description: "서비스에 영향을 줄 수 있는 유지보수 일정을 알려드립니다.",
  },
};

export function CustomerNotificationDialog({
  apiBaseUrl,
  accessToken,
  onClose,
}: {
  apiBaseUrl: string;
  accessToken: string;
  onClose: () => void;
}) {
  const [items, setItems] = useState<CustomerNotificationPreference[]>([]);
  const [destination, setDestination] = useState("");
  const [busyKey, setBusyKey] = useState("");
  const [message, setMessage] = useState("");
  const dialogRef = useRef<HTMLElement>(null);
  useDialogFocus(true, dialogRef, onClose);

  const refresh = useCallback(async () => {
    const result = await getCustomerNotificationPreferences(apiBaseUrl, accessToken);
    setDestination(result.destination);
    setItems(result.items);
  }, [accessToken, apiBaseUrl]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refresh().catch((error) => {
        setMessage(error instanceof CustomerApiError ? error.message : "알림 설정을 불러오지 못했습니다.");
      });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  async function toggle(item: CustomerNotificationPreference) {
    const key = `${item.organization_id}:${item.event_type}`;
    setBusyKey(key);
    setMessage("");
    try {
      const updated = await updateCustomerNotificationPreference(
        apiBaseUrl,
        accessToken,
        {
          organization_id: item.organization_id,
          event_type: item.event_type,
          email_enabled: !item.email_enabled,
          version: item.version,
        },
      );
      setItems((current) => current.map((entry) =>
        entry.organization_id === updated.organization_id
        && entry.event_type === updated.event_type
          ? updated
          : entry
      ));
      setMessage("알림 설정을 저장했습니다.");
    } catch (error) {
      setMessage(error instanceof CustomerApiError ? error.message : "알림 설정을 저장하지 못했습니다.");
      if (error instanceof CustomerApiError && error.status === 409) await refresh();
    } finally {
      setBusyKey("");
    }
  }

  const organizations = [...new Set(items.map((item) => item.organization_name))];
  return <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
    <section ref={dialogRef} tabIndex={-1} className="customer-notification-dialog" role="dialog" aria-modal="true" aria-labelledby="customer-notification-title" onMouseDown={(event) => event.stopPropagation()}>
      <header><div><p className="eyebrow">Notification preferences</p><h2 id="customer-notification-title">이메일 알림 설정</h2><span>수신 주소 {destination || "확인 중"}</span></div><button type="button" onClick={onClose} aria-label="알림 설정 닫기">×</button></header>
      {message && <p className="security-message" role="status">{message}</p>}
      {organizations.map((organization) => <section key={organization}>
        <h3>{organization}</h3>
        {items.filter((item) => item.organization_name === organization).map((item) => {
          const key = `${item.organization_id}:${item.event_type}`;
          return <label key={key} className={item.required_by_organization ? "required" : ""}>
            <span><strong>{labels[item.event_type].title}</strong><small>{labels[item.event_type].description}</small>{item.required_by_organization && <em>조직 필수 알림</em>}</span>
            <input type="checkbox" checked={item.email_enabled} disabled={item.required_by_organization || busyKey === key} onChange={() => void toggle(item)} aria-label={`${organization} ${labels[item.event_type].title} 이메일 알림`} />
          </label>;
        })}
      </section>)}
      {!items.length && !message && <p className="empty-state">알림 설정을 불러오는 중입니다…</p>}
    </section>
  </div>;
}

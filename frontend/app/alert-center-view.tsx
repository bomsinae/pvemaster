"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import {
  AdminApiError,
  MaintenanceWindow,
  NotificationChannel,
  PersistentAlert,
  actOnAlert,
  createMaintenanceWindow,
  createNotificationChannel,
  listAlerts,
  listMaintenanceWindows,
  listNotificationChannels,
  testNotificationChannel,
} from "@/lib/admin-api";

function message(error: unknown) {
  return error instanceof AdminApiError ? error.message : "경보 센터 요청에 실패했습니다.";
}

function oneHourFromNow(): string {
  return new Date(new Date().getTime() + 60 * 60 * 1000).toISOString();
}

export function AlertCenterView({
  apiBaseUrl,
  token,
}: {
  apiBaseUrl: string;
  token: string;
}) {
  const [alerts, setAlerts] = useState<PersistentAlert[]>([]);
  const [windows, setWindows] = useState<MaintenanceWindow[]>([]);
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const [nextAlerts, nextWindows, nextChannels] = await Promise.all([
      listAlerts(apiBaseUrl, token),
      listMaintenanceWindows(apiBaseUrl, token),
      listNotificationChannels(apiBaseUrl, token),
    ]);
    setAlerts(nextAlerts); setWindows(nextWindows); setChannels(nextChannels);
  }, [apiBaseUrl, token]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refresh().catch((caught) => setError(message(caught)));
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  async function runAction(alert: PersistentAlert, action: "acknowledge" | "resolve" | "silence") {
    setBusy(true); setError("");
    try {
      await actOnAlert(apiBaseUrl, token, alert, action, action === "silence"
        ? { silenced_until: oneHourFromNow() }
        : {});
      await refresh();
    } catch (caught) { setError(message(caught)); } finally { setBusy(false); }
  }

  async function addWindow(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      await createMaintenanceWindow(apiBaseUrl, token, {
        name: String(data.get("name")),
        target_type: String(data.get("target_type")),
        target_id: String(data.get("target_id") || "") || null,
        starts_at: new Date(String(data.get("starts_at"))).toISOString(),
        ends_at: new Date(String(data.get("ends_at"))).toISOString(),
        suppress_notifications: true,
      });
      event.currentTarget.reset(); await refresh();
    } catch (caught) { setError(message(caught)); } finally { setBusy(false); }
  }

  async function addChannel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      await createNotificationChannel(apiBaseUrl, token, {
        name: String(data.get("name")),
        type: "WEBHOOK",
        webhook_url: String(data.get("url")),
        secret: String(data.get("secret") || "") || null,
      });
      event.currentTarget.reset(); await refresh();
    } catch (caught) { setError(message(caught)); } finally { setBusy(false); }
  }

  return (
    <div className="admin-content enter-admin alert-center">
      {error && <div className="admin-message error" role="alert">{error}</div>}
      {notice && <div className="admin-message notice" role="status">{notice}</div>}
      <section className="admin-section">
        <div className="admin-section-title"><div><p className="eyebrow">Incidents</p><h2>지속 경보</h2></div><span>{alerts.filter((item) => item.status !== "RESOLVED").length} open</span></div>
        <div className="persistent-alert-list">
          {alerts.map((alert) => <article key={alert.id}>
            <div><span className={`severity ${alert.severity.toLowerCase()}`}>{alert.severity}</span><strong>{alert.type}</strong><small>{alert.status} · {alert.occurrence_count}회</small></div>
            <p>{alert.message}</p>
            <div><button disabled={busy || alert.status === "ACKNOWLEDGED"} onClick={() => runAction(alert, "acknowledge")}>확인</button><button disabled={busy} onClick={() => runAction(alert, "silence")}>1시간 silence</button><button disabled={busy || alert.status === "RESOLVED"} onClick={() => runAction(alert, "resolve")}>해결</button></div>
          </article>)}
          {!alerts.length && <p className="empty-state">지속 중인 경보가 없습니다.</p>}
        </div>
      </section>
      <section className="admin-section alert-config-grid">
        <div><h2>Maintenance window</h2><form onSubmit={addWindow}><input name="name" placeholder="작업 이름" required /><select name="target_type"><option value="ALL">전체</option><option value="cluster">클러스터</option><option value="inventory">인벤토리</option></select><input name="target_id" placeholder="대상 ID (선택)" /><label>시작<input name="starts_at" type="datetime-local" required /></label><label>종료<input name="ends_at" type="datetime-local" required /></label><button disabled={busy}>등록</button></form>{windows.map((item) => <p key={item.id}><strong>{item.name}</strong><span>{new Date(item.starts_at).toLocaleString("ko-KR")} → {new Date(item.ends_at).toLocaleString("ko-KR")}</span></p>)}</div>
        <div><h2>Notification channel</h2><form onSubmit={addChannel}><input name="name" placeholder="채널 이름" required /><input name="url" type="url" placeholder="https://hooks.example.com/..." required /><input name="secret" type="password" autoComplete="new-password" placeholder="서명 secret (선택)" /><button disabled={busy}>Webhook 등록</button></form>{channels.map((item) => <p key={item.id}><strong>{item.name}</strong><span>{item.type} · secret 비공개</span><button disabled={busy} onClick={async () => { try { const result = await testNotificationChannel(apiBaseUrl, token, item.id); setNotice(result.status === "DELIVERED" ? "테스트 알림을 전달했습니다." : `전달 실패 · ${result.last_error_code}`); } catch (caught) { setError(message(caught)); } }}>테스트</button></p>)}</div>
      </section>
    </div>
  );
}

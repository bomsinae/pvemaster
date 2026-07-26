"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { CustomerApiError } from "@/lib/customer-api";
import {
  LoginEvent,
  MfaMethod,
  SecuritySession,
  disableMfaMethod,
  loadSecurityCenter,
  registerSecurityKey,
  regenerateRecoveryCodes,
  revokeOtherSessions,
  revokeSession,
  startTotp,
  verifyTotp,
} from "@/lib/security-api";

import { useDialogFocus } from "./use-dialog-focus";

export function SecurityCenterDialog({
  apiBaseUrl,
  accessToken,
  onClose,
  onCurrentSessionRevoked,
}: {
  apiBaseUrl: string;
  accessToken: string;
  onClose: () => void;
  onCurrentSessionRevoked: () => void;
}) {
  const [methods, setMethods] = useState<MfaMethod[]>([]);
  const [sessions, setSessions] = useState<SecuritySession[]>([]);
  const [events, setEvents] = useState<LoginEvent[]>([]);
  const [policyRequired, setPolicyRequired] = useState(false);
  const [remaining, setRemaining] = useState(0);
  const [enrollment, setEnrollment] = useState<{ methodId: string; secret: string; uri: string } | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(true);
  const dialogRef = useRef<HTMLElement>(null);

  useDialogFocus(true, dialogRef, onClose);

  const refresh = useCallback(async () => {
    const result = await loadSecurityCenter(apiBaseUrl, accessToken);
    setMethods(result.methods.items);
    setPolicyRequired(result.methods.policy_required);
    setRemaining(result.methods.recovery_codes_remaining);
    setSessions(result.sessions);
    setEvents(result.events);
  }, [apiBaseUrl, accessToken]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refresh()
        .catch((error) => setMessage(error instanceof CustomerApiError ? error.message : "보안 정보를 불러오지 못했습니다."))
        .finally(() => setBusy(false));
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  async function beginTotp() {
    setBusy(true); setMessage("");
    try {
      const result = await startTotp(apiBaseUrl, accessToken);
      setEnrollment({ methodId: result.method_id, secret: result.secret, uri: result.provisioning_uri });
    } catch (error) {
      setMessage(error instanceof CustomerApiError ? error.message : "MFA 등록을 시작하지 못했습니다.");
    } finally { setBusy(false); }
  }

  async function enrollSecurityKey() {
    setBusy(true); setMessage("");
    try {
      const result = await registerSecurityKey(apiBaseUrl, accessToken);
      setRecoveryCodes(result.recovery_codes);
      await refresh();
    } catch (error) {
      setMessage(error instanceof CustomerApiError ? error.message : "보안 키를 등록하지 못했습니다.");
    } finally { setBusy(false); }
  }

  async function completeTotp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!enrollment) return;
    const code = String(new FormData(event.currentTarget).get("code") ?? "");
    setBusy(true); setMessage("");
    try {
      const result = await verifyTotp(apiBaseUrl, accessToken, enrollment.methodId, code);
      setRecoveryCodes(result.recovery_codes);
      setEnrollment(null);
      await refresh();
    } catch (error) {
      setMessage(error instanceof CustomerApiError ? error.message : "인증 코드를 확인하지 못했습니다.");
    } finally { setBusy(false); }
  }

  async function endSession(session: SecuritySession) {
    setBusy(true); setMessage("");
    try {
      await revokeSession(apiBaseUrl, accessToken, session.id);
      if (session.current) onCurrentSessionRevoked();
      else await refresh();
    } catch (error) {
      setMessage(error instanceof CustomerApiError ? error.message : "세션을 종료하지 못했습니다.");
    } finally { setBusy(false); }
  }

  async function rotateRecoveryCodes() {
    const code = window.prompt("인증 앱 코드 또는 사용하지 않은 복구 코드를 입력하세요.");
    if (!code) return;
    setBusy(true); setMessage("");
    try {
      const result = await regenerateRecoveryCodes(apiBaseUrl, accessToken, code);
      setRecoveryCodes(result.codes);
      await refresh();
    } catch (error) {
      setMessage(error instanceof CustomerApiError ? error.message : "복구 코드를 재발급하지 못했습니다.");
    } finally { setBusy(false); }
  }

  async function disableMethod(method: MfaMethod) {
    const code = window.prompt("MFA 해제를 확인할 인증 앱 코드 또는 복구 코드를 입력하세요.");
    if (!code) return;
    setBusy(true); setMessage("");
    try {
      await disableMfaMethod(apiBaseUrl, accessToken, method.id, code);
      onCurrentSessionRevoked();
    } catch (error) {
      setMessage(error instanceof CustomerApiError ? error.message : "MFA를 해제하지 못했습니다.");
    } finally { setBusy(false); }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section ref={dialogRef} tabIndex={-1} className="security-center-dialog" role="dialog" aria-modal="true" aria-labelledby="security-center-title" onMouseDown={(event) => event.stopPropagation()}>
        <header><div><p className="eyebrow">Account security</p><h2 id="security-center-title">보안 설정</h2></div><button onClick={onClose} aria-label="보안 설정 닫기">×</button></header>
        {message && <p className="security-message" role="alert">{message}</p>}
        {policyRequired && methods.length === 0 && <p className="security-warning" role="alert">관리자 MFA 정책을 준수하려면 인증 앱을 등록해야 합니다.</p>}
        <div className="security-grid">
          <section>
            <div className="security-section-title"><h3>다중 인증</h3><span><button onClick={beginTotp} disabled={busy}>인증 앱 등록</button><button onClick={enrollSecurityKey} disabled={busy}>보안 키 등록</button></span></div>
            {methods.length ? methods.map((method) => <p key={method.id}><span><strong>{method.name}</strong><small>{method.type} · {method.last_used_at ? `최근 사용 ${new Date(method.last_used_at).toLocaleString("ko-KR")}` : "사용 전"}</small></span><button disabled={busy} onClick={() => disableMethod(method)}>해제</button></p>) : <p className="empty-state">등록된 MFA가 없습니다.</p>}
            <small>사용 가능한 복구 코드 {remaining}개</small>
            {methods.length > 0 && <button disabled={busy} onClick={rotateRecoveryCodes}>복구 코드 재발급</button>}
            {enrollment && <form onSubmit={completeTotp} className="security-enrollment"><p>인증 앱에 아래 키를 등록한 뒤 6자리 코드를 입력하세요.</p><code>{enrollment.secret}</code><input name="code" inputMode="numeric" autoComplete="one-time-code" aria-label="인증 앱 코드" required /><button disabled={busy}>등록 확인</button></form>}
            {recoveryCodes.length > 0 && <div className="recovery-codes" role="status"><strong>복구 코드는 지금 한 번만 표시됩니다.</strong><code>{recoveryCodes.join("\\n")}</code><button onClick={() => setRecoveryCodes([])}>저장 완료</button></div>}
          </section>
          <section>
            <div className="security-section-title"><h3>활성 세션</h3><button disabled={busy || sessions.length < 2} onClick={async () => { setBusy(true); try { await revokeOtherSessions(apiBaseUrl, accessToken); await refresh(); } finally { setBusy(false); } }}>다른 세션 모두 종료</button></div>
            {sessions.map((session) => <p key={session.id}><span><strong>{session.device_label || "이름 없는 기기"}{session.current ? " · 현재" : ""}</strong><small>{session.created_ip || "IP 미상"} · {session.assurance_level}</small></span><button disabled={busy} onClick={() => endSession(session)}>종료</button></p>)}
          </section>
          <section>
            <h3>최근 로그인</h3>
            {events.length ? events.slice(0, 8).map((event) => <p key={event.id}><strong>{event.outcome}</strong><span>{new Date(event.created_at).toLocaleString("ko-KR")} · {event.source_ip || "IP 미상"}</span></p>) : <p className="empty-state">로그인 이력이 없습니다.</p>}
          </section>
        </div>
      </section>
    </div>
  );
}

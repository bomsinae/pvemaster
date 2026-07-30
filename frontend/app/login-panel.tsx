"use client";

import { FormEvent, useState } from "react";

import {
  AuthSession,
  CustomerApiError,
  MfaLoginChallenge,
  login,
  verifyLoginMfa,
  verifyLoginWebAuthn,
} from "@/lib/customer-api";

type LoginPanelProps = {
  apiBaseUrl: string;
  onAuthenticated: (session: AuthSession) => void | Promise<void>;
};

export function LoginPanel({ apiBaseUrl, onAuthenticated }: LoginPanelProps) {
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [challenge, setChallenge] = useState<MfaLoginChallenge | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setMessage("");
    const form = new FormData(event.currentTarget);
    try {
      const session = challenge
        ? await verifyLoginMfa(
            apiBaseUrl,
            challenge.challengeId,
            String(form.get("method") ?? "TOTP"),
            String(form.get("code") ?? ""),
          )
        : await login(
            apiBaseUrl,
            String(form.get("email") ?? ""),
            String(form.get("password") ?? ""),
            undefined,
            true,
          );
      if ("mfaRequired" in session) {
        setChallenge(session);
        setMessage("등록된 인증 앱 또는 복구 코드로 확인해 주세요.");
        return;
      }
      await onAuthenticated(session);
    } catch (error) {
      setMessage(
        error instanceof CustomerApiError
          ? error.message
          : "로그인 서버에 연결하지 못했습니다.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSecurityKey() {
    if (!challenge) return;
    setSubmitting(true); setMessage("");
    try {
      await onAuthenticated(await verifyLoginWebAuthn(apiBaseUrl, challenge.challengeId));
    } catch (error) {
      setMessage(error instanceof CustomerApiError ? error.message : "보안 키 인증에 실패했습니다.");
    } finally { setSubmitting(false); }
  }

  return (
    <form className="login-form" onSubmit={handleSubmit} data-api-url={apiBaseUrl}>
      {!challenge ? (
        <>
          <div className="field-group">
            <label htmlFor="email">이메일</label>
            <input id="email" name="email" type="email" autoComplete="username" placeholder="operator@example.com" required />
          </div>
          <div className="field-group">
            <label htmlFor="password">비밀번호</label>
            <input id="password" name="password" type="password" autoComplete="current-password" placeholder="••••••••••••" required />
          </div>
        </>
      ) : (
        <>
          <div className="field-group">
            <label htmlFor="method">인증 방법</label>
            <select id="method" name="method" defaultValue={challenge.methods.includes("TOTP") ? "TOTP" : "RECOVERY"}>
              {challenge.methods.includes("TOTP") && <option value="TOTP">인증 앱</option>}
              <option value="RECOVERY">복구 코드</option>
            </select>
          </div>
          <div className="field-group">
            <label htmlFor="code">인증 코드</label>
            <input id="code" name="code" inputMode="numeric" autoComplete="one-time-code" required />
          </div>
        </>
      )}
      <button type="submit" disabled={submitting}>
        <span>{submitting ? "확인 중" : challenge ? "인증 확인" : "로그인"}</span>
        <span aria-hidden="true">↗</span>
      </button>
      <p className="form-note" aria-live="polite">
        {message || "세션 정보는 브라우저 저장소에 남기지 않습니다."}
      </p>
      {challenge && <button type="button" className="secondary" onClick={() => { setChallenge(null); setMessage(""); }}>다른 계정으로 로그인</button>}
      {challenge?.methods.includes("WEBAUTHN") && <button type="button" className="secondary" disabled={submitting} onClick={handleSecurityKey}>보안 키로 인증</button>}
    </form>
  );
}

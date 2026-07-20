"use client";

import { FormEvent, useState } from "react";

import { AuthSession, CustomerApiError, login } from "@/lib/customer-api";

type LoginPanelProps = {
  apiBaseUrl: string;
  onAuthenticated: (session: AuthSession) => void | Promise<void>;
};

export function LoginPanel({ apiBaseUrl, onAuthenticated }: LoginPanelProps) {
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setMessage("");
    const form = new FormData(event.currentTarget);
    try {
      const session = await login(
        apiBaseUrl,
        String(form.get("email") ?? ""),
        String(form.get("password") ?? ""),
      );
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

  return (
    <form className="login-form" onSubmit={handleSubmit} data-api-url={apiBaseUrl}>
      <div className="field-group">
        <label htmlFor="email">이메일</label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="username"
          placeholder="operator@example.com"
          required
        />
      </div>
      <div className="field-group">
        <label htmlFor="password">비밀번호</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          placeholder="••••••••••••"
          required
        />
      </div>
      <button type="submit" disabled={submitting}>
        <span>{submitting ? "확인 중" : "로그인"}</span>
        <span aria-hidden="true">↗</span>
      </button>
      <p className="form-note" aria-live="polite">
        {message || "세션 정보는 브라우저 저장소에 남기지 않습니다."}
      </p>
    </form>
  );
}

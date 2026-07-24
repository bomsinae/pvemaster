"use client";

import { FormEvent, useRef, useState } from "react";

import { changePassword, CustomerApiError } from "@/lib/customer-api";
import { useDialogFocus } from "./use-dialog-focus";

function passwordError(error: unknown): string {
  if (!(error instanceof CustomerApiError)) {
    return "비밀번호 변경 서버에 연결하지 못했습니다.";
  }
  if (error.code === "CURRENT_PASSWORD_INVALID") {
    return "현재 비밀번호가 올바르지 않습니다.";
  }
  if (error.code === "VALIDATION_ERROR") {
    return "새 비밀번호는 12자 이상이어야 하며 현재 비밀번호와 달라야 합니다.";
  }
  return error.message;
}

export function PasswordChangeDialog({
  apiBaseUrl,
  accessToken,
  onClose,
  onChanged,
}: {
  apiBaseUrl: string;
  accessToken: string;
  onClose: () => void;
  onChanged: () => void | Promise<void>;
}) {
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const dialogRef = useRef<HTMLElement>(null);

  useDialogFocus(true, dialogRef, saving ? undefined : onClose);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const currentPassword = String(form.get("current_password") ?? "");
    const newPassword = String(form.get("new_password") ?? "");
    const confirmation = String(form.get("password_confirmation") ?? "");

    if (newPassword !== confirmation) {
      setMessage("새 비밀번호 확인이 일치하지 않습니다.");
      return;
    }
    if (currentPassword === newPassword) {
      setMessage("새 비밀번호는 현재 비밀번호와 달라야 합니다.");
      return;
    }

    setSaving(true);
    setMessage("");
    try {
      await changePassword(
        apiBaseUrl,
        accessToken,
        currentPassword,
        newPassword,
      );
      await onChanged();
    } catch (error) {
      setMessage(passwordError(error));
      setSaving(false);
    }
  }

  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onMouseDown={() => { if (!saving) onClose(); }}
    >
      <section
        ref={dialogRef}
        tabIndex={-1}
        className="confirm-dialog password-change-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="password-change-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <p className="eyebrow">Account security</p>
        <h2 id="password-change-title">비밀번호 변경</h2>
        <p>변경이 완료되면 현재 기기를 포함한 모든 로그인 세션이 종료됩니다.</p>
        <form className="password-change-form" onSubmit={handleSubmit}>
          <label>
            <span>현재 비밀번호</span>
            <input
              name="current_password"
              type="password"
              autoComplete="current-password"
              required
              autoFocus
            />
          </label>
          <label>
            <span>새 비밀번호</span>
            <input
              name="new_password"
              type="password"
              autoComplete="new-password"
              minLength={12}
              required
            />
            <small>12자 이상</small>
          </label>
          <label>
            <span>새 비밀번호 확인</span>
            <input
              name="password_confirmation"
              type="password"
              autoComplete="new-password"
              minLength={12}
              required
            />
          </label>
          <p className="password-change-message" role="alert" aria-live="polite">{message}</p>
          <div className="password-change-actions">
            <button type="button" className="secondary" onClick={onClose} disabled={saving}>취소</button>
            <button type="submit" disabled={saving}>{saving ? "변경 중" : "변경 후 로그아웃"}</button>
          </div>
        </form>
      </section>
    </div>
  );
}

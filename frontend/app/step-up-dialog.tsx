"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { fetchWithAccessToken } from "@/lib/authenticated-fetch";
import { CustomerApiError } from "@/lib/customer-api";
import { registerStepUpHandler } from "@/lib/step-up";
import { verifyStepUpWithSecurityKey } from "@/lib/security-api";

import { useDialogFocus } from "./use-dialog-focus";

type PendingProof = {
  action: string;
  resolve: (token: string) => void;
  reject: (reason: Error) => void;
};

const actionLabels: Record<string, string> = {
  CLUSTER_CREDENTIAL_WRITE: "클러스터 자격증명 변경",
  USER_SECURITY_WRITE: "사용자 보안 변경",
  BACKUP_RESTORE: "백업 복원",
  FORCED_STOP: "강제 중지",
  MFA_POLICY_WRITE: "MFA 정책 변경",
};

export function StepUpDialog({
  apiBaseUrl,
  accessToken,
}: {
  apiBaseUrl: string;
  accessToken: string;
}) {
  const [pending, setPending] = useState<PendingProof | null>(null);
  const [challengeId, setChallengeId] = useState("");
  const [method, setMethod] = useState("TOTP");
  const [methods, setMethods] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLElement>(null);

  function close() {
    pending?.reject(new Error("Step-up authentication was cancelled."));
    setPending(null); setChallengeId(""); setMessage("");
  }
  useDialogFocus(Boolean(pending), dialogRef, close);

  useEffect(() => registerStepUpHandler((action) => new Promise<string>((resolve, reject) => {
    setPending({ action, resolve, reject });
    setBusy(true);
    void fetchWithAccessToken(`${apiBaseUrl}/api/v1/auth/step-up/start`, accessToken, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    }).then(async (response) => {
      const body = await response.json() as {
        challenge_id?: string;
        methods?: string[];
        error?: { message?: string };
      };
      if (!response.ok || !body.challenge_id) throw new CustomerApiError(body.error?.message ?? "추가 인증을 시작하지 못했습니다.", response.status, "STEP_UP_START_FAILED");
      setChallengeId(body.challenge_id);
      setMethods(body.methods ?? []);
      setMethod(body.methods?.includes("TOTP") ? "TOTP" : "RECOVERY");
    }).catch((error) => {
      reject(error instanceof Error ? error : new Error("Step-up failed."));
      setPending(null);
    }).finally(() => setBusy(false));
  })), [apiBaseUrl, accessToken]);

  async function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!pending || !challengeId) return;
    const code = String(new FormData(event.currentTarget).get("code") ?? "");
    setBusy(true); setMessage("");
    try {
      const response = await fetchWithAccessToken(`${apiBaseUrl}/api/v1/auth/step-up/verify`, accessToken, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ challenge_id: challengeId, action: pending.action, method_type: method, code }),
      });
      const body = await response.json() as { step_up_token?: string; error?: { message?: string } };
      if (!response.ok || !body.step_up_token) throw new CustomerApiError(body.error?.message ?? "추가 인증에 실패했습니다.", response.status, "STEP_UP_FAILED");
      pending.resolve(body.step_up_token);
      setPending(null); setChallengeId("");
    } catch (error) {
      setMessage(error instanceof CustomerApiError ? error.message : "추가 인증을 확인하지 못했습니다.");
    } finally { setBusy(false); }
  }

  async function verifySecurityKey() {
    if (!pending || !challengeId) return;
    setBusy(true); setMessage("");
    try {
      const token = await verifyStepUpWithSecurityKey(
        apiBaseUrl,
        accessToken,
        challengeId,
        pending.action,
      );
      pending.resolve(token);
      setPending(null); setChallengeId("");
    } catch (error) {
      setMessage(error instanceof CustomerApiError ? error.message : "보안 키 인증에 실패했습니다.");
    } finally { setBusy(false); }
  }

  if (!pending) return null;
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={close}>
      <section ref={dialogRef} tabIndex={-1} className="confirm-dialog step-up-dialog" role="dialog" aria-modal="true" aria-labelledby="step-up-title" onMouseDown={(event) => event.stopPropagation()}>
        <p className="eyebrow">Step-up authentication</p>
        <h2 id="step-up-title">추가 인증 필요</h2>
        <p>{actionLabels[pending.action] ?? pending.action} 작업을 계속하려면 MFA를 다시 확인하세요.</p>
        {message && <p role="alert">{message}</p>}
        <form onSubmit={verify}>
          <label>인증 방법<select value={method} onChange={(event) => setMethod(event.target.value)}><option value="TOTP">인증 앱</option><option value="RECOVERY">복구 코드</option></select></label>
          <label>인증 코드<input name="code" inputMode="numeric" autoComplete="one-time-code" required /></label>
          <div><button type="button" className="secondary" onClick={close}>취소</button><button disabled={busy || !challengeId}>{busy ? "확인 중" : "확인 후 계속"}</button></div>
        </form>
        {methods.includes("WEBAUTHN") && <button type="button" className="secondary" disabled={busy || !challengeId} onClick={verifySecurityKey}>보안 키로 확인</button>}
      </section>
    </div>
  );
}

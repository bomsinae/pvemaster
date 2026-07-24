"use client";

import { useState } from "react";

import { supportsAdminPowerAction } from "@/lib/admin-vm-state";
import { fetchWithAccessToken } from "@/lib/authenticated-fetch";

type PowerAction = "start" | "shutdown" | "stop" | "reboot" | "reset";

type JobSummary = {
  id: string;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "TIMEOUT";
  action_mode: "STANDARD" | "GRACEFUL" | "FORCED";
};

type VmPowerActionsProps = {
  apiBaseUrl: string;
  accessToken: string;
  vmId: string;
  workloadKind?: "QEMU" | "LXC";
  onAccepted?: (job: JobSummary) => void;
};

export function VmPowerActions({
  apiBaseUrl,
  accessToken,
  vmId,
  workloadKind = "QEMU",
  onAccepted,
}: VmPowerActionsProps) {
  const [pending, setPending] = useState<PowerAction | null>(null);
  const [message, setMessage] = useState("");

  async function submit(action: PowerAction) {
    if (
      action === "stop" &&
      !window.confirm("강제 중지는 SIGKILL에 해당하며 데이터가 손상될 수 있습니다. 계속할까요?")
    ) {
      return;
    }
    setPending(action);
    setMessage("");
    try {
      const response = await fetchWithAccessToken(
        `${apiBaseUrl}/api/v1/admin/workloads/${encodeURIComponent(vmId)}/actions/${action}`,
        accessToken,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": crypto.randomUUID(),
          },
          body: JSON.stringify({}),
        },
      );
      if (!response.ok) {
        setMessage("전원 작업을 접수하지 못했습니다.");
        return;
      }
      const job = (await response.json()) as JobSummary;
      setMessage(`작업 ${job.id}이(가) ${job.action_mode} 방식으로 접수되었습니다.`);
      onAccepted?.(job);
    } catch {
      setMessage("API에 연결할 수 없습니다. 잠시 후 다시 시도하세요.");
    } finally {
      setPending(null);
    }
  }

  return (
    <section className="power-actions" aria-label={`${workloadKind === "QEMU" ? "VM" : "CT"} 전원 작업`}>
      <div className="power-action-row">
        <button disabled={pending !== null} onClick={() => submit("start")} type="button">
          시작
        </button>
        <button disabled={pending !== null} onClick={() => submit("reboot")} type="button">
          재부팅
        </button>
        {supportsAdminPowerAction(workloadKind, "reset") && (
          <button disabled={pending !== null} onClick={() => submit("reset")} type="button">
            강제 재설정
          </button>
        )}
      </div>
      <div className="power-action-row power-action-boundary">
        <button disabled={pending !== null} onClick={() => submit("shutdown")} type="button">
          <strong>정상 종료</strong>
          <small>Guest OS에 종료 신호 · GRACEFUL</small>
        </button>
        <button
          className="danger-action"
          disabled={pending !== null}
          onClick={() => submit("stop")}
          type="button"
        >
          <strong>강제 중지</strong>
          <small>SIGKILL 상당 · 데이터 손상 위험 · FORCED</small>
        </button>
      </div>
      <p aria-live="polite" className="power-action-status">
        {message}
      </p>
    </section>
  );
}

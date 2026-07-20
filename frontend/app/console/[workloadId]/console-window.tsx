"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";

import { AdminApiError, getMe, getWorkload, type Workload } from "@/lib/admin-api";
import { restoreBrowserSession } from "@/lib/browser-session";
import { CustomerApiError, getCustomerVm } from "@/lib/customer-api";
import type { ConsoleAccessScope } from "@/lib/console-api";

import { VmConsoleModal } from "../../vm-console-modal";

type ConsoleTarget = Pick<Workload, "id" | "name" | "kind" | "vmid">;

type ConsoleBootstrap =
  | { state: "RESTORING" }
  | { state: "READY"; accessToken: string; workload: ConsoleTarget; scope: ConsoleAccessScope }
  | { state: "FAILED"; message: string };

function bootstrapError(error: unknown): string {
  if (error instanceof AdminApiError || error instanceof CustomerApiError) {
    return `${error.message} · ${error.code}`;
  }
  return "로그인 세션을 확인할 수 없습니다. 워크스페이스에서 다시 콘솔을 열어 주세요.";
}

export function ConsoleWindow({
  apiBaseUrl,
  workloadId,
}: {
  apiBaseUrl: string;
  workloadId: string;
}) {
  const started = useRef(false);
  const [bootstrap, setBootstrap] = useState<ConsoleBootstrap>({ state: "RESTORING" });

  useEffect(() => {
    document.body.classList.add("console-standalone-body");
    return () => document.body.classList.remove("console-standalone-body");
  }, []);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    let cancelled = false;

    void restoreBrowserSession()
      .then(async (session) => {
        const currentUser = await getMe(apiBaseUrl, session.accessToken);
        const scope: ConsoleAccessScope = currentUser.role === "CUSTOMER" ? "customer" : "admin";
        const workload = scope === "customer"
          ? {
              id: workloadId,
              name: (await getCustomerVm(apiBaseUrl, session.accessToken, workloadId)).name,
              kind: "QEMU" as const,
              vmid: 0,
            }
          : await getWorkload(apiBaseUrl, session.accessToken, workloadId);
        if (!cancelled) {
          setBootstrap({ state: "READY", accessToken: session.accessToken, workload, scope });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) setBootstrap({ state: "FAILED", message: bootstrapError(error) });
      });

    return () => {
      cancelled = true;
    };
  }, [apiBaseUrl, workloadId]);

  useEffect(() => {
    if (bootstrap.state !== "READY") return;
    const previousTitle = document.title;
    const consoleLabel = bootstrap.workload.kind === "LXC" ? "CT Terminal" : "PVE Console";
    document.title = `${bootstrap.workload.name ?? `VMID ${bootstrap.workload.vmid}`} · ${consoleLabel}`;
    return () => {
      document.title = previousTitle;
    };
  }, [bootstrap]);

  if (bootstrap.state === "RESTORING") {
    return <main className="console-bootstrap" aria-live="polite"><span /><strong>콘솔 세션 확인 중</strong><small>접근 권한과 가상서버 정보를 확인하고 있습니다.</small></main>;
  }

  if (bootstrap.state === "FAILED") {
    return (
      <main className="console-bootstrap console-bootstrap-failed" role="alert">
        <strong>콘솔을 시작하지 못했습니다.</strong>
        <small>{bootstrap.message}</small>
        <div><button type="button" onClick={() => window.close()}>창 닫기</button><Link href="/">워크스페이스로 이동</Link></div>
      </main>
    );
  }

  return (
    <VmConsoleModal
      apiBaseUrl={apiBaseUrl}
      accessToken={bootstrap.accessToken}
      workloadId={bootstrap.workload.id}
      workloadName={bootstrap.workload.name ?? `VMID ${bootstrap.workload.vmid}`}
      workloadKind={bootstrap.workload.kind}
      consoleScope={bootstrap.scope}
      onClose={() => window.close()}
      standalone
    />
  );
}

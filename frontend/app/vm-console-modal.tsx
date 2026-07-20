"use client";

import { useEffect, useRef, useState } from "react";
import type RFB from "@novnc/novnc";

import {
  ConsoleApiError,
  type ConsoleAccessScope,
  consoleWebSocketUrl,
  createConsoleSession,
} from "@/lib/console-api";

import { CtTerminalConsole } from "./ct-terminal-console";

type ConsoleState = "CONNECTING" | "CONNECTED" | "DISCONNECTED" | "FAILED";

const stateLabels: Record<ConsoleState, string> = {
  CONNECTING: "연결 중",
  CONNECTED: "연결됨",
  DISCONNECTED: "연결 종료",
  FAILED: "연결 실패",
};

export function VmConsoleModal({
  apiBaseUrl,
  accessToken,
  workloadId,
  workloadName,
  workloadKind = "QEMU",
  consoleScope = "admin",
  onClose,
  standalone = false,
}: {
  apiBaseUrl: string;
  accessToken: string;
  workloadId: string;
  workloadName: string;
  workloadKind?: "QEMU" | "LXC";
  consoleScope?: ConsoleAccessScope;
  onClose: () => void;
  standalone?: boolean;
}) {
  if (workloadKind === "LXC") {
    return (
      <CtTerminalConsole
        apiBaseUrl={apiBaseUrl}
        accessToken={accessToken}
        workloadId={workloadId}
        workloadName={workloadName}
        consoleScope={consoleScope}
        onClose={onClose}
        standalone={standalone}
      />
    );
  }
  return (
    <QemuConsoleModal
      apiBaseUrl={apiBaseUrl}
      accessToken={accessToken}
      workloadId={workloadId}
      workloadName={workloadName}
      consoleScope={consoleScope}
      onClose={onClose}
      standalone={standalone}
    />
  );
}

function QemuConsoleModal({
  apiBaseUrl,
  accessToken,
  workloadId,
  workloadName,
  consoleScope,
  onClose,
  standalone = false,
}: {
  apiBaseUrl: string;
  accessToken: string;
  workloadId: string;
  workloadName: string;
  consoleScope: ConsoleAccessScope;
  onClose: () => void;
  standalone?: boolean;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<RFB | null>(null);
  const [state, setState] = useState<ConsoleState>("CONNECTING");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let activeRfb: RFB | null = null;

    async function connect() {
      try {
        const session = await createConsoleSession(
          apiBaseUrl,
          accessToken,
          workloadId,
          { scope: consoleScope },
        );
        if (session.console_type !== "NOVNC" || !session.rfb_password) {
          throw new ConsoleApiError("QEMU 화면 콘솔 세션 형식이 아닙니다.", "CONSOLE_TYPE_MISMATCH");
        }
        const rfbPassword = session.rfb_password;
        const { default: RfbClient } = await import("@novnc/novnc");
        if (cancelled || !viewportRef.current) return;
        activeRfb = new RfbClient(
          viewportRef.current,
          consoleWebSocketUrl(apiBaseUrl, session.websocket_path),
          {
            shared: true,
            credentials: { password: rfbPassword },
            wsProtocols: ["binary", `pvemaster.console.${session.protocol_token}`],
          },
        );
        rfbRef.current = activeRfb;
        activeRfb.scaleViewport = true;
        activeRfb.resizeSession = false;
        activeRfb.background = "#080b09";
        activeRfb.addEventListener("connect", () => {
          if (!cancelled) {
            setState("CONNECTED");
            activeRfb?.focus();
          }
        });
        activeRfb.addEventListener("credentialsrequired", () => {
          activeRfb?.sendCredentials({ password: rfbPassword });
        });
        activeRfb.addEventListener("disconnect", (event) => {
          if (cancelled) return;
          const clean = (event as CustomEvent<{ clean: boolean }>).detail.clean;
          setState(clean ? "DISCONNECTED" : "FAILED");
          if (!clean) setError("콘솔 연결이 예기치 않게 종료됐습니다.");
        });
        activeRfb.addEventListener("securityfailure", () => {
          if (!cancelled) {
            setState("FAILED");
            setError("PVE 콘솔 보안 협상에 실패했습니다.");
          }
        });
      } catch (caught) {
        if (cancelled) return;
        setState("FAILED");
        setError(
          caught instanceof ConsoleApiError
            ? `${caught.message} · ${caught.code}`
            : caught instanceof Error
              ? `콘솔 서버에 연결하지 못했습니다. · ${caught.message}`
              : "콘솔 서버에 연결하지 못했습니다.",
        );
      }
    }

    void connect();
    return () => {
      cancelled = true;
      activeRfb?.disconnect();
      if (rfbRef.current === activeRfb) rfbRef.current = null;
    };
  }, [accessToken, apiBaseUrl, attempt, consoleScope, workloadId]);

  function reconnect() {
    setState("CONNECTING");
    setError("");
    setAttempt((value) => value + 1);
  }

  return (
    <div
      className={standalone ? "console-page" : "console-overlay"}
      role={standalone ? undefined : "dialog"}
      aria-modal={standalone ? undefined : "true"}
      aria-label={`${workloadName} 콘솔`}
    >
      <section className={standalone ? "console-window console-window-standalone" : "console-window"}>
        <header className="console-toolbar">
          <div className="console-identity">
            <span className="console-signal" data-state={state} aria-hidden="true" />
            <div><p>LIVE CONSOLE</p><strong>{workloadName}</strong></div>
          </div>
          <div className="console-toolbar-actions">
            <span>{stateLabels[state]}</span>
            <button
              type="button"
              disabled={state !== "CONNECTED"}
              onClick={() => rfbRef.current?.sendCtrlAltDel()}
            >Ctrl + Alt + Del</button>
            <button type="button" disabled={state === "CONNECTING"} onClick={reconnect}>재연결</button>
            <button type="button" className="console-close" onClick={onClose} aria-label={standalone ? "콘솔 창 닫기" : "콘솔 닫기"}>×</button>
          </div>
        </header>
        <div className="console-stage" onMouseDown={() => rfbRef.current?.focus()}>
          <div ref={viewportRef} className="console-viewport" />
          {state === "CONNECTING" && <div className="console-status-card"><span /><strong>PVE 화면을 불러오는 중</strong><small>암호화된 중계 채널을 준비하고 있습니다.</small></div>}
          {state === "FAILED" && <div className="console-status-card failed"><strong>콘솔을 열지 못했습니다.</strong><small>{error}</small><button type="button" onClick={reconnect}>새 세션으로 다시 연결</button></div>}
        </div>
        <footer className="console-footer"><span>입력은 현재 VM으로 직접 전달됩니다.</span><span>세션 최대 60분 · 일회용 연결 토큰</span></footer>
      </section>
    </div>
  );
}

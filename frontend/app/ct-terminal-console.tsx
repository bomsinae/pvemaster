"use client";

import { useEffect, useRef, useState } from "react";
import type { Terminal as XtermTerminal } from "@xterm/xterm";
import type { FitAddon as XtermFitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

import {
  ConsoleApiError,
  type ConsoleAccessScope,
  consoleWebSocketUrl,
  createConsoleSession,
} from "@/lib/console-api";
import {
  consumeTerminalHandshake,
  terminalInputFrame,
  terminalPingFrame,
  terminalResizeFrame,
} from "@/lib/terminal-protocol";

type TerminalState = "CONNECTING" | "CONNECTED" | "DISCONNECTED" | "FAILED";

const stateLabels: Record<TerminalState, string> = {
  CONNECTING: "연결 중",
  CONNECTED: "연결됨",
  DISCONNECTED: "연결 종료",
  FAILED: "연결 실패",
};

export function CtTerminalConsole({
  apiBaseUrl,
  accessToken,
  workloadId,
  workloadName,
  consoleScope = "admin",
  onClose,
  standalone = false,
}: {
  apiBaseUrl: string;
  accessToken: string;
  workloadId: string;
  workloadName: string;
  consoleScope?: ConsoleAccessScope;
  onClose: () => void;
  standalone?: boolean;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<XtermTerminal | null>(null);
  const fitRef = useRef<XtermFitAddon | null>(null);
  const [state, setState] = useState<TerminalState>("CONNECTING");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let socket: WebSocket | null = null;
    let terminal: XtermTerminal | null = null;
    let fitAddon: XtermFitAddon | null = null;
    let resizeObserver: ResizeObserver | null = null;
    let inputSubscription: { dispose(): void } | null = null;
    let pingTimer: number | null = null;
    let terminalReady = false;

    function sendResize() {
      if (socket?.readyState === WebSocket.OPEN && terminal && terminalReady) {
        socket.send(terminalResizeFrame(terminal.cols, terminal.rows));
      }
    }

    async function connect() {
      try {
        const [{ Terminal }, { FitAddon }, session] = await Promise.all([
          import("@xterm/xterm"),
          import("@xterm/addon-fit"),
          createConsoleSession(apiBaseUrl, accessToken, workloadId, { scope: consoleScope }),
        ]);
        if (session.console_type !== "TERMINAL") {
          throw new ConsoleApiError("CT 터미널 세션 형식이 아닙니다.", "CONSOLE_TYPE_MISMATCH");
        }
        if (cancelled || !viewportRef.current) return;

        terminal = new Terminal({
          allowTransparency: true,
          convertEol: true,
          cursorBlink: true,
          cursorStyle: "block",
          fontFamily: "'IBM Plex Mono', 'SFMono-Regular', Consolas, monospace",
          fontSize: 14,
          lineHeight: 1.18,
          scrollback: 5000,
          theme: {
            background: "#050706",
            foreground: "#dce8df",
            cursor: "#a8ff6a",
            cursorAccent: "#0a100c",
            selectionBackground: "#34512c",
            black: "#111713",
            red: "#ff7663",
            green: "#a8ff6a",
            yellow: "#e7c75c",
            blue: "#7fb7ff",
            magenta: "#c99cff",
            cyan: "#74d7cb",
            white: "#dce8df",
            brightBlack: "#69766e",
            brightRed: "#ff9b8d",
            brightGreen: "#c4ff9a",
            brightYellow: "#f4dc83",
            brightBlue: "#a3cbff",
            brightMagenta: "#dabaff",
            brightCyan: "#9ce6dd",
            brightWhite: "#f5faf6",
          },
        });
        fitAddon = new FitAddon();
        terminal.loadAddon(fitAddon);
        terminal.open(viewportRef.current);
        terminalRef.current = terminal;
        fitRef.current = fitAddon;
        requestAnimationFrame(() => fitAddon?.fit());

        resizeObserver = new ResizeObserver(() => {
          requestAnimationFrame(() => {
            fitAddon?.fit();
            sendResize();
          });
        });
        resizeObserver.observe(viewportRef.current);

        socket = new WebSocket(
          consoleWebSocketUrl(apiBaseUrl, session.websocket_path),
          ["binary", `pvemaster.console.${session.protocol_token}`],
        );
        socket.binaryType = "arraybuffer";
        inputSubscription = terminal.onData((data) => {
          if (socket?.readyState === WebSocket.OPEN && terminalReady) {
            socket.send(terminalInputFrame(data));
          }
        });
        socket.addEventListener("open", () => {
          if (cancelled) return;
          pingTimer = window.setInterval(() => {
            if (socket?.readyState === WebSocket.OPEN) socket.send(terminalPingFrame());
          }, 30_000);
        });
        socket.addEventListener("message", (event) => {
          if (cancelled || !terminal) return;
          const output = event.data instanceof ArrayBuffer
            ? new Uint8Array(event.data)
            : new TextEncoder().encode(String(event.data));
          if (!terminalReady) {
            const initialOutput = consumeTerminalHandshake(output);
            if (initialOutput === null) {
              socket?.close(1002, "Terminal handshake failed");
              return;
            }
            terminalReady = true;
            setState("CONNECTED");
            terminal.write(initialOutput);
            requestAnimationFrame(() => {
              fitAddon?.fit();
              sendResize();
              terminal?.focus();
            });
            return;
          }
          terminal.write(output);
        });
        socket.addEventListener("close", (event) => {
          if (cancelled) return;
          const clean = event.code === 1000;
          setState(clean ? "DISCONNECTED" : "FAILED");
          if (!clean) setError("CT 터미널 연결이 예기치 않게 종료됐습니다.");
        });
        socket.addEventListener("error", () => {
          if (!cancelled) {
            setState("FAILED");
            setError("PVE CT 터미널 채널을 열지 못했습니다.");
          }
        });
      } catch (caught) {
        if (cancelled) return;
        setState("FAILED");
        setError(
          caught instanceof ConsoleApiError
            ? `${caught.message} · ${caught.code}`
            : caught instanceof Error
              ? `터미널 서버에 연결하지 못했습니다. · ${caught.message}`
              : "터미널 서버에 연결하지 못했습니다.",
        );
      }
    }

    void connect();
    return () => {
      cancelled = true;
      inputSubscription?.dispose();
      resizeObserver?.disconnect();
      if (pingTimer !== null) window.clearInterval(pingTimer);
      socket?.close(1000, "Terminal closed");
      terminal?.dispose();
      if (terminalRef.current === terminal) terminalRef.current = null;
      if (fitRef.current === fitAddon) fitRef.current = null;
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
      aria-label={`${workloadName} CT 터미널`}
    >
      <section className={`${standalone ? "console-window console-window-standalone" : "console-window"} ct-terminal-window`}>
        <header className="console-toolbar">
          <div className="console-identity">
            <span className="console-signal" data-state={state} aria-hidden="true" />
            <div><p>LIVE CT TERMINAL</p><strong>{workloadName}</strong></div>
          </div>
          <div className="console-toolbar-actions">
            <span>{stateLabels[state]}</span>
            <button type="button" disabled={state !== "CONNECTED"} onClick={() => terminalRef.current?.clear()}>화면 지우기</button>
            <button type="button" disabled={state === "CONNECTING"} onClick={reconnect}>재연결</button>
            <button type="button" className="console-close" onClick={onClose} aria-label={standalone ? "터미널 창 닫기" : "터미널 닫기"}>×</button>
          </div>
        </header>
        <div className="console-stage ct-terminal-stage" onMouseDown={() => terminalRef.current?.focus()}>
          <div ref={viewportRef} className="ct-terminal-viewport" />
          {state === "CONNECTING" && <div className="console-status-card"><span /><strong>CT 터미널을 여는 중</strong><small>격리된 termproxy 중계 채널을 준비하고 있습니다.</small></div>}
          {state === "FAILED" && <div className="console-status-card failed"><strong>터미널을 열지 못했습니다.</strong><small>{error}</small><button type="button" onClick={reconnect}>새 세션으로 다시 연결</button></div>}
        </div>
        <footer className="console-footer"><span>키 입력은 현재 CT의 콘솔로 직접 전달됩니다.</span><span>UTF-8 · 세션 최대 60분 · 일회용 연결 토큰</span></footer>
      </section>
    </div>
  );
}

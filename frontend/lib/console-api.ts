import { fetchWithAccessToken } from "./authenticated-fetch.ts";

export type ConsoleSession = {
  session_id: string;
  websocket_path: string;
  protocol_token: string;
  console_type: "NOVNC" | "TERMINAL";
  rfb_password: string | null;
  expires_in: number;
};

export class ConsoleApiError extends Error {
  readonly code: string;

  constructor(message: string, code: string) {
    super(message);
    this.code = code;
  }
}

export type ConsoleAccessScope = "admin" | "customer";

export async function createConsoleSession(
  apiBaseUrl: string,
  accessToken: string,
  workloadId: string,
  optionsOrFetcher: typeof fetch | { scope?: ConsoleAccessScope; fetcher?: typeof fetch } = fetch,
): Promise<ConsoleSession> {
  const scope = typeof optionsOrFetcher === "function"
    ? "admin"
    : optionsOrFetcher.scope ?? "admin";
  const fetcher = typeof optionsOrFetcher === "function"
    ? optionsOrFetcher
    : optionsOrFetcher.fetcher ?? fetch;
  const resourcePath = scope === "customer"
    ? `/api/v1/customer/vms/${encodeURIComponent(workloadId)}/console-sessions`
    : `/api/v1/admin/workloads/${encodeURIComponent(workloadId)}/console-sessions`;
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}${resourcePath}`,
    accessToken,
    {
      method: "POST",
      cache: "no-store",
    },
    fetcher,
  );
  const body = (await response.json()) as ConsoleSession & {
    error?: { code?: string; message?: string };
  };
  if (!response.ok) {
    throw new ConsoleApiError(
      body.error?.message ?? "콘솔 세션을 만들지 못했습니다.",
      body.error?.code ?? "CONSOLE_SESSION_FAILED",
    );
  }
  return body;
}

export function consoleWebSocketUrl(apiBaseUrl: string, path: string): string {
  const base = new URL(apiBaseUrl || window.location.origin, window.location.origin);
  const endpoint = new URL(path, base);
  endpoint.protocol = endpoint.protocol === "https:" ? "wss:" : "ws:";
  return endpoint.toString();
}

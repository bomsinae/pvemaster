import type { AuthSession } from "./customer-api.ts";

type Fetcher = typeof fetch;

export class BrowserSessionError extends Error {
  readonly status: number;

  constructor(status: number) {
    super("Browser session request failed");
    this.status = status;
  }
}

async function requireSuccess(response: Response): Promise<Response> {
  if (!response.ok) throw new BrowserSessionError(response.status);
  return response;
}

export async function persistBrowserSession(
  refreshToken: string,
  fetcher: Fetcher = fetch,
): Promise<void> {
  await requireSuccess(await fetcher("/api/auth/session", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  }));
}

export async function restoreBrowserSession(fetcher: Fetcher = fetch): Promise<AuthSession> {
  const response = await requireSuccess(await fetcher("/api/auth/session", {
    method: "PUT",
    credentials: "same-origin",
  }));
  const body = (await response.json()) as { access_token?: unknown };
  if (typeof body.access_token !== "string" || !body.access_token) {
    throw new Error("Browser session response was invalid");
  }
  return { accessToken: body.access_token, refreshToken: "" };
}

export async function endBrowserSession(fetcher: Fetcher = fetch): Promise<void> {
  await requireSuccess(await fetcher("/api/auth/session", {
    method: "DELETE",
    credentials: "same-origin",
  }));
}

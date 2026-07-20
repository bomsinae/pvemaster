import type { AuthSession } from "./customer-api";

type Fetcher = typeof fetch;

async function requireSuccess(response: Response): Promise<Response> {
  if (!response.ok) throw new Error("Browser session request failed");
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

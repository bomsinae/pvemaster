import { BrowserSessionError, restoreBrowserSession } from "./browser-session.ts";

type Fetcher = typeof fetch;

const tokenReplacements = new Map<string, string>();
let refreshInFlight: Promise<string> | null = null;
let tokenStateGeneration = 0;

function currentToken(accessToken: string): string {
  let current = accessToken;
  const visited = new Set<string>();
  while (tokenReplacements.has(current) && !visited.has(current)) {
    visited.add(current);
    current = tokenReplacements.get(current) ?? current;
  }
  for (const token of visited) tokenReplacements.set(token, current);
  return current;
}

function withBearer(init: RequestInit, accessToken: string): RequestInit {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${accessToken}`);
  return { ...init, headers };
}

async function isExpiredAccessToken(response: Response): Promise<boolean> {
  if (response.status !== 401) return false;
  try {
    const body = (await response.clone().json()) as { error?: { code?: unknown } };
    return body.error?.code === "INVALID_ACCESS_TOKEN";
  } catch {
    return false;
  }
}

function notifySessionExpired(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event("pvemaster:session-expired"));
  }
}

async function refreshAccessToken(fetcher: Fetcher): Promise<string> {
  if (!refreshInFlight) {
    refreshInFlight = restoreBrowserSession(fetcher)
      .then((session) => session.accessToken)
      .catch((error: unknown) => {
        if (error instanceof BrowserSessionError && error.status === 401) {
          notifySessionExpired();
        }
        throw error;
      });
  }
  const pendingRefresh = refreshInFlight;
  try {
    return await pendingRefresh;
  } finally {
    if (refreshInFlight === pendingRefresh) refreshInFlight = null;
  }
}

export async function fetchWithAccessToken(
  input: RequestInfo | URL,
  accessToken: string,
  init: RequestInit = {},
  fetcher: Fetcher = fetch,
): Promise<Response> {
  const requestGeneration = tokenStateGeneration;
  const attemptedToken = currentToken(accessToken);
  const response = await fetcher(input, withBearer(init, attemptedToken));
  if (!(await isExpiredAccessToken(response))) return response;

  try {
    const refreshedToken = await refreshAccessToken(fetcher);
    if (requestGeneration !== tokenStateGeneration) return response;
    tokenReplacements.set(attemptedToken, refreshedToken);
    return await fetcher(input, withBearer(init, refreshedToken));
  } catch {
    return response;
  }
}

export function resetAccessTokenState(): void {
  tokenStateGeneration += 1;
  tokenReplacements.clear();
  refreshInFlight = null;
}

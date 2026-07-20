import { NextRequest, NextResponse } from "next/server";

const cookieName = "pvemaster_refresh";
const requestTimeoutMs = 10_000;

function backendUrl(path: string): string {
  const base = process.env.BACKEND_INTERNAL_URL ?? process.env.NEXT_PUBLIC_API_URL;
  if (!base) throw new Error("Backend URL is not configured");
  return `${base.replace(/\/$/, "")}${path}`;
}

function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  const protocol = request.headers.get("x-forwarded-proto") ?? request.nextUrl.protocol.slice(0, -1);
  return origin !== null && host !== null && origin === `${protocol}://${host}`;
}

function cookieOptions() {
  return {
    httpOnly: true,
    secure: process.env.SESSION_COOKIE_SECURE === "true",
    sameSite: "strict" as const,
    path: "/api/auth/session",
    maxAge: 30 * 24 * 60 * 60,
  };
}

function forbidden(): NextResponse {
  return NextResponse.json({ error: { code: "ORIGIN_FORBIDDEN" } }, { status: 403 });
}

function clearCookie(response: NextResponse): NextResponse {
  response.cookies.set(cookieName, "", { ...cookieOptions(), maxAge: 0 });
  return response;
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!sameOrigin(request)) return forbidden();
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: { code: "INVALID_REQUEST" } }, { status: 400 });
  }
  const refreshToken = (body as { refresh_token?: unknown }).refresh_token;
  if (typeof refreshToken !== "string" || !refreshToken || refreshToken.length > 4096) {
    return NextResponse.json({ error: { code: "INVALID_REQUEST" } }, { status: 400 });
  }
  const response = new NextResponse(null, { status: 204 });
  response.cookies.set(cookieName, refreshToken, cookieOptions());
  return response;
}

export async function PUT(request: NextRequest): Promise<NextResponse> {
  if (!sameOrigin(request)) return forbidden();
  const refreshToken = request.cookies.get(cookieName)?.value;
  if (!refreshToken) {
    return NextResponse.json({ error: { code: "SESSION_NOT_FOUND" } }, { status: 401 });
  }
  try {
    const upstream = await fetch(backendUrl("/api/v1/auth/refresh"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
      signal: AbortSignal.timeout(requestTimeoutMs),
    });
    const body = (await upstream.json()) as {
      access_token?: unknown;
      refresh_token?: unknown;
    };
    if (
      !upstream.ok
      || typeof body.access_token !== "string"
      || typeof body.refresh_token !== "string"
    ) {
      return clearCookie(
        NextResponse.json({ error: { code: "SESSION_EXPIRED" } }, { status: 401 }),
      );
    }
    const response = NextResponse.json(
      { access_token: body.access_token },
      { headers: { "Cache-Control": "no-store" } },
    );
    response.cookies.set(cookieName, body.refresh_token, cookieOptions());
    return response;
  } catch {
    return NextResponse.json({ error: { code: "SESSION_SERVICE_UNAVAILABLE" } }, { status: 503 });
  }
}

export async function DELETE(request: NextRequest): Promise<NextResponse> {
  if (!sameOrigin(request)) return forbidden();
  const refreshToken = request.cookies.get(cookieName)?.value;
  if (refreshToken) {
    try {
      await fetch(backendUrl("/api/v1/auth/logout"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
        cache: "no-store",
        signal: AbortSignal.timeout(requestTimeoutMs),
      });
    } catch {
      // The local cookie is still removed; the server-side token expires independently.
    }
  }
  return clearCookie(new NextResponse(null, { status: 204 }));
}

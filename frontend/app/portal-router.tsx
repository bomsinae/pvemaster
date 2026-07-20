"use client";

import { useEffect, useRef, useState } from "react";

import { getMe, CurrentUser } from "@/lib/admin-api";
import { AuthSession } from "@/lib/customer-api";
import { persistBrowserSession, restoreBrowserSession } from "@/lib/browser-session";

import { AdminDashboard } from "./admin-dashboard";
import { CustomerPortal } from "./customer-portal";
import { LoginPanel } from "./login-panel";

export function PortalRouter({ apiBaseUrl }: { apiBaseUrl: string }) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [restoring, setRestoring] = useState(true);
  const restoreStarted = useRef(false);

  useEffect(() => {
    if (restoreStarted.current) return;
    restoreStarted.current = true;
    void restoreBrowserSession()
      .then(async (restored) => {
        const current = await getMe(apiBaseUrl, restored.accessToken);
        setSession(restored);
        setUser(current);
      })
      .catch(() => undefined)
      .finally(() => setRestoring(false));
  }, [apiBaseUrl]);

  async function routeSession(nextSession: AuthSession) {
    const current = await getMe(apiBaseUrl, nextSession.accessToken);
    await persistBrowserSession(nextSession.refreshToken);
    setSession({ accessToken: nextSession.accessToken, refreshToken: "" });
    setUser(current);
  }

  function clearSession() {
    setSession(null);
    setUser(null);
  }

  if (restoring) {
    return <main className="session-restore" aria-live="polite">세션을 확인하는 중입니다…</main>;
  }

  if (session && user && user.role !== "CUSTOMER") {
    return (
      <AdminDashboard
        apiBaseUrl={apiBaseUrl}
        session={session}
        user={user}
        onSessionEnded={clearSession}
      />
    );
  }

  if (session && user?.role === "CUSTOMER") {
    return (
      <CustomerPortal
        apiBaseUrl={apiBaseUrl}
        initialSession={session}
        userEmail={user.email}
        onSessionEnded={clearSession}
      />
    );
  }

  return (
    <main className="login-shell admin-login">
      <div className="ambient-grid" aria-hidden="true" />
      <header className="brand-bar">
        <div className="brand"><span className="brand-mark">PM</span><span>PVE Master</span></div>
        <div className="system-state"><span className="state-dot" /> 서비스 로그인</div>
      </header>
      <section className="login-stage" aria-labelledby="login-title">
        <div className="context-copy">
          <p className="eyebrow">VM Workspace</p>
          <h1 id="login-title">내 가상 머신을 관리하세요.</h1>
          <p className="supporting-copy">
            할당된 가상 머신의 상태를 확인하고 필요한 작업을 실행할 수 있습니다.
          </p>
        </div>
        <LoginPanel apiBaseUrl={apiBaseUrl} onAuthenticated={routeSession} />
      </section>
      <footer className="login-footer"><span>Role-aware access</span><span className="footer-rule" /><span>API v1</span></footer>
    </main>
  );
}

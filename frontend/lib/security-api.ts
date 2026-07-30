import { fetchWithAccessToken } from "./authenticated-fetch.ts";
import { CustomerApiError } from "./customer-api.ts";

export type MfaMethod = {
  id: string;
  type: string;
  name: string;
  enrolled_at: string;
  last_used_at: string | null;
};

export type SecuritySummary = {
  items: MfaMethod[];
  recovery_codes_remaining: number;
  policy_required: boolean;
};

export type SecuritySession = {
  id: string;
  device_label: string | null;
  created_ip: string | null;
  user_agent: string | null;
  created_at: string;
  last_seen_at: string | null;
  expires_at: string;
  assurance_level: string;
  current: boolean;
};

export type LoginEvent = {
  id: string;
  created_at: string;
  outcome: string;
  source_ip: string | null;
  user_agent: string | null;
  error_code: string | null;
};

type Fetcher = typeof fetch;

async function parse<T>(response: Response): Promise<T> {
  const body = (await response.json().catch(() => ({}))) as T & {
    error?: { code?: string; message?: string };
  };
  if (!response.ok) {
    throw new CustomerApiError(
      body.error?.message ?? "보안 설정 요청을 처리하지 못했습니다.",
      response.status,
      body.error?.code ?? "SECURITY_REQUEST_FAILED",
    );
  }
  return body;
}

async function authorized<T>(
  apiBaseUrl: string,
  accessToken: string,
  path: string,
  init: RequestInit = {},
  fetcher: Fetcher = fetch,
): Promise<T> {
  return parse<T>(
    await fetchWithAccessToken(`${apiBaseUrl}${path}`, accessToken, init, fetcher),
  );
}

export async function loadSecurityCenter(
  apiBaseUrl: string,
  accessToken: string,
  fetcher: Fetcher = fetch,
): Promise<{ methods: SecuritySummary; sessions: SecuritySession[]; events: LoginEvent[] }> {
  const [methods, sessions, events] = await Promise.all([
    authorized<SecuritySummary>(apiBaseUrl, accessToken, "/api/v1/auth/mfa/methods", {}, fetcher),
    authorized<{ items: SecuritySession[] }>(apiBaseUrl, accessToken, "/api/v1/auth/sessions", {}, fetcher),
    authorized<{ items: LoginEvent[] }>(apiBaseUrl, accessToken, "/api/v1/auth/login-events", {}, fetcher),
  ]);
  return { methods, sessions: sessions.items, events: events.items };
}

export function startTotp(apiBaseUrl: string, accessToken: string, fetcher: Fetcher = fetch) {
  return authorized<{ method_id: string; secret: string; provisioning_uri: string }>(
    apiBaseUrl,
    accessToken,
    "/api/v1/auth/mfa/totp/start",
    { method: "POST" },
    fetcher,
  );
}

export function verifyTotp(
  apiBaseUrl: string,
  accessToken: string,
  methodId: string,
  code: string,
  fetcher: Fetcher = fetch,
) {
  return authorized<{ recovery_codes: string[] }>(
    apiBaseUrl,
    accessToken,
    "/api/v1/auth/mfa/totp/verify",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ method_id: methodId, code }),
    },
    fetcher,
  );
}

function decodeBase64Url(value: string): ArrayBuffer {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const bytes = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
  return bytes.buffer;
}

function encodeBase64Url(value: ArrayBuffer): string {
  const bytes = new Uint8Array(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function registerSecurityKey(
  apiBaseUrl: string,
  accessToken: string,
  name = "Security key",
  fetcher: Fetcher = fetch,
): Promise<{ recovery_codes: string[] }> {
  if (!window.PublicKeyCredential) {
    throw new CustomerApiError("이 브라우저는 보안 키를 지원하지 않습니다.", 400, "WEBAUTHN_UNSUPPORTED");
  }
  const started = await authorized<{
    challenge_id: string;
    options: PublicKeyCredentialCreationOptionsJSON;
  }>(
    apiBaseUrl,
    accessToken,
    "/api/v1/auth/mfa/webauthn/start",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    },
    fetcher,
  );
  const options = started.options;
  const credential = await navigator.credentials.create({
    publicKey: {
      ...options,
      challenge: decodeBase64Url(options.challenge),
      user: { ...options.user, id: decodeBase64Url(options.user.id) },
      excludeCredentials: options.excludeCredentials?.map((item) => ({
        ...item,
        type: "public-key" as const,
        id: decodeBase64Url(item.id),
        transports: item.transports as AuthenticatorTransport[] | undefined,
      })),
    } as unknown as PublicKeyCredentialCreationOptions,
  }) as PublicKeyCredential | null;
  if (!credential) {
    throw new CustomerApiError("보안 키 등록이 취소되었습니다.", 400, "WEBAUTHN_CANCELLED");
  }
  const response = credential.response as AuthenticatorAttestationResponse;
  return authorized<{ recovery_codes: string[] }>(
    apiBaseUrl,
    accessToken,
    "/api/v1/auth/mfa/webauthn/finish",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        challenge_id: started.challenge_id,
        credential: {
          id: credential.id,
          rawId: encodeBase64Url(credential.rawId),
          type: credential.type,
          response: {
            attestationObject: encodeBase64Url(response.attestationObject),
            clientDataJSON: encodeBase64Url(response.clientDataJSON),
            transports: response.getTransports?.() ?? [],
          },
          clientExtensionResults: credential.getClientExtensionResults(),
        },
      }),
    },
    fetcher,
  );
}

export async function verifyStepUpWithSecurityKey(
  apiBaseUrl: string,
  accessToken: string,
  challengeId: string,
  action: string,
  fetcher: Fetcher = fetch,
): Promise<string> {
  const options = await authorized<PublicKeyCredentialRequestOptionsJSON>(
    apiBaseUrl,
    accessToken,
    `/api/v1/auth/step-up/${encodeURIComponent(challengeId)}/webauthn-options`,
    {},
    fetcher,
  );
  const credential = await navigator.credentials.get({
    publicKey: {
      ...options,
      challenge: decodeBase64Url(options.challenge),
      allowCredentials: options.allowCredentials?.map((item) => ({
        id: decodeBase64Url(item.id),
        type: "public-key" as const,
        transports: item.transports as AuthenticatorTransport[] | undefined,
      })),
    } as unknown as PublicKeyCredentialRequestOptions,
  }) as PublicKeyCredential | null;
  if (!credential) {
    throw new CustomerApiError("보안 키 인증이 취소되었습니다.", 400, "WEBAUTHN_CANCELLED");
  }
  const assertion = credential.response as AuthenticatorAssertionResponse;
  const result = await authorized<{ step_up_token: string }>(
    apiBaseUrl,
    accessToken,
    "/api/v1/auth/step-up/verify",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        challenge_id: challengeId,
        action,
        method_type: "WEBAUTHN",
        credential: {
          id: credential.id,
          rawId: encodeBase64Url(credential.rawId),
          type: credential.type,
          response: {
            authenticatorData: encodeBase64Url(assertion.authenticatorData),
            clientDataJSON: encodeBase64Url(assertion.clientDataJSON),
            signature: encodeBase64Url(assertion.signature),
            userHandle: assertion.userHandle ? encodeBase64Url(assertion.userHandle) : null,
          },
          clientExtensionResults: credential.getClientExtensionResults(),
        },
      }),
    },
    fetcher,
  );
  return result.step_up_token;
}

export async function revokeSession(
  apiBaseUrl: string,
  accessToken: string,
  sessionId: string,
  fetcher: Fetcher = fetch,
): Promise<void> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/auth/sessions/${encodeURIComponent(sessionId)}`,
    accessToken,
    { method: "DELETE" },
    fetcher,
  );
  if (!response.ok) await parse(response);
}

export function regenerateRecoveryCodes(
  apiBaseUrl: string,
  accessToken: string,
  code: string,
  fetcher: Fetcher = fetch,
) {
  return authorized<{ codes: string[] }>(
    apiBaseUrl,
    accessToken,
    "/api/v1/auth/mfa/recovery-codes",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    },
    fetcher,
  );
}

export async function disableMfaMethod(
  apiBaseUrl: string,
  accessToken: string,
  methodId: string,
  code: string,
  fetcher: Fetcher = fetch,
): Promise<void> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/auth/mfa/methods/${encodeURIComponent(methodId)}/disable`,
    accessToken,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    },
    fetcher,
  );
  if (!response.ok) await parse(response);
}

export async function revokeOtherSessions(
  apiBaseUrl: string,
  accessToken: string,
  fetcher: Fetcher = fetch,
): Promise<void> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/auth/sessions/others`,
    accessToken,
    { method: "DELETE" },
    fetcher,
  );
  if (!response.ok) await parse(response);
}

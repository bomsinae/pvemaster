import { fetchWithAccessToken } from "./authenticated-fetch.ts";

export type AuthSession = {
  accessToken: string;
  refreshToken: string;
};

export type MfaLoginChallenge = {
  mfaRequired: true;
  challengeId: string;
  methods: string[];
};

export type LoginResult = AuthSession | MfaLoginChallenge;

export type CustomerPowerAction = "start" | "shutdown" | "stop" | "reboot";

export type CustomerVm = {
  id: string;
  name: string;
  organization_name: string;
  power_state: string;
  cpu_cores: number | null;
  memory_bytes: number | null;
  disk_bytes: number | null;
  uptime_seconds?: number | null;
  assigned_ip_addresses: string[];
  observed_at: string;
  is_stale?: boolean;
  stale_reason?: string | null;
};

export type CustomerJob = {
  id: string;
  job_id: string;
  vm_id: string;
  action: CustomerPowerAction;
  action_mode: "STANDARD" | "GRACEFUL" | "FORCED";
  status:
    | "QUEUED"
    | "RUNNING"
    | "CANCEL_REQUESTED"
    | "SUCCEEDED"
    | "FAILED"
    | "TIMEOUT"
    | "CANCELLED"
    | "NEEDS_ATTENTION";
  result: Record<string, unknown>;
  error_code: string | null;
  error_summary: string | null;
  retryable: boolean | null;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type CustomerVmDetail = CustomerVm & {
  recent_jobs: CustomerJob[];
  recent_state_changes: Array<{
    id: number;
    change_type: string;
    summary: string;
    observed_at: string;
  }>;
  recent_backup: {
    status: string;
    completed_at: string | null;
    scheduled_for: string | null;
  } | null;
  upcoming_maintenance: Array<{
    id: string;
    name: string;
    starts_at: string;
    ends_at: string;
  }>;
};

export type CustomerMetricRange = "day" | "month" | "year";

export type CustomerMetricPoint = {
  time: string;
  sample_count: number;
  cpu_avg: number | null;
  cpu_max: number | null;
  memory_used_avg: number | null;
  memory_used_max: number | null;
  disk_read_avg: number | null;
  disk_read_max: number | null;
  disk_write_avg: number | null;
  disk_write_max: number | null;
  network_receive_avg: number | null;
  network_receive_max: number | null;
  network_transmit_avg: number | null;
  network_transmit_max: number | null;
};

export type CustomerMetricSeries = {
  vm_id: string;
  range: CustomerMetricRange;
  resolution_seconds: number;
  assignment_started_at: string;
  observed_at: string;
  partial: boolean;
  items: CustomerMetricPoint[];
};

export type CustomerNotificationEvent =
  | "VM_DOWN"
  | "OPERATION_COMPLETED"
  | "BACKUP_FAILED"
  | "MAINTENANCE";

export type CustomerNotificationPreference = {
  organization_id: string;
  organization_name: string;
  event_type: CustomerNotificationEvent;
  email_enabled: boolean;
  required_by_organization: boolean;
  version: number;
};

export type CustomerNotificationPreferences = {
  channel: "EMAIL";
  destination: string;
  items: CustomerNotificationPreference[];
};

export type CustomerAlert = {
  id: string;
  type: string;
  severity: string;
  status: string;
  message: string;
  workload_id: string | null;
  last_seen_at: string;
};

export type CustomerServiceRequestType =
  | "SSH_KEY_ADD"
  | "SSH_KEY_REPLACE"
  | "SSH_KEY_DELETE"
  | "METADATA_CHANGE"
  | "RDNS_CHANGE"
  | "SECURITY_GROUP_APPLY"
  | "BACKUP_RUN"
  | "RESTORE_REQUEST"
  | "RESIZE"
  | "REINSTALL";

export type CustomerServiceRequest = {
  id: string;
  request_type: CustomerServiceRequestType;
  vm_id: string;
  vm_name: string;
  organization_name: string;
  input: Record<string, unknown>;
  impact: { messages?: string[] };
  status:
    | "PENDING_APPROVAL"
    | "APPROVED"
    | "IN_PROGRESS"
    | "SUCCEEDED"
    | "REJECTED"
    | "CANCELLED"
    | "NEEDS_ATTENTION";
  operation_id: string | null;
  error_code: string | null;
  result_summary: string | null;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  version: number;
  approvals: Array<{
    order: number;
    approver_role: string;
    decision: string | null;
    reason: string | null;
    decided_at: string | null;
  }>;
};

export type CustomerSshKey = {
  id: string;
  label: string;
  fingerprint: string;
  public_key: string;
  created_at: string;
};

export type CustomerSecurityGroup = {
  id: string;
  name: string;
  description: string;
};

export type CustomerServiceRequestPreview = {
  request_type: CustomerServiceRequestType;
  requires_approval: boolean;
  requires_step_up: boolean;
  cancellable_until: "APPROVAL";
  impacts: string[];
  current: Record<string, unknown>;
  requested: Record<string, unknown>;
};

type Fetcher = typeof fetch;

export class CustomerApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(message: string, status: number, code: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function parseResponse<T>(response: Response): Promise<T> {
  const body = (await response.json()) as {
    error?: { code?: string; message?: string };
  } & T;
  if (!response.ok) {
    throw new CustomerApiError(
      body.error?.message ?? "요청을 처리하지 못했습니다.",
      response.status,
      body.error?.code ?? "REQUEST_FAILED",
    );
  }
  return body;
}

export function login(
  apiBaseUrl: string,
  email: string,
  password: string,
  fetcher?: Fetcher,
): Promise<AuthSession>;
export function login(
  apiBaseUrl: string,
  email: string,
  password: string,
  fetcher: Fetcher | undefined,
  allowMfaChallenge: true,
): Promise<LoginResult>;
export async function login(
  apiBaseUrl: string,
  email: string,
  password: string,
  fetcher: Fetcher = fetch,
  allowMfaChallenge = false,
): Promise<LoginResult> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const body = await parseResponse<{
    access_token: string | null;
    refresh_token: string | null;
    mfa_required: boolean;
    challenge_id: string | null;
    methods: string[];
  }>(response);
  if (body.mfa_required && body.challenge_id) {
    if (!allowMfaChallenge) {
      throw new CustomerApiError("추가 인증이 필요합니다.", 401, "MFA_REQUIRED");
    }
    return { mfaRequired: true, challengeId: body.challenge_id, methods: body.methods };
  }
  if (!body.access_token || !body.refresh_token) {
    throw new CustomerApiError("로그인 응답이 올바르지 않습니다.", 502, "INVALID_LOGIN_RESPONSE");
  }
  return { accessToken: body.access_token, refreshToken: body.refresh_token };
}

export async function verifyLoginMfa(
  apiBaseUrl: string,
  challengeId: string,
  methodType: string,
  code: string,
  fetcher: Fetcher = fetch,
): Promise<AuthSession> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/auth/mfa/challenges/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      challenge_id: challengeId,
      method_type: methodType,
      code,
    }),
  });
  const body = await parseResponse<{ access_token: string; refresh_token: string }>(response);
  return { accessToken: body.access_token, refreshToken: body.refresh_token };
}

function decodeBase64Url(value: string): ArrayBuffer {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0)).buffer;
}

function encodeBase64Url(value: ArrayBuffer): string {
  let binary = "";
  for (const byte of new Uint8Array(value)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function verifyLoginWebAuthn(
  apiBaseUrl: string,
  challengeId: string,
  fetcher: Fetcher = fetch,
): Promise<AuthSession> {
  if (!window.PublicKeyCredential) {
    throw new CustomerApiError("이 브라우저는 보안 키를 지원하지 않습니다.", 400, "WEBAUTHN_UNSUPPORTED");
  }
  const optionsResponse = await fetcher(
    `${apiBaseUrl}/api/v1/auth/mfa/challenges/${encodeURIComponent(challengeId)}/webauthn-options`,
  );
  const options = await parseResponse<PublicKeyCredentialRequestOptionsJSON>(optionsResponse);
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
  const response = await fetcher(`${apiBaseUrl}/api/v1/auth/mfa/challenges/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      challenge_id: challengeId,
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
  });
  const body = await parseResponse<{ access_token: string; refresh_token: string }>(response);
  return { accessToken: body.access_token, refreshToken: body.refresh_token };
}

export async function listCustomerVms(
  apiBaseUrl: string,
  accessToken: string,
  fetcher: Fetcher = fetch,
): Promise<CustomerVm[]> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/vms`, accessToken, {}, fetcher,
  );
  return (await parseResponse<{ items: CustomerVm[] }>(response)).items;
}

export async function listCustomerAlerts(
  apiBaseUrl: string,
  accessToken: string,
  fetcher: Fetcher = fetch,
): Promise<CustomerAlert[]> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/alerts`, accessToken, {}, fetcher,
  );
  return (await parseResponse<{ items: CustomerAlert[] }>(response)).items;
}

export async function getCustomerVm(
  apiBaseUrl: string,
  accessToken: string,
  vmId: string,
  fetcher: Fetcher = fetch,
): Promise<CustomerVmDetail> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/vms/${encodeURIComponent(vmId)}`,
    accessToken,
    {},
    fetcher,
  );
  return parseResponse<CustomerVmDetail>(response);
}

export async function getCustomerVmMetrics(
  apiBaseUrl: string,
  accessToken: string,
  vmId: string,
  range: CustomerMetricRange,
  fetcher: Fetcher = fetch,
): Promise<CustomerMetricSeries> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/vms/${encodeURIComponent(vmId)}/metrics?range=${range}`,
    accessToken,
    {},
    fetcher,
  );
  return parseResponse<CustomerMetricSeries>(response);
}

export async function requestPowerAction(
  apiBaseUrl: string,
  accessToken: string,
  vmId: string,
  action: CustomerPowerAction,
  idempotencyKey: string,
  optionsOrFetcher: Fetcher | { confirmForced?: boolean; fetcher?: Fetcher } = fetch,
): Promise<CustomerJob> {
  const options =
    typeof optionsOrFetcher === "function"
      ? { fetcher: optionsOrFetcher, confirmForced: false }
      : optionsOrFetcher;
  const fetcher = options.fetcher ?? fetch;
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/vms/${encodeURIComponent(vmId)}/actions/${action}`,
    accessToken,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({ confirm_forced: action === "stop" && options.confirmForced === true }),
    },
    fetcher,
  );
  return parseResponse<CustomerJob>(response);
}

export async function getCustomerJob(
  apiBaseUrl: string,
  accessToken: string,
  jobId: string,
  fetcher: Fetcher = fetch,
): Promise<CustomerJob> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/jobs/${encodeURIComponent(jobId)}`,
    accessToken,
    {},
    fetcher,
  );
  return parseResponse<CustomerJob>(response);
}

export async function listCustomerJobs(
  apiBaseUrl: string,
  accessToken: string,
  fetcher: Fetcher = fetch,
): Promise<CustomerJob[]> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/jobs`,
    accessToken,
    {},
    fetcher,
  );
  return (await parseResponse<{ items: CustomerJob[] }>(response)).items;
}

export async function changePassword(
  apiBaseUrl: string,
  accessToken: string,
  currentPassword: string,
  newPassword: string,
  optionsOrFetcher: Fetcher | { revokeAllSessions?: boolean; fetcher?: Fetcher } = fetch,
): Promise<void> {
  const options = typeof optionsOrFetcher === "function"
    ? { fetcher: optionsOrFetcher, revokeAllSessions: true }
    : optionsOrFetcher;
  const fetcher = options.fetcher ?? fetch;
  const response = await fetchWithAccessToken(`${apiBaseUrl}/api/v1/auth/change-password`, accessToken, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
      revoke_all_sessions: options.revokeAllSessions ?? true,
    }),
  }, fetcher);
  if (response.ok) return;
  const body = (await response.json()) as {
    error?: { code?: string; message?: string };
  };
  throw new CustomerApiError(
    body.error?.message ?? "비밀번호를 변경하지 못했습니다.",
    response.status,
    body.error?.code ?? "PASSWORD_CHANGE_FAILED",
  );
}

export async function getCustomerNotificationPreferences(
  apiBaseUrl: string,
  accessToken: string,
  fetcher: Fetcher = fetch,
): Promise<CustomerNotificationPreferences> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/notification-preferences`,
    accessToken,
    {},
    fetcher,
  );
  return parseResponse<CustomerNotificationPreferences>(response);
}

export async function updateCustomerNotificationPreference(
  apiBaseUrl: string,
  accessToken: string,
  preference: Pick<
    CustomerNotificationPreference,
    "organization_id" | "event_type" | "email_enabled" | "version"
  >,
  fetcher: Fetcher = fetch,
): Promise<CustomerNotificationPreference> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/notification-preferences`,
    accessToken,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(preference),
    },
    fetcher,
  );
  return parseResponse<CustomerNotificationPreference>(response);
}

export async function listCustomerServiceRequests(
  apiBaseUrl: string,
  accessToken: string,
  fetcher: Fetcher = fetch,
): Promise<CustomerServiceRequest[]> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/service-requests`,
    accessToken,
    {},
    fetcher,
  );
  return (await parseResponse<{ items: CustomerServiceRequest[] }>(response)).items;
}

export async function createCustomerServiceRequest(
  apiBaseUrl: string,
  accessToken: string,
  vmId: string,
  requestType: CustomerServiceRequestType,
  input: Record<string, unknown>,
  idempotencyKey: string,
  fetcher: Fetcher = fetch,
): Promise<CustomerServiceRequest> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/vms/${encodeURIComponent(vmId)}/service-requests`,
    accessToken,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({ request_type: requestType, input }),
    },
    fetcher,
  );
  return parseResponse<CustomerServiceRequest>(response);
}

export async function previewCustomerServiceRequest(
  apiBaseUrl: string,
  accessToken: string,
  vmId: string,
  requestType: CustomerServiceRequestType,
  input: Record<string, unknown>,
  fetcher: Fetcher = fetch,
): Promise<CustomerServiceRequestPreview> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/vms/${encodeURIComponent(vmId)}/service-requests/preview`,
    accessToken,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_type: requestType, input }),
    },
    fetcher,
  );
  return parseResponse<CustomerServiceRequestPreview>(response);
}

export async function cancelCustomerServiceRequest(
  apiBaseUrl: string,
  accessToken: string,
  requestId: string,
  version: number,
  fetcher: Fetcher = fetch,
): Promise<CustomerServiceRequest> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/service-requests/${encodeURIComponent(requestId)}/cancel`,
    accessToken,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version }),
    },
    fetcher,
  );
  return parseResponse<CustomerServiceRequest>(response);
}

export async function listCustomerSshKeys(
  apiBaseUrl: string,
  accessToken: string,
  fetcher: Fetcher = fetch,
): Promise<CustomerSshKey[]> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/ssh-keys`,
    accessToken,
    {},
    fetcher,
  );
  return (await parseResponse<{ items: CustomerSshKey[] }>(response)).items;
}

export async function createCustomerSshKey(
  apiBaseUrl: string,
  accessToken: string,
  vmId: string,
  label: string,
  publicKey: string,
  fetcher: Fetcher = fetch,
): Promise<CustomerSshKey> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/vms/${encodeURIComponent(vmId)}/ssh-keys`,
    accessToken,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label, public_key: publicKey }),
    },
    fetcher,
  );
  return parseResponse<CustomerSshKey>(response);
}

export async function listCustomerSecurityGroups(
  apiBaseUrl: string,
  accessToken: string,
  vmId: string,
  fetcher: Fetcher = fetch,
): Promise<CustomerSecurityGroup[]> {
  const response = await fetchWithAccessToken(
    `${apiBaseUrl}/api/v1/customer/vms/${encodeURIComponent(vmId)}/security-groups`,
    accessToken,
    {},
    fetcher,
  );
  return (await parseResponse<{ items: CustomerSecurityGroup[] }>(response)).items;
}

export async function logout(
  apiBaseUrl: string,
  refreshToken: string,
  fetcher: Fetcher = fetch,
): Promise<void> {
  await fetcher(`${apiBaseUrl}/api/v1/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
}

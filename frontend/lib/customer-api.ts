import { fetchWithAccessToken } from "./authenticated-fetch.ts";

export type AuthSession = {
  accessToken: string;
  refreshToken: string;
};

export type CustomerPowerAction = "start" | "shutdown" | "stop" | "reboot";

export type CustomerVm = {
  id: string;
  name: string;
  organization_name: string;
  power_state: string;
  cpu_cores: number | null;
  memory_bytes: number | null;
  disk_bytes: number | null;
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

export async function login(
  apiBaseUrl: string,
  email: string,
  password: string,
  fetcher: Fetcher = fetch,
): Promise<AuthSession> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
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
  fetcher: Fetcher = fetch,
): Promise<void> {
  const response = await fetchWithAccessToken(`${apiBaseUrl}/api/v1/auth/change-password`, accessToken, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
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

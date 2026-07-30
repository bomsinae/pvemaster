"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import {
  AdminApiError,
  AdminApprovalPolicy,
  AdminOrganizationQuota,
  Organization,
  getAdminOrganizationQuota,
  listAdminApprovalPolicies,
  updateAdminApprovalPolicy,
  updateAdminOrganizationQuota,
} from "@/lib/admin-api";

function readable(error: unknown) {
  return error instanceof AdminApiError
    ? `${error.message} · ${error.code}`
    : "조직 정책 API에 연결하지 못했습니다.";
}

function gib(value: number) {
  return Math.round(value / 1024 ** 3);
}

export function OrganizationGovernanceView({
  apiBaseUrl,
  token,
  organizations,
  canWrite,
}: {
  apiBaseUrl: string;
  token: string;
  organizations: Organization[];
  canWrite: boolean;
}) {
  const [organizationId, setOrganizationId] = useState(organizations[0]?.id ?? "");
  const [quota, setQuota] = useState<AdminOrganizationQuota | null>(null);
  const [policies, setPolicies] = useState<AdminApprovalPolicy[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const selectedOrganizationId = organizations.some((item) => item.id === organizationId)
    ? organizationId
    : organizations[0]?.id ?? "";

  const load = useCallback(async (selectedId: string) => {
    if (!selectedId) return;
    setBusy(true);
    try {
      const [nextQuota, nextPolicies] = await Promise.all([
        getAdminOrganizationQuota(apiBaseUrl, token, selectedId),
        listAdminApprovalPolicies(apiBaseUrl, token, selectedId),
      ]);
      setQuota(nextQuota);
      setPolicies(nextPolicies);
      setMessage("");
    } catch (error) {
      setMessage(readable(error));
    } finally {
      setBusy(false);
    }
  }, [apiBaseUrl, token]);

  useEffect(() => {
    if (!selectedOrganizationId) return;
    const timer = window.setTimeout(() => {
      void load(selectedOrganizationId);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load, selectedOrganizationId]);

  async function saveQuota(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!quota || !selectedOrganizationId) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    try {
      const updated = await updateAdminOrganizationQuota(
        apiBaseUrl,
        token,
        selectedOrganizationId,
        quota,
        {
          vcpu: Number(form.get("vcpu")),
          memory_bytes: Number(form.get("memory_gib")) * 1024 ** 3,
          disk_bytes: Number(form.get("disk_gib")) * 1024 ** 3,
          vms: Number(form.get("vms")),
          ips: Number(form.get("ips")),
          backup_bytes: Number(form.get("backup_gib")) * 1024 ** 3,
        },
      );
      setQuota(updated);
      setMessage("조직 quota를 저장했습니다.");
    } catch (error) {
      setMessage(readable(error));
    } finally {
      setBusy(false);
    }
  }

  async function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedOrganizationId) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const requestType = String(form.get("request_type") ?? "").toUpperCase();
    const existing = policies.find((item) => item.request_type === requestType) ?? null;
    setBusy(true);
    try {
      await updateAdminApprovalPolicy(
        apiBaseUrl,
        token,
        selectedOrganizationId,
        existing,
        requestType,
        String(form.get("minimum_role")) as AdminApprovalPolicy["minimum_role"],
        form.get("requires_approval") === "on",
      );
      setMessage("승인 정책을 저장했습니다.");
      formElement.reset();
      await load(selectedOrganizationId);
    } catch (error) {
      setMessage(readable(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="organization-governance-admin">
      <section className="organization-governance-hero">
        <div><p className="eyebrow">Organization governance</p><h2>조직 권한과 자원 정책</h2><p>플랫폼 역할과 조직 역할을 분리하고, 사용량과 진행 중 예약을 함께 확인합니다.</p></div>
        <label>대상 조직<select value={selectedOrganizationId} onChange={(event) => setOrganizationId(event.target.value)}>{organizations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      </section>
      {!organizations.length && <p className="empty-state">등록된 조직이 없습니다.</p>}
      {quota && <div className="organization-governance-admin-grid">
        <section>
          <header><div><p className="eyebrow">Capacity</p><h3>Quota 설정</h3></div><span>{canWrite ? "편집 가능" : "조회 전용"}</span></header>
          <div className="admin-quota-usage">
            <p><strong>vCPU</strong><span>{quota.usage.vcpu} 사용 · {quota.reserved.vcpu} 예약 / {quota.limits.vcpu}</span></p>
            <p><strong>VM</strong><span>{quota.usage.vms} 사용 · {quota.reserved.vms} 예약 / {quota.limits.vms}</span></p>
            <p><strong>IP</strong><span>{quota.usage.ips} 사용 · {quota.reserved.ips} 예약 / {quota.limits.ips}</span></p>
          </div>
          <form className="admin-quota-form" onSubmit={saveQuota}>
            <label>vCPU<input name="vcpu" type="number" min={0} defaultValue={quota.limits.vcpu} disabled={!canWrite} /></label>
            <label>RAM GiB<input name="memory_gib" type="number" min={0} defaultValue={gib(quota.limits.memory_bytes)} disabled={!canWrite} /></label>
            <label>Disk GiB<input name="disk_gib" type="number" min={0} defaultValue={gib(quota.limits.disk_bytes)} disabled={!canWrite} /></label>
            <label>VM 수<input name="vms" type="number" min={0} defaultValue={quota.limits.vms} disabled={!canWrite} /></label>
            <label>IP 수<input name="ips" type="number" min={0} defaultValue={quota.limits.ips} disabled={!canWrite} /></label>
            <label>Backup GiB<input name="backup_gib" type="number" min={0} defaultValue={gib(quota.limits.backup_bytes)} disabled={!canWrite} /></label>
            {canWrite && <button type="submit" disabled={busy}>Quota 저장</button>}
          </form>
        </section>
        <section>
          <header><div><p className="eyebrow">Approval</p><h3>요청 승인 정책</h3></div><span>{policies.length} policies</span></header>
          <div className="approval-policy-list">{policies.map((item) => <article key={item.id}><strong>{item.request_type}</strong><span>{item.requires_approval ? "승인 필요" : "자동 허용"}</span><small>{item.minimum_role} 이상</small></article>)}{!policies.length && <p>정의된 승인 정책이 없습니다.</p>}</div>
          {canWrite && <form className="approval-policy-form" onSubmit={savePolicy}><label>요청 유형<input name="request_type" required pattern="[A-Z][A-Z0-9_]+" placeholder="RESIZE" /></label><label>최소 승인 역할<select name="minimum_role" defaultValue="ORG_ADMIN"><option value="ORG_OWNER">ORG_OWNER</option><option value="ORG_ADMIN">ORG_ADMIN</option><option value="ORG_OPERATOR">ORG_OPERATOR</option></select></label><label className="checkbox-field"><input name="requires_approval" type="checkbox" defaultChecked />승인 필요</label><button type="submit" disabled={busy}>정책 저장</button></form>}
        </section>
      </div>}
      <p className="admin-inline-status" role="status" aria-live="polite">{busy ? "정책을 불러오는 중…" : message}</p>
    </div>
  );
}

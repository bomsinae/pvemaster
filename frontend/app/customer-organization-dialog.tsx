"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  CustomerApiError,
  CustomerOrganizationActivity,
  CustomerOrganizationInvitation,
  CustomerOrganizationMembership,
  CustomerOrganizationQuota,
  OrganizationRole,
  acceptCustomerOrganizationInvitation,
  getCustomerOrganizationQuota,
  inviteCustomerOrganizationMember,
  listCustomerOrganizationActivity,
  listCustomerOrganizationInvitations,
  listCustomerOrganizationMembers,
  listCustomerOrganizations,
  removeCustomerOrganizationMember,
  updateCustomerOrganizationMember,
} from "@/lib/customer-api";

import { useDialogFocus } from "./use-dialog-focus";

const roleLabels: Record<OrganizationRole, string> = {
  ORG_OWNER: "소유자",
  ORG_ADMIN: "조직 관리자",
  ORG_OPERATOR: "운영자",
  ORG_VIEWER: "조회자",
  BILLING_VIEWER: "비용 조회자",
};

function readableError(error: unknown) {
  return error instanceof CustomerApiError
    ? `${error.message} · ${error.code}`
    : "조직 정보를 불러오지 못했습니다.";
}

function bytes(value: number) {
  if (value < 1024 ** 3) return `${Math.round(value / 1024 ** 2)} MiB`;
  if (value < 1024 ** 4) return `${(value / 1024 ** 3).toFixed(1)} GiB`;
  return `${(value / 1024 ** 4).toFixed(1)} TiB`;
}

export function CustomerOrganizationDialog({
  apiBaseUrl,
  accessToken,
  onClose,
}: {
  apiBaseUrl: string;
  accessToken: string;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const [organizations, setOrganizations] = useState<CustomerOrganizationMembership[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [members, setMembers] = useState<CustomerOrganizationMembership[]>([]);
  const [invitations, setInvitations] = useState<CustomerOrganizationInvitation[]>([]);
  const [quota, setQuota] = useState<CustomerOrganizationQuota | null>(null);
  const [activity, setActivity] = useState<CustomerOrganizationActivity[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [createdToken, setCreatedToken] = useState("");
  useDialogFocus(true, dialogRef, onClose);

  const selected = organizations.find((item) => item.organization_id === selectedId) ?? null;
  const canReadMembers = selected?.permissions.includes("MEMBER_READ") ?? false;
  const canInvite = selected?.permissions.includes("MEMBER_INVITE") ?? false;
  const canChangeRoles = selected?.permissions.includes("MEMBER_ROLE_WRITE") ?? false;
  const canRemove = selected?.permissions.includes("MEMBER_REMOVE") ?? false;

  const loadOrganization = useCallback(async (organizationId: string) => {
    if (!organizationId) return;
    const membership = organizations.find((item) => item.organization_id === organizationId);
    const [nextQuota, nextMembers, nextInvitations, nextActivity] = await Promise.all([
      getCustomerOrganizationQuota(apiBaseUrl, accessToken, organizationId),
      membership?.permissions.includes("MEMBER_READ")
        ? listCustomerOrganizationMembers(apiBaseUrl, accessToken, organizationId)
        : Promise.resolve([]),
      membership?.permissions.includes("MEMBER_READ")
        ? listCustomerOrganizationInvitations(apiBaseUrl, accessToken, organizationId)
        : Promise.resolve([]),
      membership?.permissions.includes("ACTIVITY_READ")
        ? listCustomerOrganizationActivity(apiBaseUrl, accessToken, organizationId)
        : Promise.resolve([]),
    ]);
    setQuota(nextQuota);
    setMembers(nextMembers);
    setInvitations(nextInvitations);
    setActivity(nextActivity);
  }, [accessToken, apiBaseUrl, organizations]);

  useEffect(() => {
    let active = true;
    void listCustomerOrganizations(apiBaseUrl, accessToken)
      .then((items) => {
        if (!active) return;
        setOrganizations(items);
        setSelectedId(items[0]?.organization_id ?? "");
      })
      .catch((error) => active && setMessage(readableError(error)));
    return () => { active = false; };
  }, [accessToken, apiBaseUrl]);

  useEffect(() => {
    if (!selectedId) return;
    const timer = window.setTimeout(() => {
      setBusy(true);
      void loadOrganization(selectedId)
        .catch((error) => setMessage(readableError(error)))
        .finally(() => setBusy(false));
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadOrganization, selectedId]);

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedId) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy(true);
    setCreatedToken("");
    try {
      const invitation = await inviteCustomerOrganizationMember(
        apiBaseUrl,
        accessToken,
        selectedId,
        String(form.get("email") ?? ""),
        String(form.get("role") ?? "ORG_VIEWER") as OrganizationRole,
      );
      setCreatedToken(invitation.accept_token ?? "");
      setMessage("초대를 만들었습니다. 표시된 토큰은 다시 조회할 수 없습니다.");
      formElement.reset();
      await loadOrganization(selectedId);
    } catch (error) {
      setMessage(readableError(error));
    } finally {
      setBusy(false);
    }
  }

  async function acceptInvitation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const token = String(new FormData(formElement).get("invitation_token") ?? "");
    setBusy(true);
    try {
      await acceptCustomerOrganizationInvitation(apiBaseUrl, accessToken, token);
      const items = await listCustomerOrganizations(apiBaseUrl, accessToken);
      setOrganizations(items);
      setSelectedId(items[0]?.organization_id ?? "");
      setMessage("조직 초대를 수락했습니다.");
      formElement.reset();
    } catch (error) {
      setMessage(readableError(error));
    } finally {
      setBusy(false);
    }
  }

  async function changeRole(member: CustomerOrganizationMembership, role: OrganizationRole) {
    if (!selectedId) return;
    setBusy(true);
    try {
      await updateCustomerOrganizationMember(
        apiBaseUrl,
        accessToken,
        selectedId,
        member,
        role,
      );
      setMessage("구성원 역할을 변경했습니다.");
      await loadOrganization(selectedId);
    } catch (error) {
      setMessage(readableError(error));
    } finally {
      setBusy(false);
    }
  }

  async function remove(member: CustomerOrganizationMembership) {
    if (!selectedId || !window.confirm(`${member.display_name} 구성원을 제거할까요?`)) return;
    setBusy(true);
    try {
      await removeCustomerOrganizationMember(
        apiBaseUrl,
        accessToken,
        selectedId,
        member.id,
      );
      setMessage("구성원을 제거했습니다.");
      await loadOrganization(selectedId);
    } catch (error) {
      setMessage(readableError(error));
    } finally {
      setBusy(false);
    }
  }

  const quotaRows = quota ? [
    ["vCPU", quota.usage.vcpu, quota.reserved.vcpu, quota.limits.vcpu],
    ["VM", quota.usage.vms, quota.reserved.vms, quota.limits.vms],
    ["IP", quota.usage.ips, quota.reserved.ips, quota.limits.ips],
    ["RAM", bytes(quota.usage.memory_bytes), bytes(quota.reserved.memory_bytes), bytes(quota.limits.memory_bytes)],
    ["Disk", bytes(quota.usage.disk_bytes), bytes(quota.reserved.disk_bytes), bytes(quota.limits.disk_bytes)],
    ["Backup", bytes(quota.usage.backup_bytes), bytes(quota.reserved.backup_bytes), bytes(quota.limits.backup_bytes)],
  ] : [];

  return (
    <div className="customer-dialog-backdrop" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        tabIndex={-1}
        className="customer-dialog customer-organization-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="organization-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div><p className="eyebrow">Organization workspace</p><h2 id="organization-dialog-title">조직과 팀 관리</h2></div>
          <button type="button" onClick={onClose} aria-label="조직 관리 닫기">×</button>
        </header>
        <label className="organization-picker">조직<select value={selectedId} onChange={(event) => { setSelectedId(event.target.value); setCreatedToken(""); }}>
          {organizations.map((item) => <option key={item.organization_id} value={item.organization_id}>{item.organization_name} · {roleLabels[item.organization_role]}</option>)}
        </select></label>
        <form className="organization-accept-form" onSubmit={acceptInvitation}><label>받은 초대 토큰<input name="invitation_token" required autoComplete="off" /></label><button type="submit" disabled={busy}>초대 수락</button></form>
        {!organizations.length && <p className="empty-state">활성 조직 멤버십이 없습니다.</p>}
        {selected && <div className="organization-governance-grid">
          <section>
            <h3>Quota와 예약</h3>
            <div className="organization-quota-list">
              {quotaRows.map(([label, used, reserved, limit]) => <div key={String(label)}><strong>{label}</strong><span>사용 {used}</span><span>예약 {reserved}</span><em>/ {limit}</em></div>)}
            </div>
          </section>
          <section>
            <h3>팀 구성원</h3>
            {canReadMembers ? <div className="organization-member-list">
              {members.map((member) => <article key={member.id}><div><strong>{member.display_name}</strong><small>{member.email}</small></div>{canChangeRoles ? <select aria-label={`${member.display_name} 조직 역할`} value={member.organization_role} disabled={busy} onChange={(event) => void changeRole(member, event.target.value as OrganizationRole)}>{Object.entries(roleLabels).map(([role, label]) => <option key={role} value={role}>{label}</option>)}</select> : <span>{roleLabels[member.organization_role]}</span>}{canRemove && <button type="button" disabled={busy} onClick={() => void remove(member)}>제거</button>}</article>)}
            </div> : <p>이 역할에는 구성원 조회 권한이 없습니다.</p>}
          </section>
          {canInvite && <section>
            <h3>구성원 초대</h3>
            <form className="organization-invite-form" onSubmit={invite}><label>이메일<input name="email" type="email" required /></label><label>역할<select name="role" defaultValue="ORG_VIEWER">{Object.entries(roleLabels).map(([role, label]) => <option key={role} value={role}>{label}</option>)}</select></label><button type="submit" disabled={busy}>72시간 초대 생성</button></form>
            {createdToken && <div className="invitation-token" role="status"><strong>1회 표시 초대 토큰</strong><code>{createdToken}</code></div>}
            <div className="pending-invitations">{invitations.filter((item) => !item.accepted_at && !item.revoked_at).map((item) => <p key={item.id}><span>{item.email}</span><small>{roleLabels[item.organization_role]} · {new Date(item.expires_at).toLocaleString("ko-KR")} 만료</small></p>)}</div>
          </section>}
          <section>
            <h3>최근 조직 활동</h3>
            <div className="organization-activity-list">{activity.slice(0, 12).map((item) => <p key={item.id}><strong>{item.action}</strong><span>{item.outcome}</span><time>{new Date(item.created_at).toLocaleString("ko-KR")}</time></p>)}</div>
          </section>
        </div>}
        <p className="self-service-message" role="status" aria-live="polite">{busy ? "처리 중…" : message}</p>
      </section>
    </div>
  );
}

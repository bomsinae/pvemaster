"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  cancelCustomerServiceRequest,
  createCustomerServiceRequest,
  createCustomerSshKey,
  CustomerApiError,
  CustomerSecurityGroup,
  CustomerServiceRequest,
  CustomerServiceRequestPreview,
  CustomerServiceRequestType,
  CustomerSshKey,
  CustomerVm,
  listCustomerSecurityGroups,
  listCustomerServiceRequests,
  listCustomerSshKeys,
  previewCustomerServiceRequest,
} from "@/lib/customer-api";
import { useDialogFocus } from "./use-dialog-focus";

const labels: Record<CustomerServiceRequestType, string> = {
  SSH_KEY_ADD: "SSH 공개키 추가",
  SSH_KEY_REPLACE: "SSH 공개키 교체",
  SSH_KEY_DELETE: "SSH 공개키 삭제",
  METADATA_CHANGE: "Hostname · 설명 변경",
  RDNS_CHANGE: "rDNS 변경 요청",
  SECURITY_GROUP_APPLY: "Security group 적용",
  BACKUP_RUN: "백업 실행 요청",
  RESTORE_REQUEST: "별도 VM 복구 요청",
  RESIZE: "vCPU · RAM · Disk 증설",
  REINSTALL: "재설치 요청",
};

function errorText(error: unknown) {
  return error instanceof CustomerApiError
    ? `${error.message} · ${error.code}`
    : "Self-service API에 연결하지 못했습니다.";
}

function requestInput(form: FormData, type: CustomerServiceRequestType, vm: CustomerVm) {
  const input: Record<string, unknown> = {};
  const text = (name: string) => String(form.get(name) ?? "").trim();
  const number = (name: string, multiplier = 1) => {
    const value = Number(form.get(name));
    return Number.isFinite(value) && value > 0 ? Math.round(value * multiplier) : null;
  };
  if (type.startsWith("SSH_KEY_")) input.ssh_key_id = text("ssh_key_id");
  if (type === "METADATA_CHANGE") {
    if (text("hostname")) input.hostname = text("hostname");
    if (text("description")) input.description = text("description");
  }
  if (type === "RDNS_CHANGE") input.rdns = text("rdns");
  if (type === "SECURITY_GROUP_APPLY") input.security_group_id = text("security_group_id");
  if (type === "RESTORE_REQUEST") {
    input.backup_run_id = text("backup_run_id");
    input.confirmation = `RESTORE ${vm.name}`;
  }
  if (type === "RESIZE") {
    const cpu = number("cpu_cores");
    const memory = number("memory_gib", 1024 ** 3);
    const disk = number("disk_gib", 1024 ** 3);
    if (cpu) input.cpu_cores = cpu;
    if (memory) input.memory_bytes = memory;
    if (disk) input.disk_bytes = disk;
  }
  if (type === "REINSTALL") input.confirmation = text("confirmation");
  if (text("reason")) input.reason = text("reason");
  return input;
}

export function CustomerSelfServiceDialog({
  apiBaseUrl,
  accessToken,
  vm,
  onClose,
}: {
  apiBaseUrl: string;
  accessToken: string;
  vm: CustomerVm;
  onClose: () => void;
}) {
  const [type, setType] = useState<CustomerServiceRequestType>("METADATA_CHANGE");
  const [requests, setRequests] = useState<CustomerServiceRequest[]>([]);
  const [keys, setKeys] = useState<CustomerSshKey[]>([]);
  const [groups, setGroups] = useState<CustomerSecurityGroup[]>([]);
  const [preview, setPreview] = useState<CustomerServiceRequestPreview | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const dialogRef = useRef<HTMLElement>(null);

  useDialogFocus(true, dialogRef, saving ? undefined : onClose);

  const load = useCallback(async () => {
    const [nextRequests, nextKeys, nextGroups] = await Promise.all([
      listCustomerServiceRequests(apiBaseUrl, accessToken),
      listCustomerSshKeys(apiBaseUrl, accessToken),
      listCustomerSecurityGroups(apiBaseUrl, accessToken, vm.id),
    ]);
    setRequests(nextRequests.filter((item) => item.vm_id === vm.id));
    setKeys(nextKeys);
    setGroups(nextGroups);
  }, [accessToken, apiBaseUrl, vm.id]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load().catch((error) => setMessage(errorText(error)));
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function previewRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input = requestInput(new FormData(event.currentTarget), type, vm);
    setSaving(true);
    setMessage("");
    try {
      const result = await previewCustomerServiceRequest(
        apiBaseUrl,
        accessToken,
        vm.id,
        type,
        input,
      );
      setDraft(input);
      setPreview(result);
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setSaving(false);
    }
  }

  async function submitRequest() {
    if (!draft) return;
    setSaving(true);
    try {
      await createCustomerServiceRequest(
        apiBaseUrl,
        accessToken,
        vm.id,
        type,
        draft,
        crypto.randomUUID(),
      );
      setPreview(null);
      setDraft(null);
      setMessage("승인 요청을 접수했습니다.");
      await load();
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setSaving(false);
    }
  }

  async function addKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setSaving(true);
    try {
      await createCustomerSshKey(
        apiBaseUrl,
        accessToken,
        vm.id,
        String(form.get("key_label") ?? ""),
        String(form.get("public_key") ?? ""),
      );
      formElement.reset();
      setMessage("공개키를 저장했습니다. Private key는 전송하지 마세요.");
      await load();
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setSaving(false);
    }
  }

  async function cancel(item: CustomerServiceRequest) {
    setSaving(true);
    try {
      await cancelCustomerServiceRequest(
        apiBaseUrl,
        accessToken,
        item.id,
        item.version,
      );
      setMessage("승인 전 요청을 취소했습니다.");
      await load();
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setSaving(false);
    }
  }

  const sshType = type.startsWith("SSH_KEY_");
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={() => { if (!saving) onClose(); }}>
      <section
        ref={dialogRef}
        tabIndex={-1}
        className="customer-self-service-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="self-service-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div><p className="eyebrow">Controlled self-service</p><h2 id="self-service-title">{vm.name} 변경 요청</h2></div>
          <button type="button" onClick={onClose} disabled={saving} aria-label="변경 요청 닫기">×</button>
        </header>
        <div className="self-service-columns">
          <div>
            <form className="self-service-form" onSubmit={previewRequest}>
              <label>요청 유형<select value={type} onChange={(event) => { setType(event.target.value as CustomerServiceRequestType); setPreview(null); }}>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
              {sshType && <label>SSH 공개키<select name="ssh_key_id" required><option value="">키 선택</option>{keys.map((key) => <option key={key.id} value={key.id}>{key.label} · {key.fingerprint.slice(0, 20)}</option>)}</select></label>}
              {type === "METADATA_CHANGE" && <><label>Hostname<input name="hostname" pattern="[A-Za-z0-9][A-Za-z0-9.-]{0,62}" /></label><label>설명<input name="description" maxLength={300} /></label></>}
              {type === "RDNS_CHANGE" && <label>Reverse DNS<input name="rdns" required placeholder="vm.example.com" /></label>}
              {type === "SECURITY_GROUP_APPLY" && <label>승인된 security group<select name="security_group_id" required><option value="">정책 선택</option>{groups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label>}
              {type === "RESTORE_REQUEST" && <label>백업 실행 ID<input name="backup_run_id" required /></label>}
              {type === "RESIZE" && <div className="self-service-size-grid"><label>vCPU<input name="cpu_cores" type="number" min={vm.cpu_cores ?? 1} max={128} /></label><label>RAM GiB<input name="memory_gib" type="number" min={1} /></label><label>Disk GiB<input name="disk_gib" type="number" min={1} /></label></div>}
              {type === "REINSTALL" && <label className="danger-field">영향 확인<input name="confirmation" required placeholder={`REINSTALL ${vm.name}`} /><small>정확히 `REINSTALL {vm.name}`을 입력하고 MFA로 다시 확인합니다.</small></label>}
              <label>요청 사유<textarea name="reason" maxLength={500} /></label>
              <button type="submit" disabled={saving}>영향 미리보기</button>
            </form>
            {preview && <section className="self-service-preview" aria-live="polite"><strong>승인 전 영향</strong><ul>{preview.impacts.map((impact) => <li key={impact}>{impact}</li>)}</ul><p>{preview.requires_step_up ? "MFA 재인증 필요 · " : ""}승인 전까지만 취소할 수 있습니다.</p><button type="button" onClick={submitRequest} disabled={saving}>승인 요청 제출</button></section>}
          </div>
          <div>
            <form className="ssh-key-create" onSubmit={addKey}>
              <h3>SSH 공개키 보관함</h3>
              <label>키 이름<input name="key_label" required maxLength={80} /></label>
              <label>Public key<textarea name="public_key" required placeholder="ssh-ed25519 AAAA…" /></label>
              <small>Public key만 저장합니다. Private key는 브라우저 밖으로 전송하지 마세요.</small>
              <button type="submit" disabled={saving}>공개키 추가</button>
            </form>
            <section className="self-service-history">
              <h3>이 VM의 요청 내역</h3>
              {requests.map((item) => <article key={item.id}><div><strong>{labels[item.request_type]}</strong><span>{item.status}</span></div><small>{new Date(item.requested_at).toLocaleString("ko-KR")}</small>{item.result_summary && <p>{item.result_summary}</p>}{item.status === "PENDING_APPROVAL" && <button type="button" onClick={() => cancel(item)} disabled={saving}>요청 취소</button>}</article>)}
              {!requests.length && <p>아직 접수된 요청이 없습니다.</p>}
            </section>
          </div>
        </div>
        <p className="self-service-message" role="status" aria-live="polite">{message}</p>
      </section>
    </div>
  );
}

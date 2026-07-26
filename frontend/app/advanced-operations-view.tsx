"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  AdvancedFeature,
  AdvancedFeatureCapability,
  AdvancedInspection,
  AdvancedPreview,
  AdvancedPreviewInput,
  AdminApiError,
  Workload,
  createAdvancedOperation,
  getAdvancedCapabilities,
  inspectAdvancedWorkload,
  previewAdvancedOperation,
} from "@/lib/admin-api";

const featureLabels: Record<AdvancedFeature, string> = {
  SNAPSHOT: "VM Snapshot",
  MIGRATION: "Live / Offline Migration",
  HA: "HA 정책",
  NODE_MAINTENANCE: "Node Maintenance & Drain",
  BULK: "일괄 전원 작업",
  GUEST_CONFIG: "상세 VM / CT 구성",
  FIREWALL_SDN: "Firewall & SDN",
};

function errorText(error: unknown) {
  return error instanceof AdminApiError
    ? `${error.message} · ${error.code}`
    : "고급 운영 API에 연결하지 못했습니다.";
}

function stringValue(form: FormData, key: string) {
  const value = String(form.get(key) ?? "").trim();
  return value || undefined;
}

function optionsFor(feature: AdvancedFeature, action: string, form: FormData) {
  if (feature === "SNAPSHOT") {
    return {
      snapshot_name: stringValue(form, "snapshot_name"),
      include_memory: form.get("include_memory") === "on",
    };
  }
  if (feature === "MIGRATION") {
    return {
      target_node: stringValue(form, "target_node"),
      target_storage: stringValue(form, "target_storage"),
      target_network: stringValue(form, "target_network"),
      local_disks_compatible: form.get("local_disks_compatible") === "on",
      passthrough_free: form.get("passthrough_free") === "on",
      ha_compatible: form.get("ha_compatible") === "on",
      replication_compatible: form.get("replication_compatible") === "on",
    };
  }
  if (feature === "HA") {
    return {
      requested_state: stringValue(form, "requested_state"),
      group: stringValue(form, "group"),
    };
  }
  if (feature === "NODE_MAINTENANCE") {
    if (action !== "DRAIN") return {};
    return {
      target_node: stringValue(form, "target_node"),
      backup_confirmed: form.get("backup_confirmed") === "on",
      customer_notification_confirmed:
        form.get("customer_notification_confirmed") === "on",
    };
  }
  if (feature === "GUEST_CONFIG") {
    const cores = stringValue(form, "cores");
    const memory = stringValue(form, "memory_mib");
    const vlan = stringValue(form, "vlan_tag");
    return {
      ...(cores ? { cores: Number(cores) } : {}),
      ...(memory ? { memory_mib: Number(memory) } : {}),
      ...(stringValue(form, "bridge") ? { bridge: stringValue(form, "bridge") } : {}),
      ...(vlan ? { vlan_tag: Number(vlan) } : {}),
      ...(stringValue(form, "boot_order")
        ? { boot_order: stringValue(form, "boot_order") }
        : {}),
    };
  }
  return {};
}

export function AdvancedOperationsView({
  apiBaseUrl,
  token,
  workloads,
  canWrite,
}: {
  apiBaseUrl: string;
  token: string;
  workloads: Workload[];
  canWrite: boolean;
}) {
  const [capabilities, setCapabilities] = useState<AdvancedFeatureCapability[]>([]);
  const [feature, setFeature] = useState<AdvancedFeature>("SNAPSHOT");
  const [action, setAction] = useState("CREATE");
  const [selected, setSelected] = useState<string[]>([]);
  const [input, setInput] = useState<AdvancedPreviewInput | null>(null);
  const [preview, setPreview] = useState<AdvancedPreview | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [inspection, setInspection] = useState<AdvancedInspection | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const capability = capabilities.find((item) => item.feature === feature);
  const visibleWorkloads = useMemo(
    () => workloads.filter((item) => item.is_present && !item.is_template),
    [workloads],
  );

  useEffect(() => {
    let active = true;
    void getAdvancedCapabilities(apiBaseUrl, token)
      .then((items) => {
        if (!active) return;
        setCapabilities(items);
        const first = items[0];
        if (first) {
          setFeature(first.feature);
          setAction(first.actions[0] ?? "");
        }
      })
      .catch((error) => active && setMessage(errorText(error)));
    return () => {
      active = false;
    };
  }, [apiBaseUrl, token]);

  function chooseFeature(next: AdvancedFeature) {
    const nextCapability = capabilities.find((item) => item.feature === next);
    setFeature(next);
    setAction(nextCapability?.actions[0] ?? "");
    setSelected([]);
    setPreview(null);
    setInspection(null);
    setConfirmation("");
  }

  function toggleTarget(workloadId: string) {
    setSelected((current) =>
      current.includes(workloadId)
        ? current.filter((item) => item !== workloadId)
        : [...current, workloadId],
    );
    setPreview(null);
    setInspection(null);
  }

  async function requestPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected.length) {
      setMessage("대상을 한 개 이상 선택하세요.");
      return;
    }
    const form = new FormData(event.currentTarget);
    const nextInput: AdvancedPreviewInput = {
      feature,
      action,
      workload_ids: selected,
      options: optionsFor(feature, action, form),
    };
    setBusy(true);
    setMessage("");
    try {
      const nextPreview = await previewAdvancedOperation(apiBaseUrl, token, nextInput);
      setInput(nextInput);
      setPreview(nextPreview);
      setConfirmation("");
      setMessage(
        nextPreview.executable
          ? "실행 전 검사가 완료됐습니다."
          : "차단 항목을 해결한 뒤 다시 검사하세요.",
      );
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setBusy(false);
    }
  }

  async function execute() {
    if (!preview || !input) return;
    setBusy(true);
    setMessage("");
    try {
      const operation = await createAdvancedOperation(
        apiBaseUrl,
        token,
        input,
        confirmation,
        crypto.randomUUID(),
      );
      setMessage(`작업을 접수했습니다 · ${operation.operation_id.slice(0, 8)}`);
      setPreview(null);
      setConfirmation("");
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setBusy(false);
    }
  }

  async function inspect() {
    if (selected.length !== 1) {
      setMessage("조회 대상은 한 개만 선택하세요.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      setInspection(
        await inspectAdvancedWorkload(
          apiBaseUrl,
          token,
          selected[0],
          feature,
        ),
      );
      setMessage("PVE의 현재 구성을 읽었습니다.");
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setBusy(false);
    }
  }

  const inspectable = ["SNAPSHOT", "HA", "FIREWALL_SDN"].includes(feature);

  return (
    <div className="advanced-operations">
      <section className="advanced-feature-strip" aria-label="고급 기능 상태">
        {capabilities.map((item) => (
          <button
            type="button"
            key={item.feature}
            className={item.feature === feature ? "active" : ""}
            onClick={() => chooseFeature(item.feature)}
          >
            <span>{featureLabels[item.feature]}</span>
            <small>{item.enabled ? item.mode : "비활성"}</small>
          </button>
        ))}
      </section>

      <div className="advanced-workspace-grid">
        <section className="advanced-targets">
          <header>
            <div>
              <p className="eyebrow">Immutable target snapshot</p>
              <h2>실행 대상</h2>
            </div>
            <strong>{selected.length} selected</strong>
          </header>
          <div className="advanced-target-list">
            {visibleWorkloads.map((workload) => (
              <label key={workload.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(workload.id)}
                  onChange={() => toggleTarget(workload.id)}
                />
                <span>
                  <strong>{workload.name ?? workload.vmid}</strong>
                  <small>
                    {workload.kind} · {workload.node} · {workload.power_state}
                  </small>
                </span>
              </label>
            ))}
          </div>
        </section>

        <section className="advanced-control">
          <header>
            <div>
              <p className="eyebrow">Preview before execute</p>
              <h2>{featureLabels[feature]}</h2>
            </div>
            <span className={capability?.enabled ? "feature-on" : "feature-off"}>
              {capability?.enabled ? "Enabled" : "Disabled"}
            </span>
          </header>
          <form onSubmit={requestPreview}>
            <label>
              작업
              <select value={action} onChange={(event) => { setAction(event.target.value); setPreview(null); }}>
                {(capability?.actions ?? []).map((item) => (
                  <option key={item}>{item}</option>
                ))}
              </select>
            </label>
            {feature === "SNAPSHOT" && (
              <>
                <label>Snapshot 이름<input name="snapshot_name" required pattern="[A-Za-z][A-Za-z0-9_-]{0,39}" /></label>
                <label className="advanced-check"><input name="include_memory" type="checkbox" />memory 포함</label>
              </>
            )}
            {(feature === "MIGRATION" || (feature === "NODE_MAINTENANCE" && action === "DRAIN")) && (
              <>
                <label>대상 node<input name="target_node" required /></label>
                {feature === "MIGRATION" && (
                  <>
                    <label>대상 storage<input name="target_storage" /></label>
                    <label>migration network<input name="target_network" /></label>
                    {["local_disks_compatible", "passthrough_free", "ha_compatible", "replication_compatible"].map((name) => (
                      <label className="advanced-check" key={name}><input name={name} type="checkbox" />{name}</label>
                    ))}
                  </>
                )}
                {feature === "NODE_MAINTENANCE" && (
                  <>
                    <label className="advanced-check"><input name="backup_confirmed" type="checkbox" />백업 상태 확인</label>
                    <label className="advanced-check"><input name="customer_notification_confirmed" type="checkbox" />고객 downtime 알림 확인</label>
                  </>
                )}
              </>
            )}
            {feature === "HA" && (
              <>
                <label>요청 상태<select name="requested_state"><option>started</option><option>stopped</option><option>ignored</option><option>disabled</option></select></label>
                <label>HA group<input name="group" /></label>
              </>
            )}
            {feature === "GUEST_CONFIG" && (
              <div className="advanced-config-fields">
                <label>vCPU<input name="cores" type="number" min="1" max="512" /></label>
                <label>Memory MiB<input name="memory_mib" type="number" min="128" /></label>
                <label>Bridge<input name="bridge" /></label>
                <label>VLAN<input name="vlan_tag" type="number" min="1" max="4094" /></label>
                <label>Boot order<input name="boot_order" /></label>
              </div>
            )}
            <div className="advanced-form-actions">
              {inspectable && <button type="button" className="secondary" disabled={busy || !capability?.enabled} onClick={() => void inspect()}>현재 구성 조회</button>}
              <button disabled={busy || !capability?.enabled}>실행 전 검사</button>
            </div>
          </form>
          {preview && (
            <div className="advanced-preview" role="status">
              <h3>{preview.executable ? "실행 가능" : "실행 차단"}</h3>
              {preview.warnings.map((item) => <p key={item} className="warning">경고 · {item}</p>)}
              {preview.blockers.map((item) => <p key={item} className="blocker">차단 · {item}</p>)}
              <p>{preview.targets.length}개 대상의 이름·node·상태·version이 고정됩니다.</p>
              {preview.executable && canWrite && (
                <div className="advanced-confirm">
                  <label>확인 문구 <code>{preview.required_confirmation}</code><input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>
                  <button type="button" disabled={busy || confirmation !== preview.required_confirmation} onClick={() => void execute()}>작업 접수</button>
                </div>
              )}
            </div>
          )}
          {inspection && (
            <div className="advanced-inspection">
              <h3>현재 PVE 구성 · {inspection.scope}</h3>
              <pre>{JSON.stringify({ items: inspection.items, related: inspection.related }, null, 2)}</pre>
            </div>
          )}
          <p className="admin-inline-status" aria-live="polite">{busy ? "처리 중…" : message}</p>
        </section>
      </div>
    </div>
  );
}

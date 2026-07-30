"use client";

import type {
  CustomerMetricPoint,
  CustomerMetricRange,
  CustomerMetricSeries,
  CustomerVmDetail,
} from "@/lib/customer-api";

type Series = {
  label: string;
  color: string;
  value: (point: CustomerMetricPoint) => number | null;
};

const rangeLabels: Record<CustomerMetricRange, string> = {
  day: "24시간",
  month: "30일",
  year: "1년",
};

function formatBytes(value: number | null): string {
  if (value === null) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let result = value;
  let unit = 0;
  while (result >= 1024 && unit < units.length - 1) {
    result /= 1024;
    unit += 1;
  }
  return `${result.toFixed(result >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function formatTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatUptime(seconds: number | null): string {
  if (seconds === null) return "—";
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3600);
  return `${days}일 ${hours}시간`;
}

function path(
  points: CustomerMetricPoint[],
  series: Series,
  maximum: number,
): string {
  if (!points.length) return "";
  const start = new Date(points[0].time).getTime();
  const end = new Date(points.at(-1)?.time ?? points[0].time).getTime();
  const span = Math.max(1, end - start);
  let drawing = false;
  return points.map((point) => {
    const value = series.value(point);
    if (value === null || !Number.isFinite(value)) {
      drawing = false;
      return "";
    }
    const x = ((new Date(point.time).getTime() - start) / span) * 400;
    const y = 100 - (Math.max(0, Math.min(maximum, value)) / maximum) * 100;
    const command = drawing ? "L" : "M";
    drawing = true;
    return `${command}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
}

function CustomerMetricChart({
  title,
  unit,
  points,
  series,
  fixedMaximum,
}: {
  title: string;
  unit: string;
  points: CustomerMetricPoint[];
  series: Series[];
  fixedMaximum?: number;
}) {
  const values = series.flatMap((item) => points
    .map(item.value)
    .filter((value): value is number => value !== null && Number.isFinite(value)));
  const maximum = fixedMaximum ?? Math.max(1, ...values) * 1.1;
  const latest = [...points].reverse().map(series[0].value)
    .find((value): value is number => value !== null && Number.isFinite(value));
  return <section className="customer-metric-chart" aria-label={`${title} 시계열`}>
    <header><span><strong>{title}</strong><small>{unit}</small></span><b>{latest === undefined ? "—" : unit === "%" ? `${(latest * 100).toFixed(1)}%` : formatBytes(latest)}</b></header>
    {values.length ? <svg viewBox="0 0 400 100" preserveAspectRatio="none" role="img" aria-label={`${title} 성능 그래프`}>
      <line x1="0" y1="25" x2="400" y2="25" /><line x1="0" y1="50" x2="400" y2="50" /><line x1="0" y1="75" x2="400" y2="75" />
      {series.map((item) => <path key={item.label} d={path(points, item, maximum)} style={{ stroke: item.color }} />)}
    </svg> : <div className="customer-metric-empty"><strong>수집된 값 없음</strong><span>지원되지 않거나 일부 구간의 지표가 비어 있습니다.</span></div>}
    <footer>{series.map((item) => <span key={item.label}><i style={{ background: item.color }} />{item.label}</span>)}</footer>
  </section>;
}

export function CustomerVmDetailView({
  detail,
  metrics,
  range,
  loading,
  onRange,
  onClose,
}: {
  detail: CustomerVmDetail;
  metrics: CustomerMetricSeries | null;
  range: CustomerMetricRange;
  loading: boolean;
  onRange: (range: CustomerMetricRange) => void;
  onClose: () => void;
}) {
  const points = metrics?.items ?? [];
  return <section className="customer-vm-detail" aria-labelledby="customer-vm-detail-title">
    <header className="customer-vm-detail-header">
      <div><p className="eyebrow">Virtual machine detail</p><h1 id="customer-vm-detail-title">{detail.name}</h1><span>{detail.organization_name} · {detail.power_state}</span></div>
      <button type="button" onClick={onClose}>VM 목록으로</button>
    </header>
    {detail.is_stale && <div className="customer-inline-alert" role="status">마지막 확인 정보가 오래되어 현재 값과 차이가 있을 수 있습니다.</div>}
    <div className="customer-detail-facts">
      <article><small>vCPU</small><strong>{detail.cpu_cores ?? "—"}</strong></article>
      <article><small>메모리</small><strong>{formatBytes(detail.memory_bytes)}</strong></article>
      <article><small>디스크</small><strong>{formatBytes(detail.disk_bytes)}</strong></article>
      <article><small>Uptime</small><strong>{formatUptime(detail.uptime_seconds ?? null)}</strong></article>
      <article><small>IP 주소</small><strong>{detail.assigned_ip_addresses.join(", ") || "미할당"}</strong></article>
      <article><small>마지막 확인</small><strong>{formatTime(detail.observed_at)}</strong></article>
    </div>
    <div className="customer-metric-toolbar">
      <div><h2>성능 지표</h2><span>{metrics?.partial ? "일부 구간 누락" : loading ? "불러오는 중" : "수집 정상"}</span></div>
      <nav aria-label="성능 지표 조회 기간">{(Object.keys(rangeLabels) as CustomerMetricRange[]).map((item) => <button key={item} type="button" className={range === item ? "active" : ""} aria-pressed={range === item} onClick={() => onRange(item)}>{rangeLabels[item]}</button>)}</nav>
    </div>
    <div className="customer-metric-grid">
      <CustomerMetricChart title="CPU" unit="%" points={points} fixedMaximum={1} series={[{ label: "평균", color: "var(--accent)", value: (item) => item.cpu_avg }, { label: "최대", color: "#a66b45", value: (item) => item.cpu_max }]} />
      <CustomerMetricChart title="Memory" unit="bytes" points={points} fixedMaximum={detail.memory_bytes ?? undefined} series={[{ label: "평균", color: "#987b35", value: (item) => item.memory_used_avg }, { label: "최대", color: "#8f4650", value: (item) => item.memory_used_max }]} />
      <CustomerMetricChart title="Disk I/O" unit="B/s" points={points} series={[{ label: "읽기 평균", color: "var(--accent)", value: (item) => item.disk_read_avg }, { label: "쓰기 최대", color: "#6688a1", value: (item) => item.disk_write_max }]} />
      <CustomerMetricChart title="Network" unit="B/s" points={points} series={[{ label: "수신 평균", color: "var(--accent)", value: (item) => item.network_receive_avg }, { label: "송신 최대", color: "#6688a1", value: (item) => item.network_transmit_max }]} />
    </div>
    <div className="customer-detail-grid">
      <section><h2>최근 작업</h2>{detail.recent_jobs.map((job) => <article key={job.id}><strong>{job.action} · {job.status}</strong><span>{job.error_summary ?? (job.finished_at ? "작업이 완료되었습니다." : "작업을 처리하고 있습니다.")}</span><time>{formatTime(job.requested_at)}</time></article>)}{!detail.recent_jobs.length && <p>최근 요청한 작업이 없습니다.</p>}</section>
      <section><h2>상태 변화</h2>{detail.recent_state_changes.map((item) => <article key={item.id}><strong>{item.summary}</strong><time>{formatTime(item.observed_at)}</time></article>)}{!detail.recent_state_changes.length && <p>현재 소유 기간의 상태 변화가 없습니다.</p>}</section>
      <section><h2>백업 상태</h2>{detail.recent_backup ? <article><strong>{detail.recent_backup.status}</strong><span>최근 완료 {formatTime(detail.recent_backup.completed_at)}</span><span>예정 {formatTime(detail.recent_backup.scheduled_for)}</span></article> : <p>표시할 백업 상태가 없습니다.</p>}</section>
      <section><h2>예정된 유지보수</h2>{detail.upcoming_maintenance.map((item) => <article key={item.id}><strong>{item.name}</strong><span>{formatTime(item.starts_at)} – {formatTime(item.ends_at)}</span></article>)}{!detail.upcoming_maintenance.length && <p>예정된 유지보수가 없습니다.</p>}</section>
    </div>
  </section>;
}

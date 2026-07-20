"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getNodeMetrics,
  NodeMetricPoint,
  NodeMetricRange,
  NodeMetricSeries,
} from "@/lib/admin-api";

type MetricSeriesDefinition = {
  label: string;
  color: string;
  value: (point: NodeMetricPoint) => number | null;
};

type MetricDefinition = {
  id: string;
  title: string;
  unit: string;
  fixedMax?: number;
  format: (value: number) => string;
  series: MetricSeriesDefinition[];
};

const RANGE_OPTIONS: Array<{ value: NodeMetricRange; label: string }> = [
  { value: "hour", label: "1시간" },
  { value: "six_hours", label: "6시간" },
  { value: "day", label: "24시간" },
  { value: "week", label: "7일" },
];

const percent = (value: number) => `${value.toFixed(value >= 10 ? 0 : 1)}%`;
const rate = (value: number) => {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB/s`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB/s`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB/s`;
  return `${value.toFixed(0)} B/s`;
};

const METRICS: MetricDefinition[] = [
  {
    id: "cpu",
    title: "CPU Usage",
    unit: "사용률",
    fixedMax: 100,
    format: percent,
    series: [{ label: "CPU", color: "var(--accent)", value: (point) => point.cpu_usage === null ? null : point.cpu_usage * 100 }],
  },
  {
    id: "load",
    title: "Server Load",
    unit: "load average",
    format: (value) => value.toFixed(2),
    series: [{ label: "Load", color: "#727d69", value: (point) => point.server_load }],
  },
  {
    id: "memory",
    title: "Memory Usage",
    unit: "사용률",
    fixedMax: 100,
    format: percent,
    series: [{
      label: "Memory",
      color: "#987b35",
      value: (point) => point.memory_used_bytes !== null && point.memory_total_bytes
        ? (point.memory_used_bytes / point.memory_total_bytes) * 100
        : null,
    }],
  },
  {
    id: "network",
    title: "Network Traffic",
    unit: "초당 전송량",
    format: rate,
    series: [
      { label: "수신", color: "var(--accent)", value: (point) => point.network_receive_bps },
      { label: "송신", color: "#6688a1", value: (point) => point.network_transmit_bps },
    ],
  },
  {
    id: "cpu-pressure",
    title: "CPU Pressure Stall",
    unit: "some avg10",
    fixedMax: 100,
    format: percent,
    series: [{ label: "Some", color: "#a66b45", value: (point) => point.cpu_pressure_some }],
  },
  {
    id: "io-pressure",
    title: "IO Pressure Stall",
    unit: "avg10",
    fixedMax: 100,
    format: percent,
    series: [
      { label: "Some", color: "#a66b45", value: (point) => point.io_pressure_some },
      { label: "Full", color: "#8f4650", value: (point) => point.io_pressure_full },
    ],
  },
  {
    id: "memory-pressure",
    title: "Memory Pressure Stall",
    unit: "avg10",
    fixedMax: 100,
    format: percent,
    series: [
      { label: "Some", color: "#987b35", value: (point) => point.memory_pressure_some },
      { label: "Full", color: "#8f4650", value: (point) => point.memory_pressure_full },
    ],
  },
];

function linePath(
  points: NodeMetricPoint[],
  definition: MetricSeriesDefinition,
  max: number,
  width: number,
  height: number,
) {
  if (!points.length) return "";
  const minTime = points[0].time;
  const maxTime = points.at(-1)?.time ?? minTime;
  const span = Math.max(1, maxTime - minTime);
  let drawing = false;
  return points.map((point) => {
    const value = definition.value(point);
    if (value === null || !Number.isFinite(value)) {
      drawing = false;
      return "";
    }
    const x = ((point.time - minTime) / span) * width;
    const y = height - (Math.min(max, Math.max(0, value)) / max) * height;
    const command = drawing ? "L" : "M";
    drawing = true;
    return `${command}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
}

function MetricChart({ metric, points, compact = false, state = null }: {
  metric: MetricDefinition;
  points: NodeMetricPoint[];
  compact?: boolean;
  state?: "loading" | "error" | null;
}) {
  const values = metric.series.flatMap((series) => points
    .map(series.value)
    .filter((value): value is number => value !== null && Number.isFinite(value)));
  const unsupported = values.length === 0;
  const latest = [...points].reverse().map(metric.series[0].value)
    .find((value): value is number => value !== null && Number.isFinite(value));
  const maximum = metric.fixedMax ?? Math.max(1, ...values) * 1.12;
  const height = compact ? 62 : 104;

  return <section className={`metric-chart${compact ? " compact" : ""}`} aria-label={metric.title}>
    <header><div><strong>{metric.title}</strong><small>{metric.unit}</small></div><b>{latest === undefined ? "—" : metric.format(latest)}</b></header>
    {unsupported ? <div className="metric-chart-empty"><span>{state === "loading" ? "불러오는 중" : state === "error" ? "조회 실패" : "지원 안 함"}</span><small>{state === "loading" ? "Proxmox RRD 시계열을 요청하고 있습니다." : state === "error" ? "다음 자동 갱신 때 다시 시도합니다." : "이 노드가 해당 RRD 지표를 제공하지 않습니다."}</small></div> : <>
      <svg viewBox={`0 0 400 ${height}`} preserveAspectRatio="none" role="img" aria-label={`${metric.title} 시계열`}>
        <line x1="0" y1={height * 0.25} x2="400" y2={height * 0.25} />
        <line x1="0" y1={height * 0.5} x2="400" y2={height * 0.5} />
        <line x1="0" y1={height * 0.75} x2="400" y2={height * 0.75} />
        {metric.series.map((series) => <path key={series.label} d={linePath(points, series, maximum, 400, height)} style={{ stroke: series.color }} />)}
      </svg>
      {metric.series.length > 1 && <footer>{metric.series.map((series) => <span key={series.label}><i style={{ background: series.color }} />{series.label}</span>)}</footer>}
    </>}
  </section>;
}

export function ClusterMetricsPanel({ apiBaseUrl, token, clusterId, nodes }: {
  apiBaseUrl: string;
  token: string;
  clusterId: string;
  nodes: Array<{ node: string; status: string | null }>;
}) {
  const preferredNode = nodes.find((node) => node.status?.toLowerCase() === "online")?.node ?? nodes[0]?.node ?? "";
  const [selectedNode, setSelectedNode] = useState(preferredNode);
  const [range, setRange] = useState<NodeMetricRange>("hour");
  const [metrics, setMetrics] = useState<NodeMetricSeries | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const activeRequest = useRef<AbortController | null>(null);
  const activeNode = nodes.some((node) => node.node === selectedNode) ? selectedNode : preferredNode;

  const load = useCallback(async () => {
    if (!activeNode || document.visibilityState === "hidden") return;
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    setLoading(true);
    try {
      const result = await getNodeMetrics(apiBaseUrl, token, clusterId, activeNode, range, controller.signal);
      setMetrics(result);
      setError("");
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setError("시계열 자원을 불러오지 못했습니다.");
    } finally {
      if (activeRequest.current === controller) {
        activeRequest.current = null;
        setLoading(false);
      }
    }
  }, [activeNode, apiBaseUrl, clusterId, range, token]);

  useEffect(() => {
    const initialTimer = window.setTimeout(() => void load(), 0);
    const timer = window.setInterval(() => void load(), 60_000);
    const onVisibility = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      activeRequest.current?.abort();
    };
  }, [load]);

  const points = metrics?.items ?? [];
  const miniMetrics = useMemo(() => METRICS.slice(0, 4), []);
  const chartState = metrics ? null : error ? "error" as const : "loading" as const;

  if (!nodes.length) return null;
  return <div className="cluster-history">
    <div className="cluster-history-toolbar">
      <div><span>Node summary</span><select value={activeNode} onChange={(event) => { setMetrics(null); setError(""); setSelectedNode(event.target.value); }} aria-label="그래프 노드 선택">{nodes.map((node) => <option value={node.node} key={node.node}>{node.node}</option>)}</select></div>
      <div className="metric-ranges" aria-label="그래프 조회 범위">{RANGE_OPTIONS.map((option) => <button type="button" className={range === option.value ? "active" : ""} aria-pressed={range === option.value} onClick={() => { setMetrics(null); setError(""); setRange(option.value); }} key={option.value}>{option.label}</button>)}</div>
      <span className={`metric-fetch-state${error ? " error" : ""}`}>{loading ? "60초 갱신 · 갱신 중" : error ? "60초 갱신 · 재시도 대기" : metrics ? `60초 갱신 · ${new Date(metrics.observed_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}` : "60초 갱신 · 대기 중"}</span>
    </div>
    <div className="metric-mini-grid">{miniMetrics.map((metric) => <MetricChart key={metric.id} metric={metric} points={points} compact state={chartState} />)}</div>
    <button type="button" className="metric-detail-toggle" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}><span>{expanded ? "상세 그래프 접기" : "상세 그래프 7개 보기"}</span><b>{expanded ? "−" : "+"}</b></button>
    {expanded && <div className="metric-detail-grid">{METRICS.map((metric) => <MetricChart key={metric.id} metric={metric} points={points} state={chartState} />)}</div>}
  </div>;
}

// Per-case time-travel debugger — plots cost / latency / tokens / pass-fail
// over the case's run history. Hand-rolled SVG, no chart-library dep.

import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";

import { api } from "@/api/client";
import { Card } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";

export interface CaseTimelinePoint {
  run_id: number;
  timestamp: string;
  passed: boolean | null;
  is_regression?: boolean | null;
  cost_usd?: number | null;
  latency_ms?: number | null;
}

export interface CaseTimelineResponse {
  points: CaseTimelinePoint[];
}

type MetricKey = "cost_usd" | "latency_ms";
const METRICS: { key: MetricKey; label: string; format: (v: number) => string }[] = [
  {
    key: "cost_usd",
    label: "cost",
    format: (v) => (v < 0.0001 ? `$${v.toExponential(1)}` : `$${v.toFixed(4)}`),
  },
  {
    key: "latency_ms",
    label: "latency",
    format: (v) => (v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(2)} s`),
  },
];

export function CaseTimelinePage() {
  const { suiteId, caseName: rawCase } = useParams();
  const caseName = decodeURIComponent(rawCase ?? "");
  const suite = Number(suiteId);

  const q = useQuery<CaseTimelineResponse>({
    queryKey: ["case-timeline", suite, caseName],
    queryFn: () => api.caseTimeline(suite, caseName),
    enabled: !Number.isNaN(suite) && caseName.length > 0,
  });

  const sorted = useMemo(() => {
    const points = q.data?.points ?? [];
    return [...points].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  }, [q.data]);

  const [selected, setSelected] = useState<number | null>(null);

  if (q.isLoading) {
    return (
      <Card className="p-12 text-center text-sm text-muted-foreground">
        <Spinner className="mx-auto mb-2" /> Loading…
      </Card>
    );
  }
  if (q.error) {
    return (
      <Card className="border-destructive/40 p-6">
        <p className="text-sm text-destructive">{String(q.error)}</p>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <Link
        to="/"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> Back to projects
      </Link>

      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">
          Timeline · <span className="font-mono">{caseName}</span>
        </h1>
        <span className="font-mono text-xs text-muted-foreground">
          {sorted.length} run{sorted.length === 1 ? "" : "s"}
        </span>
      </div>

      {sorted.length === 0 ? (
        <Card className="p-12 text-center text-sm italic text-muted-foreground">
          No runs recorded for this case yet.
        </Card>
      ) : (
        <Card className="space-y-1 p-4">
          {METRICS.map((m) => (
            <MetricPanel
              key={m.key}
              metric={m}
              points={sorted}
              selectedIdx={selected}
              onSelect={setSelected}
            />
          ))}
          <PassFailStrip
            points={sorted}
            selectedIdx={selected}
            onSelect={setSelected}
          />
          <TimeAxis points={sorted} />
          {selected != null && sorted[selected] && (
            <SelectedDetail point={sorted[selected]} onClose={() => setSelected(null)} />
          )}
        </Card>
      )}
    </div>
  );
}

interface PanelProps {
  metric: (typeof METRICS)[number];
  points: CaseTimelinePoint[];
  selectedIdx: number | null;
  onSelect: (idx: number | null) => void;
}

function MetricPanel({ metric, points, selectedIdx, onSelect }: PanelProps) {
  const w = 720;
  const h = 70;
  const padL = 60;
  const padR = 12;
  const padT = 8;
  const padB = 8;

  const values = points.map((p) => p[metric.key]);
  const numericValues = values.filter((v): v is number => v != null && Number.isFinite(v));
  if (numericValues.length === 0) return null;
  let min = Math.min(...numericValues);
  let max = Math.max(...numericValues);
  if (min === max) {
    min = min - 1;
    max = max + 1;
  }
  const innerW = w - padL - padR;
  const innerH = h - padT - padB;
  const xStep = points.length > 1 ? innerW / (points.length - 1) : 0;
  const xAt = (i: number) => padL + i * xStep;
  const yAt = (v: number | null | undefined) => {
    if (v == null || !Number.isFinite(v)) return null;
    return padT + (1 - (v - min) / (max - min)) * innerH;
  };

  // Build path, breaking at nulls.
  const segments: string[] = [];
  let current = "";
  values.forEach((v, i) => {
    const y = yAt(v);
    if (y == null) {
      if (current) segments.push(current);
      current = "";
      return;
    }
    const cmd = current === "" ? "M" : "L";
    current += `${current === "" ? "" : " "}${cmd} ${xAt(i).toFixed(1)} ${y.toFixed(1)}`;
  });
  if (current) segments.push(current);

  return (
    <div className="grid grid-cols-[80px_1fr] items-stretch gap-0 border-b border-border last:border-b-0">
      <div className="flex flex-col justify-between border-r border-border bg-muted/20 px-2 py-2 font-mono text-[10px] text-muted-foreground">
        <span className="font-semibold">{metric.label}</span>
        <div className="space-y-0.5">
          <div>{metric.format(max)}</div>
          <div className="opacity-70">{metric.format(min)}</div>
        </div>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-[70px] w-full">
        <line
          x1={padL}
          y1={h - padB}
          x2={w - padR}
          y2={h - padB}
          className="stroke-border"
        />
        {segments.map((d, i) => (
          <path
            key={i}
            d={d}
            className={cn(
              "fill-none stroke-[1.5]",
              metric.key === "cost_usd" ? "stroke-primary" : "stroke-[hsl(var(--warning))]",
            )}
          />
        ))}
        {points.map((p, i) => {
          const v = p[metric.key];
          const y = yAt(v);
          if (y == null) return null;
          const isSel = i === selectedIdx;
          const isReg = !!p.is_regression;
          return (
            <circle
              key={i}
              cx={xAt(i)}
              cy={y}
              r={isSel ? 5 : 3}
              className={cn(
                "cursor-pointer",
                metric.key === "cost_usd" ? "fill-primary" : "fill-[hsl(var(--warning))]",
                isReg && "stroke-destructive stroke-[2]",
                isSel && "stroke-foreground stroke-[2]",
              )}
              onClick={() => onSelect(i)}
            >
              <title>{`${metric.label}: ${v != null ? metric.format(v) : "—"} · ${p.timestamp}`}</title>
            </circle>
          );
        })}
      </svg>
    </div>
  );
}

function PassFailStrip({
  points,
  selectedIdx,
  onSelect,
}: {
  points: CaseTimelinePoint[];
  selectedIdx: number | null;
  onSelect: (i: number | null) => void;
}) {
  const w = 720;
  const h = 30;
  const padL = 60;
  const padR = 12;
  const innerW = w - padL - padR;
  const xStep = points.length > 1 ? innerW / (points.length - 1) : 0;
  const cy = h / 2;
  return (
    <div className="grid grid-cols-[80px_1fr] items-stretch gap-0 border-b border-border">
      <div className="flex items-center border-r border-border bg-muted/20 px-2 py-1 font-mono text-[10px] font-semibold text-muted-foreground">
        pass / fail
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-[30px] w-full">
        <line x1={padL} y1={cy} x2={w - padR} y2={cy} className="stroke-border" />
        {points.map((p, i) => {
          const isSel = i === selectedIdx;
          const fill =
            p.passed === true
              ? "fill-[hsl(var(--success))]"
              : p.passed === false
                ? "fill-destructive"
                : "fill-muted-foreground";
          return (
            <circle
              key={i}
              cx={padL + i * xStep}
              cy={cy}
              r={isSel ? 7 : 5}
              className={cn(
                "cursor-pointer",
                fill,
                p.is_regression && "stroke-destructive stroke-[2]",
                isSel && "stroke-foreground stroke-[2]",
              )}
              onClick={() => onSelect(i)}
            >
              <title>{`${p.passed === true ? "pass" : p.passed === false ? "fail" : "?"} · ${p.timestamp}`}</title>
            </circle>
          );
        })}
      </svg>
    </div>
  );
}

function TimeAxis({ points }: { points: CaseTimelinePoint[] }) {
  const w = 720;
  const h = 24;
  const padL = 60;
  const padR = 12;
  const innerW = w - padL - padR;
  const xStep = points.length > 1 ? innerW / (points.length - 1) : 0;
  // Pick a few label indices
  const picks = new Set<number>([0, points.length - 1]);
  const stride = Math.max(1, Math.floor(points.length / 4));
  for (let i = stride; i < points.length - 1; i += stride) picks.add(i);
  const picked = [...picks].sort((a, b) => a - b);
  return (
    <div className="grid grid-cols-[80px_1fr] items-stretch gap-0">
      <div />
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-[24px] w-full">
        {picked.map((i) => {
          const x = padL + i * xStep;
          const d = new Date(points[i].timestamp);
          const label = isNaN(d.getTime())
            ? "?"
            : `${(d.getMonth() + 1).toString().padStart(2, "0")}-${d
                .getDate()
                .toString()
                .padStart(2, "0")} ${d.getHours().toString().padStart(2, "0")}:${d
                .getMinutes()
                .toString()
                .padStart(2, "0")}`;
          return (
            <text
              key={i}
              x={x}
              y={14}
              textAnchor="middle"
              className="fill-muted-foreground font-mono text-[9px]"
            >
              {label}
            </text>
          );
        })}
      </svg>
    </div>
  );
}

function SelectedDetail({
  point,
  onClose,
}: {
  point: CaseTimelinePoint;
  onClose: () => void;
}) {
  return (
    <div className="mt-2 rounded-md border border-border bg-muted/20">
      <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Run detail
        </span>
        <button
          type="button"
          onClick={onClose}
          className="text-muted-foreground hover:text-foreground"
        >
          ✕
        </button>
      </div>
      <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1 px-3 py-2 font-mono text-xs">
        <dt className="text-muted-foreground">run id</dt>
        <dd>{point.run_id}</dd>
        <dt className="text-muted-foreground">timestamp</dt>
        <dd>{point.timestamp}</dd>
        <dt className="text-muted-foreground">passed</dt>
        <dd>
          {point.passed === true ? "✓ pass" : point.passed === false ? "✗ fail" : "—"}
          {point.is_regression && <span className="ml-2 text-destructive">regression</span>}
        </dd>
        {point.cost_usd != null && (
          <>
            <dt className="text-muted-foreground">cost</dt>
            <dd>{METRICS[0].format(point.cost_usd)}</dd>
          </>
        )}
        {point.latency_ms != null && (
          <>
            <dt className="text-muted-foreground">latency</dt>
            <dd>{METRICS[1].format(point.latency_ms)}</dd>
          </>
        )}
      </dl>
    </div>
  );
}

// Suite Health dashboard — grid of suite cards with pass rate sparkline,
// regression badge, cost trend, and last-run timestamp.
//
// Mounted at /projects/:projectId/health. Backend endpoint is
// GET /api/projects/{id}/suites/health.

import { useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";

import { api } from "@/api/client";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";

export interface SuiteRunPoint {
  timestamp: string;
  pass_rate: number;
  cost_usd?: number | null;
}

export interface SuiteHealthSummary {
  id: number;
  name: string;
  last_run_id?: number | null;
  last_run_at?: string | null;
  cases_passing?: number | null;
  cases_total?: number | null;
  regression_count?: number | null;
  current_cost_usd?: number | null;
  previous_cost_usd?: number | null;
  recent_runs?: SuiteRunPoint[];
}

export interface SuiteHealthResponse {
  suites: SuiteHealthSummary[];
}

export function SuiteHealthPage() {
  const { id } = useParams();
  const projectId = Number(id);
  const navigate = useNavigate();

  const q = useQuery<SuiteHealthResponse>({
    queryKey: ["suite-health", projectId],
    queryFn: () => api.suiteHealth(projectId),
    enabled: !Number.isNaN(projectId),
  });

  if (q.isLoading) {
    return (
      <Card className="p-12 text-center text-sm text-muted-foreground">
        <Spinner className="mx-auto mb-2" /> Loading suite health…
      </Card>
    );
  }
  if (q.error) {
    return (
      <Card className="border-destructive/40 p-6">
        <p className="text-sm text-destructive">Couldn't load: {String(q.error)}</p>
      </Card>
    );
  }
  const suites = q.data?.suites ?? [];

  return (
    <div className="space-y-6">
      <Link
        to={`/projects/${projectId}`}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> Back to project
      </Link>

      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Suite health</h1>
        <span className="font-mono text-xs text-muted-foreground">
          {suites.length} suite{suites.length === 1 ? "" : "s"}
        </span>
      </div>

      {suites.length === 0 ? (
        <Card className="p-12 text-center text-sm italic text-muted-foreground">
          No suites yet. Once you record a run, suites will appear here.
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {suites.map((s) => (
            <SuiteCard
              key={s.id}
              suite={s}
              onOpen={() => {
                // Take the user to the most recent run for this suite so
                // they can drill into the regressed case from the run
                // detail view. Falls back to the project page if the suite
                // has no runs yet.
                if (s.last_run_id != null) {
                  navigate(`/runs/${s.last_run_id}`);
                } else {
                  navigate(`/projects/${projectId}`);
                }
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function SuiteCard({
  suite,
  onOpen,
}: {
  suite: SuiteHealthSummary;
  onOpen: () => void;
}) {
  const hasRun = (suite.cases_total ?? 0) > 0 || !!suite.last_run_at;
  const regressions = suite.regression_count ?? 0;
  const total = suite.cases_total ?? 0;
  const passing = suite.cases_passing ?? 0;
  const passRate = total > 0 ? passing / total : 0;
  const state: "pass" | "regression" | "empty" = !hasRun
    ? "empty"
    : regressions > 0
      ? "regression"
      : "pass";

  const trend = useMemo(
    () => classifyCostTrend(suite.current_cost_usd, suite.previous_cost_usd),
    [suite.current_cost_usd, suite.previous_cost_usd],
  );

  if (state === "empty") {
    return (
      <Card className="cursor-default border-l-[3px] border-l-muted-foreground/40 p-4 opacity-80">
        <h3 className="font-mono text-sm font-medium">{suite.name}</h3>
        <p className="mt-1 text-xs italic text-muted-foreground">
          No runs yet for this suite.
        </p>
      </Card>
    );
  }

  return (
    <Card
      onClick={onOpen}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      className={cn(
        "cursor-pointer border-l-[3px] p-4 transition-shadow hover:shadow-md",
        state === "pass" && "border-l-[hsl(var(--success))]",
        state === "regression" && "border-l-destructive",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-mono text-sm font-medium">{suite.name}</h3>
        {regressions > 0 && (
          <Badge tone="danger">
            {regressions} regression{regressions === 1 ? "" : "s"}
          </Badge>
        )}
      </div>

      <div className="mt-2 flex items-baseline justify-between text-xs">
        <span>
          <span className="font-mono font-semibold">
            {passing}/{total}
          </span>{" "}
          <span className="text-muted-foreground">passing</span>
        </span>
        <span className="font-mono text-muted-foreground">
          {suite.last_run_at ? formatRelativeTime(suite.last_run_at) : "—"}
        </span>
      </div>

      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
        <span
          className={cn(
            "block h-full rounded-full transition-all",
            passRate >= 1
              ? "bg-[hsl(var(--success))]"
              : passRate >= 0.8
                ? "bg-[hsl(var(--warning))]"
                : "bg-destructive",
          )}
          style={{ width: `${Math.max(0, Math.min(1, passRate)) * 100}%` }}
        />
      </div>

      <div className="mt-3 flex items-center gap-2">
        <Sparkline points={suite.recent_runs ?? []} />
        <span className="font-mono text-[10px] text-muted-foreground">
          last {suite.recent_runs?.length ?? 0} run
          {(suite.recent_runs?.length ?? 0) === 1 ? "" : "s"}
        </span>
      </div>

      <div className="mt-2 font-mono text-xs text-muted-foreground">
        <CostTrend trend={trend} current={suite.current_cost_usd} />
      </div>
    </Card>
  );
}

function Sparkline({ points }: { points: SuiteRunPoint[] }) {
  const w = 120;
  const h = 28;
  const pad = 2;
  if (points.length === 0) {
    return (
      <svg viewBox={`0 0 ${w} ${h}`} className="h-7 w-32">
        <line
          x1={pad}
          y1={h - pad}
          x2={w - pad}
          y2={h - pad}
          className="stroke-border"
          strokeDasharray="2 2"
        />
      </svg>
    );
  }
  const innerW = w - pad * 2;
  const innerH = h - pad * 2;
  const xStep = points.length > 1 ? innerW / (points.length - 1) : 0;
  const xy = points.map((p, i) => {
    const x = pad + i * xStep;
    const y = pad + (1 - Math.max(0, Math.min(1, p.pass_rate))) * innerH;
    return [x, y] as const;
  });
  const path = xy
    .map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`)
    .join(" ");
  const last = xy[xy.length - 1];
  const lastRate = points[points.length - 1].pass_rate;
  const tone =
    lastRate >= 1
      ? "text-[hsl(var(--success))]"
      : lastRate >= 0.8
        ? "text-[hsl(var(--warning))]"
        : "text-destructive";
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className={cn("h-7 w-32", tone)}>
      <path d={path} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r={2.5} fill="currentColor" />
    </svg>
  );
}

type TrendKind = "up" | "down" | "stable" | "unknown";

function classifyCostTrend(
  current: number | null | undefined,
  previous: number | null | undefined,
): { kind: TrendKind; pct: number | null } {
  if (current == null || previous == null) return { kind: "unknown", pct: null };
  if (previous === 0) {
    if (current === 0) return { kind: "stable", pct: 0 };
    return { kind: "up", pct: null };
  }
  const pct = ((current - previous) / previous) * 100;
  if (!Number.isFinite(pct)) return { kind: "unknown", pct: null };
  if (pct > 5) return { kind: "up", pct };
  if (pct < -5) return { kind: "down", pct };
  return { kind: "stable", pct };
}

function CostTrend({
  trend,
  current,
}: {
  trend: { kind: TrendKind; pct: number | null };
  current: number | null | undefined;
}) {
  if (trend.kind === "unknown") {
    return (
      <span className="italic">
        cost: {current != null ? formatCost(current) : "—"}
      </span>
    );
  }
  const arrow = trend.kind === "up" ? "↑" : trend.kind === "down" ? "↓" : "→";
  const sign = trend.pct != null && trend.pct > 0 ? "+" : "";
  const pctLabel = trend.pct == null ? "" : ` ${sign}${trend.pct.toFixed(0)}%`;
  const tone =
    trend.kind === "up"
      ? "text-destructive"
      : trend.kind === "down"
        ? "text-[hsl(var(--success))]"
        : "";
  return (
    <span>
      cost: {current != null ? formatCost(current) : "—"}{" "}
      <span className={cn("font-semibold", tone)}>
        {arrow}
        {pctLabel}
      </span>
    </span>
  );
}

function formatRelativeTime(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const diffMs = Date.now() - t;
  if (diffMs < 0) return "just now";
  const sec = Math.floor(diffMs / 1000);
  if (sec < 45) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day === 1) return "yesterday";
  if (day < 7) return `${day}d ago`;
  return new Date(t).toISOString().slice(0, 10);
}

function formatCost(usd: number): string {
  if (usd === 0) return "$0.0000";
  if (usd < 0.0001) return `$${usd.toExponential(1)}`;
  return `$${usd.toFixed(4)}`;
}

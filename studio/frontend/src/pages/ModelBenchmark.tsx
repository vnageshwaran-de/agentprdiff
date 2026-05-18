// Multi-model benchmarking — pick two models, run the same suite against
// both, show side-by-side per-case results + Pareto chart.
//
// v0 backend runs the two models sequentially via the existing executor with
// an AGENTPRDIFF_MODEL_OVERRIDE env var. If the engine adapter doesn't honor
// the override (current state), the backend returns 501 and we display the
// error inline.

import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";

import { api, ApiError } from "@/api/client";
import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/cn";

export interface ModelCaseResult {
  case_name: string;
  passed: boolean;
  cost_usd?: number | null;
  latency_ms?: number | null;
  error?: string | null;
}
export interface ModelRun {
  model: string;
  cases: ModelCaseResult[];
  total_cost_usd?: number | null;
  total_latency_ms?: number | null;
  cases_passing?: number | null;
  cases_total?: number | null;
}
export interface ModelBenchmarkResult {
  models: ModelRun[];
  suite_name: string;
  run_at?: string;
}

const DEFAULT_MODELS = [
  "gpt-4o-mini",
  "gpt-4o",
  "claude-opus-4-6",
  "claude-sonnet-4-6",
  "claude-haiku-4-5",
  "gemini-flash-latest",
];

export function ModelBenchmarkPage() {
  const { suiteId } = useParams();
  const suite = Number(suiteId);
  const [modelA, setModelA] = useState("gpt-4o-mini");
  const [modelB, setModelB] = useState("claude-haiku-4-5");
  const [result, setResult] = useState<ModelBenchmarkResult | null>(null);

  const run = useMutation({
    mutationFn: (vars: { a: string; b: string }) =>
      api.runBenchmark(suite, [vars.a, vars.b]),
    onSuccess: (data) => setResult(data),
  });

  const canRun = modelA && modelB && modelA !== modelB && !run.isPending;

  return (
    <div className="space-y-6">
      <Link
        to="/"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> Back to projects
      </Link>

      <h1 className="text-2xl font-semibold tracking-tight">Compare models</h1>

      <Card className="space-y-3 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <ModelPicker label="Model A" value={modelA} onChange={setModelA} />
          <span className="pb-2 font-mono text-sm text-muted-foreground">vs</span>
          <ModelPicker label="Model B" value={modelB} onChange={setModelB} />
          <button
            type="button"
            disabled={!canRun}
            onClick={() => run.mutate({ a: modelA, b: modelB })}
            className={cn(
              "rounded-md px-4 py-2 text-sm font-medium",
              canRun
                ? "bg-foreground text-background hover:opacity-90"
                : "bg-muted text-muted-foreground",
            )}
          >
            {run.isPending ? "Running…" : "Run benchmark ▶"}
          </button>
        </div>
        {modelA === modelB && (
          <p className="text-xs text-destructive">Pick two different models to compare.</p>
        )}
      </Card>

      {run.error && (
        <Card className="border-destructive/40 p-4 text-sm text-destructive">
          {run.error instanceof ApiError && typeof run.error.detail === "string"
            ? run.error.detail
            : String(run.error)}
        </Card>
      )}

      {result && result.models.length > 0 && (
        <>
          <ParetoChart models={result.models} />
          <PerCaseTable models={result.models} />
        </>
      )}
    </div>
  );
}

function ModelPicker({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const listId = `models-${label.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <label className="flex flex-1 flex-col gap-1">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <input
        type="text"
        list={listId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
        className="rounded-md border border-border bg-background px-2 py-1 font-mono text-sm"
      />
      <datalist id={listId}>
        {DEFAULT_MODELS.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
    </label>
  );
}

function ParetoChart({ models }: { models: ModelRun[] }) {
  const pts = models.map((m) => ({
    model: m.model,
    cost: m.total_cost_usd ?? 0,
    pass:
      (m.cases_passing ?? m.cases.filter((c) => c.passed).length) /
      Math.max(1, m.cases_total ?? m.cases.length),
  }));
  const xMax = Math.max(0.0001, ...pts.map((p) => p.cost));
  const w = 460;
  const h = 200;
  const padL = 50;
  const padR = 50;
  const padT = 20;
  const padB = 32;
  const xScale = (v: number) =>
    padL + ((v - 0) / (xMax - 0 || 1)) * (w - padL - padR);
  const yScale = (v: number) => padT + (1 - v) * (h - padT - padB);

  const winner = useMemo(() => {
    let best = 0;
    pts.forEach((p, i) => {
      if (p.pass > pts[best].pass || (p.pass === pts[best].pass && p.cost < pts[best].cost))
        best = i;
    });
    return best;
  }, [pts]);

  return (
    <Card className="p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="font-semibold">Pareto · cost vs pass rate</h3>
        <span className="font-mono text-xs italic text-muted-foreground">
          top-left = better
        </span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} className="h-[200px] w-full">
        <line x1={padL} y1={h - padB} x2={w - padR} y2={h - padB} className="stroke-border" />
        <line x1={padL} y1={padT} x2={padL} y2={h - padB} className="stroke-border" />
        {[0, 0.5, 1].map((y) => (
          <g key={y}>
            <line
              x1={padL}
              y1={yScale(y)}
              x2={w - padR}
              y2={yScale(y)}
              className="stroke-border"
              strokeDasharray="2 3"
              strokeWidth={0.5}
            />
            <text
              x={padL - 4}
              y={yScale(y) + 3}
              textAnchor="end"
              className="fill-muted-foreground font-mono text-[9px]"
            >
              {Math.round(y * 100)}%
            </text>
          </g>
        ))}
        {[0, xMax / 2, xMax].map((x, i) => (
          <text
            key={i}
            x={xScale(x)}
            y={h - padB + 14}
            textAnchor="middle"
            className="fill-muted-foreground font-mono text-[9px]"
          >
            ${x.toFixed(4)}
          </text>
        ))}
        {pts.map((p, i) => (
          <g key={p.model}>
            <circle
              cx={xScale(p.cost)}
              cy={yScale(p.pass)}
              r={i === winner ? 8 : 6}
              className={cn(
                i === 0 ? "fill-primary" : "fill-[hsl(var(--warning))]",
                "stroke-background",
                i === winner && "stroke-[hsl(var(--success))] stroke-[2.5]",
              )}
            />
            <text
              x={xScale(p.cost) + 10}
              y={yScale(p.pass) + 4}
              className={cn(
                "font-mono text-[11px]",
                i === winner ? "fill-[hsl(var(--success))] font-semibold" : "fill-foreground",
              )}
            >
              {p.model}
              {i === winner ? " ★" : ""}
            </text>
          </g>
        ))}
        <text
          x={padL + (w - padL - padR) / 2}
          y={h - 6}
          textAnchor="middle"
          className="fill-muted-foreground text-[10px]"
        >
          cost (USD)
        </text>
      </svg>
    </Card>
  );
}

function PerCaseTable({ models }: { models: ModelRun[] }) {
  const caseNames = useMemo(() => {
    const set = new Set<string>();
    const order: string[] = [];
    for (const m of models) {
      for (const c of m.cases) {
        if (!set.has(c.case_name)) {
          set.add(c.case_name);
          order.push(c.case_name);
        }
      }
    }
    return order;
  }, [models]);

  return (
    <Card className="overflow-x-auto p-0">
      <div className="border-b border-border bg-muted/20 px-4 py-2 font-semibold">
        Per-case results
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/20 text-xs uppercase tracking-wide text-muted-foreground">
            <th className="px-3 py-2 text-left">case</th>
            {models.map((m) => (
              <th key={m.model} className="px-3 py-2 text-center font-mono">
                {m.model}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr className="border-b border-border bg-muted/10">
            <th className="px-3 py-2 text-left font-mono text-xs">Σ totals</th>
            {models.map((m) => {
              const passing = m.cases_passing ?? m.cases.filter((c) => c.passed).length;
              return (
                <td key={m.model} className="px-3 py-2 text-center font-mono text-xs">
                  <div className="font-semibold">
                    {passing}/{m.cases_total ?? m.cases.length} passing
                  </div>
                  <div className="text-muted-foreground">
                    ${(m.total_cost_usd ?? 0).toFixed(4)} ·{" "}
                    {Math.round(m.total_latency_ms ?? 0)} ms
                  </div>
                </td>
              );
            })}
          </tr>
          {caseNames.map((name) => (
            <tr key={name} className="border-b border-border">
              <th className="px-3 py-2 text-left font-mono text-xs font-medium text-muted-foreground">
                {name}
              </th>
              {models.map((m) => {
                const c = m.cases.find((x) => x.case_name === name);
                if (!c)
                  return (
                    <td key={m.model} className="px-3 py-2 text-center text-muted-foreground">
                      —
                    </td>
                  );
                return (
                  <td
                    key={m.model}
                    className={cn(
                      "px-3 py-2 text-center font-mono text-xs",
                      c.passed
                        ? "bg-[hsl(var(--success))]/10"
                        : "bg-destructive/10",
                    )}
                  >
                    <span
                      className={cn(
                        "mr-2 font-semibold",
                        c.passed ? "text-[hsl(var(--success))]" : "text-destructive",
                      )}
                    >
                      {c.passed ? "✓" : "✗"}
                    </span>
                    {c.error ? (
                      <span className="text-destructive italic">err</span>
                    ) : (
                      <>
                        ${(c.cost_usd ?? 0).toFixed(4)} ·{" "}
                        {Math.round(c.latency_ms ?? 0)} ms
                      </>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

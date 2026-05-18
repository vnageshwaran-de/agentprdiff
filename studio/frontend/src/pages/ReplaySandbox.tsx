// Replay sandbox — prompt playground for one case.
//
// Live grader preview re-evaluates contains / regex_match / latency_lt_ms /
// cost_lt_usd in the browser as the user edits the output. Replay-from-step
// fires a backend call (currently a stub) for graders that depend on agent
// runtime.

import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";

import { api, ApiError } from "@/api/client";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/cn";

export interface ReplaySeed {
  case_name: string;
  suite_name: string;
  input: unknown;
  output: string;
  latency_ms: number;
  cost_usd: number;
  graders: string[];
  messages: Array<{ id: string; role: string; content: string }>;
}

type GraderKind = "live" | "needs-replay" | "deferred" | "parse-error";

interface GraderEval {
  grader: string;
  kind: GraderKind;
  pass?: boolean;
  reason?: string;
}

function evaluateGrader(
  grader: string,
  state: { output: string; latency_ms: number; cost_usd: number },
): GraderEval {
  const t = grader.trim();
  const contains = t.match(/^contains\(\s*(['"])([\s\S]+?)\1\s*\)\s*$/);
  if (contains) {
    const needle = contains[2];
    const pass = state.output.includes(needle);
    return {
      grader,
      kind: "live",
      pass,
      reason: pass
        ? `output contains ${JSON.stringify(needle)}`
        : `output does not contain ${JSON.stringify(needle)}`,
    };
  }
  const regex = t.match(/^regex_match\(\s*r?(['"])([\s\S]+?)\1\s*\)\s*$/);
  if (regex) {
    try {
      const re = new RegExp(regex[2]);
      const pass = re.test(state.output);
      return {
        grader,
        kind: "live",
        pass,
        reason: pass ? `pattern matched` : `pattern did not match`,
      };
    } catch (e) {
      return { grader, kind: "parse-error", pass: false, reason: (e as Error).message };
    }
  }
  const lat = t.match(/^latency_lt_ms\(\s*([0-9]+(?:\.[0-9]+)?)\s*\)\s*$/);
  if (lat) {
    const limit = parseFloat(lat[1]);
    return {
      grader,
      kind: "live",
      pass: state.latency_ms < limit,
      reason: `${state.latency_ms} ms ${state.latency_ms < limit ? "<" : "≥"} ${limit} ms`,
    };
  }
  const cost = t.match(/^cost_lt_usd\(\s*([0-9]+(?:\.[0-9]+)?)\s*\)\s*$/);
  if (cost) {
    const limit = parseFloat(cost[1]);
    return {
      grader,
      kind: "live",
      pass: state.cost_usd < limit,
      reason: `$${state.cost_usd.toFixed(4)} ${state.cost_usd < limit ? "<" : "≥"} $${limit.toFixed(4)}`,
    };
  }
  if (/^tool_called\(/.test(t))
    return { grader, kind: "needs-replay", reason: "tool calls happen at agent runtime" };
  if (/^semantic\(/.test(t))
    return { grader, kind: "deferred", reason: "evaluate with LLM" };
  return { grader, kind: "needs-replay", reason: "grader not understood by in-browser evaluator" };
}

export function ReplaySandboxPage() {
  const { caseRunId } = useParams();
  const id = Number(caseRunId);
  const q = useQuery<ReplaySeed>({
    queryKey: ["replay-seed", id],
    queryFn: () => api.replaySeed(id),
    enabled: !Number.isNaN(id),
  });

  const seed = q.data;
  const [output, setOutput] = useState("");
  const [latencyMs, setLatencyMs] = useState(0);
  const [costUsd, setCostUsd] = useState(0);

  useEffect(() => {
    if (seed) {
      setOutput(seed.output);
      setLatencyMs(seed.latency_ms);
      setCostUsd(seed.cost_usd);
    }
  }, [seed]);

  const replay = useMutation({
    mutationFn: () =>
      api.replayCase(id, {
        output,
        latency_ms: latencyMs,
        cost_usd: costUsd,
      }),
  });

  const evals = useMemo(
    () =>
      (seed?.graders ?? []).map((g) =>
        evaluateGrader(g, { output, latency_ms: latencyMs, cost_usd: costUsd }),
      ),
    [seed, output, latencyMs, costUsd],
  );

  const stats = useMemo(() => {
    let pass = 0;
    let fail = 0;
    let pending = 0;
    for (const e of evals) {
      if (e.kind === "live" || e.kind === "parse-error") {
        if (e.pass) pass++;
        else fail++;
      } else pending++;
    }
    return { pass, fail, pending };
  }, [evals]);

  if (q.isLoading) {
    return <Card className="p-6 text-sm text-muted-foreground">Loading…</Card>;
  }
  if (q.error || !seed) {
    return (
      <Card className="border-destructive/40 p-6">
        <p className="text-sm text-destructive">{String(q.error ?? "no seed")}</p>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <Link
        to={`/runs`}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> Back
      </Link>

      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">
          Replay sandbox · <span className="font-mono">{seed.case_name}</span>
        </h1>
        <div className="flex gap-2 text-xs">
          {stats.pass > 0 && <Badge tone="success">{stats.pass} pass</Badge>}
          {stats.fail > 0 && <Badge tone="danger">{stats.fail} fail</Badge>}
          {stats.pending > 0 && <Badge tone="warning">{stats.pending} pending</Badge>}
        </div>
      </div>

      <Card className="space-y-2 p-4">
        <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          input (JSON)
        </h3>
        <pre className="m-0 max-h-48 overflow-auto rounded-md border border-border bg-muted/20 px-3 py-2 font-mono text-xs">
          {JSON.stringify(seed.input ?? null, null, 2)}
        </pre>
      </Card>

      <Card className="space-y-2 p-4">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            output
          </h3>
          <span className="font-mono text-[10px] italic text-muted-foreground">
            edits re-evaluate live graders on every keystroke
          </span>
        </div>
        <textarea
          value={output}
          onChange={(e) => setOutput(e.target.value)}
          rows={Math.min(12, Math.max(4, output.split("\n").length))}
          spellCheck={false}
          className="w-full rounded-md border border-border bg-muted/20 px-3 py-2 font-mono text-xs"
        />
        <div className="flex flex-wrap gap-4 pt-1">
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-mono text-muted-foreground">latency (ms)</span>
            <input
              type="number"
              value={Number.isFinite(latencyMs) ? latencyMs : 0}
              onChange={(e) => setLatencyMs(parseFloat(e.target.value) || 0)}
              className="w-32 rounded-md border border-border bg-background px-2 py-1 font-mono text-xs"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-mono text-muted-foreground">cost (USD)</span>
            <input
              type="number"
              step="0.0001"
              value={Number.isFinite(costUsd) ? costUsd : 0}
              onChange={(e) => setCostUsd(parseFloat(e.target.value) || 0)}
              className="w-32 rounded-md border border-border bg-background px-2 py-1 font-mono text-xs"
            />
          </label>
        </div>
      </Card>

      <Card className="space-y-2 p-4">
        <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          graders
        </h3>
        <ul className="space-y-1">
          {evals.map((e, i) => (
            <li
              key={i}
              className={cn(
                "grid grid-cols-[auto_1fr_1fr] items-center gap-3 rounded-md border-l-[3px] px-3 py-1.5",
                e.kind === "live" && e.pass && "border-l-[hsl(var(--success))] bg-muted/10",
                e.kind === "live" &&
                  e.pass === false &&
                  "border-l-destructive bg-destructive/5",
                e.kind === "needs-replay" && "border-l-primary bg-muted/10",
                e.kind === "deferred" && "border-l-[hsl(var(--warning))] bg-muted/10",
                e.kind === "parse-error" && "border-l-destructive bg-destructive/10",
              )}
            >
              <span
                className={cn(
                  "rounded-md px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide",
                  e.kind === "live" && e.pass && "bg-[hsl(var(--success))]/15 text-[hsl(var(--success))]",
                  e.kind === "live" && !e.pass && "bg-destructive/15 text-destructive",
                  e.kind === "needs-replay" && "bg-primary/15 text-primary",
                  e.kind === "deferred" && "bg-[hsl(var(--warning))]/15 text-[hsl(var(--warning))]",
                  e.kind === "parse-error" && "bg-destructive/15 text-destructive",
                )}
              >
                {e.kind === "live"
                  ? e.pass
                    ? "✓ live"
                    : "✗ live"
                  : e.kind === "parse-error"
                    ? "parse err"
                    : e.kind === "needs-replay"
                      ? "needs replay"
                      : "deferred"}
              </span>
              <code className="truncate rounded-md border border-border bg-card px-1.5 py-0.5 font-mono text-xs">
                {e.grader}
              </code>
              <span className="truncate font-mono text-[11px] italic text-muted-foreground">
                {e.reason}
              </span>
            </li>
          ))}
        </ul>
      </Card>

      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => replay.mutate()}
          disabled={replay.isPending}
          className="rounded-md bg-foreground px-4 py-2 text-sm font-semibold text-background"
        >
          {replay.isPending ? "Replaying…" : "Replay from start ▶"}
        </button>
      </div>

      {replay.error && (
        <Card className="border-destructive/40 p-4 text-sm text-destructive">
          {replay.error instanceof ApiError && typeof replay.error.detail === "string"
            ? replay.error.detail
            : String(replay.error)}
        </Card>
      )}
    </div>
  );
}

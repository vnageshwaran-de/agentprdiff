// Diff viewer + baseline approval — the heart of the UI.
//
// Renders the engine's TraceDelta in four panels:
//   1. Assertion changes (was→now table, the most user-facing signal)
//   2. Tool sequence diff (side-by-side LCS-ish view)
//   3. Cost / latency / token deltas (compact stat strip)
//   4. Output diff (the unified diff string the engine produced)
//
// The Approve button writes the case's current trace as the new baseline.
// For git/zip projects, that also lands a JSON file inside the workspace —
// the response includes the disk path so we can confirm in the UI.

import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  AlertTriangle,
  Check,
  History,
  Play,
  Sparkles,
} from "lucide-react";

import { api, ApiError } from "@/api/client";
import { useToast } from "@/components/Toaster";
import { AssertionMatrix } from "@/components/AssertionMatrix";
import { OutputDiff } from "@/components/OutputDiff";
import { ToolSequenceDiff } from "@/components/ToolSequenceDiff";
import { TraceInspector } from "@/components/TraceInspector";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import type { CaseRunDetail, TraceDeltaJson, TraceJson } from "@/api/types";

const STATUS_TONE: Record<
  CaseRunDetail["status"],
  "neutral" | "success" | "warning" | "danger" | "info"
> = {
  passed: "success",
  failed: "danger",
  regression: "warning",
  error: "danger",
};

export function CaseDetail() {
  const { runId, caseRunId } = useParams();
  const id = Number(caseRunId);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  // Cross-component anchoring state: AssertionMatrix's "Highlight in trace"
  // button writes here, TraceInspector reads it to highlight the matching row.
  const [highlightedStepId, setHighlightedStepId] = useState<string | null>(null);

  const detail = useQuery({
    queryKey: ["case-run", id],
    queryFn: () => api.getCaseRun(id),
    enabled: !Number.isNaN(id),
  });

  const baselines = useQuery({
    queryKey: ["baselines", detail.data?.project_id, detail.data?.suite_id, detail.data?.case_name],
    queryFn: () =>
      api.listBaselines({
        project_id: detail.data!.project_id,
        suite_id: detail.data!.suite_id,
        case_name: detail.data!.case_name,
      }),
    enabled: !!detail.data,
  });

  const approve = useMutation({
    mutationFn: () => api.approveBaseline(id),
    onSuccess: (out) => {
      qc.invalidateQueries({ queryKey: ["baselines"] });
      qc.invalidateQueries({ queryKey: ["case-run", id] });
      toast.push({
        kind: "success",
        title: `Approved baseline v${out.baseline.version}`,
        description: out.wrote_to_disk
          ? "Written to the project workspace."
          : "Stored in the database for this http-mode project.",
      });
    },
    onError: (err) => {
      toast.push({
        kind: "error",
        title: "Couldn't approve baseline",
        description:
          err instanceof ApiError && typeof err.detail === "string"
            ? err.detail
            : String(err),
      });
    },
  });

  if (detail.isLoading) {
    return (
      <Card className="p-12 text-center text-sm text-muted-foreground">
        <Spinner className="mx-auto mb-2" /> Loading case…
      </Card>
    );
  }
  if (detail.error || !detail.data) {
    return (
      <Card className="border-destructive/40 p-6">
        <p className="text-sm text-destructive">Couldn't load case: {String(detail.error)}</p>
      </Card>
    );
  }

  const c = detail.data;
  const delta = (c.delta ?? null) as TraceDeltaJson | null;
  const trace = (c.trace ?? null) as TraceJson | null;

  return (
    <div className="space-y-6">
      <button
        onClick={() => navigate(`/runs/${runId}`)}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> Back to run #{runId}
      </button>

      <Card className="p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-semibold tracking-tight">{c.case_name}</h1>
              <Badge tone={STATUS_TONE[c.status]}>{c.status}</Badge>
              {baselines.data && baselines.data.length > 0 && (
                <Badge tone="neutral">baseline v{baselines.data[0].version}</Badge>
              )}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              <Link to={`/projects/${c.project_id}`} className="hover:text-foreground">
                project #{c.project_id}
              </Link>{" "}
              · suite <span className="font-mono">{c.suite_name}</span>
            </p>
            <p className="mt-2 flex flex-wrap gap-3 text-xs">
              <Link
                to={`/suites/${c.suite_id}/cases/${encodeURIComponent(c.case_name)}/timeline`}
                className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
              >
                <History className="h-3 w-3" /> Timeline
              </Link>
              <Link
                to={`/runs/${runId}/cases/${caseRunId}/replay`}
                className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
              >
                <Play className="h-3 w-3" /> Replay sandbox
              </Link>
              <Link
                to={`/suites/${c.suite_id}/benchmark`}
                className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
              >
                <Sparkles className="h-3 w-3" /> Compare models
              </Link>
            </p>
          </div>
          <div className="text-right">
            <Button
              onClick={() => approve.mutate()}
              disabled={approve.isPending || !c.trace}
              title="Promote this run's trace to be the new baseline for this case"
            >
              {approve.isPending ? <Spinner /> : <Sparkles className="h-4 w-4" />}
              Approve as new baseline
            </Button>
            {approve.data && (
              <p className="mt-2 inline-flex items-center gap-1.5 text-xs text-[hsl(var(--success))]">
                <Check className="h-3.5 w-3.5" /> approved v{approve.data.baseline.version}
                {approve.data.wrote_to_disk ? " · wrote to disk" : ""}
              </p>
            )}
            {approve.error && (
              <p className="mt-2 text-xs text-destructive">
                {approve.error instanceof ApiError
                  ? typeof approve.error.detail === "string"
                    ? approve.error.detail
                    : approve.error.message
                  : String(approve.error)}
              </p>
            )}
          </div>
        </div>

        <StatStrip delta={delta} trace={trace} />
      </Card>

      {delta == null && (
        <Card className="flex items-start gap-3 border-primary/30 bg-primary/5 p-4 text-sm">
          <Sparkles className="h-5 w-5 shrink-0 text-primary" aria-hidden />
          <div>
            <div className="font-medium">This is a recording, not a check.</div>
            <p className="text-muted-foreground">
              The case ran and its trace is saved as the baseline for future
              check runs. There's nothing to diff against yet — run{" "}
              <span className="font-medium">Check</span> on this suite to see
              a side-by-side comparison here.
            </p>
          </div>
        </Card>
      )}

      <AssertionsPanel
        delta={delta}
        trace={trace}
        onAnchorTrace={setHighlightedStepId}
      />
      <ToolSequencePanel delta={delta} />
      <OutputDiffPanel delta={delta} trace={trace} />
      <TraceInspectorPanel
        trace={trace}
        highlightedStepId={highlightedStepId}
        onStepClick={setHighlightedStepId}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------

function StatStrip({ delta, trace }: { delta: TraceDeltaJson | null; trace: TraceJson | null }) {
  if (!trace && !delta) return null;
  const stat = (label: string, value: React.ReactNode, sub?: React.ReactNode) => (
    <div className="min-w-0">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold">{value}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
  return (
    <div className="mt-6 grid gap-4 sm:grid-cols-4">
      {stat(
        "cost",
        `$${(trace?.total_cost_usd ?? 0).toFixed(4)}`,
        delta?.cost_delta_usd != null && (
          <Delta v={delta.cost_delta_usd} format={(v) => `${v >= 0 ? "+" : ""}$${v.toFixed(4)}`} />
        ),
      )}
      {stat(
        "latency",
        `${Math.round(trace?.total_latency_ms ?? 0)} ms`,
        delta?.latency_delta_ms != null && (
          <Delta v={delta.latency_delta_ms} format={(v) => `${v >= 0 ? "+" : ""}${Math.round(v)} ms`} />
        ),
      )}
      {stat(
        "prompt tokens",
        trace?.total_prompt_tokens ?? 0,
        delta?.prompt_tokens_delta != null && (
          <Delta v={delta.prompt_tokens_delta} format={(v) => `${v >= 0 ? "+" : ""}${v}`} />
        ),
      )}
      {stat(
        "completion tokens",
        trace?.total_completion_tokens ?? 0,
        delta?.completion_tokens_delta != null && (
          <Delta v={delta.completion_tokens_delta} format={(v) => `${v >= 0 ? "+" : ""}${v}`} />
        ),
      )}
    </div>
  );
}

function Delta({ v, format }: { v: number; format: (v: number) => string }) {
  const tone = v > 0 ? "text-destructive" : v < 0 ? "text-[hsl(var(--success))]" : "";
  return <span className={tone}>{format(v)}</span>;
}

// ---------------------------------------------------------------------------

function AssertionsPanel({
  delta,
  trace,
  onAnchorTrace,
}: {
  delta: TraceDeltaJson | null;
  trace: TraceJson | null;
  onAnchorTrace?: (stepId: string) => void;
}) {
  if (delta == null) return null;
  // The TraceJson type has tool_calls as a loose Array<{ name; ... }>; we cast
  // to the AssertionMatrix's ToolCallShape (just { name }) by trusting that
  // each entry has a `name` (the engine always emits it).
  const toolCalls = (trace?.tool_calls ?? []) as Array<{ name: string }>;
  return (
    <AssertionMatrix
      assertions={delta.assertion_changes}
      toolCalls={toolCalls}
      onAnchorTrace={onAnchorTrace}
    />
  );
}

function TraceInspectorPanel({
  trace,
  highlightedStepId,
  onStepClick,
}: {
  trace: TraceJson | null;
  highlightedStepId: string | null;
  onStepClick: (stepId: string) => void;
}) {
  if (!trace) return null;
  return (
    <TraceInspector
      llmCalls={trace.llm_calls as any}
      toolCalls={trace.tool_calls as any}
      totalCostUsd={trace.total_cost_usd}
      totalLatencyMs={trace.total_latency_ms}
      highlightedStepId={highlightedStepId}
      onStepClick={onStepClick}
    />
  );
}

// ---------------------------------------------------------------------------

function ToolSequencePanel({ delta }: { delta: TraceDeltaJson | null }) {
  if (delta == null) return null;
  return (
    <ToolSequenceDiff
      baseline={delta.baseline_tool_sequence}
      current={delta.current_tool_sequence}
      changed={delta.tool_sequence_changed}
    />
  );
}

// ---------------------------------------------------------------------------

function OutputDiffPanel({
  delta,
  trace,
}: {
  delta: TraceDeltaJson | null;
  trace: TraceJson | null;
}) {
  const changed = !!delta?.output_changed;
  return (
    <Card>
      <div className="flex items-center justify-between border-b border-border p-4">
        <h2 className="font-semibold">Output</h2>
        {delta && (
          <Badge tone={changed ? "warning" : "success"}>
            {changed ? "changed" : "unchanged"}
            {delta?.baseline_exists === false ? " · no baseline yet" : ""}
          </Badge>
        )}
      </div>
      <OutputDiff unifiedDiff={delta?.output_diff} fallbackOutput={trace?.output} />
      {delta?.current_error && (
        <div className="border-t border-destructive/30 bg-destructive/10 p-3 text-xs">
          <AlertTriangle className="mr-1 inline h-3.5 w-3.5" /> {delta.current_error}
        </div>
      )}
    </Card>
  );
}

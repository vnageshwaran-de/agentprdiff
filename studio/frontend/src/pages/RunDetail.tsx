import { useEffect, useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { CaseRunOut } from "@/api/types";
import { ArrowLeft, Activity, Circle, CheckCircle2, XCircle, AlertTriangle } from "lucide-react";

import { api } from "@/api/client";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { useRunStream, type CaseStatus } from "@/hooks/useRunStream";
import type { RunOut } from "@/api/types";

const STATUS_TONE: Record<RunOut["status"], "neutral" | "success" | "warning" | "danger" | "info"> = {
  pending: "neutral",
  running: "info",
  succeeded: "success",
  failed: "danger",
  regression: "warning",
  error: "danger",
};

const CASE_TONE: Record<CaseStatus, "neutral" | "success" | "warning" | "danger" | "info"> = {
  queued: "neutral",
  running: "info",
  passed: "success",
  failed: "danger",
  regression: "warning",
  error: "danger",
};

const CASE_ICON: Record<CaseStatus, typeof Circle> = {
  queued: Circle,
  running: Activity,
  passed: CheckCircle2,
  failed: XCircle,
  regression: AlertTriangle,
  error: XCircle,
};

export function RunDetail() {
  const { id } = useParams();
  const runId = Number(id);
  const qc = useQueryClient();

  const stream = useRunStream(Number.isNaN(runId) ? null : runId);

  // Poll the run row while live; refresh once more when stream terminates.
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId),
    enabled: !Number.isNaN(runId),
    refetchInterval: (q) => (q.state.data?.finished_at ? false : 1500),
  });

  useEffect(() => {
    if (stream.terminal) qc.invalidateQueries({ queryKey: ["run", runId] });
  }, [stream.terminal, qc, runId]);

  // Once the run is terminal, fetch the case rows so we can wire each case
  // grid entry to its case-detail page (we need the case_run id, which the
  // SSE stream doesn't expose).
  const cases = useQuery({
    queryKey: ["cases", runId],
    queryFn: () => api.getCases(runId),
    enabled: !Number.isNaN(runId) && stream.terminal,
  });
  const caseIdByName = useMemo<Record<string, number>>(() => {
    const out: Record<string, number> = {};
    for (const c of cases.data ?? []) out[c.case_name] = c.id;
    return out;
  }, [cases.data]);

  if (run.isLoading) {
    return (
      <Card className="p-12 text-center text-sm text-muted-foreground">
        <Spinner className="mx-auto mb-2" /> Loading run…
      </Card>
    );
  }
  if (run.error || !run.data) {
    return (
      <Card className="border-destructive/40 p-6">
        <p className="text-sm text-destructive">Couldn't load run: {String(run.error)}</p>
      </Card>
    );
  }

  const r = run.data;

  return (
    <div className="space-y-6">
      <Link
        to={`/projects/${r.project_id}`}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> Back to project
      </Link>

      {/* Header */}
      <Card className="p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-semibold tracking-tight">Run #{r.id}</h1>
              <Badge tone="neutral">{r.command}</Badge>
              <Badge tone={STATUS_TONE[r.status]}>{r.status}</Badge>
              {!stream.terminal && r.status !== "succeeded" && r.status !== "failed" && r.status !== "regression" && r.status !== "error" && (
                <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Spinner className="h-3 w-3" /> live
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              Started {r.started_at ?? "—"} &nbsp;·&nbsp; finished {r.finished_at ?? "—"}
            </p>
          </div>
          <div className="text-right text-sm">
            <div>
              <span className="text-muted-foreground">cases passed:</span>{" "}
              <span className="font-semibold">{r.cases_passed}</span> /{" "}
              <span className="text-muted-foreground">{r.cases_total}</span>
            </div>
            <div>
              <span className="text-muted-foreground">regressed:</span>{" "}
              <span className="font-semibold">{r.cases_regressed}</span>
            </div>
            <div className="text-muted-foreground">
              exit {r.exit_code ?? "—"}
            </div>
          </div>
        </div>
        {r.stderr_tail && (
          <pre className="mt-4 max-h-40 overflow-auto rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs">
            {r.stderr_tail}
          </pre>
        )}
      </Card>

      {/* Case grid */}
      <Card>
        <div className="border-b border-border p-4">
          <h2 className="font-semibold">Cases</h2>
          <p className="text-xs text-muted-foreground">
            Updates live as the run executes. Click a case to inspect its trace once the run finishes.
          </p>
        </div>
        <CaseGrid statuses={stream.caseStatuses} runId={runId} caseIds={caseIdByName} />
      </Card>

      {/* Event log */}
      <Card>
        <div className="flex items-center justify-between border-b border-border p-4">
          <h2 className="font-semibold">Event log</h2>
          <span className="text-xs text-muted-foreground">
            {stream.terminal ? "stream closed" : stream.connected ? "live" : "connecting…"}
          </span>
        </div>
        <EventLog stream={stream} />
      </Card>
    </div>
  );
}

function CaseGrid({
  statuses,
  runId,
  caseIds,
}: {
  statuses: Record<string, CaseStatus>;
  runId: number;
  caseIds: Record<string, number>;
}) {
  const entries = useMemo(() => Object.entries(statuses), [statuses]);
  if (entries.length === 0) {
    return (
      <div className="p-6 text-sm text-muted-foreground">
        Waiting for the first event…
      </div>
    );
  }
  return (
    <ul className="grid gap-2 p-4 sm:grid-cols-2 lg:grid-cols-3">
      {entries.map(([name, status]) => {
        const Icon = CASE_ICON[status];
        const caseRunId = caseIds[name];
        const inner = (
          <>
            <Icon
              className={cn(
                "h-4 w-4 shrink-0",
                status === "running" && "animate-spin",
              )}
              aria-hidden
            />
            <span className="truncate font-medium">{name}</span>
            <Badge tone={CASE_TONE[status]} className="ml-auto">{status}</Badge>
          </>
        );
        const baseClasses =
          "flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2";
        return (
          <li key={name}>
            {caseRunId != null ? (
              <Link
                to={`/runs/${runId}/cases/${caseRunId}`}
                className={cn(baseClasses, "transition-colors hover:bg-muted/40")}
              >
                {inner}
              </Link>
            ) : (
              <div className={baseClasses}>{inner}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

// Avoid an unused-import warning when CaseRunOut isn't referenced in the file.
export type _UnusedCaseRunOut = CaseRunOut;

function EventLog({ stream }: { stream: ReturnType<typeof useRunStream> }) {
  if (stream.events.length === 0) {
    return (
      <div className="p-6 text-sm text-muted-foreground">No events yet.</div>
    );
  }
  return (
    <div className="max-h-80 overflow-auto font-mono text-xs">
      <ul className="divide-y divide-border">
        {stream.events.map((ev, i) => (
          <li key={i} className="grid grid-cols-[80px_1fr] gap-2 px-4 py-1.5">
            <span
              className={cn(
                "truncate",
                ev.level === "error" ? "text-destructive" : "text-muted-foreground",
              )}
            >
              {ev.kind}
            </span>
            <span className="truncate">{ev.message || JSON.stringify(ev.payload ?? {})}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

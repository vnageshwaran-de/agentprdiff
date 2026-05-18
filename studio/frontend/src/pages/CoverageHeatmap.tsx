// Assertion coverage view — matrix of (grader-type × suite) + tool coverage
// list showing which tools are exercised but never asserted against.

import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ChevronDown, ChevronRight } from "lucide-react";

import { api } from "@/api/client";
import { Card } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";

export interface GraderMatrix {
  grader_types: string[];
  suites: string[];
  counts: number[][];
}

export interface ToolCoverageRow {
  name: string;
  asserted_count: number;
  exercised_count: number;
  suites_asserting?: string[] | null;
}

export interface CoverageResponse {
  grader_matrix: GraderMatrix;
  tool_coverage: ToolCoverageRow[];
}

export function CoverageHeatmapPage() {
  const { id } = useParams();
  const projectId = Number(id);
  const [view, setView] = useState<"matrix" | "tools">("matrix");

  const q = useQuery<CoverageResponse>({
    queryKey: ["coverage", projectId],
    queryFn: () => api.coverage(projectId),
    enabled: !Number.isNaN(projectId),
  });

  if (q.isLoading) {
    return (
      <Card className="p-12 text-center text-sm text-muted-foreground">
        <Spinner className="mx-auto mb-2" /> Loading coverage…
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
        to={`/projects/${projectId}`}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> Back to project
      </Link>

      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Assertion coverage</h1>
        <div className="inline-flex gap-0.5 rounded-md border border-border bg-muted/20 p-0.5 text-xs">
          <button
            type="button"
            onClick={() => setView("matrix")}
            className={cn(
              "rounded-md px-3 py-1",
              view === "matrix" ? "bg-foreground text-background" : "text-muted-foreground",
            )}
          >
            Grader matrix
          </button>
          <button
            type="button"
            onClick={() => setView("tools")}
            className={cn(
              "rounded-md px-3 py-1",
              view === "tools" ? "bg-foreground text-background" : "text-muted-foreground",
            )}
          >
            Tool coverage
          </button>
        </div>
      </div>

      {view === "matrix" ? (
        <MatrixView matrix={q.data?.grader_matrix} />
      ) : (
        <ToolsView rows={q.data?.tool_coverage ?? []} />
      )}
    </div>
  );
}

function MatrixView({ matrix }: { matrix?: GraderMatrix }) {
  if (!matrix || matrix.grader_types.length === 0 || matrix.suites.length === 0) {
    return (
      <Card className="p-12 text-center text-sm italic text-muted-foreground">
        No assertions recorded yet.
      </Card>
    );
  }
  const max = useMemo(() => {
    let m = 0;
    for (const row of matrix.counts) for (const v of row) if (v > m) m = v;
    return m;
  }, [matrix]);
  const rowSums = matrix.counts.map((r) => r.reduce((s, v) => s + v, 0));
  const colSums = matrix.suites.map((_, i) =>
    matrix.counts.reduce((s, r) => s + (r[i] ?? 0), 0),
  );
  return (
    <Card className="overflow-x-auto p-4">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
            <th className="w-36" />
            {matrix.suites.map((s) => (
              <th key={s} className="px-2 py-2 font-medium" title={s}>
                {s}
              </th>
            ))}
            <th className="bg-muted/20 px-2 py-2 font-medium">Σ</th>
          </tr>
        </thead>
        <tbody>
          {matrix.grader_types.map((g, gi) => (
            <tr key={g}>
              <th className="border-r border-border px-2 py-1 text-right font-mono text-xs font-medium text-muted-foreground">
                {g}
              </th>
              {matrix.suites.map((s, si) => {
                const v = matrix.counts[gi]?.[si] ?? 0;
                const intensity = max > 0 ? v / max : 0;
                return (
                  <td
                    key={s}
                    className={cn(
                      "border border-card px-2 py-1 text-center font-mono text-xs",
                      v === 0 && "bg-muted/30 text-muted-foreground/40",
                    )}
                    style={
                      v > 0
                        ? {
                            backgroundColor: `hsl(244 80% ${95 - intensity * 50}%)`,
                            color: 95 - intensity * 50 < 60 ? "white" : "#374151",
                          }
                        : undefined
                    }
                  >
                    {v || ""}
                  </td>
                );
              })}
              <td className="bg-muted/20 px-2 py-1 text-center font-mono text-xs font-semibold text-muted-foreground">
                {rowSums[gi]}
              </td>
            </tr>
          ))}
          <tr>
            <th className="border-r border-border bg-muted/20 px-2 py-1 text-right font-mono text-xs font-semibold text-muted-foreground">
              Σ
            </th>
            {colSums.map((v, i) => (
              <td
                key={i}
                className="bg-muted/20 px-2 py-1 text-center font-mono text-xs font-semibold text-muted-foreground"
              >
                {v}
              </td>
            ))}
            <td className="bg-foreground px-2 py-1 text-center font-mono text-xs font-semibold text-background">
              {colSums.reduce((s, v) => s + v, 0)}
            </td>
          </tr>
        </tbody>
      </table>
    </Card>
  );
}

function ToolsView({ rows }: { rows: ToolCoverageRow[] }) {
  const buckets = useMemo(() => {
    const exercisedOnly: ToolCoverageRow[] = [];
    const assertedOnly: ToolCoverageRow[] = [];
    const covered: ToolCoverageRow[] = [];
    for (const r of rows) {
      if (r.exercised_count > 0 && r.asserted_count === 0) exercisedOnly.push(r);
      else if (r.asserted_count > 0 && r.exercised_count === 0) assertedOnly.push(r);
      else if (r.asserted_count > 0 && r.exercised_count > 0) covered.push(r);
    }
    const sortDesc = (a: ToolCoverageRow, b: ToolCoverageRow) =>
      b.exercised_count - a.exercised_count || a.name.localeCompare(b.name);
    exercisedOnly.sort(sortDesc);
    covered.sort(sortDesc);
    assertedOnly.sort((a, b) => a.name.localeCompare(b.name));
    return { exercisedOnly, assertedOnly, covered };
  }, [rows]);

  return (
    <div className="space-y-3">
      <Bucket
        kind="warn"
        title="Exercised but not asserted"
        summary="Coverage gap — these tools were called by the agent during real runs, but no case asserts they were called."
        rows={buckets.exercisedOnly}
        defaultOpen
      />
      <Bucket
        kind="danger"
        title="Asserted but never exercised"
        summary="Possible dead code — your cases reference these tools but the agent has never invoked them."
        rows={buckets.assertedOnly}
        defaultOpen
      />
      <Bucket
        kind="success"
        title="Covered"
        summary="Both asserted against and exercised — no action needed."
        rows={buckets.covered}
      />
    </div>
  );
}

function Bucket({
  kind,
  title,
  summary,
  rows,
  defaultOpen,
}: {
  kind: "warn" | "danger" | "success";
  title: string;
  summary: string;
  rows: ToolCoverageRow[];
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(!!defaultOpen);
  const icon = kind === "warn" ? "⚠" : kind === "danger" ? "✗" : "✓";
  return (
    <Card
      className={cn(
        "overflow-hidden p-0",
        kind === "warn" && "border-[hsl(var(--warning))]/40",
        kind === "danger" && "border-destructive/40",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex w-full items-center gap-2 border-b border-border px-4 py-3 text-left",
          kind === "warn" && "bg-[hsl(var(--warning))]/10",
          kind === "danger" && "bg-destructive/10",
          kind === "success" && "bg-muted/20",
        )}
      >
        <span
          className={cn(
            "font-mono text-base",
            kind === "warn" && "text-[hsl(var(--warning))]",
            kind === "danger" && "text-destructive",
            kind === "success" && "text-[hsl(var(--success))]",
          )}
        >
          {icon}
        </span>
        <span className="flex-1 font-medium">{title}</span>
        <span className="font-mono text-xs text-muted-foreground">{rows.length}</span>
        {open ? (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        )}
      </button>
      {open && (
        <>
          <p className="px-4 pt-3 text-xs italic text-muted-foreground">{summary}</p>
          {rows.length === 0 ? (
            <p className="px-4 py-3 text-xs italic text-muted-foreground">
              Nothing in this bucket.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {rows.map((r) => (
                <li
                  key={r.name}
                  className="grid grid-cols-[1fr_auto] items-baseline gap-3 px-4 py-2"
                >
                  <code className="font-mono text-sm font-medium">{r.name}</code>
                  <div className="flex gap-1.5 text-[11px] font-mono">
                    <span className="rounded-md bg-primary/10 px-1.5 py-0.5 text-primary">
                      {r.asserted_count} assert{r.asserted_count === 1 ? "" : "s"}
                    </span>
                    <span className="rounded-md bg-[hsl(var(--warning))]/10 px-1.5 py-0.5 text-[hsl(var(--warning))]">
                      {r.exercised_count} call{r.exercised_count === 1 ? "" : "s"}
                    </span>
                  </div>
                  {r.suites_asserting && r.suites_asserting.length > 0 && (
                    <div className="col-span-2 text-[11px] font-mono text-muted-foreground">
                      asserted in:{" "}
                      {r.suites_asserting.map((s, i) => (
                        <span key={s}>
                          <code className="rounded-md bg-muted/30 px-1">{s}</code>
                          {i < r.suites_asserting!.length - 1 ? ", " : ""}
                        </span>
                      ))}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Card>
  );
}

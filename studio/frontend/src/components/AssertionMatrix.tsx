// Structured assertion table for the case detail page. Replaces the flat
// "grader → verdict" list with a was→now table that:
//
//   * shows pass/fail/n-a pills side-by-side per row,
//   * highlights regression rows (was=pass, now=fail) with a red left border,
//   * lets the user expand a row to see the full `current_reason`.
//
// Wired contract for cross-component anchoring (not used in v1): when the
// grader is `tool_called('foo')`, an optional `onAnchorTrace(stepId)` callback
// receives `"t-{index}"` if `foo` exists in `toolCalls`. The trace inspector
// component (next patch) will subscribe to that selection to highlight the
// matching step.

import { useMemo, useState } from "react";
import { ChevronRight, ChevronDown, CheckCircle2, XCircle, CircleDashed } from "lucide-react";

import { cn } from "@/lib/cn";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";

export interface AssertionChange {
  grader_name: string;
  baseline_passed?: boolean | null;
  current_passed?: boolean | null;
  current_reason?: string;
  // Optional engine fields — not in the existing TraceDeltaJson today, but
  // the component reads them defensively so future backend additions slot in
  // without a frontend change.
  baseline_reason?: string | null;
  is_regression?: boolean | null;
}

export interface ToolCallShape {
  name: string;
}

export interface AssertionMatrixProps {
  assertions?: AssertionChange[] | null;
  toolCalls?: ToolCallShape[] | null;
  onAnchorTrace?: (stepId: string) => void;
}

function extractToolCalledName(graderName: string): string | null {
  const match = graderName.match(/^tool_called\(\s*['"]?([A-Za-z_][\w.-]*)['"]?/);
  return match ? match[1] : null;
}

type Status = "regression" | "improvement" | "pass" | "fail" | "new";

function computeStatus(a: AssertionChange): Status {
  if (a.is_regression === true) return "regression";
  const before = a.baseline_passed;
  const now = a.current_passed;
  if (before == null) return "new";
  if (before === false && now === true) return "improvement";
  if (before === true && now === false) return "regression";
  if (now === true) return "pass";
  return "fail";
}

export function AssertionMatrix({
  assertions,
  toolCalls,
  onAnchorTrace,
}: AssertionMatrixProps) {
  const list = assertions ?? [];
  const tools = toolCalls ?? [];

  const stats = useMemo(() => {
    let passing = 0;
    let regressions = 0;
    let improvements = 0;
    for (const a of list) {
      if (a.current_passed === true) passing++;
      const s = computeStatus(a);
      if (s === "regression") regressions++;
      if (s === "improvement") improvements++;
    }
    return { total: list.length, passing, regressions, improvements };
  }, [list]);

  // Resolve which assertions can anchor to a tool call in the current trace.
  const anchorByIndex = useMemo(() => {
    const result: Record<number, string | null> = {};
    list.forEach((a, i) => {
      const toolName = extractToolCalledName(a.grader_name);
      if (toolName == null) {
        result[i] = null;
        return;
      }
      const idx = tools.findIndex((t) => t.name === toolName);
      result[i] = idx >= 0 ? `t-${idx}` : null;
    });
    return result;
  }, [list, tools]);

  return (
    <Card>
      <div className="flex items-center justify-between border-b border-border p-4">
        <div>
          <h2 className="font-semibold">Assertions</h2>
          <p className="text-xs text-muted-foreground">
            Each grader's verdict in this run, compared to the baseline.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">
            {stats.total} total · <span className="text-foreground">{stats.passing} pass</span>
          </span>
          {stats.regressions > 0 && (
            <Badge tone="danger">
              {stats.regressions} regression{stats.regressions === 1 ? "" : "s"}
            </Badge>
          )}
          {stats.improvements > 0 && (
            <Badge tone="success">
              {stats.improvements} improvement{stats.improvements === 1 ? "" : "s"}
            </Badge>
          )}
        </div>
      </div>

      {list.length === 0 ? (
        <div className="p-6 text-sm text-muted-foreground">No assertions recorded.</div>
      ) : (
        <table className="w-full table-fixed text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/20 text-xs uppercase tracking-wide text-muted-foreground">
              <th scope="col" className="w-14 px-3 py-2 text-left font-medium">
                was
              </th>
              <th scope="col" className="w-14 px-3 py-2 text-left font-medium">
                now
              </th>
              <th scope="col" className="w-2/5 px-3 py-2 text-left font-medium">
                grader
              </th>
              <th scope="col" className="px-3 py-2 text-left font-medium">
                reason
              </th>
              <th scope="col" className="w-6 px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {list.map((a, i) => (
              <Row
                key={`${i}-${a.grader_name}`}
                assertion={a}
                anchorStepId={anchorByIndex[i]}
                onAnchorTrace={onAnchorTrace}
              />
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

interface RowProps {
  assertion: AssertionChange;
  anchorStepId: string | null;
  onAnchorTrace?: (stepId: string) => void;
}

function Row({ assertion, anchorStepId, onAnchorTrace }: RowProps) {
  const [expanded, setExpanded] = useState(false);
  const status = computeStatus(assertion);

  const rowCls = cn(
    "border-b border-border align-middle transition-colors hover:bg-muted/30 cursor-pointer",
    status === "regression" && "bg-destructive/5",
  );
  // Left border accent: rendered as a 3px box-shadow on the first cell so we
  // don't fight the table cell padding.
  const accentCls = cn(
    status === "regression" && "shadow-[inset_3px_0_0_hsl(var(--destructive))]",
    status === "improvement" &&
      "shadow-[inset_3px_0_0_hsl(var(--success))]",
    status === "new" && "shadow-[inset_3px_0_0_hsl(var(--warning))]",
  );

  return (
    <>
      <tr className={rowCls} onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
        <td className={cn("px-3 py-2", accentCls)}>
          <Verdict passed={assertion.baseline_passed} muted />
        </td>
        <td className="px-3 py-2">
          <Verdict passed={assertion.current_passed} />
        </td>
        <td className="truncate px-3 py-2">
          <code className="rounded-md border border-border bg-muted/30 px-1.5 py-0.5 font-mono text-xs">
            {assertion.grader_name}
          </code>
        </td>
        <td className="truncate px-3 py-2 text-xs text-muted-foreground">
          {assertion.current_reason || (
            <span className="italic opacity-60">no reason recorded</span>
          )}
        </td>
        <td className="px-3 py-2 text-muted-foreground">
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-border bg-muted/10">
          <td colSpan={5} className="px-3 py-3">
            <ExpandedBody
              assertion={assertion}
              status={status}
              anchorStepId={anchorStepId}
              onAnchorTrace={onAnchorTrace}
            />
          </td>
        </tr>
      )}
    </>
  );
}

function Verdict({
  passed,
  muted,
}: {
  passed: boolean | null | undefined;
  muted?: boolean;
}) {
  if (passed === true) {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 text-[hsl(var(--success))]",
          muted && "opacity-60",
        )}
      >
        <CheckCircle2 className="h-3.5 w-3.5" /> pass
      </span>
    );
  }
  if (passed === false) {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 text-destructive",
          muted && "opacity-60",
        )}
      >
        <XCircle className="h-3.5 w-3.5" /> fail
      </span>
    );
  }
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-muted-foreground",
        muted && "opacity-60",
      )}
    >
      <CircleDashed className="h-3.5 w-3.5" /> n/a
    </span>
  );
}

function ExpandedBody({
  assertion,
  status,
  anchorStepId,
  onAnchorTrace,
}: {
  assertion: AssertionChange;
  status: Status;
  anchorStepId: string | null;
  onAnchorTrace?: (stepId: string) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Badge
          tone={
            status === "regression"
              ? "danger"
              : status === "improvement"
                ? "success"
                : status === "new"
                  ? "warning"
                  : status === "pass"
                    ? "success"
                    : "neutral"
          }
        >
          {status}
        </Badge>
        {anchorStepId && onAnchorTrace && (
          <button
            type="button"
            className="rounded-md border border-border bg-card px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground"
            onClick={(e) => {
              e.stopPropagation();
              onAnchorTrace(anchorStepId);
            }}
          >
            ↳ Highlight in trace
          </button>
        )}
      </div>
      <ReasonBlock label="baseline reason" text={assertion.baseline_reason} />
      <ReasonBlock label="current reason" text={assertion.current_reason} />
    </div>
  );
}

function ReasonBlock({ label, text }: { label: string; text?: string | null }) {
  if (!text || text.trim() === "") return null;
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <pre className="mt-0.5 rounded-md border border-border bg-card px-3 py-2 font-mono text-xs">
        {text}
      </pre>
    </div>
  );
}

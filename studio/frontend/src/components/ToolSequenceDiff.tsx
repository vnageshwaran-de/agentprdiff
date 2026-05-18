// LCS-aligned swimlane diff for `baseline_tool_sequence` vs
// `current_tool_sequence`. Replaces the side-by-side numbered lists with a
// single grid where each column is one LCS op and the two lanes share column
// tracks so matched tools sit in the same column on both lanes.
//
// Hand-rolled LCS (~20 lines) so we don't take a new dep.

import { useMemo } from "react";
import { cn } from "@/lib/cn";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";

type Op = { type: "equal" | "delete" | "insert"; tool: string };

function lcsAlign(a: string[], b: string[]): Op[] {
  const m = a.length;
  const n = b.length;
  // dp[i][j] = LCS length of a[i..] and b[j..]
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      if (a[i] === b[j]) dp[i][j] = dp[i + 1][j + 1] + 1;
      else dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops: Op[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      ops.push({ type: "equal", tool: a[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      ops.push({ type: "delete", tool: a[i] });
      i++;
    } else {
      ops.push({ type: "insert", tool: b[j] });
      j++;
    }
  }
  while (i < m) {
    ops.push({ type: "delete", tool: a[i] });
    i++;
  }
  while (j < n) {
    ops.push({ type: "insert", tool: b[j] });
    j++;
  }
  return ops;
}

interface Tally {
  added: number;
  removed: number;
  kept: number;
}

function tally(ops: Op[]): Tally {
  let added = 0;
  let removed = 0;
  let kept = 0;
  for (const op of ops) {
    if (op.type === "insert") added++;
    else if (op.type === "delete") removed++;
    else kept++;
  }
  return { added, removed, kept };
}

export interface ToolSequenceDiffProps {
  baseline?: string[] | null;
  current?: string[] | null;
  changed?: boolean | null;
}

export function ToolSequenceDiff({ baseline, current, changed }: ToolSequenceDiffProps) {
  const baseList = baseline ?? [];
  const currList = current ?? [];

  const ops = useMemo(() => lcsAlign(baseList, currList), [baseList, currList]);
  const stats = useMemo(() => tally(ops), [ops]);

  if (baseList.length === 0 && currList.length === 0) return null;

  const isChanged = changed ?? stats.added + stats.removed > 0;

  return (
    <Card>
      <div className="flex items-center justify-between border-b border-border p-4">
        <h2 className="font-semibold">Tool sequence</h2>
        <div className="flex items-center gap-2 text-xs">
          {isChanged ? (
            <>
              {stats.removed > 0 && (
                <span className="font-mono font-semibold text-destructive">
                  −{stats.removed}
                </span>
              )}
              {stats.added > 0 && (
                <span className="font-mono font-semibold text-[hsl(var(--success))]">
                  +{stats.added}
                </span>
              )}
              <span className="font-mono text-muted-foreground">
                {stats.kept} unchanged
              </span>
            </>
          ) : (
            <Badge tone="success">unchanged</Badge>
          )}
        </div>
      </div>

      <div className="overflow-x-auto p-4">
        <div
          className="grid items-stretch gap-x-1 gap-y-1"
          style={{
            gridTemplateColumns: `auto repeat(${ops.length}, minmax(0, max-content))`,
            gridTemplateRows: "auto auto",
          }}
        >
          <LaneLabel row={1}>baseline</LaneLabel>
          {ops.map((op, idx) => (
            <Cell key={`b-${idx}`} side="top" op={op} column={idx + 2} />
          ))}

          <LaneLabel row={2}>current</LaneLabel>
          {ops.map((op, idx) => (
            <Cell key={`c-${idx}`} side="bottom" op={op} column={idx + 2} />
          ))}
        </div>
      </div>
    </Card>
  );
}

function LaneLabel({ row, children }: { row: 1 | 2; children: React.ReactNode }) {
  return (
    <div
      className="sticky left-0 z-10 self-center bg-card pr-3 text-xs uppercase tracking-wide text-muted-foreground"
      style={{ gridColumn: 1, gridRow: row }}
    >
      {children}
    </div>
  );
}

function Cell({
  side,
  op,
  column,
}: {
  side: "top" | "bottom";
  op: Op;
  column: number;
}) {
  let label = "";
  let kind: "equal" | "delete" | "insert" | "empty";
  if (op.type === "equal") {
    label = op.tool;
    kind = "equal";
  } else if (side === "top") {
    if (op.type === "delete") {
      label = op.tool;
      kind = "delete";
    } else {
      kind = "empty";
    }
  } else {
    if (op.type === "insert") {
      label = op.tool;
      kind = "insert";
    } else {
      kind = "empty";
    }
  }

  const cls = cn(
    "rounded-md border px-2.5 py-1 text-center font-mono text-xs whitespace-nowrap",
    kind === "equal" && "border-border bg-muted/40 text-foreground",
    kind === "delete" &&
      "border-destructive/30 bg-destructive/15 text-destructive before:mr-1 before:opacity-70 before:content-['−']",
    kind === "insert" &&
      "border-[hsl(var(--success))]/30 bg-[hsl(var(--success))]/15 text-[hsl(var(--success))] before:mr-1 before:opacity-70 before:content-['+']",
    kind === "empty" && "border-dashed border-border bg-muted/20 text-transparent",
  );
  return (
    <div
      className={cls}
      style={{ gridColumn: column, gridRow: side === "top" ? 1 : 2 }}
      title={label || undefined}
    >
      {label || " "}
    </div>
  );
}

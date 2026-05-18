// Split-panel output diff. Replaces the raw unified-diff <pre> block on the
// case detail page with a two-column baseline/current view that highlights
// removed lines in red and inserted lines in green.
//
// v1 parses the engine's unified-diff string (`delta.output_diff`) directly
// — no backend changes required. A future iteration can swap in raw baseline
// and current output strings on the response and run a word-level diff via
// diff-match-patch, with a JSON-tree mode auto-selected when both sides parse.

import { useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import { Badge } from "@/components/ui/Badge";

type Mode = "split" | "raw";

interface ParsedLine {
  baseline: string | null;
  current: string | null;
}

/**
 * Parse a unified diff string into aligned baseline/current rows.
 *
 * For each hunk, we collect the contiguous run of `-` lines and the
 * contiguous run of `+` lines that follow, then pair them up by index.
 * Context lines (those starting with a space) appear in both columns.
 * Hunk markers (`@@`) become a single-cell separator row.
 */
function parseUnifiedDiff(diff: string): ParsedLine[] {
  const lines = diff.split("\n");
  const rows: ParsedLine[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // Skip file headers (--- a/foo, +++ b/foo).
    if (line.startsWith("--- ") || line.startsWith("+++ ")) {
      i++;
      continue;
    }
    if (line.startsWith("@@")) {
      // Section separator — render as a single row with the hunk header in
      // both columns so it visually spans both sides.
      rows.push({ baseline: line, current: line });
      i++;
      continue;
    }
    if (line.startsWith("-")) {
      // Collect contiguous runs of deletes then inserts.
      const deletes: string[] = [];
      while (i < lines.length && lines[i].startsWith("-") && !lines[i].startsWith("---")) {
        deletes.push(lines[i].slice(1));
        i++;
      }
      const inserts: string[] = [];
      while (i < lines.length && lines[i].startsWith("+") && !lines[i].startsWith("+++")) {
        inserts.push(lines[i].slice(1));
        i++;
      }
      const max = Math.max(deletes.length, inserts.length);
      for (let k = 0; k < max; k++) {
        rows.push({
          baseline: deletes[k] ?? null,
          current: inserts[k] ?? null,
        });
      }
      continue;
    }
    if (line.startsWith("+")) {
      // Stand-alone insert (no preceding deletes).
      const inserts: string[] = [];
      while (i < lines.length && lines[i].startsWith("+") && !lines[i].startsWith("+++")) {
        inserts.push(lines[i].slice(1));
        i++;
      }
      for (const ins of inserts) rows.push({ baseline: null, current: ins });
      continue;
    }
    if (line.startsWith(" ") || line === "") {
      rows.push({ baseline: line.slice(1), current: line.slice(1) });
      i++;
      continue;
    }
    // Unknown line; render as plain text in both columns to be safe.
    rows.push({ baseline: line, current: line });
    i++;
  }
  return rows;
}

interface OutputDiffProps {
  /** The engine's unified-diff string. Empty when nothing changed. */
  unifiedDiff?: string | null;
  /** The current run's raw output (for the no-baseline / no-diff fallback). */
  fallbackOutput?: unknown;
}

export function OutputDiff({ unifiedDiff, fallbackOutput }: OutputDiffProps) {
  const diff = (unifiedDiff ?? "").trim();
  const [mode, setMode] = useState<Mode>("split");

  const rows = useMemo(() => (diff ? parseUnifiedDiff(diff) : []), [diff]);

  // No diff — render the current trace's output as plain text, matching the
  // existing fallback behavior.
  if (!diff) {
    return (
      <pre className="max-h-96 overflow-auto bg-muted/30 p-4 text-xs">
        {typeof fallbackOutput === "string"
          ? fallbackOutput
          : JSON.stringify(fallbackOutput ?? null, null, 2)}
      </pre>
    );
  }

  return (
    <div>
      <div className="flex items-center gap-2 border-b border-border bg-muted/20 px-4 py-2">
        <ModeToggle current={mode} value="split" onChange={setMode}>
          Split
        </ModeToggle>
        <ModeToggle current={mode} value="raw" onChange={setMode}>
          Raw
        </ModeToggle>
      </div>
      {mode === "split" ? <SplitView rows={rows} /> : <RawView diff={diff} />}
    </div>
  );
}

function ModeToggle({
  current,
  value,
  onChange,
  children,
}: {
  current: Mode;
  value: Mode;
  onChange: (m: Mode) => void;
  children: React.ReactNode;
}) {
  const active = current === value;
  return (
    <button
      type="button"
      onClick={() => onChange(value)}
      className={cn(
        "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
        active
          ? "bg-foreground text-background"
          : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function SplitView({ rows }: { rows: ParsedLine[] }) {
  return (
    <div className="grid max-h-[480px] grid-cols-2 gap-0 overflow-auto">
      <div className="border-r border-border">
        <PaneHeader side="baseline" />
        <pre className="m-0 text-xs leading-relaxed">
          {rows.map((r, i) => (
            <DiffRow key={i} text={r.baseline} kind={kindFor(r, "baseline")} />
          ))}
        </pre>
      </div>
      <div>
        <PaneHeader side="current" />
        <pre className="m-0 text-xs leading-relaxed">
          {rows.map((r, i) => (
            <DiffRow key={i} text={r.current} kind={kindFor(r, "current")} />
          ))}
        </pre>
      </div>
    </div>
  );
}

function PaneHeader({ side }: { side: "baseline" | "current" }) {
  return (
    <div
      className={cn(
        "sticky top-0 z-10 border-b border-border bg-muted/30 px-3 py-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground",
      )}
    >
      {side === "baseline" ? "— baseline" : "+ current"}
    </div>
  );
}

type RowKind = "equal" | "delete" | "insert" | "empty" | "hunk";

function kindFor(r: ParsedLine, side: "baseline" | "current"): RowKind {
  if (r.baseline === r.current && r.baseline?.startsWith("@@")) return "hunk";
  if (side === "baseline") {
    if (r.baseline === null) return "empty";
    if (r.current === null) return "delete";
    return "equal";
  }
  if (r.current === null) return "empty";
  if (r.baseline === null) return "insert";
  return "equal";
}

function DiffRow({ text, kind }: { text: string | null; kind: RowKind }) {
  const cls = cn(
    "block whitespace-pre-wrap break-words px-3 py-px font-mono",
    kind === "delete" && "bg-destructive/15 text-destructive",
    kind === "insert" && "bg-[hsl(var(--success))]/15 text-[hsl(var(--success))]",
    kind === "empty" && "bg-muted/30",
    kind === "hunk" && "bg-muted/40 text-muted-foreground",
  );
  return <span className={cls}>{text === null || text === "" ? " " : text}</span>;
}

function RawView({ diff }: { diff: string }) {
  const lines = diff.split("\n");
  return (
    <pre className="m-0 max-h-[480px] overflow-auto text-xs leading-relaxed">
      <code>
        {lines.map((line, i) => {
          let cls = "block px-4";
          if (line.startsWith("+") && !line.startsWith("+++"))
            cls += " bg-[hsl(var(--success))]/15 text-[hsl(var(--success))]";
          else if (line.startsWith("-") && !line.startsWith("---"))
            cls += " bg-destructive/15 text-destructive";
          else if (line.startsWith("@@")) cls += " text-muted-foreground";
          return (
            <span key={i} className={cls}>
              {line || " "}
            </span>
          );
        })}
      </code>
    </pre>
  );
}

// Convenience wrapper that wraps the diff in a Card with a header. Lets the
// page render `<OutputDiffSection delta={...} trace={...} />` and get the
// full panel with badge + heading without repeating the Card boilerplate.
export function OutputDiffStatusBadge({
  output_changed,
  baseline_exists,
}: {
  output_changed?: boolean;
  baseline_exists?: boolean;
}) {
  const changed = !!output_changed;
  return (
    <Badge tone={changed ? "warning" : "success"}>
      {changed ? "changed" : "unchanged"}
      {baseline_exists === false ? " · no baseline yet" : ""}
    </Badge>
  );
}

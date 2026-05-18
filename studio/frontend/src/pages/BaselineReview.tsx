// Baseline review — historical view of approved baselines for a project,
// grouped by suite. Each entry shows a diff vs its prior version (when one
// exists) so you can audit what changed.
//
// v0 is read-only — no destructive revert because that would delete real
// engine-on-disk baselines. The "Approve as new baseline" button on the case
// detail page is still the way to push changes; this page surfaces what's
// already been approved.

import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";

import { api } from "@/api/client";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { OutputDiff } from "@/components/OutputDiff";

export interface BaselineHistoryEntry {
  id: number;
  suite_id: number;
  suite_name: string;
  case_name: string;
  version: number;
  created_at: string;
  approved_by_run_id?: number | null;
  current_output?: string | null;
  prior_output?: string | null;
  unified_diff?: string | null;
}

export interface BaselineHistoryResponse {
  entries: BaselineHistoryEntry[];
}

export function BaselineReviewPage() {
  const { id } = useParams();
  const projectId = Number(id);

  const q = useQuery<BaselineHistoryResponse>({
    queryKey: ["baseline-history", projectId],
    queryFn: () => api.baselineHistory(projectId),
    enabled: !Number.isNaN(projectId),
  });

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
  const entries = q.data?.entries ?? [];

  // Group by suite for visual scanning
  const bySuite = new Map<string, BaselineHistoryEntry[]>();
  for (const e of entries) {
    const arr = bySuite.get(e.suite_name) ?? [];
    arr.push(e);
    bySuite.set(e.suite_name, arr);
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
        <h1 className="text-2xl font-semibold tracking-tight">Baseline activity</h1>
        <span className="font-mono text-xs text-muted-foreground">
          {entries.length} approval{entries.length === 1 ? "" : "s"}
        </span>
      </div>

      {entries.length === 0 ? (
        <Card className="p-12 text-center text-sm italic text-muted-foreground">
          No baseline approvals yet. Approve a baseline from any case detail
          page and it will appear here.
        </Card>
      ) : (
        <div className="space-y-6">
          {[...bySuite.entries()].map(([suiteName, items]) => (
            <section key={suiteName} className="space-y-3">
              <div className="flex items-center justify-between border-b border-border pb-1">
                <h2 className="font-mono text-sm font-medium">{suiteName}</h2>
                <span className="font-mono text-xs text-muted-foreground">
                  {items.length} approval{items.length === 1 ? "" : "s"}
                </span>
              </div>
              <ol className="space-y-3">
                {items.map((e) => (
                  <li key={e.id}>
                    <EntryCard entry={e} />
                  </li>
                ))}
              </ol>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function EntryCard({ entry }: { entry: BaselineHistoryEntry }) {
  const isNew = entry.version === 1;
  return (
    <Card className="border-l-[3px] border-l-primary/40 p-0">
      <div className="flex items-center justify-between gap-3 border-b border-border bg-muted/20 px-4 py-2">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-sm font-medium">{entry.case_name}</span>
          <Badge tone="neutral">v{entry.version}</Badge>
          {isNew && <Badge tone="success">first baseline</Badge>}
        </div>
        <span className="font-mono text-xs text-muted-foreground">
          {new Date(entry.created_at).toLocaleString()}
        </span>
      </div>
      {isNew ? (
        <pre className="m-0 max-h-72 overflow-auto px-4 py-3 text-xs">
          {entry.current_output ?? <span className="italic text-muted-foreground">no output recorded</span>}
        </pre>
      ) : (
        <OutputDiff
          unifiedDiff={entry.unified_diff}
          fallbackOutput={entry.current_output}
        />
      )}
    </Card>
  );
}

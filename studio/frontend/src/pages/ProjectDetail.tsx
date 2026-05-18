import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  RefreshCw,
  FileCode2,
  Play,
  CheckCircle2,
  Eye,
  ArrowRight,
  Lightbulb,
  Compass,
  Stethoscope,
  AlertTriangle,
  Trash2,
  Activity,
  GitBranch,
  Grid3x3,
  Sparkles,
} from "lucide-react";

import { api, ApiError } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/Toaster";
import { ProjectGuide } from "@/components/ProjectGuide";
import type { DiscoveryDiagnostics, RunOut } from "@/api/types";

export function ProjectDetail() {
  const { id } = useParams();
  const projectId = Number(id);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();

  const triggerRun = useMutation({
    mutationFn: ({ suiteId, command }: { suiteId: number; command: "record" | "check" | "review" }) =>
      api.createRun({ project_id: projectId, suite_id: suiteId, command }),
    onSuccess: (run) => navigate(`/runs/${run.id}`),
    onError: (err) =>
      toast.push({
        kind: "error",
        title: "Couldn't start the run",
        description:
          err instanceof ApiError && typeof err.detail === "string"
            ? err.detail
            : String(err),
      }),
  });

  const deleteSuite = useMutation({
    mutationFn: (suiteId: number) => api.deleteSuite(projectId, suiteId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["suites", projectId] });
      qc.invalidateQueries({ queryKey: ["discovery-diagnostics", projectId] });
      qc.invalidateQueries({ queryKey: ["project-runs", projectId] });
      // The backend also removes the companion *_cases.md dossier; refresh
      // the ProjectGuide so its parsed-cases chips disappear too.
      qc.invalidateQueries({ queryKey: ["agents-md", projectId] });
      toast.push({ kind: "info", title: "Suite deleted" });
    },
    onError: (err) =>
      toast.push({
        kind: "error",
        title: "Couldn't delete suite",
        description:
          err instanceof ApiError && typeof err.detail === "string"
            ? err.detail
            : String(err),
      }),
  });

  // Used by the Diagnose panel to remove broken candidate files that
  // discovery rejected (so they don't have suite rows the regular delete
  // can reach).
  const deleteWorkspaceFile = useMutation({
    mutationFn: (path: string) => api.deleteWorkspaceFile(projectId, path),
    onSuccess: (out) => {
      qc.invalidateQueries({ queryKey: ["discovery-diagnostics", projectId] });
      qc.invalidateQueries({ queryKey: ["suites", projectId] });
      toast.push({ kind: "info", title: "File deleted", description: out.deleted });
    },
    onError: (err) =>
      toast.push({
        kind: "error",
        title: "Couldn't delete file",
        description:
          err instanceof ApiError && typeof err.detail === "string"
            ? err.detail
            : String(err),
      }),
  });

  const clearRuns = useMutation({
    mutationFn: () => api.clearProjectRuns(projectId),
    onSuccess: (out) => {
      qc.invalidateQueries({ queryKey: ["project-runs", projectId] });
      toast.push({
        kind: "success",
        title: "Run history cleared",
        description:
          out.skipped > 0
            ? `Removed ${out.deleted} runs; ${out.skipped} in-flight runs were left alone.`
            : `Removed ${out.deleted} run${out.deleted === 1 ? "" : "s"}.`,
      });
    },
    onError: (err) =>
      toast.push({
        kind: "error",
        title: "Couldn't clear runs",
        description:
          err instanceof ApiError && typeof err.detail === "string"
            ? err.detail
            : String(err),
      }),
  });

  const deleteRun = useMutation({
    mutationFn: (runId: number) => api.deleteRun(runId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project-runs", projectId] });
      toast.push({ kind: "info", title: "Run deleted" });
    },
    onError: (err) =>
      toast.push({
        kind: "error",
        title: "Couldn't delete run",
        description:
          err instanceof ApiError && typeof err.detail === "string"
            ? err.detail
            : String(err),
      }),
  });

  const recentRuns = useQuery({
    queryKey: ["project-runs", projectId],
    queryFn: () => api.listProjectRuns(projectId, 10),
    enabled: !Number.isNaN(projectId),
    refetchInterval: 5_000,
  });

  const tour = useQuery({
    queryKey: ["tour", projectId],
    queryFn: () => api.getTour(projectId),
    enabled: !Number.isNaN(projectId),
  });

  // Auto-launch the tour the first time a user lands on a fresh project
  // (zero runs, not yet marked done, not yet dismissed for this session).
  // We honor a sessionStorage flag so the redirect doesn't fight the user
  // if they navigate back via the breadcrumb.
  useEffect(() => {
    if (!tour.data || !recentRuns.data) return;
    if (tour.data.state.completed) return;
    if (recentRuns.data.length > 0) return;
    const dismissKey = `tour:dismissed:${projectId}`;
    if (sessionStorage.getItem(dismissKey) === "1") return;
    sessionStorage.setItem(dismissKey, "1");
    navigate(`/projects/${projectId}/tour`, { replace: true });
  }, [tour.data, recentRuns.data, projectId, navigate]);

  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    enabled: !Number.isNaN(projectId),
  });

  const suites = useQuery({
    queryKey: ["suites", projectId],
    queryFn: () => api.listSuites(projectId),
    enabled: !Number.isNaN(projectId),
  });

  const [diagnoseOpen, setDiagnoseOpen] = useState(false);

  // Fetched lazily — when the suites list is empty (to auto-explain the
  // empty state), and when the user manually toggles the Diagnose button
  // (to inspect even on a working project).
  const diagnostics = useQuery({
    queryKey: ["discovery-diagnostics", projectId],
    queryFn: () => api.discoveryDiagnostics(projectId),
    enabled:
      !Number.isNaN(projectId) &&
      (diagnoseOpen || (suites.data?.length ?? 0) === 0),
  });

  const sync = useMutation({
    mutationFn: () => api.syncProject(projectId),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["suites", projectId] });
      toast.push({
        kind: "success",
        title: "Synced",
        description: `Found ${result.suites_found} suite${result.suites_found === 1 ? "" : "s"}.`,
      });
    },
    onError: (err) =>
      toast.push({
        kind: "error",
        title: "Couldn't sync",
        description:
          err instanceof ApiError && typeof err.detail === "string"
            ? err.detail
            : String(err),
      }),
  });

  if (project.isLoading) {
    return (
      <Card className="p-12 text-center text-sm text-muted-foreground">
        <Spinner className="mx-auto mb-2" /> Loading project…
      </Card>
    );
  }
  if (project.error || !project.data) {
    return (
      <Card className="border-destructive/40 p-6">
        <p className="text-sm text-destructive">
          Couldn't load project: {String(project.error)}
        </p>
      </Card>
    );
  }

  const p = project.data;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{p.name}</h1>
          <p className="mt-1 flex items-center gap-2 text-sm text-muted-foreground">
            <Badge tone="neutral">{p.intake_mode}</Badge>
            <span className="truncate">{p.source}</span>
          </p>
        </div>
        <Button
          variant="secondary"
          onClick={() => sync.mutate()}
          disabled={sync.isPending}
        >
          {sync.isPending ? <Spinner /> : <RefreshCw className="h-4 w-4" />}
          Sync
        </Button>
      </div>

      {/* Project-scoped tools — Suite health, baseline activity, coverage,
          AI-generated case review. Each is a separate page so the project
          detail stays focused on suites + recent runs. */}
      <nav className="flex flex-wrap gap-2 border-b border-border pb-3">
        <Link
          to={`/projects/${projectId}/health`}
          className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 text-sm hover:bg-muted/40"
        >
          <Activity className="h-3.5 w-3.5" /> Suite health
        </Link>
        <Link
          to={`/projects/${projectId}/baselines`}
          className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 text-sm hover:bg-muted/40"
        >
          <GitBranch className="h-3.5 w-3.5" /> Baseline activity
        </Link>
        <Link
          to={`/projects/${projectId}/coverage`}
          className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 text-sm hover:bg-muted/40"
        >
          <Grid3x3 className="h-3.5 w-3.5" /> Coverage
        </Link>
        <Link
          to={`/projects/${projectId}/review`}
          className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 text-sm hover:bg-muted/40"
        >
          <Sparkles className="h-3.5 w-3.5" /> Propose &amp; review cases
        </Link>
      </nav>

      {tour.data && !tour.data.state.completed && (
        <Card className="flex items-center justify-between gap-3 border-primary/30 bg-primary/5 p-4">
          <div className="flex items-start gap-3">
            <Compass className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden />
            <div className="text-sm">
              <div className="font-medium">
                Guided setup —{" "}
                {tour.data.steps.filter((s) => s.status === "complete" || s.status === "skipped").length}{" "}
                of {tour.data.steps.length} steps done
              </div>
              <p className="text-muted-foreground">
                Walk through the full record → check → approve loop with
                one click per step.
              </p>
            </div>
          </div>
          <Link to={`/projects/${projectId}/tour`}>
            <Button size="sm">Resume tour <ArrowRight className="h-3.5 w-3.5" /></Button>
          </Link>
        </Card>
      )}

      <Card>
        <div className="flex items-start justify-between gap-3 border-b border-border p-4">
          <div>
            <h2 className="font-semibold">Suites</h2>
            <p className="text-xs text-muted-foreground">
              {p.intake_mode === "http"
                ? "Author Studio-native suites for this endpoint."
                : "Discovered automatically from the workspace on sync."}
            </p>
          </div>
          {p.intake_mode !== "http" && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setDiagnoseOpen((o) => !o)}
              title="Show every .py file Studio considered, plus any import errors that hid a suite."
            >
              <Stethoscope className="h-4 w-4" />
              {diagnoseOpen ? "Hide diagnose" : "Diagnose"}
            </Button>
          )}
        </div>

        {diagnoseOpen && (
          <DiscoveryDiagnosticsPanel
            data={diagnostics.data}
            loading={diagnostics.isFetching}
            onDeleteFile={(path) => {
              if (!window.confirm(`Delete ${path} from the project workspace?`)) return;
              deleteWorkspaceFile.mutate(path);
            }}
            deleting={deleteWorkspaceFile.isPending}
          />
        )}

        {suites.isLoading && (
          <div className="p-12 text-center text-sm text-muted-foreground">
            <Spinner className="mx-auto mb-2" /> Loading suites…
          </div>
        )}

        {suites.data && suites.data.length === 0 && (
          <div className="p-12 text-center text-sm text-muted-foreground">
            {p.intake_mode === "http" ? (
              <>
                <p className="font-medium text-foreground">No suites yet.</p>
                <p className="mt-1">
                  For HTTP projects you author suites as JSON via the API:
                </p>
                <pre className="mx-auto mt-3 max-w-md overflow-auto rounded-md bg-muted/40 p-3 text-left text-xs">
{`POST /api/projects/${projectId}/suites
{
  "name": "my_suite",
  "cases": [
    {"name": "happy",
     "input": "I want a refund",
     "expect": [{"type":"contains","value":"refund"}]}
  ]
}`}
                </pre>
              </>
            ) : (
              <>
                <p className="font-medium text-foreground">No suites found yet.</p>
                <p className="mt-1">
                  Studio walked the workspace looking for files that import{" "}
                  <code>agentprdiff</code> and call <code>suite(...)</code>.
                  Once you've written one, hit Sync.
                </p>
                {diagnostics.data && diagnostics.data.failed.length > 0 && (
                  <div className="mx-auto mt-4 max-w-2xl rounded-md border border-warning/40 bg-warning/10 p-3 text-left">
                    <p className="font-medium text-foreground">
                      {diagnostics.data.failed.length} candidate file
                      {diagnostics.data.failed.length === 1 ? "" : "s"} matched
                      the heuristic but failed to load:
                    </p>
                    <ul className="mt-2 space-y-2 text-xs">
                      {diagnostics.data.failed.map((f) => (
                        <li key={f.relative_path}>
                          <div className="font-mono">{f.relative_path}</div>
                          <div className="text-muted-foreground">
                            <code>{f.load_error}</code>
                          </div>
                        </li>
                      ))}
                    </ul>
                    <p className="mt-2 text-xs text-muted-foreground">
                      Fix the import / missing dep / syntax error and hit Sync.
                    </p>
                  </div>
                )}
                <div className="mt-4 flex justify-center gap-2">
                  <Link to={`/projects/${projectId}/tour`}>
                    <Button size="sm">
                      <Lightbulb className="h-4 w-4" /> Open the guided tour
                    </Button>
                  </Link>
                </div>
              </>
            )}
          </div>
        )}

        {suites.data && suites.data.length > 0 && (
          <ul>
            {suites.data.map((s) => (
              <li
                key={s.id}
                className="flex items-center justify-between gap-4 border-b border-border px-4 py-3 last:border-0"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <FileCode2 className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0">
                    <div className="font-medium">{s.name}</div>
                    <div className="truncate text-xs text-muted-foreground">{s.file_path}</div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone="info">{s.case_count} case{s.case_count === 1 ? "" : "s"}</Badge>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={triggerRun.isPending}
                    onClick={() => triggerRun.mutate({ suiteId: s.id, command: "record" })}
                    title="Run and save the resulting traces as the new baselines"
                  >
                    <Play className="h-3.5 w-3.5" /> Record
                  </Button>
                  <Button
                    size="sm"
                    disabled={triggerRun.isPending}
                    onClick={() => triggerRun.mutate({ suiteId: s.id, command: "check" })}
                    title="Run and diff against the saved baselines"
                  >
                    <CheckCircle2 className="h-3.5 w-3.5" /> Check
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={triggerRun.isPending}
                    onClick={() => triggerRun.mutate({ suiteId: s.id, command: "review" })}
                    title="Same as check, but verbose and always exits 0"
                  >
                    <Eye className="h-3.5 w-3.5" /> Review
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={deleteSuite.isPending}
                    onClick={() => {
                      const msg =
                        p.intake_mode === "http"
                          ? `Delete suite "${s.name}" and all its runs?`
                          : `Delete suite "${s.name}" and all its runs?\n\nThe source file at ${s.file_path} will also be removed from the workspace.`;
                      if (window.confirm(msg)) deleteSuite.mutate(s.id);
                    }}
                    title="Delete this suite (and its runs)"
                    aria-label={`Delete ${s.name}`}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-destructive" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <ProjectGuide projectId={projectId} />

      {suites.data && suites.data.length > 0 && (recentRuns.data?.length ?? 0) === 0 && (
        <Card className="flex items-start gap-3 border-primary/30 bg-primary/5 p-4">
          <Lightbulb className="h-5 w-5 shrink-0 text-primary" aria-hidden />
          <div className="text-sm">
            <div className="font-medium">No runs yet for this project.</div>
            <p className="text-muted-foreground">
              Click <span className="font-medium">Record</span> on a suite above to capture
              the first baseline. After that, every <span className="font-medium">Check</span>{" "}
              run compares against it and flags regressions.
            </p>
          </div>
        </Card>
      )}

      <RecentRuns
        runs={recentRuns.data ?? []}
        loading={recentRuns.isLoading}
        onDeleteRun={(id) => {
          if (window.confirm("Delete this run and its case results?")) {
            deleteRun.mutate(id);
          }
        }}
        onClearAll={() => {
          const n = recentRuns.data?.length ?? 0;
          if (n === 0) return;
          if (window.confirm(`Delete all ${n} run${n === 1 ? "" : "s"} for this project?`)) {
            clearRuns.mutate();
          }
        }}
        clearing={clearRuns.isPending}
      />

      <Card className="p-4 text-xs text-muted-foreground">
        <div>workspace: {p.workspace_path ?? "—"}</div>
        <div>last synced: {p.last_synced_at ?? "—"}</div>
        <div>created: {p.created_at}</div>
      </Card>
    </div>
  );
}

function RecentRuns({
  runs,
  loading,
  onDeleteRun,
  onClearAll,
  clearing,
}: {
  runs: RunOut[];
  loading: boolean;
  onDeleteRun: (id: number) => void;
  onClearAll: () => void;
  clearing: boolean;
}) {
  return (
    <Card>
      <div className="flex items-center justify-between border-b border-border p-4">
        <div>
          <h2 className="font-semibold">Recent runs</h2>
          <p className="text-xs text-muted-foreground">
            Latest first. Refreshes automatically while a run is in flight.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {loading && <Spinner />}
          {runs.length > 0 && (
            <Button
              size="sm"
              variant="ghost"
              disabled={clearing}
              onClick={onClearAll}
              title="Delete every finished run for this project. In-flight runs are kept."
            >
              {clearing ? <Spinner /> : <Trash2 className="h-3.5 w-3.5 text-destructive" />}
              Clear all
            </Button>
          )}
        </div>
      </div>
      {!loading && runs.length === 0 && (
        <div className="p-6 text-sm text-muted-foreground">No runs yet.</div>
      )}
      {runs.length > 0 && (
        <ul className="divide-y divide-border">
          {runs.map((r) => (
            <li key={r.id} className="group flex items-center gap-2 pr-2">
              <Link
                to={`/runs/${r.id}`}
                className="flex flex-1 items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/40"
              >
                <span className="w-12 text-xs text-muted-foreground">#{r.id}</span>
                <Badge tone="neutral">{r.command}</Badge>
                <Badge tone={statusTone(r.status)}>{r.status}</Badge>
                <span className="ml-2 text-sm text-muted-foreground">
                  {r.cases_passed}/{r.cases_total} passed
                  {r.cases_regressed > 0 ? ` · ${r.cases_regressed} regressed` : ""}
                </span>
                <span className="ml-auto text-xs text-muted-foreground">
                  {r.finished_at ?? r.started_at ?? r.created_at}
                </span>
                <ArrowRight className="h-4 w-4 text-muted-foreground" />
              </Link>
              <button
                onClick={(e) => {
                  e.preventDefault();
                  onDeleteRun(r.id);
                }}
                className="rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-destructive group-hover:opacity-100"
                title="Delete this run"
                aria-label={`Delete run #${r.id}`}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function statusTone(status: RunOut["status"]): "neutral" | "success" | "warning" | "danger" | "info" {
  switch (status) {
    case "pending":
      return "neutral";
    case "running":
      return "info";
    case "succeeded":
      return "success";
    case "regression":
      return "warning";
    case "failed":
    case "error":
      return "danger";
  }
}

// ---------------------------------------------------------------------------

function DiscoveryDiagnosticsPanel({
  data,
  loading,
  onDeleteFile,
  deleting,
}: {
  data: DiscoveryDiagnostics | undefined;
  loading: boolean;
  onDeleteFile: (relativePath: string) => void;
  deleting: boolean;
}) {
  if (loading || !data) {
    return (
      <div className="border-b border-border bg-muted/30 p-4 text-xs text-muted-foreground">
        <Spinner className="mr-2 inline" /> Walking the workspace…
      </div>
    );
  }
  const both = data.loaded.length + data.failed.length;
  return (
    <div className="space-y-3 border-b border-border bg-muted/30 p-4 text-xs">
      <div className="text-muted-foreground">
        workspace: <code>{data.workspace_path ?? "—"}</code>
      </div>

      {both === 0 && (
        <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-foreground">
          Studio found no <code>.py</code> file containing both{" "}
          <code>from agentprdiff</code> (or <code>import agentprdiff</code>) and a{" "}
          <code>suite(...)</code> call. Both substrings need to appear in the same file
          for the heuristic to match.
        </div>
      )}

      {data.loaded.length > 0 && (
        <section>
          <h3 className="font-semibold text-foreground">
            Loaded ({data.loaded.length})
          </h3>
          <ul className="mt-1 space-y-1">
            {data.loaded.map((l) => (
              <li key={l.relative_path} className="flex items-center gap-2">
                <CheckCircle2 className="h-3 w-3 text-[hsl(var(--success))]" />
                <code className="truncate">{l.relative_path}</code>
                <span className="text-muted-foreground">
                  → <code>{l.name}</code> ({l.case_count} case{l.case_count === 1 ? "" : "s"})
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {data.failed.length > 0 && (
        <section>
          <h3 className="font-semibold text-foreground">
            Failed ({data.failed.length})
          </h3>
          <ul className="mt-1 space-y-2">
            {data.failed.map((f) => (
              <li key={f.relative_path}>
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-3 w-3 shrink-0 text-warning" />
                  <code className="flex-1 truncate">{f.relative_path}</code>
                  <button
                    onClick={() => onDeleteFile(f.relative_path)}
                    disabled={deleting}
                    className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-destructive disabled:opacity-50"
                    title={`Delete ${f.relative_path} from the workspace`}
                    aria-label={`Delete ${f.relative_path}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
                <div className="ml-5 text-muted-foreground">
                  <code className="break-all">{f.load_error}</code>
                </div>
                <DiagnosticHint loadError={f.load_error} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

// Small pattern-match against common ImportError shapes so we can give the
// user actionable next-step copy alongside the raw error.
function DiagnosticHint({ loadError }: { loadError: string }) {
  const m1 = loadError.match(/ModuleNotFoundError: No module named '([^']+)'/);
  if (m1) {
    const mod = m1[1];
    return (
      <div className="ml-5 mt-0.5 text-muted-foreground">
        ↳ Add <code>{mod}</code> to your project's{" "}
        <code>requirements.txt</code> and click <strong>Sync</strong>, OR generate the
        suite so it doesn't import <code>{mod}</code>.
      </div>
    );
  }
  const m2 = loadError.match(/cannot import name '([^']+)' from '([^']+)'/);
  if (m2) {
    return (
      <div className="ml-5 mt-0.5 text-muted-foreground">
        ↳ The module <code>{m2[2]}</code> exists but doesn't define{" "}
        <code>{m2[1]}</code>. Edit the suite to use the actual function name your
        project exports, or regenerate (Studio auto-detects entry-point names now).
      </div>
    );
  }
  if (loadError.includes("SyntaxError")) {
    return (
      <div className="ml-5 mt-0.5 text-muted-foreground">
        ↳ Edit the file to fix the syntax, then click <strong>Sync</strong>.
      </div>
    );
  }
  return null;
}

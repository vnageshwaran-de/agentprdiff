// Studio Tour — guided end-to-end walkthrough on top of the existing API.
//
// Layout:
//   ┌──────────────────────────────────────────────────────────────────────┐
//   │  ◄ back to project                                          tour: N/7 │
//   ├──── rail ────┬──────────── active step content ────────────────────┤ │
//   │  steps list  │  heading + prose + embedded UI + footer (next/skip)  │
//   └──────────────┴───────────────────────────────────────────────────────┘
//
// Most step completion is computed server-side from real data (suite count,
// run history, regressions, secrets), so the tour rail just reflects state.
// User actions (record, generate, simulate, commit) hit normal API
// endpoints; we refetch the tour snapshot after each.

import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  Circle,
  CircleDotDashed,
  Copy,
  ExternalLink,
  GitBranch,
  KeyRound,
  PartyPopper,
  Play,
  Sparkles,
  Wand2,
} from "lucide-react";

import { api, ApiError } from "@/api/client";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { Input, Label } from "@/components/ui/Input";
import { useToast } from "@/components/Toaster";
import { cn } from "@/lib/cn";
import type { ProjectOut, SuiteOut, TourSnapshot, TourStep, TourStepStatus } from "@/api/types";

const STEP_ICONS: Record<TourStepStatus, typeof Circle> = {
  pending: Circle,
  in_progress: CircleDotDashed,
  complete: CheckCircle2,
  skipped: Circle,
};

const STEP_TONE: Record<TourStepStatus, string> = {
  pending: "text-muted-foreground",
  in_progress: "text-primary",
  complete: "text-[hsl(var(--success))]",
  skipped: "text-muted-foreground",
};

function friendlyError(err: unknown): string {
  if (err instanceof ApiError && typeof err.detail === "string") return err.detail;
  if (err instanceof Error) return err.message;
  return String(err);
}

export function TourPage() {
  const { id } = useParams();
  const projectId = Number(id);
  const navigate = useNavigate();

  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    enabled: !Number.isNaN(projectId),
  });

  const tour = useQuery({
    queryKey: ["tour", projectId],
    queryFn: () => api.getTour(projectId),
    enabled: !Number.isNaN(projectId),
    // Pulled every couple seconds so step completion lights up when a run
    // succeeds in the background.
    refetchInterval: 3_000,
  });

  const [selected, setSelected] = useState<string | null>(null);

  // Default selection: the server-computed active step.
  useEffect(() => {
    if (tour.data && selected == null) setSelected(tour.data.active_step);
  }, [tour.data, selected]);

  const activeStep = useMemo(() => {
    if (!tour.data) return null;
    return tour.data.steps.find((s) => s.id === selected) ?? tour.data.steps[0];
  }, [tour.data, selected]);

  if (project.isLoading || tour.isLoading) {
    return (
      <Card className="p-12 text-center text-sm text-muted-foreground">
        <Spinner className="mx-auto mb-2" /> Loading tour…
      </Card>
    );
  }
  if (project.error || !project.data || tour.error || !tour.data) {
    return (
      <Card className="border-destructive/40 p-6 text-sm text-destructive">
        Couldn't load tour: {String(project.error ?? tour.error)}
      </Card>
    );
  }

  const completedCount = tour.data.steps.filter(
    (s) => s.status === "complete" || s.status === "skipped",
  ).length;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <button
          onClick={() => navigate(`/projects/${projectId}`)}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> Back to {project.data.name}
        </button>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-muted-foreground">
            {completedCount} of {tour.data.steps.length} steps
          </span>
          {completedCount === tour.data.steps.length && (
            <Badge tone="success">
              <PartyPopper className="mr-1 h-3 w-3" /> done
            </Badge>
          )}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-[260px_1fr]">
        <Rail
          steps={tour.data.steps}
          activeId={activeStep?.id ?? ""}
          onSelect={setSelected}
        />
        <ActiveStepCard
          project={project.data}
          tour={tour.data}
          step={activeStep!}
          onAdvance={() => {
            const idx = tour.data.steps.findIndex((s) => s.id === activeStep!.id);
            const next = tour.data.steps[idx + 1];
            if (next) setSelected(next.id);
          }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function Rail({
  steps,
  activeId,
  onSelect,
}: {
  steps: TourStep[];
  activeId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <Card className="p-2">
      <ol className="space-y-1">
        {steps.map((s, i) => {
          const Icon = STEP_ICONS[s.status];
          const active = s.id === activeId;
          return (
            <li key={s.id}>
              <button
                onClick={() => onSelect(s.id)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm",
                  active ? "bg-muted" : "hover:bg-muted/60",
                )}
              >
                <Icon
                  className={cn("h-4 w-4 shrink-0", STEP_TONE[s.status])}
                  aria-hidden
                />
                <span className="min-w-[1ch] text-xs text-muted-foreground">{i + 1}.</span>
                <span className="truncate">{s.label}</span>
                {active && <ChevronRight className="ml-auto h-4 w-4 text-muted-foreground" />}
              </button>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

// ---------------------------------------------------------------------------

function ActiveStepCard({
  project,
  tour,
  step,
  onAdvance,
}: {
  project: ProjectOut;
  tour: TourSnapshot;
  step: TourStep;
  onAdvance: () => void;
}) {
  switch (step.id) {
    case "connect":
      return <StepConnect project={project} step={step} onAdvance={onAdvance} />;
    case "discover":
      return <StepDiscover project={project} step={step} onAdvance={onAdvance} />;
    case "scaffold":
      return <StepScaffold project={project} step={step} onAdvance={onAdvance} />;
    case "configure-keys":
      return <StepKeys project={project} tour={tour} step={step} onAdvance={onAdvance} />;
    case "record-baseline":
      return <StepRecord project={project} step={step} />;
    case "regression-demo":
      return <StepRegression project={project} step={step} onAdvance={onAdvance} />;
    case "ship-ci":
      return <StepShipCI project={project} step={step} />;
    default:
      return null;
  }
}

// ---------- common header / footer pieces ----------------------------------

function StepHeader({ step, prose }: { step: TourStep; prose: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h2 className="text-xl font-semibold tracking-tight">{step.label}</h2>
      <div className="text-sm text-muted-foreground">{prose}</div>
    </div>
  );
}

function StepFooter({
  onNext,
  onSkip,
  nextDisabled,
  nextLabel = "Continue",
}: {
  onNext?: () => void;
  onSkip?: () => void;
  nextDisabled?: boolean;
  nextLabel?: string;
}) {
  return (
    <div className="mt-6 flex items-center justify-between gap-2 border-t border-border pt-4">
      {onSkip ? (
        <Button variant="ghost" onClick={onSkip}>Skip this step</Button>
      ) : (
        <span />
      )}
      {onNext && (
        <Button onClick={onNext} disabled={nextDisabled}>
          {nextLabel} <ArrowRight className="h-4 w-4" />
        </Button>
      )}
    </div>
  );
}

function StepShell({ children }: { children: React.ReactNode }) {
  return <Card className="p-6">{children}</Card>;
}

// ---------- 1. Connect -----------------------------------------------------

function StepConnect({
  project,
  step,
  onAdvance,
}: {
  project: ProjectOut;
  step: TourStep;
  onAdvance: () => void;
}) {
  return (
    <StepShell>
      <StepHeader
        step={step}
        prose={
          <>
            You're connected to <strong>{project.name}</strong> ({project.intake_mode}).
            agentprdiff will use this as the test target.
          </>
        }
      />
      <ul className="mt-4 space-y-1.5 text-sm">
        <li><Badge tone="neutral">source</Badge> <code>{project.source}</code></li>
        {project.workspace_path && (
          <li><Badge tone="neutral">workspace</Badge> <code>{project.workspace_path}</code></li>
        )}
      </ul>
      <StepFooter onNext={onAdvance} />
    </StepShell>
  );
}

// ---------- 2. Discover ----------------------------------------------------

function StepDiscover({
  project,
  step,
  onAdvance,
}: {
  project: ProjectOut;
  step: TourStep;
  onAdvance: () => void;
}) {
  const qc = useQueryClient();
  const suites = useQuery({
    queryKey: ["suites", project.id],
    queryFn: () => api.listSuites(project.id),
  });
  const sync = useMutation({
    mutationFn: () => api.syncProject(project.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["suites", project.id] });
      qc.invalidateQueries({ queryKey: ["tour", project.id] });
    },
  });

  return (
    <StepShell>
      <StepHeader
        step={step}
        prose={
          <>
            Studio walked the workspace looking for files that import{" "}
            <code>agentprdiff</code> and call <code>suite(...)</code>. If you've
            written suites already they show up below. If not, the next step
            generates one for you.
          </>
        }
      />
      <div className="mt-4 rounded-md border border-border">
        {suites.isLoading && (
          <div className="p-4 text-sm text-muted-foreground">
            <Spinner className="mr-2 inline" /> Loading…
          </div>
        )}
        {suites.data && suites.data.length === 0 && (
          <div className="p-4 text-sm text-muted-foreground">
            No suites found yet.
          </div>
        )}
        {suites.data && suites.data.length > 0 && (
          <ul className="divide-y divide-border">
            {suites.data.map((s) => (
              <li key={s.id} className="flex items-center justify-between p-3">
                <span className="font-mono text-sm">{s.name}</span>
                <Badge tone="info">{s.case_count} cases</Badge>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="mt-3 flex justify-start">
        <Button variant="secondary" onClick={() => sync.mutate()} disabled={sync.isPending}>
          {sync.isPending ? <Spinner /> : <GitBranch className="h-4 w-4" />}
          Re-sync workspace
        </Button>
      </div>
      <StepFooter
        onNext={onAdvance}
        nextLabel={(suites.data?.length ?? 0) > 0 ? "Continue" : "Generate a suite next"}
      />
    </StepShell>
  );
}

// ---------- 3. Scaffold ----------------------------------------------------

function StepScaffold({
  project,
  step,
  onAdvance,
}: {
  project: ProjectOut;
  step: TourStep;
  onAdvance: () => void;
}) {
  return (
    <StepShell>
      <StepHeader
        step={step}
        prose={
          <>
            Don't have a suite yet? Two ways to scaffold one without leaving
            Studio. Both land back here when done.
          </>
        }
      />
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Card className="p-4">
          <div className="font-medium">Generate with AI</div>
          <p className="mt-1 text-xs text-muted-foreground">
            Sends the canonical AGENTS.md + your prompt to the configured LLM
            (Gemini by default). Best when you don't have <code>*_cases.md</code>{" "}
            yet.
          </p>
          <Link to={`/projects/${project.id}/review`} className="mt-3 inline-block">
            <Button size="sm">
              <Wand2 className="h-4 w-4" /> Open Generate-with-AI
            </Button>
          </Link>
        </Card>
        <Card className="p-4">
          <div className="font-medium">Deterministic, from *_cases.md</div>
          <p className="mt-1 text-xs text-muted-foreground">
            Parses any <code>*_cases.md</code> dossier files you've already
            written. Free, offline, predictable.
          </p>
          <Link to={`/projects/${project.id}#project-guide`} className="mt-3 inline-block">
            <Button size="sm" variant="secondary">
              <Sparkles className="h-4 w-4" /> Open Project guide
            </Button>
          </Link>
        </Card>
      </div>
      <StepFooter onNext={onAdvance} />
    </StepShell>
  );
}

// ---------- 4. Configure keys ----------------------------------------------

function StepKeys({
  project,
  tour,
  step,
  onAdvance,
}: {
  project: ProjectOut;
  tour: TourSnapshot;
  step: TourStep;
  onAdvance: () => void;
}) {
  const qc = useQueryClient();
  const skip = useMutation({
    mutationFn: () => api.patchTourState(project.id, { skip: "configure-keys" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tour", project.id] });
      onAdvance();
    },
  });
  return (
    <StepShell>
      <StepHeader
        step={step}
        prose={
          tour.semantic_suites.length === 0 ? (
            <>None of your suites use the <code>semantic(...)</code> grader, so
              no LLM judge key is required. You can still configure secrets
              for your agent (e.g. <code>OPENAI_API_KEY</code>) in the Secrets
              tab.
            </>
          ) : (
            <>
              {tour.semantic_suites.length} suite
              {tour.semantic_suites.length === 1 ? "" : "s"} use the{" "}
              <code>semantic(...)</code> grader, which falls back to keyword
              matching unless an LLM judge key is present. Save{" "}
              <code>ANTHROPIC_API_KEY</code>, <code>OPENAI_API_KEY</code>, or{" "}
              <code>GEMINI_API_KEY</code> in Secrets to enable a real judge.
            </>
          )
        }
      />
      <div className="mt-4 flex flex-wrap gap-2">
        <Link to="/secrets">
          <Button size="sm">
            <KeyRound className="h-4 w-4" /> Open Secrets
          </Button>
        </Link>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => skip.mutate()}
          disabled={skip.isPending}
        >
          Skip — accept keyword-only matching
        </Button>
      </div>
      <StepFooter onNext={onAdvance} />
    </StepShell>
  );
}

// ---------- 5. Record baseline --------------------------------------------

function StepRecord({ project, step }: { project: ProjectOut; step: TourStep }) {
  const navigate = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const suites = useQuery({
    queryKey: ["suites", project.id],
    queryFn: () => api.listSuites(project.id),
  });
  const [pickedSuiteId, setPickedSuiteId] = useState<number | null>(null);
  const list = suites.data ?? [];
  const suiteId = pickedSuiteId ?? list[0]?.id;
  const record = useMutation({
    mutationFn: (sid: number) =>
      api.createRun({ project_id: project.id, suite_id: sid, command: "record" }),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["tour", project.id] });
      navigate(`/runs/${run.id}`);
    },
    onError: (err) =>
      toast.push({ kind: "error", title: "Couldn't start record", description: friendlyError(err) }),
  });

  return (
    <StepShell>
      <StepHeader
        step={step}
        prose={
          <>
            Pick a suite and click Record. Studio runs every case, captures the
            trace (output, tool calls, latency, cost), and saves it as the
            baseline. The first record run also provisions a per-project
            virtualenv — it takes a few seconds; subsequent runs are sub-second.
          </>
        }
      />
      {list.length === 0 ? (
        <p className="mt-4 text-sm text-muted-foreground">
          No suites yet — go back to step 3 to scaffold one.
        </p>
      ) : (
        <div className="mt-4 space-y-3">
          <SuitePicker suites={list} value={suiteId} onChange={setPickedSuiteId} />
          <Button
            onClick={() => suiteId && record.mutate(suiteId)}
            disabled={!suiteId || record.isPending}
          >
            {record.isPending ? <Spinner /> : <Play className="h-4 w-4" />}
            Record baseline for selected suite
          </Button>
        </div>
      )}
      <p className="mt-4 text-xs text-muted-foreground">
        When the run reaches <strong>succeeded</strong>, this step will tick
        green automatically.
      </p>
    </StepShell>
  );
}

// ---------- 6. Regression demo --------------------------------------------

function StepRegression({
  project,
  step,
  onAdvance,
}: {
  project: ProjectOut;
  step: TourStep;
  onAdvance: () => void;
}) {
  const navigate = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const suites = useQuery({
    queryKey: ["suites", project.id],
    queryFn: () => api.listSuites(project.id),
  });
  const [pickedSuiteId, setPickedSuiteId] = useState<number | null>(null);
  const list = suites.data ?? [];
  const suiteId = pickedSuiteId ?? list[0]?.id;

  const simulate = useMutation({
    mutationFn: (sid: number) => api.simulateRegression(project.id, sid),
    onSuccess: (out) => {
      toast.push({
        kind: "info",
        title: "Regression simulated",
        description: `Replaced "${out.plan.original_word}" → "${out.plan.replacement}" in ${out.plan.file_path}. Watch the live run; revert from this step when you're done.`,
      });
      qc.invalidateQueries({ queryKey: ["tour", project.id] });
      navigate(`/runs/${out.run_id}`);
    },
    onError: (err) =>
      toast.push({ kind: "error", title: "Couldn't simulate", description: friendlyError(err) }),
  });

  const revert = useMutation({
    mutationFn: () => api.revertSimulation(project.id),
    onSuccess: (out) => {
      toast.push({
        kind: "success",
        title: out.reverted ? "File restored" : "Nothing to revert",
        description: out.file_path,
      });
      qc.invalidateQueries({ queryKey: ["tour", project.id] });
    },
  });

  const skip = useMutation({
    mutationFn: () => api.patchTourState(project.id, { skip: "regression-demo" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tour", project.id] });
      onAdvance();
    },
  });

  return (
    <StepShell>
      <StepHeader
        step={step}
        prose={
          <>
            Click <strong>Simulate a regression</strong> below. Studio will
            edit one line of your agent file, kick off a Check run, and
            navigate you to the live run page. You'll see the diff viewer
            light up. Come back here to revert the file when you're done —
            nothing on disk stays mutated.
          </>
        }
      />
      {list.length === 0 ? (
        <p className="mt-4 text-sm text-muted-foreground">
          No suites yet — go back to step 3 to scaffold one.
        </p>
      ) : (
        <div className="mt-4 space-y-3">
          <SuitePicker suites={list} value={suiteId} onChange={setPickedSuiteId} />
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => suiteId && simulate.mutate(suiteId)}
              disabled={!suiteId || simulate.isPending}
            >
              {simulate.isPending ? <Spinner /> : <Wand2 className="h-4 w-4" />}
              Simulate a regression
            </Button>
            <Button variant="secondary" onClick={() => revert.mutate()} disabled={revert.isPending}>
              {revert.isPending ? <Spinner /> : <Check className="h-4 w-4" />}
              Revert the change
            </Button>
          </div>
        </div>
      )}
      <StepFooter
        onSkip={() => skip.mutate()}
        onNext={onAdvance}
      />
    </StepShell>
  );
}

// ---------- 7. Ship CI ----------------------------------------------------

function StepShipCI({ project, step }: { project: ProjectOut; step: TourStep }) {
  const qc = useQueryClient();
  const toast = useToast();
  const preview = useQuery({
    queryKey: ["ci-yaml", project.id],
    queryFn: () => api.ciYamlPreview(project.id),
  });
  const commit = useMutation({
    mutationFn: () => api.commitCIYaml(project.id),
    onSuccess: (out) => {
      qc.invalidateQueries({ queryKey: ["tour", project.id] });
      toast.push({
        kind: out.committed ? "success" : "info",
        title: out.committed ? (out.pushed ? "Committed + pushed" : "Committed") : "No-op",
        description: out.message,
      });
    },
    onError: (err) =>
      toast.push({ kind: "error", title: "Commit failed", description: friendlyError(err) }),
  });

  const copy = async () => {
    if (!preview.data) return;
    try {
      await navigator.clipboard.writeText(preview.data.content);
      toast.push({ kind: "success", title: "Copied to clipboard" });
    } catch {
      toast.push({ kind: "error", title: "Clipboard blocked", description: "Select the text below and copy manually." });
    }
  };

  return (
    <StepShell>
      <StepHeader
        step={step}
        prose={
          <>
            This is the GitHub Actions workflow that runs{" "}
            <code>agentprdiff check</code> on every PR. Copy it into{" "}
            <code>.github/workflows/agentprdiff.yml</code> in your repo, or for
            git projects, let Studio commit and push it for you (needs a{" "}
            <code>GIT_TOKEN</code> secret with repo write access).
          </>
        }
      />
      {preview.data && (
        <pre className="mt-4 max-h-72 overflow-auto rounded-md bg-muted p-3 text-xs">
          {preview.data.content}
        </pre>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" onClick={copy}>
          <Copy className="h-4 w-4" /> Copy YAML
        </Button>
        {project.intake_mode === "git" && (
          <Button
            size="sm"
            onClick={() => commit.mutate()}
            disabled={commit.isPending}
          >
            {commit.isPending ? <Spinner /> : <GitBranch className="h-4 w-4" />}
            Commit & push to origin
          </Button>
        )}
        <a
          href="https://docs.github.com/en/actions/quickstart"
          target="_blank"
          rel="noreferrer"
          className="ml-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:underline"
        >
          <ExternalLink className="h-3 w-3" /> GitHub Actions docs
        </a>
      </div>
    </StepShell>
  );
}

// ---------- shared bits ----------------------------------------------------

function SuitePicker({
  suites,
  value,
  onChange,
}: {
  suites: SuiteOut[];
  value: number | undefined;
  onChange: (id: number) => void;
}) {
  if (suites.length === 0) return null;
  return (
    <div className="grid gap-1.5">
      <Label htmlFor="suite-picker">Suite</Label>
      <select
        id="suite-picker"
        className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background"
        value={value ?? ""}
        onChange={(e) => onChange(Number(e.target.value))}
      >
        {suites.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name} ({s.case_count} cases)
          </option>
        ))}
      </select>
    </div>
  );
}

// Avoid an unused-import warning when Input isn't referenced.
export type _UnusedInput = typeof Input;

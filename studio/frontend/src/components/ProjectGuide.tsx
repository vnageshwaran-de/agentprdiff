// Project Guide — renders AGENTS.md + parsed case dossiers, with two actions:
//
//   • "Scaffold a starter AGENTS.md" (git/zip projects with no AGENTS.md)
//   • "Create suite skeleton from cases" (when *_cases.md files were parsed)
//
// Markdown is rendered with react-markdown + remark-gfm so tables, fenced
// code, and task lists all look reasonable.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  FileText,
  Sparkles,
  ListChecks,
  Upload,
  AlertTriangle,
  Wand2,
  X,
  CheckCircle2,
  Eraser,
} from "lucide-react";

import { api, ApiError } from "@/api/client";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input, Label, Textarea } from "@/components/ui/Input";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { useToast } from "@/components/Toaster";
import type { CaseDossier, GenerateSuiteOut } from "@/api/types";

export function ProjectGuide({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();

  const guide = useQuery({
    queryKey: ["agents-md", projectId],
    queryFn: () => api.getAgentsMd(projectId),
    enabled: !Number.isNaN(projectId),
  });

  const scaffoldStarter = useMutation({
    mutationFn: (overwrite: boolean) => api.scaffoldStarterAgentsMd(projectId, overwrite),
    onSuccess: (out) => {
      qc.invalidateQueries({ queryKey: ["agents-md", projectId] });
      toast.push({
        kind: "success",
        title: "Starter AGENTS.md written",
        description: `${out.path} (${out.wrote_bytes} bytes). Edit it in your editor, then refresh.`,
      });
    },
    onError: (err) =>
      toast.push({
        kind: "error",
        title: "Couldn't scaffold",
        description: friendlyError(err),
      }),
  });

  // Remove *_cases.md files whose suite no longer exists. Useful when a
  // suite was deleted before the dossier-cleanup-on-delete fix landed —
  // the markdown stays in the workspace and the parser keeps showing the
  // stale cases.
  const cleanupOrphans = useMutation({
    mutationFn: () => api.cleanupCaseOrphans(projectId),
    onSuccess: (out) => {
      qc.invalidateQueries({ queryKey: ["agents-md", projectId] });
      qc.invalidateQueries({ queryKey: ["suites", projectId] });
      const n = out.deleted.length;
      toast.push({
        kind: n > 0 ? "success" : "info",
        title:
          n === 0
            ? "No orphan dossiers found"
            : `Removed ${n} orphan dossier${n === 1 ? "" : "s"}`,
        description: n > 0 ? out.deleted.join(", ") : undefined,
      });
    },
    onError: (err) =>
      toast.push({
        kind: "error",
        title: "Couldn't clean up dossiers",
        description: friendlyError(err),
      }),
  });

  const [suiteName, setSuiteName] = useState("");
  const scaffoldSuite = useMutation({
    mutationFn: () =>
      api.scaffoldSuiteFromAgentsMd(projectId, { suite_name: suiteName }),
    onSuccess: (out) => {
      qc.invalidateQueries({ queryKey: ["suites", projectId] });
      qc.invalidateQueries({ queryKey: ["agents-md", projectId] });
      toast.push({
        kind: "success",
        title: "Suite skeleton created",
        description:
          out.intake_mode === "http"
            ? `${out.cases_used} cases written to the database. Open the Suites panel above.`
            : `Wrote ${out.file_path}. Hit Sync above so Studio discovers the new suite.`,
      });
      setSuiteName("");
    },
    onError: (err) =>
      toast.push({
        kind: "error",
        title: "Couldn't create skeleton",
        description: friendlyError(err),
      }),
  });

  if (guide.isLoading) {
    return (
      <Card className="p-6 text-sm text-muted-foreground">
        <Spinner className="mr-2 inline" /> Loading project guide…
      </Card>
    );
  }
  if (guide.error || !guide.data) {
    return null;
  }

  const g = guide.data;

  return (
    <Card>
      <div className="flex items-start justify-between gap-3 border-b border-border p-4">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 font-semibold">
            <FileText className="h-4 w-4" /> Project guide
          </h2>
          <p className="text-xs text-muted-foreground">
            Rendered from <code>AGENTS.md</code> and any <code>*_cases.md</code>{" "}
            files in the workspace.
          </p>
        </div>
        {g.exists && (
          <Badge tone="info">
            {g.cases.length} parsed case{g.cases.length === 1 ? "" : "s"}
          </Badge>
        )}
      </div>

      {!g.exists && (
        <EmptyState
          supportsDisk={g.supports_disk_scaffold}
          onScaffold={() => scaffoldStarter.mutate(false)}
          loading={scaffoldStarter.isPending}
        />
      )}

      {g.exists && (
        <>
          {/* AI scaffold action — always available when AGENTS.md exists */}
          <AIScaffoldStrip projectId={projectId} supportsDisk={g.supports_disk_scaffold} />

          {/* Parsed cases + scaffold action come FIRST when present —
              that's the actionable surface; the rendered AGENTS.md prose
              is reference material and lives in a collapsed details below. */}
          {g.cases.length > 0 && (
            <div className="space-y-3 border-b border-border bg-muted/30 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <ListChecks className="h-4 w-4" />
                <h3 className="font-semibold">Parsed cases</h3>
                <Badge tone="neutral">
                  from {uniqueSources(g.cases).join(", ")}
                </Badge>
                <div className="ml-auto">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => cleanupOrphans.mutate()}
                    disabled={cleanupOrphans.isPending}
                    title="Delete *_cases.md files whose suite no longer exists"
                  >
                    {cleanupOrphans.isPending ? (
                      <Spinner className="h-3.5 w-3.5" />
                    ) : (
                      <Eraser className="h-3.5 w-3.5" />
                    )}
                    Clean up orphans
                  </Button>
                </div>
              </div>
              <ul className="grid gap-2 sm:grid-cols-2">
                {g.cases.map((c, i) => (
                  <CaseChip key={i} c={c} />
                ))}
              </ul>

              <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_auto]">
                <div className="grid gap-1.5">
                  <Label htmlFor="scaffold-suite-name">New suite name</Label>
                  <Input
                    id="scaffold-suite-name"
                    placeholder="e.g. checkout_agent"
                    value={suiteName}
                    onChange={(e) => setSuiteName(e.target.value)}
                  />
                </div>
                <Button
                  onClick={() => scaffoldSuite.mutate()}
                  disabled={!suiteName || scaffoldSuite.isPending}
                  className="self-end"
                >
                  {scaffoldSuite.isPending ? <Spinner /> : <Sparkles className="h-4 w-4" />}
                  Create suite skeleton
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                Studio seeds each case with a placeholder <code>contains(...)</code> grader
                from the input text plus a 5s latency budget. Tighten the
                assertions before recording baselines.
              </p>
            </div>
          )}

          {/* AGENTS.md body — collapsed by default so it doesn't swamp the
              card. TOC chips stay visible inside the summary as a teaser. */}
          <details className="group">
            <summary className="flex cursor-pointer flex-wrap items-center gap-2 border-b border-border p-3 text-xs hover:bg-muted/30">
              <span
                aria-hidden
                className="inline-block text-muted-foreground transition-transform group-open:rotate-90"
              >
                ▸
              </span>
              <span className="font-medium">AGENTS.md</span>
              <span className="text-muted-foreground">
                ({g.agents_md_path})
              </span>
              {g.sections.length > 0 && (
                <span className="ml-2 flex flex-wrap items-center gap-1.5">
                  {g.sections.slice(0, 6).map((s) => (
                    <Badge key={s} tone="neutral" className="font-normal">
                      {s}
                    </Badge>
                  ))}
                  {g.sections.length > 6 && (
                    <span className="text-muted-foreground">
                      +{g.sections.length - 6} more
                    </span>
                  )}
                </span>
              )}
            </summary>
            <div className="prose prose-sm max-w-none p-4">
              <RenderedMarkdown markdown={g.agents_md_content} />
            </div>
          </details>
        </>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------

function EmptyState({
  supportsDisk,
  onScaffold,
  loading,
}: {
  supportsDisk: boolean;
  onScaffold: () => void;
  loading: boolean;
}) {
  return (
    <div className="p-6 text-sm">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
        <div>
          <p className="font-medium text-foreground">No AGENTS.md found.</p>
          <p className="mt-1 text-muted-foreground">
            AGENTS.md is the prose playbook that explains how agentprdiff is
            adopted in a project. Paired with one or more <code>*_cases.md</code>{" "}
            files, Studio can parse the case definitions and auto-generate a
            suite skeleton.
          </p>
          {supportsDisk ? (
            <div className="mt-3 flex items-center gap-2">
              <Button onClick={onScaffold} disabled={loading}>
                {loading ? <Spinner /> : <Upload className="h-4 w-4" />}
                Scaffold a starter AGENTS.md
              </Button>
              <a
                className="text-xs text-muted-foreground underline-offset-2 hover:underline"
                href="https://github.com/vnageshwaran-de/agentprdiff/blob/main/AGENTS.md"
                target="_blank"
                rel="noreferrer"
              >
                view the canonical AGENTS.md
              </a>
            </div>
          ) : (
            <p className="mt-3 text-xs text-muted-foreground">
              HTTP projects have no workspace on disk; this scaffold action
              works for git and zip projects.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function CaseChip({ c }: { c: CaseDossier }) {
  return (
    <li className="rounded-md border border-border bg-card text-sm">
      <details className="group">
        <summary className="cursor-pointer list-none p-3">
          <div className="flex items-baseline justify-between gap-2">
            <span className="flex items-center gap-1.5 truncate">
              {/* Caret rotates on open via the parent <details>[open] selector */}
              <span
                aria-hidden
                className="inline-block transition-transform group-open:rotate-90 text-muted-foreground"
              >
                ▸
              </span>
              <code className="truncate font-semibold">{c.name}</code>
            </span>
            <span className="shrink-0 text-xs text-muted-foreground">{c.source_file}</span>
          </div>
          {c.what_it_tests && (
            <p className="mt-1 line-clamp-2 pl-5 text-xs text-muted-foreground">
              {c.what_it_tests}
            </p>
          )}
          {c.assertions.length > 0 && (
            <p className="mt-1 pl-5 text-xs text-muted-foreground">
              {c.assertions.length} assertion{c.assertions.length === 1 ? "" : "s"} parsed
            </p>
          )}
        </summary>
        <div className="space-y-3 border-t border-border p-3 text-xs">
          {c.what_it_tests && (
            <Field title="What it tests">{c.what_it_tests}</Field>
          )}
          {c.input_text && (
            <Field title="Input">
              <code className="break-all">{c.input_text}</code>
            </Field>
          )}
          {c.assertions.length > 0 && (
            <Field title="Assertions">
              <ul className="list-disc space-y-0.5 pl-5">
                {c.assertions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            </Field>
          )}
          {c.code_impacted && (
            <Field title="Code impacted">
              <code className="break-all">{c.code_impacted}</code>
            </Field>
          )}
          {c.application_impact && (
            <Field title="Application impact">{c.application_impact}</Field>
          )}
        </div>
      </details>
    </li>
  );
}

function Field({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {title}
      </div>
      <div className="mt-0.5">{children}</div>
    </div>
  );
}

function RenderedMarkdown({ markdown }: { markdown: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        // Tailwind doesn't bundle prose styles by default; we ship just
        // enough hand-rolled rules to make the markdown readable inside
        // the card.
        h1: (props) => <h1 className="text-xl font-semibold tracking-tight" {...props} />,
        h2: (props) => <h2 className="mt-4 text-base font-semibold" {...props} />,
        h3: (props) => <h3 className="mt-3 text-sm font-semibold" {...props} />,
        p: (props) => <p className="my-2 leading-relaxed" {...props} />,
        ul: (props) => <ul className="my-2 list-disc pl-5" {...props} />,
        ol: (props) => <ol className="my-2 list-decimal pl-5" {...props} />,
        li: (props) => <li className="my-1" {...props} />,
        a: (props) => (
          <a
            className="text-primary underline underline-offset-2"
            target="_blank"
            rel="noreferrer"
            {...props}
          />
        ),
        code: ({ className, children, ...rest }) => {
          const inline = !className;
          if (inline) {
            return (
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs" {...rest}>
                {children}
              </code>
            );
          }
          return (
            <pre className="my-2 max-h-72 overflow-auto rounded-md bg-muted p-3 text-xs">
              <code className={className} {...rest}>
                {children}
              </code>
            </pre>
          );
        },
        blockquote: (props) => (
          <blockquote
            className="my-2 border-l-2 border-border pl-3 text-sm text-muted-foreground"
            {...props}
          />
        ),
        table: (props) => (
          <div className="my-2 overflow-x-auto">
            <table className="w-full text-sm" {...props} />
          </div>
        ),
        th: (props) => <th className="border-b border-border p-1 text-left font-semibold" {...props} />,
        td: (props) => <td className="border-b border-border/50 p-1 align-top" {...props} />,
      }}
    >
      {markdown}
    </ReactMarkdown>
  );
}

// ---------------------------------------------------------------------------

function uniqueSources(cases: CaseDossier[]): string[] {
  const set = new Set<string>();
  for (const c of cases) set.add(c.source_file);
  return Array.from(set);
}

function friendlyError(err: unknown): string {
  if (err instanceof ApiError && typeof err.detail === "string") return err.detail;
  if (err instanceof Error) return err.message;
  return String(err);
}

function ValidationBlock({ result }: { result: GenerateSuiteOut }) {
  // Three-state load check:
  //   "ok"          loaded cleanly in Studio's host process
  //   "venv"        host fails but it's a known runtime dep (e.g. `openai`)
  //                  → will load at run time in the project's venv
  //   "broken"     hard fail: missing heuristic markers, real import error
  const loadState: "ok" | "venv" | "broken" = result.loadable
    ? "ok"
    : result.loadable_via_venv
    ? "venv"
    : "broken";

  const checks = [
    { label: "Parses as Python", pass: result.compiles, detail: result.parse_error },
    { label: "Imports agentprdiff", pass: result.has_imports, detail: null },
    { label: "Defines a suite(...)", pass: result.has_suite_call, detail: null },
    {
      label:
        loadState === "ok"
          ? "Loads without error"
          : loadState === "venv"
          ? `Imports your project's deps (${result.missing_module})`
          : "Loads without error",
      pass: loadState !== "broken",
      tone: loadState === "venv" ? ("warn" as const) : undefined,
      detail:
        loadState === "venv"
          ? `Missing in Studio's container, but expected to be in your project venv. ` +
            `If your requirements.txt lists ${result.missing_module}, the run will work.`
          : loadState === "broken"
          ? result.load_error
          : null,
    },
  ];
  const allGood = checks.every((c) => c.pass);
  const anyWarn = checks.some((c) => "tone" in c && c.tone === "warn");

  return (
    <div
      className={cn(
        "border-y border-border px-4 py-3 text-xs",
        allGood && !anyWarn && "bg-[hsl(var(--success))]/10",
        anyWarn && "bg-warning/10",
        !allGood && "bg-warning/10",
      )}
    >
      <div className="grid gap-1.5 sm:grid-cols-2">
        {checks.map((c) => {
          const Icon = c.pass
            ? "tone" in c && c.tone === "warn"
              ? AlertTriangle
              : CheckCircle2
            : AlertTriangle;
          const iconClass = c.pass
            ? "tone" in c && c.tone === "warn"
              ? "text-warning"
              : "text-[hsl(var(--success))]"
            : "text-warning";
          return (
            <div key={c.label} className="flex items-start gap-2">
              <Icon className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", iconClass)} />
              <div className="min-w-0">
                <div className={c.pass ? "" : "font-medium"}>{c.label}</div>
                {c.detail && (
                  <div className="text-muted-foreground">
                    <code className="break-all">{c.detail}</code>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {loadState === "ok" && result.discovered_suites.length > 0 && (
        <div className="mt-2 text-muted-foreground">
          Discovered suite{result.discovered_suites.length === 1 ? "" : "s"}:{" "}
          <code>{result.discovered_suites.join(", ")}</code> ·{" "}
          {result.total_cases} case{result.total_cases === 1 ? "" : "s"} total.
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AI scaffold — drawer + preview modal
// ---------------------------------------------------------------------------

function AIScaffoldStrip({
  projectId,
  supportsDisk,
}: {
  projectId: number;
  supportsDisk: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<GenerateSuiteOut | null>(null);
  const [submittedName, setSubmittedName] = useState("");
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border bg-primary/5 px-4 py-3">
      <div className="text-sm">
        <span className="font-medium">Generate a suite with AI.</span>{" "}
        <span className="text-muted-foreground">
          Studio sends the bundled agentprdiff AGENTS.md plus your prompt to
          the model configured in <a className="underline" href="/secrets">Secrets</a>{" "}
          (Anthropic → OpenAI → Gemini, fallback Ollama).
        </span>
      </div>
      <Button onClick={() => setOpen(true)} disabled={!supportsDisk} title={
        supportsDisk
          ? "Open the AI scaffold drawer"
          : "AI scaffold writes a file to the workspace — http projects don't have one"
      }>
        <Wand2 className="h-4 w-4" /> Generate with AI
      </Button>
      {open && (
        <AIScaffoldDrawer
          projectId={projectId}
          onClose={() => setOpen(false)}
          onResult={(name, out) => {
            setSubmittedName(name);
            setPreview(out);
            setOpen(false);
          }}
        />
      )}
      {preview && (
        <AIScaffoldPreviewModal
          projectId={projectId}
          suiteName={submittedName}
          result={preview}
          onClose={() => setPreview(null)}
          onRegenerate={() => {
            setPreview(null);
            setOpen(true);
          }}
        />
      )}
    </div>
  );
}

function AIScaffoldDrawer({
  projectId,
  onClose,
  onResult,
}: {
  projectId: number;
  onClose: () => void;
  onResult: (suiteName: string, out: GenerateSuiteOut) => void;
}) {
  const toast = useToast();
  const [suiteName, setSuiteName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const generate = useMutation({
    mutationFn: () =>
      api.generateSuiteWithAI(projectId, {
        suite_name: suiteName,
        prompt,
        model: model || undefined,
      }),
    onSuccess: (out) => onResult(suiteName, out),
    onError: (err) =>
      toast.push({
        kind: "error",
        title: "Couldn't generate",
        description: friendlyError(err),
      }),
  });

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-lg border border-border bg-card shadow-lg">
        <div className="flex items-center justify-between border-b border-border p-4">
          <h2 className="flex items-center gap-2 font-semibold">
            <Wand2 className="h-4 w-4" /> Generate suite with AI
          </h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-3 p-4">
          <div className="grid gap-1.5">
            <Label htmlFor="ai-suite-name">Suite name</Label>
            <Input
              id="ai-suite-name"
              value={suiteName}
              onChange={(e) => setSuiteName(e.target.value)}
              placeholder="e.g. checkout_agent"
            />
            <p className="text-xs text-muted-foreground">
              Becomes the Python identifier + filename: <code>suites/&lt;name&gt;.py</code>.
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ai-prompt">What should this suite test?</Label>
            <Textarea
              id="ai-prompt"
              rows={6}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder={
                "Be specific. e.g. 'Refund flow for orders: must call lookup_order " +
                "once, mention the refund amount in the output, latency under 5s. " +
                "Also cover the no-order-number error path.'"
              }
            />
            <p className="text-xs text-muted-foreground">
              The model also sees the canonical agentprdiff AGENTS.md so it knows the
              suite/case/grader shapes.
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ai-model">Model override (optional)</Label>
            <Input
              id="ai-model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="claude-sonnet-4-6 / gpt-4o-mini / llama3.1:8b / …"
            />
          </div>
          {generate.error && (
            <p className="text-sm text-destructive">{friendlyError(generate.error)}</p>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-border p-4">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button
            onClick={() => generate.mutate()}
            disabled={!suiteName || !prompt || generate.isPending}
          >
            {generate.isPending ? <Spinner /> : <Wand2 className="h-4 w-4" />}
            Generate
          </Button>
        </div>
      </div>
    </div>
  );
}

function AIScaffoldPreviewModal({
  projectId,
  suiteName,
  result,
  onClose,
  onRegenerate,
}: {
  projectId: number;
  suiteName: string;
  result: GenerateSuiteOut;
  onClose: () => void;
  onRegenerate: () => void;
}) {
  const toast = useToast();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [content, setContent] = useState(result.generated_python);
  const [dossier, setDossier] = useState(result.generated_dossier);
  const [activeTab, setActiveTab] = useState<"suite" | "dossier">("suite");
  const [forceSave, setForceSave] = useState(false);
  // Post-save state: a success panel with next-step buttons, instead of
  // a silent close. The user just did real work — make the next click
  // obvious.
  const [saved, setSaved] = useState<{
    filePath: string;
    bytesWritten: number;
    suiteId: number | null;
    dossierPath: string | null;
  } | null>(null);

  const save = useMutation({
    mutationFn: () =>
      api.saveGeneratedSuite(projectId, {
        suite_name: suiteName,
        content,
        dossier: dossier.trim() ? dossier : undefined,
        overwrite: false,
        force: forceSave,
      }),
    onSuccess: async (out) => {
      qc.invalidateQueries({ queryKey: ["suites", projectId] });
      qc.invalidateQueries({ queryKey: ["agents-md", projectId] });
      toast.push({
        kind: "success",
        title: "Suite saved",
        description: out.dossier_path
          ? `${out.file_path} + ${out.dossier_path}`
          : `${out.file_path} (${out.bytes_written} bytes)`,
      });
      // Look up the just-discovered suite so we can offer to record its
      // first baseline in one click. Discovery runs synchronously inside
      // save-generated, so by the time we get here the row exists.
      try {
        const suites = await api.listSuites(projectId);
        const match = suites.find((s) => s.name === suiteName);
        setSaved({
          filePath: out.file_path,
          bytesWritten: out.bytes_written,
          suiteId: match?.id ?? null,
          dossierPath: out.dossier_path,
        });
      } catch {
        setSaved({
          filePath: out.file_path,
          bytesWritten: out.bytes_written,
          suiteId: null,
          dossierPath: out.dossier_path,
        });
      }
    },
    onError: (err) =>
      toast.push({
        kind: "error",
        title: "Couldn't save",
        description: friendlyError(err),
      }),
  });

  const recordNow = useMutation({
    mutationFn: () => {
      if (saved?.suiteId == null) throw new Error("suite id not available yet");
      return api.createRun({
        project_id: projectId,
        suite_id: saved.suiteId,
        command: "record",
      });
    },
    onSuccess: (run) => {
      onClose();
      navigate(`/runs/${run.id}`);
    },
    onError: (err) =>
      toast.push({
        kind: "error",
        title: "Couldn't start the record run",
        description: friendlyError(err),
      }),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex h-[80vh] w-full max-w-4xl flex-col rounded-lg border border-border bg-card shadow-lg">
        <div className="flex items-center justify-between border-b border-border p-4">
          <div>
            <h2 className="flex items-center gap-2 font-semibold">
              <Sparkles className="h-4 w-4" />
              {saved ? "Suite saved" : "Preview generated suite"}
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {saved ? (
                <>
                  Wrote <code>{saved.filePath}</code>
                  {saved.dossierPath && (
                    <>
                      {" "}+ <code>{saved.dossierPath}</code>
                    </>
                  )}{" "}
                  and ran discovery.
                </>
              ) : (
                <>
                  produced by <code>{result.provider}</code> / <code>{result.model}</code> (
                  <code>{result.source}</code>) · agent target{" "}
                  <code>{result.agent_import_target}</code>
                </>
              )}
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {!saved && (
          <>
            <ValidationBlock result={result} />
            {/* Tabs */}
            <div className="flex border-b border-border bg-muted/30 text-xs">
              <button
                onClick={() => setActiveTab("suite")}
                className={cn(
                  "px-4 py-2 transition-colors",
                  activeTab === "suite"
                    ? "border-b-2 border-primary font-medium text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                Suite (.py)
              </button>
              <button
                onClick={() => setActiveTab("dossier")}
                className={cn(
                  "px-4 py-2 transition-colors",
                  activeTab === "dossier"
                    ? "border-b-2 border-primary font-medium text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                Case dossier (.md)
                {result.dossier_has_cases && (
                  <Badge tone="info" className="ml-2">
                    ready
                  </Badge>
                )}
                {!result.dossier_has_cases && (
                  <Badge tone="warning" className="ml-2">
                    empty
                  </Badge>
                )}
              </button>
              <span className="ml-auto px-4 py-2 text-muted-foreground">
                {activeTab === "suite"
                  ? `suites/${suiteName || "<name>"}.py`
                  : `suites/${suiteName || "<name>"}_cases.md`}
              </span>
            </div>
            {activeTab === "suite" && (
              <Textarea
                rows={24}
                value={content}
                onChange={(e) => setContent(e.target.value)}
                className="flex-1 resize-none rounded-none border-0 border-b border-border font-mono text-xs"
              />
            )}
            {activeTab === "dossier" && (
              <Textarea
                rows={24}
                value={dossier}
                onChange={(e) => setDossier(e.target.value)}
                placeholder={
                  "### `case_name`\n\n**What it tests.** ...\n**Input.** ...\n**Assertions.**\n- ...\n**Code impacted.** path/to/file.py:NN\n**Application impact.** ..."
                }
                className="flex-1 resize-none rounded-none border-0 border-b border-border font-mono text-xs"
              />
            )}
          </>
        )}

        {saved && (
          <div className="flex flex-1 flex-col gap-4 p-6">
            <div className="rounded-md border border-[hsl(var(--success))]/40 bg-[hsl(var(--success))]/10 p-4">
              <div className="flex items-start gap-3">
                <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-[hsl(var(--success))]" />
                <div className="text-sm">
                  <div className="font-medium">All set.</div>
                  <p className="mt-1 text-muted-foreground">
                    The suite is on disk at <code>{saved.filePath}</code> and
                    discovered. You can run <strong>Record</strong> right now to
                    capture the first baseline, or come back to it later from
                    the project page.
                  </p>
                </div>
              </div>
            </div>
            <details className="rounded-md border border-border bg-muted/30 p-3 text-xs">
              <summary className="cursor-pointer text-muted-foreground">
                Preview the saved file
              </summary>
              <pre className="mt-2 max-h-64 overflow-auto font-mono">{content}</pre>
            </details>
          </div>
        )}

        <div className="flex items-center justify-between gap-2 border-t border-border p-4">
          {!saved ? (
            <>
              <Button variant="ghost" onClick={onRegenerate}>
                <Wand2 className="h-4 w-4" /> Regenerate
              </Button>
              <div className="flex items-center gap-3">
                {!result.loadable && !result.loadable_via_venv && result.compiles && (
                  <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={forceSave}
                      onChange={(e) => setForceSave(e.target.checked)}
                    />
                    Save anyway
                  </label>
                )}
                <Button variant="secondary" onClick={onClose}>Discard</Button>
                <Button
                  onClick={() => save.mutate()}
                  disabled={
                    save.isPending ||
                    !result.compiles ||
                    (!result.loadable && !result.loadable_via_venv && !forceSave)
                  }
                >
                  {save.isPending ? <Spinner /> : <CheckCircle2 className="h-4 w-4" />}
                  Save & sync
                </Button>
              </div>
            </>
          ) : (
            <>
              <span className="text-xs text-muted-foreground">
                {saved.suiteId != null
                  ? "Discovered automatically — ready to run."
                  : "Saved on disk; refresh the suites list to pick it up."}
              </span>
              <div className="flex gap-2">
                <Button variant="secondary" onClick={onClose}>Close</Button>
                <Button
                  onClick={() => recordNow.mutate()}
                  disabled={saved.suiteId == null || recordNow.isPending}
                  title={
                    saved.suiteId == null
                      ? "Suite wasn't discovered automatically — check the suites list and try again."
                      : "Run the suite once and save the traces as the baseline."
                  }
                >
                  {recordNow.isPending ? <Spinner /> : <Sparkles className="h-4 w-4" />}
                  Record baseline now
                </Button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

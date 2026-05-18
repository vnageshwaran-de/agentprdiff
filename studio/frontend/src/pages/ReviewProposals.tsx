// HITL review of AI-generated test cases.
//
// v0 wraps the existing /agents-md/generate-suite endpoint with a review UX:
// generate, preview the suite Python the LLM produced, edit it freely, save.
// Per-case approval (the review's full §3.2 vision) requires an AST-aware
// backend refactor of agents_md.py that splits the generated suite into
// individual proposals — flagged in INTEGRATION-review-proposals.md.

import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, PackagePlus, Sparkles } from "lucide-react";

import { api, ApiError } from "@/api/client";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/cn";
import type { AgentsMdOut, GenerateSuiteOut } from "@/api/types";

export function ReviewProposalsPage() {
  const { id } = useParams();
  const projectId = Number(id);
  const qc = useQueryClient();

  const agents = useQuery<AgentsMdOut>({
    queryKey: ["agents-md", projectId],
    queryFn: () => api.getAgentsMd(projectId),
    enabled: !Number.isNaN(projectId),
  });

  // One-click fix for the most common load failure: the LLM-generated suite
  // imports the project's agent, the agent imports `openai` (or any other
  // dep), and the project's venv doesn't have it. We append the missing
  // package to requirements.txt and re-sync, which triggers a venv rebuild
  // with the new dep on the next run.
  const addRequirement = useMutation({
    mutationFn: async (pkg: string) => {
      const result = await api.addRequirement(projectId, { package: pkg });
      // Sync rebuilds the venv with the new dep on the next run trigger,
      // and re-discovers suites so the load error disappears.
      await api.syncProject(projectId);
      return result;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["suites", projectId] });
      qc.invalidateQueries({ queryKey: ["agents-md", projectId] });
    },
  });

  const [suiteName, setSuiteName] = useState("ai_generated");
  const [prompt, setPrompt] = useState(
    "Generate cases covering the agent's main happy paths and one regression scenario.",
  );
  // Deep scan: when on, Studio reads the agent module + siblings + tools/* +
  // README and includes them in the LLM context. Default ON because that's
  // what gives the LLM enough context to write a "full suite" tied to the
  // actual code (rather than a generic suite from the playbook alone).
  const [deepScan, setDeepScan] = useState(true);

  const generate = useMutation({
    mutationFn: () =>
      api.generateSuiteWithAI(projectId, {
        suite_name: suiteName,
        prompt,
        deep_scan: deepScan,
      }),
  });

  const [edited, setEdited] = useState<string>("");
  const [editedDossier, setEditedDossier] = useState<string>("");

  // Reset the editor when a new generation lands.
  const result: GenerateSuiteOut | undefined = generate.data;

  const save = useMutation({
    mutationFn: () =>
      api.saveGeneratedSuite(projectId, {
        suite_name: suiteName,
        content: edited || result?.generated_python || "",
        dossier: editedDossier || result?.generated_dossier || undefined,
        overwrite: true,
        force: !result?.loadable,
      }),
  });

  if (agents.isLoading) {
    return <Card className="p-6 text-sm text-muted-foreground">Loading…</Card>;
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
        <h1 className="text-2xl font-semibold tracking-tight">Propose & review cases</h1>
        <span className="font-mono text-xs italic text-muted-foreground">
          v0 — full per-proposal editing pending agents_md refactor
        </span>
      </div>

      <Card className="space-y-3 p-4">
        <label className="flex flex-col gap-1">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            suite name
          </span>
          <input
            type="text"
            value={suiteName}
            onChange={(e) => setSuiteName(e.target.value)}
            className="rounded-md border border-border bg-background px-2 py-1 font-mono text-sm"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            prompt
          </span>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            className="rounded-md border border-border bg-background px-2 py-1 text-sm"
          />
        </label>
        <label className="flex items-start gap-2 rounded-md border border-border bg-muted/20 p-2.5 text-xs">
          <input
            type="checkbox"
            checked={deepScan}
            onChange={(e) => setDeepScan(e.target.checked)}
            className="mt-0.5 h-4 w-4 cursor-pointer"
          />
          <div className="flex-1">
            <div className="font-medium">Deep scan workspace</div>
            <div className="text-muted-foreground">
              Read the agent module, its siblings, any{" "}
              <code className="rounded-md bg-card px-1 font-mono">tools/</code>{" "}
              files, and the README into the LLM context so cases reference
              real behaviors instead of generic patterns. Adds ~30 KB of
              context per request.
            </div>
          </div>
        </label>
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => generate.mutate()}
            disabled={generate.isPending}
            className="inline-flex items-center gap-1 rounded-md bg-foreground px-3 py-1.5 text-sm font-medium text-background disabled:opacity-50"
          >
            <Sparkles className="h-4 w-4" />
            {generate.isPending ? "Generating…" : "Generate with AI"}
          </button>
        </div>
        {generate.error && (
          <p className="text-xs text-destructive">
            {generate.error instanceof ApiError &&
            typeof generate.error.detail === "string"
              ? generate.error.detail
              : String(generate.error)}
          </p>
        )}
      </Card>

      {result && (
        <Card className="space-y-3 p-4">
          <div className="flex items-baseline justify-between">
            <h3 className="font-semibold">Generated suite preview</h3>
            <div className="flex gap-2 text-xs">
              <Badge tone={result.compiles ? "success" : "danger"}>
                {result.compiles ? "compiles" : "syntax error"}
              </Badge>
              <Badge tone={result.loadable ? "success" : "warning"}>
                {result.loadable ? "loadable" : "engine load failed"}
              </Badge>
              <Badge tone="neutral">{result.total_cases} cases</Badge>
              {result.deep_scan_files && result.deep_scan_files.length > 0 && (
                <Badge tone="info">
                  deep scan: {result.deep_scan_files.length} file
                  {result.deep_scan_files.length === 1 ? "" : "s"}
                </Badge>
              )}
            </div>
          </div>
          {result.deep_scan_files && result.deep_scan_files.length > 0 && (
            <details className="rounded-md border border-border bg-muted/20 px-3 py-2 text-xs">
              <summary className="cursor-pointer font-medium">
                Workspace files included in the LLM context (
                {result.deep_scan_files
                  .reduce((s, f) => s + f.bytes, 0)
                  .toLocaleString()}{" "}
                bytes total)
              </summary>
              <ul className="mt-2 space-y-1 font-mono">
                {result.deep_scan_files.map((f) => (
                  <li
                    key={f.path}
                    className="flex items-baseline justify-between gap-2"
                  >
                    <code className="truncate">{f.path}</code>
                    <span className="text-muted-foreground">
                      {f.bytes.toLocaleString()} B
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}
          <p className="text-xs italic text-muted-foreground">
            Edit the suite Python or dossier below before saving. The save
            target is{" "}
            <code className="rounded-md bg-muted/30 px-1">{suiteName}.py</code>{" "}
            in your project workspace.
          </p>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              suite python
            </span>
            <textarea
              value={edited || result.generated_python}
              onChange={(e) => setEdited(e.target.value)}
              rows={16}
              spellCheck={false}
              className="rounded-md border border-border bg-muted/20 px-3 py-2 font-mono text-xs"
            />
          </label>
          {result.generated_dossier && (
            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                companion dossier
              </span>
              <textarea
                value={editedDossier || result.generated_dossier}
                onChange={(e) => setEditedDossier(e.target.value)}
                rows={8}
                spellCheck={false}
                className="rounded-md border border-border bg-muted/20 px-3 py-2 font-mono text-xs"
              />
            </label>
          )}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex-1 space-y-1.5 text-xs">
              {result.parse_error && (
                <p className="text-destructive">
                  parse error: {result.parse_error}
                </p>
              )}
              {!result.parse_error && result.load_error && (
                <p className="text-[hsl(var(--warning))]">
                  load warning: {result.load_error}
                </p>
              )}
              {result.missing_module && (
                <div className="flex flex-wrap items-center gap-2 rounded-md border border-[hsl(var(--warning))]/30 bg-[hsl(var(--warning))]/5 px-3 py-2">
                  <span className="text-muted-foreground">
                    Your project's agent imports{" "}
                    <code className="rounded-md bg-card px-1.5 py-0.5 font-mono">
                      {result.missing_module}
                    </code>
                    , but the project venv doesn't have it.
                  </span>
                  <button
                    type="button"
                    onClick={() => addRequirement.mutate(result.missing_module!)}
                    disabled={addRequirement.isPending}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-md border border-[hsl(var(--warning))]/40 bg-[hsl(var(--warning))]/10 px-2.5 py-1 font-mono text-[11px] text-[hsl(var(--warning))] hover:bg-[hsl(var(--warning))]/20",
                      addRequirement.isPending && "opacity-50",
                    )}
                  >
                    <PackagePlus className="h-3.5 w-3.5" />
                    {addRequirement.isPending
                      ? "Adding & syncing…"
                      : `Add ${result.missing_module} to requirements.txt & re-sync`}
                  </button>
                  {addRequirement.data && (
                    <span className="text-[hsl(var(--success))]">
                      {addRequirement.data.already_present
                        ? "✓ already in requirements.txt — synced"
                        : "✓ added & synced — venv will rebuild on next run"}
                    </span>
                  )}
                  {addRequirement.error && (
                    <span className="text-destructive">
                      {addRequirement.error instanceof ApiError &&
                      typeof addRequirement.error.detail === "string"
                        ? addRequirement.error.detail
                        : String(addRequirement.error)}
                    </span>
                  )}
                </div>
              )}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => {
                  setEdited("");
                  setEditedDossier("");
                  generate.reset();
                }}
                className="rounded-md border border-border px-3 py-1.5 text-sm"
              >
                Reject
              </button>
              <button
                type="button"
                onClick={() => save.mutate()}
                disabled={save.isPending}
                className={cn(
                  "rounded-md bg-[hsl(var(--success))] px-3 py-1.5 text-sm font-medium text-background",
                  save.isPending && "opacity-50",
                )}
              >
                {save.isPending ? "Saving…" : `Save → ${suiteName}.py`}
              </button>
            </div>
          </div>
          {save.data && (
            <p className="text-xs text-[hsl(var(--success))]">
              ✓ Saved {save.data.bytes_written.toLocaleString()} bytes to{" "}
              <code className="rounded-md bg-muted/30 px-1">{save.data.file_path}</code>
            </p>
          )}
          {save.error && (
            <p className="text-xs text-destructive">
              {save.error instanceof ApiError &&
              typeof save.error.detail === "string"
                ? save.error.detail
                : String(save.error)}
            </p>
          )}
        </Card>
      )}
    </div>
  );
}

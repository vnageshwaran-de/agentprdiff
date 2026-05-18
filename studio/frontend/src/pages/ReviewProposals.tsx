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
import { PreflightPanel } from "@/components/PreflightPanel";
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
  // Sibling-repos opt-in. Default off — the scan stays scoped to the
  // selected project root unless the user explicitly expands it. We
  // surface this in the UI as a sub-checkbox of deep_scan because
  // sibling scanning makes no sense without deep_scan in the first
  // place.
  const [scanIncludeParent, setScanIncludeParent] = useState(false);
  // Auto-install preview: when on and the preflight import_load stage
  // fails with APD_PREFLIGHT_MODULE_NOT_FOUND, Studio spins up an
  // ephemeral /tmp venv with the missing package + engine, re-runs the
  // load stage there, and tears the venv down before responding. The
  // user can SEE the suite shape this way without committing to install.
  const [autoInstallPreview, setAutoInstallPreview] = useState(false);

  const generate = useMutation({
    mutationFn: () =>
      api.generateSuiteWithAI(projectId, {
        suite_name: suiteName,
        prompt,
        deep_scan: deepScan,
        scan_include_parent: scanIncludeParent,
        auto_install_preview: autoInstallPreview,
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
        // Force the save when preflight didn't fully pass. We prefer
        // the canonical ``preflight_ok`` signal when the backend
        // exposes it (newer hardened pipeline), falling back to the
        // legacy ``loadable`` flag for older builds.
        force:
          result?.preflight_ok != null
            ? !result.preflight_ok
            : !result?.loadable,
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
            <label className="mt-2 flex items-start gap-2 text-[11px]">
              <input
                type="checkbox"
                checked={scanIncludeParent}
                disabled={!deepScan}
                onChange={(e) => setScanIncludeParent(e.target.checked)}
                className="mt-0.5 h-3.5 w-3.5 cursor-pointer"
              />
              <span className="flex-1">
                <span className="font-medium">Include sibling repositories</span>
                <span className="ml-1 text-muted-foreground">
                  — expand scan past the project root into the parent
                  folder. Off by default; the manifest in the result
                  pane will show exactly which files were added.
                </span>
              </span>
            </label>
          </div>
        </label>
        <label className="flex items-start gap-2 rounded-md border border-border bg-muted/20 p-2.5 text-xs">
          <input
            type="checkbox"
            checked={autoInstallPreview}
            onChange={(e) => setAutoInstallPreview(e.target.checked)}
            className="mt-0.5 h-4 w-4 cursor-pointer"
          />
          <div className="flex-1">
            <div className="font-medium">
              Auto-install missing deps for preview only
            </div>
            <div className="text-muted-foreground">
              When the preflight import stage hits{" "}
              <code className="rounded-md bg-card px-1 font-mono">
                ModuleNotFoundError
              </code>
              , Studio will spin up an ephemeral{" "}
              <code className="rounded-md bg-card px-1 font-mono">/tmp</code>{" "}
              venv with the missing package installed, retry the
              import there, then tear it down. Non-persistent — your
              project venv and{" "}
              <code className="rounded-md bg-card px-1 font-mono">
                requirements.txt
              </code>{" "}
              are untouched.
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
            <h3 className="font-semibold">
              {result.preflight_ok === true
                ? "Generated suite preview"
                : result.preflight_ok === false
                  ? "Generated suite — preflight failed"
                  : "Generated suite preview"}
            </h3>
            <div className="flex gap-2 text-xs">
              <Badge tone="neutral">{result.total_cases} cases</Badge>
              {result.deep_scan_files && result.deep_scan_files.length > 0 && (
                <Badge tone="info">
                  deep scan: {result.deep_scan_files.length} file
                  {result.deep_scan_files.length === 1 ? "" : "s"}
                </Badge>
              )}
            </div>
          </div>
          <PreflightPanel
            preflightOk={result.preflight_ok}
            stages={result.preflight_stages}
            errorCode={result.error_code}
            strategy={result.strategy}
            framework={result.framework}
            scanManifest={result.scan_manifest}
            previewVenvUsed={result.preview_venv_used}
            onAddRequirement={(pkg) => addRequirement.mutate(pkg)}
            addRequirementPending={addRequirement.isPending}
          />
          {/* Fallback for older backends that don't return preflight_stages: */}
          {(!result.preflight_stages || result.preflight_stages.length === 0) && (
            <div className="flex gap-2 text-xs">
              <Badge tone={result.compiles ? "success" : "danger"}>
                {result.compiles ? "compiles" : "syntax error"}
              </Badge>
              <Badge tone={result.loadable ? "success" : "warning"}>
                {result.loadable ? "loadable" : "engine load failed"}
              </Badge>
            </div>
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
              {/* Legacy diagnostics — only shown when the backend
                  didn't emit structured preflight stages (older
                  studio backends, or HTTP-mode projects that skip
                  the load stage). The PreflightPanel above carries
                  the new, richer surface. */}
              {(!result.preflight_stages || result.preflight_stages.length === 0) &&
                result.parse_error && (
                <p className="text-destructive">
                  parse error: {result.parse_error}
                </p>
              )}
              {(!result.preflight_stages || result.preflight_stages.length === 0) &&
                !result.parse_error && result.load_error && (
                <p className="text-[hsl(var(--warning))]">
                  load warning: {result.load_error}
                </p>
              )}
              {(!result.preflight_stages || result.preflight_stages.length === 0) &&
                result.missing_module && (
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

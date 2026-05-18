// Preflight UX for the "Generate suite with AI" flow.
//
// The hardened backend pipeline (see studio/backend/.../agents_md/preflight.py)
// reports three stages — syntax, import_load, suite_discovery — each with
// its own status, duration, and a list of structured diagnostics that
// carry a stable error code + an optional remediation dict.
//
// The user-facing rules are simple:
//   * Only show the "generation succeeded" affordance when
//     `preflight_ok` is true. Compilation succeeding is not enough.
//   * Diagnostics render with line/col + a friendly fix hint by default,
//     and pop out a remediation card when the backend included one
//     (today: missing-dep, hyphenated import path).
//
// The component is presentational. Mutations (e.g. the "Add to
// requirements.txt" button) live on the parent page so the page owns
// query invalidation. We just call the supplied callbacks.

import { CheckCircle2, CircleDashed, Clock3, XCircle, AlertTriangle, Copy } from "lucide-react";
import { useState } from "react";

import type {
  PreflightDiagnostic,
  PreflightStage,
  PreflightStageStatus,
  ScanManifest,
} from "@/api/types";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";

interface Props {
  preflightOk?: boolean;
  stages?: PreflightStage[];
  errorCode?: string | null;
  strategy?: string;
  framework?: string | null;
  scanManifest?: ScanManifest | null;
  previewVenvUsed?: boolean;
  /** Optional handler — when set, the missing-dep card renders an
   *  "Add to requirements.txt" button that calls this. */
  onAddRequirement?: (pkg: string) => void;
  addRequirementPending?: boolean;
}

const STAGE_LABELS: Record<PreflightStage["name"], string> = {
  syntax: "Syntax & imports",
  import_load: "Import & engine load",
  suite_discovery: "Suite discovery",
};

const STATUS_ICON: Record<PreflightStageStatus, JSX.Element> = {
  passed: <CheckCircle2 className="h-4 w-4 text-[hsl(var(--success))]" />,
  failed: <XCircle className="h-4 w-4 text-destructive" />,
  skipped: <CircleDashed className="h-4 w-4 text-muted-foreground" />,
  pending: <Clock3 className="h-4 w-4 text-muted-foreground" />,
};

const SEVERITY_CLASS: Record<PreflightDiagnostic["severity"], string> = {
  error:
    "border-destructive/40 bg-destructive/5 text-destructive",
  warning:
    "border-[hsl(var(--warning))]/40 bg-[hsl(var(--warning))]/5 text-[hsl(var(--warning))]",
  info:
    "border-border bg-muted/20 text-foreground",
};

export function PreflightPanel({
  preflightOk,
  stages,
  errorCode,
  strategy,
  framework,
  scanManifest,
  previewVenvUsed,
  onAddRequirement,
  addRequirementPending,
}: Props) {
  // The backend always returns a stages array once preflight runs.
  // When it's absent (e.g. an older backend), fall back to a minimal
  // rendering so the page still works.
  if (!stages || stages.length === 0) {
    return null;
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={preflightOk ? "success" : "danger"}>
          {preflightOk ? "preflight passed" : "preflight failed"}
        </Badge>
        {strategy && (
          <Badge tone="info" title="Generation strategy Studio chose">
            strategy: {strategy}
          </Badge>
        )}
        {framework && framework !== "module" && (
          <Badge tone="neutral" title="Detected framework shape">
            framework: {framework}
          </Badge>
        )}
        {previewVenvUsed && (
          <Badge
            tone="warning"
            title="Studio installed missing deps in a non-persistent /tmp venv just for this preview"
          >
            preview venv (non-persistent)
          </Badge>
        )}
        {errorCode && !preflightOk && (
          <code
            className="rounded-md bg-destructive/10 px-1.5 py-0.5 font-mono text-[10px] text-destructive"
            title="Stable error code — copy this into a bug report"
          >
            {errorCode}
          </code>
        )}
      </div>

      <ol className="space-y-2">
        {stages.map((s) => (
          <li
            key={s.name}
            className="rounded-md border border-border bg-card px-3 py-2"
          >
            <div className="flex items-center gap-2 text-sm">
              {STATUS_ICON[s.status]}
              <span className="font-medium">{STAGE_LABELS[s.name]}</span>
              <span className="text-[10px] text-muted-foreground">
                {s.status}
                {s.duration_ms > 0 && ` · ${s.duration_ms} ms`}
              </span>
            </div>
            {s.diagnostics.length > 0 && (
              <ul className="mt-2 space-y-2">
                {s.diagnostics.map((d, i) => (
                  <li key={i}>
                    <DiagnosticCard
                      diag={d}
                      onAddRequirement={onAddRequirement}
                      addRequirementPending={addRequirementPending}
                    />
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>

      {scanManifest && <ScanManifestPanel manifest={scanManifest} />}
    </div>
  );
}

function DiagnosticCard({
  diag,
  onAddRequirement,
  addRequirementPending,
}: {
  diag: PreflightDiagnostic;
  onAddRequirement?: (pkg: string) => void;
  addRequirementPending?: boolean;
}) {
  const remediation = diag.remediation;
  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2 text-xs",
        SEVERITY_CLASS[diag.severity],
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-medium">{diag.message}</p>
        <code className="rounded-md bg-card/60 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          {diag.code}
        </code>
      </div>
      {(diag.line != null || diag.statement) && (
        <p className="mt-1 font-mono text-[11px] text-muted-foreground">
          {diag.line != null && (
            <>
              line {diag.line}
              {diag.col != null ? `:${diag.col}` : ""}
              {diag.statement ? " · " : ""}
            </>
          )}
          {diag.statement && <code>{diag.statement}</code>}
        </p>
      )}
      {diag.fix_hint && (
        <p className="mt-2 text-[11px] text-foreground/80">
          <AlertTriangle className="mr-1 inline h-3 w-3" />
          {diag.fix_hint}
        </p>
      )}
      {remediation && (
        <RemediationCard
          remediation={remediation}
          onAddRequirement={onAddRequirement}
          addRequirementPending={addRequirementPending}
        />
      )}
    </div>
  );
}

function RemediationCard({
  remediation,
  onAddRequirement,
  addRequirementPending,
}: {
  remediation: NonNullable<PreflightDiagnostic["remediation"]>;
  onAddRequirement?: (pkg: string) => void;
  addRequirementPending?: boolean;
}) {
  const pkg = remediation.top_level_package || remediation.missing_module;
  return (
    <div className="mt-2 rounded-md border border-border bg-background/60 p-2 text-foreground">
      <p className="text-[11px]">
        <span className="font-medium">Fix:</span>{" "}
        Declare{" "}
        <code className="rounded-md bg-muted/40 px-1 font-mono">{pkg}</code>
        {remediation.where_to_declare && (
          <>
            {" "}
            in{" "}
            <code className="rounded-md bg-muted/40 px-1 font-mono">
              {remediation.where_to_declare}
            </code>
          </>
        )}
        .
      </p>
      {remediation.install_commands && remediation.install_commands.length > 0 && (
        <ul className="mt-2 space-y-1">
          {remediation.install_commands.map((cmd, i) => (
            <li key={i}>
              <CopyableCommand command={cmd} />
            </li>
          ))}
        </ul>
      )}
      {pkg && onAddRequirement && (
        <button
          type="button"
          onClick={() => onAddRequirement(pkg)}
          disabled={addRequirementPending}
          className={cn(
            "mt-2 inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1 font-mono text-[10px] hover:bg-muted/40",
            addRequirementPending && "opacity-50",
          )}
        >
          {addRequirementPending
            ? "Adding & syncing…"
            : `Add ${pkg} to requirements.txt`}
        </button>
      )}
      {remediation.note && (
        <p className="mt-2 text-[10px] italic text-muted-foreground">
          {remediation.note}
        </p>
      )}
    </div>
  );
}

function CopyableCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard.writeText(command).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="flex w-full items-baseline justify-between gap-2 rounded-md bg-card px-2 py-1 text-left font-mono text-[11px] text-foreground/90 hover:bg-muted/40"
      title="Copy to clipboard"
    >
      <code className="truncate">{command}</code>
      <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
        <Copy className="h-3 w-3" />
        {copied ? "copied" : "copy"}
      </span>
    </button>
  );
}

function ScanManifestPanel({ manifest }: { manifest: ScanManifest }) {
  return (
    <details className="rounded-md border border-border bg-muted/10 px-3 py-2 text-xs">
      <summary className="cursor-pointer font-medium">
        Scan manifest · {manifest.files.length} file
        {manifest.files.length === 1 ? "" : "s"} ·{" "}
        {manifest.total_bytes.toLocaleString()} bytes
        {manifest.sibling_repos_included && (
          <Badge tone="warning" className="ml-2 inline-flex">
            siblings included
          </Badge>
        )}
      </summary>
      <div className="mt-2 space-y-2">
        <p className="font-mono text-[10px] text-muted-foreground">
          root: <code>{manifest.root}</code>
        </p>
        <ul className="space-y-1 font-mono">
          {manifest.files.map((f) => (
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
        {manifest.rejected.length > 0 && (
          <div>
            <p className="font-mono text-[10px] text-destructive">
              Rejected (out of root):
            </p>
            <ul className="space-y-1 font-mono">
              {manifest.rejected.map((r, i) => (
                <li key={i} className="text-destructive">
                  <code>{r.path}</code>{" "}
                  <span className="text-muted-foreground">
                    ({r.reason})
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}

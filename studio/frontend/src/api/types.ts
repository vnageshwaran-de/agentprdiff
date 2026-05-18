// Mirror of the pydantic schemas in
// studio/backend/src/agentprdiff_studio/api/schemas.py.
//
// Kept hand-rolled (no OpenAPI codegen) because the surface is small and we
// want the freedom to evolve quickly. When the API stabilizes (post-M7) we
// should switch to fastapi → openapi.json → openapi-typescript.

export type IntakeMode = "git" | "zip" | "http";

export interface ProjectOut {
  id: number;
  name: string;
  intake_mode: IntakeMode;
  source: string;
  git_ref: string | null;
  workspace_path: string | null;
  http_config: HttpConfig | null;
  last_synced_at: string | null;
  created_at: string;
}

export interface HttpConfig {
  method: string;
  url: string;
  headers: Record<string, string>;
  body_template: unknown;
  output_path: string;
  timeout_seconds: number;
}

export interface SuiteOut {
  id: number;
  project_id: number;
  name: string;
  file_path: string;
  case_count: number;
  discovered_at: string;
}

export interface SyncResult {
  project_id: number;
  suites_found: number;
  suites: SuiteOut[];
}

export interface RunOut {
  id: number;
  project_id: number;
  suite_id: number;
  command: "record" | "check" | "review";
  status: "pending" | "running" | "succeeded" | "failed" | "regression" | "error";
  case_filter: string | null;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  cases_total: number;
  cases_passed: number;
  cases_regressed: number;
  stderr_tail: string | null;
  created_at: string;
}

export interface CaseRunOut {
  id: number;
  run_id: number;
  case_name: string;
  status: "passed" | "failed" | "regression" | "error";
  cost_usd: number;
  latency_ms: number;
  trace?: Record<string, unknown> | null;
  delta?: Record<string, unknown> | null;
}

export interface ProjectCreateGit {
  name: string;
  intake_mode: "git";
  source: string;
  git_ref?: string | null;
}

export interface ProjectCreateHttp {
  name: string;
  intake_mode: "http";
  source: string; // user-visible label; usually mirrors http_config.url
  http_config: Partial<HttpConfig> & { url: string };
}

export type ProjectCreate = ProjectCreateGit | ProjectCreateHttp;

export interface HttpSuiteCreate {
  name: string;
  cases: Array<{
    name: string;
    input: unknown;
    expect: Array<Record<string, unknown>>;
    tags?: string[];
  }>;
}

export interface SecretOut {
  id: number;
  name: string;
  scope: string;
  created_at: string;
}

export interface SecretCreate {
  name: string;
  value: string;
  scope: string;
}

export interface CaseRunDetail {
  id: number;
  run_id: number;
  project_id: number;
  suite_id: number;
  suite_name: string;
  case_name: string;
  status: "passed" | "failed" | "regression" | "error";
  cost_usd: number;
  latency_ms: number;
  trace: TraceJson | null;
  delta: TraceDeltaJson | null;
}

// Mirror of agentprdiff.core.Trace.model_dump(mode='json'). Many fields are
// optional because we accept missing data gracefully (the engine sometimes
// fills only what it can capture).
export interface TraceJson {
  case_name?: string;
  suite_name?: string;
  input?: unknown;
  output?: unknown;
  llm_calls?: Array<Record<string, unknown>>;
  tool_calls?: Array<{ name: string; arguments?: Record<string, unknown>; result?: unknown; error?: string | null }>;
  total_cost_usd?: number;
  total_latency_ms?: number;
  total_prompt_tokens?: number;
  total_completion_tokens?: number;
  error?: string | null;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

// Mirror of agentprdiff.differ.TraceDelta.model_dump(mode='json').
export interface TraceDeltaJson {
  suite_name?: string;
  case_name?: string;
  baseline_exists?: boolean;
  baseline_error?: string | null;
  current_error?: string | null;
  assertion_changes?: Array<{
    grader_name: string;
    baseline_passed?: boolean | null;
    current_passed?: boolean | null;
    current_reason?: string;
  }>;
  baseline_tool_sequence?: string[];
  current_tool_sequence?: string[];
  tool_sequence_changed?: boolean;
  output_changed?: boolean;
  output_diff?: string;
  cost_delta_usd?: number;
  latency_delta_ms?: number;
  prompt_tokens_delta?: number;
  completion_tokens_delta?: number;
  [key: string]: unknown;
}

export interface BaselineOut {
  id: number;
  project_id: number;
  suite_id: number;
  case_name: string;
  version: number;
  approved_by_run_id: number | null;
  created_at: string;
}

export interface ApproveBaselineOut {
  baseline: BaselineOut;
  wrote_to_disk: boolean;
  disk_path: string | null;
}

export interface CaseDossier {
  name: string;
  source_file: string;
  what_it_tests: string;
  input_text: string;
  assertions: string[];
  code_impacted: string;
  application_impact: string;
}

export interface AgentsMdOut {
  exists: boolean;
  workspace: string | null;
  agents_md_path: string | null;
  agents_md_content: string;
  sections: string[];
  code_blocks: Array<{ lang: string; code: string }>;
  cases_files: string[];
  cases: CaseDossier[];
  supports_disk_scaffold: boolean;
}

export interface ScaffoldStarterOut {
  path: string;
  wrote_bytes: number;
}

export interface ScaffoldSuiteOut {
  intake_mode: string;
  suite_id: number | null;
  file_path: string | null;
  cases_used: number;
}

/** One stage of the hardened preflight pipeline. The UI renders these
 *  vertically with per-stage status + diagnostics. */
export type PreflightStageName = "syntax" | "import_load" | "suite_discovery";
export type PreflightStageStatus = "pending" | "passed" | "failed" | "skipped";
export type PreflightSeverity = "error" | "warning" | "info";

export interface PreflightRemediation {
  missing_module?: string;
  top_level_package?: string;
  where_to_declare?: string;
  install_commands?: string[];
  note?: string;
}

export interface PreflightDiagnostic {
  stage: PreflightStageName;
  /** Stable error code, e.g. APD_PREFLIGHT_HYPHENATED_IMPORT. */
  code: string;
  severity: PreflightSeverity;
  message: string;
  file?: string | null;
  line?: number | null;
  col?: number | null;
  fix_hint?: string | null;
  /** Structured remediation, when applicable (missing dep, etc.). */
  remediation?: PreflightRemediation | null;
  statement?: string;
}

export interface PreflightStage {
  name: PreflightStageName;
  status: PreflightStageStatus;
  duration_ms: number;
  diagnostics: PreflightDiagnostic[];
}

export interface ScanManifest {
  /** Absolute filesystem path bounding the scan. Surfaced verbatim so
   *  the user can verify nothing leaked from outside the project root. */
  root: string;
  /** True when the user explicitly opted in to scanning sibling
   *  repositories alongside the selected workspace. */
  sibling_repos_included: boolean;
  files: Array<{ path: string; bytes: number }>;
  total_bytes: number;
  /** Files Studio refused to include and why (e.g. resolved outside root). */
  rejected: Array<{ path: string; reason: string }>;
}

export interface GenerateSuiteOut {
  provider: string;
  model: string;
  source: string;
  generated_python: string;
  generated_dossier: string;
  dossier_has_cases: boolean;
  compiles: boolean;
  parse_error: string | null;
  has_imports: boolean;
  has_suite_call: boolean;
  loadable: boolean;
  loadable_via_venv: boolean;
  missing_module: string | null;
  load_error: string | null;
  discovered_suites: string[];
  total_cases: number;
  agent_import_target: string;
  /** Files Studio included in the LLM context for the deep-scan generate. */
  deep_scan_files?: Array<{ path: string; bytes: number }>;

  // ----- Hardened preflight + manifest -----
  /** Canonical "did it work" signal. Replaces ad-hoc combos of compiles
   *  + loadable + missing_module. */
  preflight_ok?: boolean;
  preflight_stages?: PreflightStage[];
  /** First failing error code in stage order; null when everything passed. */
  error_code?: string | null;
  /** Generation strategy: direct | adapter | extend_existing | scaffold. */
  strategy?: string;
  /** Detected framework: flask | fastapi | cloud_function | cli | module. */
  framework?: string | null;
  scan_manifest?: ScanManifest | null;
  /** True if preflight retried inside an ephemeral venv via auto_install_preview. */
  preview_venv_used?: boolean;
}

export interface SaveGeneratedOut {
  file_path: string;
  bytes_written: number;
  dossier_path: string | null;
  dossier_bytes: number;
}

// ----- Studio Tour --------------------------------------------------------

export type TourStepStatus = "pending" | "in_progress" | "complete" | "skipped";

export interface TourStep {
  id: string;
  label: string;
  status: TourStepStatus;
  hint: string;
}

export interface TourStateRaw {
  skipped_steps: string[];
  ci_committed: boolean;
  completed: boolean;
}

export interface TourSnapshot {
  project_id: number;
  supports_disk_actions: boolean;
  state: TourStateRaw;
  steps: TourStep[];
  active_step: string;
  semantic_suites: string[];
}

export interface SimulateRegressionOut {
  run_id: number;
  plan: {
    file_path: string;
    original_word: string;
    replacement: string;
  };
}

export interface RevertSimulationOut {
  reverted: boolean;
  file_path?: string;
  message?: string;
}

export interface CIYamlPreviewOut {
  path: string;
  content: string;
}

export interface CommitCIOut {
  path: string;
  committed: boolean;
  pushed: boolean;
  message: string;
}

export interface DiscoveryDiagnostics {
  workspace_path: string | null;
  loaded: Array<{ name: string; relative_path: string; case_count: number; load_error: string | null }>;
  failed: Array<{ name: string; relative_path: string; case_count: number; load_error: string }>;
}

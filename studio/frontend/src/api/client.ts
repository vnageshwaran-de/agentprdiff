// Tiny typed fetch wrapper. All Studio requests go through here so we have
// exactly one place to add auth headers, retries, or instrumentation later.

import type {
  AgentsMdOut,
  ApproveBaselineOut,
  BaselineOut,
  CaseRunDetail,
  CaseRunOut,
  CIYamlPreviewOut,
  CommitCIOut,
  DiscoveryDiagnostics,
  GenerateSuiteOut,
  HttpSuiteCreate,
  ProjectCreate,
  ProjectOut,
  RevertSimulationOut,
  RunOut,
  SaveGeneratedOut,
  ScaffoldStarterOut,
  ScaffoldSuiteOut,
  SecretCreate,
  SecretOut,
  SimulateRegressionOut,
  SuiteOut,
  SyncResult,
  TourSnapshot,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const { json, headers, ...rest } = init;
  const opts: RequestInit = {
    ...rest,
    headers: {
      ...(json !== undefined ? { "content-type": "application/json" } : {}),
      ...(headers ?? {}),
    },
    body: json !== undefined ? JSON.stringify(json) : init.body,
  };

  const res = await fetch(`${BASE}${path}`, opts);
  if (res.status === 204) return undefined as T;

  const ct = res.headers.get("content-type") ?? "";
  const payload = ct.includes("application/json") ? await res.json() : await res.text();

  if (!res.ok) {
    const detail =
      typeof payload === "object" && payload && "detail" in payload
        ? (payload as { detail: unknown }).detail
        : payload;
    throw new ApiError(
      `${res.status} ${typeof detail === "string" ? detail : res.statusText}`,
      res.status,
      detail,
    );
  }
  return payload as T;
}

// ----- projects -----------------------------------------------------------

export const api = {
  listProjects: () => request<ProjectOut[]>("/projects"),
  getProject: (id: number) => request<ProjectOut>(`/projects/${id}`),
  createProject: (body: ProjectCreate) =>
    request<ProjectOut>("/projects", { method: "POST", json: body }),
  uploadProject: (name: string, file: File) => {
    const fd = new FormData();
    fd.append("name", name);
    fd.append("file", file);
    return request<ProjectOut>("/projects/upload", { method: "POST", body: fd });
  },
  replaceUpload: (id: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<ProjectOut>(`/projects/${id}/upload`, { method: "POST", body: fd });
  },
  syncProject: (id: number) =>
    request<SyncResult>(`/projects/${id}/sync`, { method: "POST" }),
  listSuites: (id: number) => request<SuiteOut[]>(`/projects/${id}/suites`),
  discoveryDiagnostics: (id: number) =>
    request<DiscoveryDiagnostics>(`/projects/${id}/discovery-diagnostics`),
  // Safe-path file removal from the project workspace. Used by the
  // Diagnose panel to clean up broken suite candidates.
  deleteWorkspaceFile: (projectId: number, path: string) =>
    request<{ deleted: string }>(
      `/projects/${projectId}/workspace-files?path=${encodeURIComponent(path)}`,
      { method: "DELETE" },
    ),
  // Append a package to requirements.txt in the project workspace. Used by
  // the "Add missing dep & re-sync" button on the Propose & review page
  // when the LLM-generated suite hits a ModuleNotFoundError on load.
  addRequirement: (projectId: number, body: { package: string; version?: string }) =>
    request<{ added: boolean; already_present: boolean; package: string; path: string }>(
      `/projects/${projectId}/requirements`,
      { method: "POST", json: body },
    ),
  listProjectRuns: (id: number, limit = 20) =>
    request<RunOut[]>(`/projects/${id}/runs?limit=${limit}`),

  // AGENTS.md (Project guide)
  getAgentsMd: (projectId: number) =>
    request<AgentsMdOut>(`/projects/${projectId}/agents-md`),
  scaffoldStarterAgentsMd: (projectId: number, overwrite = false) =>
    request<ScaffoldStarterOut>(`/projects/${projectId}/agents-md/scaffold`, {
      method: "POST",
      json: { overwrite },
    }),
  scaffoldSuiteFromAgentsMd: (projectId: number, body: { suite_name: string; agent_import_target?: string }) =>
    request<ScaffoldSuiteOut>(`/projects/${projectId}/agents-md/scaffold-suite`, {
      method: "POST",
      json: body,
    }),
  generateSuiteWithAI: (
    projectId: number,
    body: {
      suite_name: string;
      prompt: string;
      model?: string;
      agent_import_target?: string;
      // When true, Studio scans the workspace for the agent module + siblings
      // + tools/* + README and includes them in the LLM context. Off by
      // default — turn on for the "full suite from real code" flow.
      deep_scan?: boolean;
      // When true, expand the deep scan past the workspace root into the
      // parent directory — explicit opt-in for picking up sibling repos.
      scan_include_parent?: boolean;
      // When true and preflight reports a missing dep, retry inside an
      // ephemeral /tmp venv so the user can preview suite shape without
      // persistently installing anything. Non-persistent.
      auto_install_preview?: boolean;
    },
  ) =>
    request<GenerateSuiteOut>(`/projects/${projectId}/agents-md/generate-suite`, {
      method: "POST",
      json: body,
    }),
  // Sweep orphan *_cases.md dossiers — files whose suite was deleted but
  // whose markdown lingered in the workspace.
  cleanupCaseOrphans: (projectId: number) =>
    request<{
      deleted: string[];
      kept: Array<{ path: string; reason: string }>;
      workspace: string | null;
    }>(`/projects/${projectId}/agents-md/cleanup-orphans`, { method: "POST" }),
  saveGeneratedSuite: (
    projectId: number,
    body: {
      suite_name: string;
      content: string;
      dossier?: string;
      overwrite?: boolean;
      force?: boolean;
    },
  ) =>
    request<SaveGeneratedOut>(`/projects/${projectId}/agents-md/save-generated`, {
      method: "POST",
      json: body,
    }),

  // HTTP-mode suite authoring
  createHttpSuite: (projectId: number, body: HttpSuiteCreate) =>
    request<SuiteOut>(`/projects/${projectId}/suites`, { method: "POST", json: body }),
  // Works for any intake mode. For git/zip projects, the on-disk file is
  // removed too so the next sync doesn't bring it back.
  deleteSuite: (projectId: number, suiteId: number) =>
    request<void>(`/projects/${projectId}/suites/${suiteId}`, { method: "DELETE" }),

  // ----- runs --------------------------------------------------------------

  createRun: (body: {
    project_id: number;
    suite_id: number;
    command: "record" | "check" | "review";
    case_filter?: string;
  }) => request<RunOut>("/runs", { method: "POST", json: body }),
  getRun: (id: number) => request<RunOut>(`/runs/${id}`),
  deleteRun: (id: number) => request<void>(`/runs/${id}`, { method: "DELETE" }),
  clearProjectRuns: (projectId: number) =>
    request<{ deleted: number; skipped: number }>(
      `/projects/${projectId}/runs`,
      { method: "DELETE" },
    ),
  getCases: (
    id: number,
    opts: { includeTrace?: boolean; includeDelta?: boolean } = {},
  ) => {
    const q = new URLSearchParams();
    if (opts.includeTrace) q.set("include_trace", "true");
    if (opts.includeDelta) q.set("include_delta", "true");
    const suffix = q.toString() ? `?${q.toString()}` : "";
    return request<CaseRunOut[]>(`/runs/${id}/cases${suffix}`);
  },

  // ----- case-runs + baselines --------------------------------------------

  getCaseRun: (caseRunId: number) =>
    request<CaseRunDetail>(`/case-runs/${caseRunId}`),
  approveBaseline: (caseRunId: number) =>
    request<ApproveBaselineOut>("/baselines/approve", {
      method: "POST",
      json: { case_run_id: caseRunId },
    }),
  listBaselines: (params: { project_id: number; suite_id: number; case_name?: string }) => {
    const q = new URLSearchParams();
    q.set("project_id", String(params.project_id));
    q.set("suite_id", String(params.suite_id));
    if (params.case_name) q.set("case_name", params.case_name);
    return request<BaselineOut[]>(`/baselines?${q.toString()}`);
  },

  // ----- Studio Tour ------------------------------------------------------

  getTour: (projectId: number) =>
    request<TourSnapshot>(`/projects/${projectId}/tour`),
  patchTourState: (
    projectId: number,
    body: { skip?: string; unskip?: string; ci_committed?: boolean; completed?: boolean },
  ) =>
    request<{ skipped_steps: string[]; ci_committed: boolean; completed: boolean }>(
      `/projects/${projectId}/tour/state`,
      { method: "POST", json: body },
    ),
  simulateRegression: (projectId: number, suite_id: number) =>
    request<SimulateRegressionOut>(`/projects/${projectId}/tour/simulate-regression`, {
      method: "POST",
      json: { suite_id },
    }),
  revertSimulation: (projectId: number) =>
    request<RevertSimulationOut>(`/projects/${projectId}/tour/revert-simulation`, {
      method: "POST",
    }),
  ciYamlPreview: (projectId: number) =>
    request<CIYamlPreviewOut>(`/projects/${projectId}/tour/ci-yaml`),
  commitCIYaml: (projectId: number) =>
    request<CommitCIOut>(`/projects/${projectId}/tour/commit-ci-yaml`, { method: "POST" }),

  // ----- secrets -----------------------------------------------------------

  listSecrets: () => request<SecretOut[]>("/secrets"),
  upsertSecret: (body: SecretCreate) =>
    request<SecretOut>("/secrets", { method: "POST", json: body }),
  deleteSecret: (id: number) =>
    request<void>(`/secrets/${id}`, { method: "DELETE" }),

  // ----- Phase 2-4 endpoints ----------------------------------------------
  // Each page declares its own response interface and casts via the generic
  // here. The any in the return type is intentional v0 — pages own type
  // narrowing locally so we don't need to mirror server schemas in two places
  // until the API surface stabilizes.

  suiteHealth: <T = any>(projectId: number) =>
    request<T>(`/projects/${projectId}/suites/health`),
  baselineHistory: <T = any>(projectId: number) =>
    request<T>(`/projects/${projectId}/baselines/history`),
  caseTimeline: <T = any>(suiteId: number, caseName: string) =>
    request<T>(
      `/suites/${suiteId}/cases/${encodeURIComponent(caseName)}/timeline`,
    ),
  coverage: <T = any>(projectId: number) =>
    request<T>(`/projects/${projectId}/coverage`),
  runBenchmark: <T = any>(suiteId: number, models: string[]) =>
    request<T>(`/suites/${suiteId}/benchmark`, {
      method: "POST",
      json: { models },
    }),
  replaySeed: <T = any>(caseRunId: number) =>
    request<T>(`/case-runs/${caseRunId}/replay-seed`),
  replayCase: <T = any>(
    caseRunId: number,
    body: { output: string; latency_ms: number; cost_usd: number },
  ) =>
    request<T>(`/case-runs/${caseRunId}/replay`, {
      method: "POST",
      json: body,
    }),
};

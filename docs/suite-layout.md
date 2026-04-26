# Canonical suite layout

This page is the single source of truth for what files an `agentprdiff` adoption produces, what each file contains, and which are mandatory.

If you're an AI coding agent following [`AGENTS.md`](../AGENTS.md), use this as the layout reference. If you're a human reviewer reading an adoption PR, this is the spec to check the diff against.

---

## At a glance

```
<project_root>/
├── suites/
│   ├── __init__.py                 ← optional
│   ├── _eval_agent.py              ← MANDATORY
│   ├── _stubs.py                   ← MANDATORY iff any tool has side effects
│   ├── <project>.py                ← MANDATORY (the suite definition)
│   └── README.md                   ← recommended
│
├── .agentprdiff/
│   ├── .gitignore                  ← MANDATORY (auto-created by `init`)
│   ├── baselines/
│   │   └── <suite_name>/
│   │       └── <case_name>.json    ← MANDATORY (auto-created by `record`)
│   └── runs/                       ← auto-created, NEVER committed
│
└── .github/workflows/
    └── agentprdiff.yml             ← strongly recommended; not strictly required
```

Five mandatory artifacts, two recommended, one optional. Everything else lives elsewhere — your existing production code, tests, requirements file, CI scripts.

---

## File-by-file reference

### `suites/<project>.py` — **MANDATORY**

This is the suite definition. The single file `agentprdiff record` and `agentprdiff check` are pointed at. Renaming or relocating it does nothing to its function as long as the path you pass to the CLI matches.

**Must contain:**

- A `sys.path` insert block at the top, adding the project root to `sys.path` so `from <project_module> import ...` resolves under the loader. (As of agentprdiff 0.2 the loader does this defensively too, but keep the block — belt-and-suspenders.)
- Imports from `agentprdiff` (`case`, `suite`) and `agentprdiff.graders` (whatever graders the cases use).
- An import of the eval-mode agent function (typically `from suites._eval_agent import <fn>`).
- Exactly one module-level `Suite` instance, bound to a variable. Multiple `Suite`s in one file is allowed but unusual; one suite per file is the norm.
- A list of `case(...)` entries inside the `cases=[...]` parameter.

**Must not contain:**

- Logic that mutates global state on import (DB connections, file writes, network calls).
- Calls to your production agent — that's what the suite *runs*, not what the file contains.

**Naming convention:** the filename can be whatever you want; `<project>.py` is recommended for the obvious mapping. The `name=` field on the `Suite` becomes the directory name under `.agentprdiff/baselines/`, so pick a slug that's stable and project-identifying.

### `suites/_eval_agent.py` — **MANDATORY**

The eval-mode wrapper around your production agent. Holds the function the suite passes as `agent=<fn>`. Returns `(output_text, Trace)`.

**Must contain:**

- A function (commonly named `eval_agent` or `<project>_eval_agent`) that takes a single string argument (the user input) and returns `(str, Trace)`.
- Either (a) an `instrument_client(...)` context manager wrapping the LLM client, plus `instrument_tools(...)` swapping in stubs, OR (b) manual `Trace.record_llm_call(...)` / `record_tool_call(...)` calls.
- Imports of production constants you reuse — `SYSTEM_PROMPT`, the tools spec, the `_call_llm` helper if one exists. Do not redefine them.

**Must not contain:**

- Any modification of the production agent module's global state. No monkey-patching `agent.agent.TOOL_MAP`, no overwriting `SYSTEM_PROMPT`, no patching the production client factory. The whole point is that production stays byte-identical.
- Direct calls to side-effecting production tools. Those go through stubs (see below).

**Why a separate file from the suite definition?** The suite is data; the wrapper is code. Keeping them apart makes both files easier to read in PR review, and the suite file is shorter, which matters when reviewers are scanning case lists.

### `suites/_stubs.py` — **MANDATORY iff any tool has side effects**

Deterministic stand-ins for production tools that hit external systems. If your agent has *no* side-effecting tools (pure-function tools only — calculators, data transformers, in-memory lookups), you don't need this file.

**Must contain:**

- One Python function per side-effecting production tool. Each returns a dict (or whatever shape the production tool returns) with deterministic, plausible fake values.
- A `STUB_TOOL_MAP` dict mapping the same string keys as the production `TOOL_MAP` to the stub functions. The eval wrapper imports this and passes it to `instrument_tools`.

**Must not contain:**

- Real network calls (the entire reason for stubs is to avoid this).
- Real filesystem writes outside `tmp_path`-style scoped fixtures.
- Logic that depends on environment state (current time, env vars). Stubs must be reproducible.

**Stub-shape rule:** match the production tool's return-value keys exactly. If the production tool returns `{"success": True, "rows": [...]}`, the stub returns the same keys. The LLM reads these dicts as `role="tool"` messages; if the shape diverges, the model's downstream reasoning diverges in ways that break the suite's reproducibility for unrelated reasons.

### `suites/__init__.py` — **OPTIONAL**

Marks `suites/` as a Python package so `from suites._eval_agent import ...` resolves cleanly under more import-path conditions. With agentprdiff 0.2's loader (which adds cwd to `sys.path`), this is no longer strictly required for `record` and `check` to work — but it's a one-line file, costs nothing, and makes the directory legible to other tools.

**Recommended content:** a docstring describing the suite's purpose and how to run it. No imports.

### `suites/README.md` — **RECOMMENDED**

Human-readable docs for the suite. The audience is a future reviewer (or a future you) trying to understand why specific cases exist.

**Should contain:**

- One paragraph: what this suite tests and why.
- The setup commands (`pip install`, env vars, secrets).
- The case-by-case rationale: a short table mapping each case name to the contract it pins.
- Any project-specific notes on running, e.g. "set `LLM_API_KEY=` before `record`."

**Should NOT contain:** copies of agentprdiff's own documentation. Link to `docs/adapters.md` and `docs/ai-driven-adoption.md` in the agentprdiff repo instead.

### `.agentprdiff/.gitignore` — **MANDATORY (auto-created)**

`agentprdiff init` writes this. It contains exactly two non-comment lines:

```
# Committed:   baselines/
# Not committed: runs/
runs/
```

The intent is encoded in the filenames themselves. **Commit this file.** Without it, contributors who run `agentprdiff check` locally will accumulate `runs/` directories and accidentally commit them.

### `.agentprdiff/baselines/<suite_name>/<case_name>.json` — **MANDATORY (auto-created)**

One file per case. `agentprdiff record` writes them. `agentprdiff check` reads them.

**Must contain (per the schema in `agentprdiff.core.Trace`):**

- `case_name`, `suite_name`, `input`, `output` — what came in and what went out.
- `llm_calls[]` — every model invocation, with provider, model, input messages, output text, tool calls, token counts, cost in USD, and latency in ms.
- `tool_calls[]` — every tool invocation, with name, arguments, result, latency, and any exception.
- `total_cost_usd`, `total_latency_ms`, `total_prompt_tokens`, `total_completion_tokens` — aggregate counters.
- `metadata` — free-form dict you can stamp from your wrapper (provider, model, deployment env, etc.).
- `run_id`, `created_at` — identifiers.

**Must NOT contain:** real API keys, customer PII, internal URLs you don't want public. Inspect at least one baseline manually before the first commit. If you find sensitive data, redact it (replace with `<REDACTED>`) and re-record after fixing the leak in your input fixtures or the agent's response.

**Format:** pretty-printed JSON, sorted keys preserved. Designed for human-readable `git diff` output. Don't reformat with a JSON minifier — you'll lose the diffability that's load-bearing for the workflow.

### `.agentprdiff/runs/` — **AUTO-CREATED, NEVER COMMITTED**

Each `agentprdiff check` writes this run's traces here, timestamped. The `.gitignore` from `init` keeps them out of git. They're for local debugging — when a check fails, you can compare the trace under `runs/` to the trace under `baselines/` to see what changed.

### `.github/workflows/agentprdiff.yml` — **STRONGLY RECOMMENDED**

The CI workflow that runs `agentprdiff check` on every PR. Not strictly required (you can run check locally before merging) but realistically, suites that aren't enforced in CI atrophy.

**Must contain:**

- A trigger that fires on PRs touching the agent code or the suite. Use `paths:` to scope it.
- A step that installs your project's deps + agentprdiff.
- A step that runs `agentprdiff check suites/<name>.py` with the API-key env var set from a repository secret.
- A graceful skip when the secret is absent (so forks/contributor PRs don't fail with confusing errors).
- An artifact-upload step that captures the JSON output for post-mortem.

A complete template is in [AGENTS.md Step 7](../AGENTS.md#step-7--wire-ci).

---

## Mandatory / recommended / optional, summarized

| File | Status | Created by | Notes |
|---|---|---|---|
| `suites/<project>.py` | MANDATORY | adopter | The suite definition |
| `suites/_eval_agent.py` | MANDATORY | adopter | Wrapper returning `(output, Trace)` |
| `suites/_stubs.py` | MANDATORY iff side-effecting tools exist | adopter | Deterministic tool stand-ins |
| `suites/__init__.py` | optional | adopter | Package marker; recommended |
| `suites/README.md` | recommended | adopter | Human docs |
| `.agentprdiff/.gitignore` | MANDATORY | `agentprdiff init` | Commit this |
| `.agentprdiff/baselines/<suite>/<case>.json` | MANDATORY (one per case) | `agentprdiff record` | Commit these |
| `.agentprdiff/runs/` | auto-created | `agentprdiff check` | Never commit |
| `.github/workflows/agentprdiff.yml` | strongly recommended | adopter | The PR-time enforcer |

Total artifacts in a typical adoption: 5 hand-written files, plus 1 `.gitignore` and N baseline JSONs (one per case). For a 7-case suite that's 13 files in the diff.

---

## Anti-patterns the layout exists to prevent

**Mixing the suite definition and the wrapper into one file.** Tempting, but it makes the case list harder to scan and review. Keep them separate.

**Stubbing tools that don't have side effects.** Wastes your time and creates drift between stub and production. If a tool is a pure function, use it as-is.

**Putting baselines in `runs/`.** They'll be `.gitignore`'d and disappear on the next clean checkout. Always under `baselines/`.

**Storing baselines outside `.agentprdiff/`.** The store class hardcodes the path. Don't try to relocate the directory; it's a convention, not a configuration.

**Committing `runs/`.** Floods the repo with noise. The `.gitignore` from `init` prevents this; don't override it.

**One giant suite file with 50 cases.** Split by domain. `suites/billing.py`, `suites/onboarding.py`, etc. Each gets its own baselines directory, each gets its own CI step. Smaller failure surface.

**Modifying production code to make the wrapper cleaner.** No. The wrapper imports production constants and re-implements the loop. Production code stays byte-identical.

---

## Validation: what the diff should look like

When you finish an adoption, the diff your PR introduces should match this shape exactly — no more, no less:

```
A  suites/__init__.py            (~5 lines, docstring only)
A  suites/_eval_agent.py         (~50–100 lines)
A  suites/_stubs.py              (~30–80 lines if needed)
A  suites/<project>.py           (~50–150 lines depending on case count)
A  suites/README.md              (~50–150 lines)
A  .agentprdiff/.gitignore       (3 lines)
A  .agentprdiff/baselines/<project>/*.json   (one per case)
A  .github/workflows/agentprdiff.yml         (~30 lines)
```

If your diff also includes changes under your production agent module path, **stop and reconsider**. The integration is supposed to be additive only. Production-code changes belong in a separate PR, not the adoption PR.

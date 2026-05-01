# Changelog

All notable changes to `agentprdiff` are documented in this file. Originally
prototyped under the name `tracediff`; renamed before first public release.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.5] — 2026-04-30

Infrastructure-only release. Code is identical to 0.2.4 — this exists
solely to publish via PyPI's Trusted Publishing flow (OIDC from GitHub
Actions) so the project page shows verified details for the source
repository. No API token is used to upload this release.

### Internal

- Added `.github/workflows/release.yml` that builds sdist + wheel and
  publishes via `pypa/gh-action-pypi-publish` with OIDC. Triggered on
  GitHub `release: published` and `workflow_dispatch`. Uses the `pypi`
  GitHub environment for tag-restricted deploys (`v*`).
- First release published through the configured PyPI Trusted Publisher
  for `vnageshwaran-de/agentprdiff` → `release.yml` → `pypi` env.

## [0.2.4] — 2026-04-29

Metadata-only release. Code is identical to 0.2.3 — this exists solely
to ship the new docs site URL onto pypi.org's package sidebar (PyPI
reads `Project-URL` entries from the published wheel, not from the
GitHub repo, so the docs site otherwise wouldn't be discoverable from
pypi.org/project/agentprdiff).

### Changed

- `pyproject.toml` `[project.urls]`: `Homepage` and `Documentation` now
  point at the new MkDocs Material site (https://agentprdiff.dev).
  `Repository` and `Issues` continue to point at GitHub. Added a
  `Changelog` URL.
- `README.md`: prominent docs link in the header and pepy.tech download
  badges (lifetime + monthly).

### Internal

- Synced `agentprdiff.__version__` string with `pyproject.toml`'s
  `version` field — was a leftover miss from the 0.2.3 release.

## [0.2.3] — 2026-04-28

### Added

- **Semantic-judge banner in `check` and `review` output.** `TerminalReporter`
  and `ReviewReporter` now print one line — `semantic judge: <description>`
  — directly under the header whenever the suite contains at least one
  `semantic(...)` grader. The description names the active backend
  (`fake_judge`, `openai/<model>`, or `anthropic/<model>`) and the env-var
  signal that selected it, with explicit `silent fallback` wording when
  no judge is configured. Closes the most common adoption trap: shipping
  suites whose semantic coverage is decorative because no key was set
  and the runner stayed quiet about it. Suites without `semantic(...)`
  are unaffected — no banner is printed. New helpers
  `agentprdiff.graders.semantic.describe_default_judge()` and
  `case_uses_semantic()` power the rendering and are reusable by
  third-party tooling.
- **Scaffolded workflow YAML now flags judge-SDK installs explicitly.**
  `_TPL_WORKFLOW` ships commented `pip install anthropic` /
  `pip install openai` lines tied to the chosen `AGENTGUARD_JUDGE` mode,
  with guidance that a missing SDK raises `ImportError` rather than
  falling back silently. Pairs with the in-band judge banner for
  end-to-end coverage of the silent-fake_judge trap.

### Fixed

- Adoption checklist in `AGENTS.md` now requires the adopter to verify
  the installed CLI supports the documented commands (`agentprdiff
  check --help`) before writing run commands into the case dossier —
  prevents the "source docs reference `--case` but the pinned wheel
  predates it" confusion reported during 0.2.x adoption.
- New `Step 5b — decide and document the semantic-judge mode` mandates
  a `## Semantic Judge Keys` section in `suites/README.md` and an
  explicit `AGENTGUARD_JUDGE=<mode>` line in the workflow YAML, replacing
  the implicit "first available key wins" precedence with a deliberate
  declaration.

## [0.2.2] — 2026-04-28

### Added

- **Native `AsyncOpenAI` support in `agentprdiff.adapters.openai`.** The same
  `instrument_client` and `instrument_tools` API now works with the async
  OpenAI client — and any async OpenAI-compatible provider (Groq, Gemini,
  OpenRouter, Ollama, vLLM, Together, Fireworks, DeepInfra). The adapter
  inspects `client.chat.completions.create` at `with`-block entry; if it's
  a coroutine function, an awaitable patched method is installed and the
  user's `await client.chat.completions.create(...)` call sites work
  unchanged. `instrument_tools` matches per-tool: `async def` tools come
  back as `async def` wrappers (`await tools[name](**args)`), sync tools
  stay sync — a single `TOOL_MAP` may freely mix the two. The `with` block
  is a regular `with`, not `async with`, since the patch is bound to the
  client instance rather than the running event loop. agentprdiff's runner
  remains sync; async agents bridge with `asyncio.run` in their
  `eval_agent` entry point. Removes the previous adoption recommendation
  to use manual `Trace.record_llm_call` instrumentation for async agents.
- **Updated `--recipe async-openai` scaffold** to use the new adapter
  natively. The generated `_eval_agent.py` no longer carries TODO markers
  for manual instrumentation; it imports `instrument_client` /
  `instrument_tools` and wraps an async tool-calling loop with an
  `asyncio.run` bridge to agentprdiff's sync runner.
- **`agentprdiff review <suite_file>`** — new subcommand for local iteration
  on a single failing case. Runs the same comparison `check` does, but
  renders one verbose panel per case (input echo, full assertion table with
  `was → now` baseline-vs-current marks, per-metric deltas for cost /
  latency / prompt and completion tokens, tool-sequence diff, and a unified
  output diff in its own panel when output changed) and **always exits 0**
  so it can sit inside watcher / `entr` / `fzf` loops without flipping the
  shell red on every regression. Accepts the same `--case` / `--skip` /
  `--list` flags as `record` and `check`. The CI gate stays `agentprdiff
  check`; `review` is the `pytest -k` of agentprdiff. New `ReviewReporter`
  in `agentprdiff.reporters` powers the rendering and is reusable by
  third-party tooling.
- **`agentprdiff scaffold <name>`** — new subcommand that stamps out the
  canonical adoption layout (`suites/__init__.py`, `_eval_agent.py`,
  `_stubs.py`, `<name>.py`, `<name>_cases.md`, `suites/README.md`, and
  `.github/workflows/agentprdiff.yml`). Three recipes via `--recipe`:
  `sync-openai` (default; uses `instrument_client`), `async-openai` (manual
  asyncio wrapper, until the async adapter ships in 0.3), and `stubbed`
  (substitutes a single LLM helper — see the new "stubbed LLM-boundary
  pattern" recipe in `docs/adapters.md`). The generated workflow includes
  `permissions: contents: read` so GitHub Advanced Security stops flagging
  it. Pre-existing files are never overwritten — they're reported as
  `[skip]` and the rest are still written.
- **Case dossier** (`suites/<name>_cases.md`) — new mandatory artifact
  produced by `scaffold` and documented in AGENTS.md and
  `docs/suite-layout.md`. Reviewer-facing markdown with one block per case
  using a fixed five-field structure: *What it tests*, *Input*,
  *Assertions* (plain English), *Code impacted* (file:line references back
  to production code), and *Application impact* (one concrete sentence
  about what breaks for end users on regression). Closes the gap between
  case names that look meaningful in CI output ("article_summary_preserves_acquisition_entities")
  and reviewers who need to know what each case actually pins.
- New "stubbed LLM-boundary pattern" recipe in `docs/adapters.md` for
  agents whose LLM call is wrapped in a single helper (summarization,
  classification, embedding-prep). Stubbing the helper is cleaner than
  stubbing the SDK client and works equally well for sync and async clients.
- `agentprdiff record` and `agentprdiff check` now accept `--case PATTERN` and
  `--skip PATTERN` for narrowing a run to a subset of cases. Patterns are
  case-insensitive substrings by default and use `fnmatch` semantics when they
  contain `*`, `?`, or `[`. Both flags are repeatable, accept comma-separated
  lists (`--case refund,policy`), and support qualifier syntax
  (`--case billing:refund*`). A leading `~` (or `!`) negates a pattern, so
  `--case ~slow` is equivalent to `--skip slow`.
- `agentprdiff record --list` / `check --list` prints suite and case names
  without running anything, so you can discover what's filterable before
  reaching for `--case`.
- When a filter is active, the CLI now prints a per-suite header
  (`running 2 of 4 cases in customer_support: ...`) so a partial selection is
  visible at a glance. A filter that matches zero cases exits with code 2 and
  prints the available case names — previously a typo'd filter would have
  silently exited 0.

### Changed

- The CI workflow templates in `AGENTS.md`, `README.md`, and
  `docs/ci-integration.md` now declare `permissions: contents: read`
  explicitly. GitHub Advanced Security flags workflows without an explicit
  permissions block, and least-privilege is the right default anyway.
- Documented the recommended `.gitignore` entry (`artifacts/agentprdiff*.json`)
  alongside every CI snippet that uses `--json-out artifacts/...`. The path
  uploads cleanly as a CI build artifact, but the same file lands on every
  local run; without an ignore line it eventually gets `git add`-ed by
  accident.

### Documentation

- New "API keys" section in `AGENTS.md` covering both key surfaces (the
  production agent's own keys vs. agentprdiff's semantic-judge keys —
  `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `AGENTGUARD_JUDGE` with the
  silent fake_judge fallback), local setup options (.env / shell export /
  direnv), CI secret wiring, and a short list of "never do this." Adopting
  AI agents are now instructed to explicitly prompt the user about which
  env var their production agent reads, whether to use a real semantic
  judge in CI, and to verify `.env` is gitignored.
- The scaffolded `.github/workflows/agentprdiff.yml` now includes both
  `OPENAI_API_KEY` and an optional `ANTHROPIC_API_KEY` (for the semantic
  judge) with explanatory comments. The scaffolded `suites/README.md` has a
  new "Setup" section walking through local key configuration.
- New "Rerun semantics" section in `AGENTS.md` (referenced from README and
  `docs/ci-integration.md`) covering what every subcommand does on the
  second invocation: `record` overwrites baselines in place, `check`
  accumulates a timestamped directory under `.agentprdiff/runs/` on every
  call (gitignored; `rm -rf` to clean), `--json-out PATH` overwrites a
  single file, and `scaffold`/`init` refuse-to-overwrite / are idempotent.
  Adopting AI agents are now instructed to surface the `runs/` accumulation
  behavior to the human user during handoff — it's the only one of the
  four that creates new files on every invocation.

## [0.2.1] — 2026-04-26

### Changed

- README links to `AGENTS.md`, `docs/adapters.md`, `docs/ai-driven-adoption.md`,
  `docs/suite-layout.md`, `docs/ci-integration.md`, `LICENSE`, and `CHANGELOG.md`
  are now absolute GitHub URLs instead of relative paths. PyPI's project page
  and Libraries.io render the README but cannot resolve relative repo paths;
  the previous links rendered as broken from those surfaces. Absolute URLs
  fix the click-through from PyPI / Libraries.io directly to the docs on
  GitHub.
- Status section refreshed: 0.2.x is the current alpha line; OpenAI and
  Anthropic SDK adapters are now shipped (previously listed as 0.2 roadmap).
  LangChain/LangGraph adapters and the JS companion package moved to the
  0.3 roadmap.

### Fixed

- Wheel metadata now emits a separate `Author:` header in addition to
  `Author-email:`. Previously, the PEP 621 `authors = [{ name = ..., email = ... }]`
  form generated only `Author-email: "Name" <email>`, which downstream
  parsers like pypistats.org could not read (showing "Author: None").
  Splitting `authors` into a name-only entry plus an email-only entry,
  and adding a parallel `maintainers` field, makes the package author
  display correctly across PyPI, Libraries.io, and pypistats.

## [0.2.0] — 2026-04-26

### Added

- **SDK adapters** for the two dominant agent toolchains, eliminating the need
  for manual `Trace` instrumentation:
  - `agentprdiff.adapters.openai.instrument_client` — context manager that
    monkey-patches `client.chat.completions.create` for the duration of one
    agent call. Records each invocation as an `LLMCall` (provider, model,
    input messages, output text, tool calls, tokens, cost, latency) and
    restores the original on exit. Works with **OpenAI, Groq, Gemini's
    OpenAI-compatible endpoint, OpenRouter, Ollama, vLLM, Together,
    Fireworks, DeepInfra**, and any other SDK that follows the OpenAI client
    shape.
  - `agentprdiff.adapters.anthropic.instrument_client` — equivalent for the
    Anthropic Messages API (`client.messages.create`). Handles the
    content-block response shape (text + `tool_use` blocks) and the
    Messages-API token field names.
  - `instrument_tools(tool_map, trace)` — wraps a dict of callables so each
    invocation records a `ToolCall` with name, arguments, result, latency,
    and any raised exception. Shared between both adapters.
  - `agentprdiff.adapters.pricing` — curated model→price table for cost
    estimation, with `register_prices()` and per-call `prices=` overrides.
    Unknown models record `cost_usd=0.0` and emit a single `RuntimeWarning`
    per process so missing pricing is loud rather than silent.
- Documentation: `docs/adapters.md` (full reference) and
  `docs/adapters-vercel.md` (manual integration recipe for the Vercel AI
  SDK, which is JS-only and lives in a future companion package).
- `AGENTS.md` at the repo root — an instruction set written for AI
  coding agents (Claude Code, Cursor, Aider, etc.) that have been asked
  to add `agentprdiff` to a codebase. Covers codebase discovery,
  contract identification, wrap-the-agent recipes (OpenAI / Anthropic /
  custom), stub patterns, suite scaffolding, baseline recording, CI
  wiring, common pitfalls, and a validation checklist. Optimized for
  AI-agent-driven adoption with copy-paste templates.
- `docs/ai-driven-adoption.md` — human-facing companion to AGENTS.md.
  Three prompt templates (minimum viable / recommended / contract-driven)
  for adopters using Claude Code / Cursor / Aider, plus a sample
  first-session transcript and tips for working with the AI agent
  through the adoption flow.
- `docs/suite-layout.md` — canonical reference for the suite directory
  structure. Lists each file (`suites/<project>.py`, `_eval_agent.py`,
  `_stubs.py`, baselines, CI workflow, etc.), classifies them as
  mandatory / recommended / optional, and specifies what each must
  and must not contain. Cross-referenced from AGENTS.md and the
  validation checklist.

### Changed

- The suite loader now inserts the current working directory onto
  `sys.path` in addition to the suite file's parent directory. Adopters
  who run `agentprdiff record suites/foo.py` from their project root no
  longer have to manually patch `sys.path` to import their own modules
  (e.g. `from agent.agent import ...`, `from config import ...`).
  Both insertions are reverted after the suite loads, so no path leakage
  between runs.

### Notes

- The base `pip install agentprdiff` does **not** require the `openai` or
  `anthropic` packages. The adapters operate on a client object's shape,
  not on imported SDK modules — so installing only the SDKs you actually
  use keeps the dependency footprint small. Optional extras are still
  declared (`agentprdiff[openai]`, `agentprdiff[anthropic]`) for adopters
  who prefer to pin the SDK version alongside agentprdiff itself.

## [0.1.0] — 2026-04-22

Initial public release.

### Added

- Core `Suite` / `Case` / `Trace` model for defining agent regression tests.
- Deterministic graders: `contains`, `contains_any`, `regex_match`, `tool_called`,
  `tool_sequence`, `output_length_lt`, `latency_lt_ms`, `cost_lt_usd`,
  `no_tool_called`.
- Semantic grader (`semantic`) with a pluggable `judge` callable and built-in
  fake judge for CI environments without API keys.
- Baseline store (JSON files under `.agentprdiff/baselines/`) designed to be
  committed to version control.
- Trace diff engine producing a structured `TraceDelta` (assertion pass/fail
  changes, cost delta, latency delta, tool-call sequence changes, output
  change).
- CLI: `agentprdiff init`, `agentprdiff record`, `agentprdiff check`, `agentprdiff diff`.
- Rich-formatted terminal reporter and machine-readable JSON reporter for CI.
- Quickstart example with a mock agent that runs without any API keys.
- Pytest test suite covering graders, runner, differ, store, and CLI smoke.
- GitHub Actions CI workflow.

### Known limitations

- Only a manual instrumentation API for provider SDKs is shipped in 0.1.0.
  Drop-in wrappers for OpenAI / Anthropic / Vercel AI SDK are planned for 0.2.
- The semantic grader's built-in judge supports OpenAI and Anthropic via user-
  supplied API keys; hosted judge endpoints are not yet offered.

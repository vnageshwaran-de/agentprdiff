# agentprdiff

**Guard your LLM agents in CI.** Snapshot tests that catch behavioral regressions when models, prompts, or vendors change.

📚 **[Documentation: agentprdiff.dev](https://agentprdiff.dev/)** &nbsp;·&nbsp; ⚡ [Quickstart](https://agentprdiff.dev/quickstart/) &nbsp;·&nbsp; 🤖 [AI-agent adoption](https://agentprdiff.dev/quickstart/#path-a--let-an-ai-agent-adopt-the-package-for-you-recommended) &nbsp;·&nbsp; 📦 [PyPI](https://pypi.org/project/agentprdiff/)

> You upgraded Claude. You tweaked a system prompt. You swapped `gpt-4o` for `gpt-4o-mini` in the cheap path. Which of your agent's behaviors just changed? `agentprdiff` tells you — before the PR merges.

```bash
pip install agentprdiff
```

> **Don't have Python 3.10+ yet?** Step-by-step install instructions for
> [macOS, Windows, and Linux](https://agentprdiff.dev/installation/#install-python-310-first-if-you-dont-have-it).
>
> **Multiple Python versions on your machine?** If `pip install` reports
> `No matching distribution found` even after installing Python 3.10+,
> use `python3.12 -m pip install agentprdiff` (substitute your installed
> 3.10+ binary). Sidesteps `$PATH` confusion when Homebrew's Python and
> the system Python coexist. Full troubleshooting: [Installation guide](https://agentprdiff.dev/installation/).

[![CI](https://github.com/vnageshwaran-de/agentprdiff/actions/workflows/ci.yml/badge.svg)](https://github.com/vnageshwaran-de/agentprdiff/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agentprdiff.svg)](https://pypi.org/project/agentprdiff/)
[![Python](https://img.shields.io/pypi/pyversions/agentprdiff.svg)](https://pypi.org/project/agentprdiff/)
[![Downloads](https://static.pepy.tech/badge/agentprdiff)](https://pepy.tech/project/agentprdiff)
[![Downloads/month](https://static.pepy.tech/badge/agentprdiff/month)](https://pepy.tech/project/agentprdiff)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/vnageshwaran-de/agentprdiff/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-agentprdiff.dev-blue)](https://agentprdiff.dev/)

> **Adopting with an AI coding agent?** Point Claude Code, Cursor, Aider, or any agentic IDE at [`AGENTS.md`](https://github.com/vnageshwaran-de/agentprdiff/blob/main/AGENTS.md) — a step-by-step adoption playbook the agent reads directly. Humans driving the adoption: see [`docs/ai-driven-adoption.md`](https://github.com/vnageshwaran-de/agentprdiff/blob/main/docs/ai-driven-adoption.md) for copy-paste prompt templates. The canonical file layout — what's mandatory, what's recommended, what's optional — is at [`docs/suite-layout.md`](https://github.com/vnageshwaran-de/agentprdiff/blob/main/docs/suite-layout.md).

## Why

Unit tests assume determinism. Agents aren't deterministic, but they do have *behaviors you rely on* — a specific tool gets called, a refund amount is quoted, a latency budget is respected, a safety guardrail fires. When a model or prompt changes, those behaviors drift. Today most teams find out in production.

`agentprdiff` turns those behaviors into versioned, diffable baselines you check into git, and a CI command that fails the build when they regress.

It is **not** a framework. Your agent stays exactly the way it is. `agentprdiff` records what it did, lets you assert what should be true about what it did, and compares runs across time.

## 10-line hello world

```python
# suite.py
from agentprdiff import case, suite
from agentprdiff.graders import contains, tool_called, latency_lt_ms, semantic
from my_agent import run  # your agent — unchanged

support = suite(
    name="customer_support",
    agent=run,
    cases=[
        case(
            name="refund_happy_path",
            input="I want a refund for order #1234",
            expect=[
                contains("refund"),
                tool_called("lookup_order"),
                semantic("agent acknowledges the refund and explains the timeline"),
                latency_lt_ms(10_000),
            ],
        ),
    ],
)
```

```bash
agentprdiff init
agentprdiff record suite.py     # save this run as the baseline
agentprdiff check  suite.py     # in CI: diff vs baseline, exit 1 on regression
```

That's the whole product. Five CLI commands (`init`, `record`, `check`, `review`, `scaffold`). One Python file. Zero framework lock-in.

## What's in the box

- **Case + Suite model** — tiny, opinionated, no magic.
- **10 batteries-included graders** — `contains`, `contains_any`, `regex_match`, `tool_called`, `tool_sequence`, `no_tool_called`, `output_length_lt`, `latency_lt_ms`, `cost_lt_usd`, `semantic` (LLM-as-judge with pluggable backend).
- **Baseline store** — JSON files under `.agentprdiff/baselines/`, meant to be **committed**. Reviewers see trace changes in pull requests.
- **Diff engine** — per-case `TraceDelta` with assertion pass/fail changes, cost delta, latency delta, tool-sequence changes, and a unified output diff.
- **CI-ready CLI** — exit 1 on regression, `--json-out` for artifact archiving, Rich-formatted terminal output.
- **Zero SDK lock-in** — works with OpenAI, Anthropic, Gemini, Bedrock, LangChain, LangGraph, LlamaIndex, Vercel AI SDK, custom wrappers — if you can wrap your agent in a function, `agentprdiff` can test it.
- **One-line SDK adapters** — `with instrument_client(client) as trace:` automatically records every LLM and tool call when you're on the OpenAI Python SDK (sync **or** async — `AsyncOpenAI` is supported by the same context manager) or any OpenAI-compatible provider (Groq / Gemini / OpenRouter / Ollama / vLLM / Together / Fireworks / DeepInfra) or the Anthropic SDK. No manual `Trace` wiring required.

## How it compares

| | Unit tests | LLM-as-judge eval | `agentprdiff` |
|---|---|---|---|
| Deterministic pass/fail | yes | no | **yes** (when assertions are deterministic) |
| Catches behavioral drift | no | yes | **yes** |
| Runs in CI on every PR | yes | too expensive | **yes** |
| Human-readable diff of what changed | n/a | rare | **yes** |
| Works without API keys | yes | no | **yes** (deterministic graders + fake judge) |

The value is in the combination: deterministic assertions for the 80% of behaviors you can encode as rules ("this tool was called", "this word appeared", "cost stayed under $0.02"), plus a semantic grader for the 20% that need a judge — with a fake-judge fallback so your CI stays green and free when API keys aren't available.

## The workflow

1. Write a `Suite` alongside your agent code.
2. Run `agentprdiff record` once on a known-good version. Commit the resulting `.agentprdiff/baselines/` directory.
3. In CI, on every PR, run `agentprdiff check`. If any assertion regresses, or cost/latency budgets are breached, the job fails.
4. When behavior intentionally changes, the PR author re-runs `agentprdiff record`, commits the new baseline, and explains the change in the PR description. Reviewers see the before/after in the diff.

This is the same loop as Jest snapshot tests or VCR cassettes — applied to LLM agents.

### API keys

`agentprdiff` doesn't read your agent's API key — your agent does, through whatever env var it already uses. Set that locally (in `.env`, your shell, direnv, whatever) and as a GitHub Actions secret in CI. The scaffold's workflow YAML has the right shape; you fill in the env var name to match your agent.

The `semantic()` grader is the one piece of agentprdiff that can use an API key directly — for the LLM judge. Without one, it silently falls back to keyword matching. Set `ANTHROPIC_API_KEY` (cheaper) or `OPENAI_API_KEY` if you want a real judge in CI; leave both unset to keep CI free with fake_judge.

See [AGENTS.md → API keys](https://github.com/vnageshwaran-de/agentprdiff/blob/main/AGENTS.md#api-keys--what-to-set-where-and-how-to-ask-the-user-about-them) for the full setup (local options, CI secrets, what never to do).

### What each command does on rerun

A common first-day question. Short version:

- `record` — overwrites baselines in place. Re-recording an intentional change shows up as a regular git diff in your PR; that's the review surface.
- `check` — creates a new timestamped directory under `.agentprdiff/runs/` on every invocation. It's gitignored by default, so it never reaches CI; clean local history any time with `rm -rf .agentprdiff/runs/`. `--json-out PATH` overwrites a single file at PATH.
- `review` — same comparison as `check`, but renders one verbose panel per case and **always exits 0**. Designed for local iteration loops; not meant for CI. Writes to the same `.agentprdiff/runs/` directory.
- `scaffold` — never overwrites. Skips files that already exist (`[skip]`) and writes the rest.
- `init` — idempotent; running it twice does nothing the second time.

See [AGENTS.md → Rerun semantics](https://github.com/vnageshwaran-de/agentprdiff/blob/main/AGENTS.md#rerun-semantics--what-each-command-does-on-the-second-run) for examples.

### Scaffolding a new suite

Skip the copy-paste from [AGENTS.md](https://github.com/vnageshwaran-de/agentprdiff/blob/main/AGENTS.md):

```bash
agentprdiff scaffold ai_content_summary --recipe sync-openai
```

Writes the canonical layout (`suites/__init__.py`, `_eval_agent.py`, `_stubs.py`, `<name>.py`, `<name>_cases.md`, `suites/README.md`, and `.github/workflows/agentprdiff.yml`) with TODO markers where you wire in your agent. The `<name>_cases.md` file is a *case dossier* — reviewer-facing prose with one block per case (what it tests, input, assertions in plain English, file:line references to production code, and the application impact if the case regresses). Three recipes:

- `sync-openai` (default): uses `instrument_client` from the OpenAI adapter with a sync `OpenAI()` client.
- `async-openai`: same `instrument_client`, paired with an `asyncio.run` bridge so an `AsyncOpenAI` agent works with agentprdiff's sync runner. The adapter detects the async client at entry — no separate API.
- `stubbed`: substitutes a single LLM helper instead of the SDK client. Best for summarization / classification / embedding-prep agents — see [`docs/adapters.md`](https://github.com/vnageshwaran-de/agentprdiff/blob/main/docs/adapters.md#stubbed-llm-boundary-pattern).

The generated workflow includes `permissions: contents: read` so GHAS doesn't flag it. Pre-existing files are never overwritten.

## Instrumenting your agent

You have two paths. Most agents need the first.

### Option A — SDK adapters (zero manual work)

If your agent uses the OpenAI Python SDK — sync `OpenAI` **or** async `AsyncOpenAI`, including any OpenAI-compatible provider (Groq, Gemini, OpenRouter, Ollama, vLLM, Together, Fireworks, DeepInfra) — or the Anthropic SDK, the SDK adapter captures every model and tool call automatically:

```python
from openai import OpenAI
from agentprdiff.adapters.openai import instrument_client, instrument_tools

TOOL_MAP = {"lookup_order": lookup_order, "send_email": send_email}

def my_agent(query: str):
    client = OpenAI()
    with instrument_client(client) as trace:
        tools = instrument_tools(TOOL_MAP, trace)
        # ... your existing tool-calling loop, untouched ...
        # the only swap: TOOL_MAP[fn](**args) → tools[fn](**args)
        return final_text, trace
```

For `AsyncOpenAI`, the same `instrument_client` works — it inspects `client.chat.completions.create` at entry and installs an awaitable patched method when the underlying one is `async def`. `instrument_tools` mirrors per-tool: `async def` tools come back awaitable, sync tools stay sync. The `with` block is still a regular `with`:

```python
import asyncio
from openai import AsyncOpenAI
from agentprdiff.adapters.openai import instrument_client, instrument_tools

async def my_agent_async(query: str):
    client = AsyncOpenAI()
    with instrument_client(client) as trace:
        tools = instrument_tools(TOOL_MAP, trace)
        response = await client.chat.completions.create(...)
        # ... await tools[name](**args) for async tools, tools[name](**args) for sync ...
        return final_text, trace

def my_agent(query: str):
    return asyncio.run(my_agent_async(query))
```

The patch is scoped to the specific client instance and reversed when the `with` block exits — no global SDK state is touched. Anthropic adopters use `agentprdiff.adapters.anthropic` with the same shape (sync clients today; async Anthropic is on the roadmap).

See [`docs/adapters.md`](https://github.com/vnageshwaran-de/agentprdiff/blob/main/docs/adapters.md) for the full reference, including pricing overrides, custom provider tags, and recipes for nested agents.

### Option B — Manual instrumentation

If you're not on either SDK, or you want full control, build the `Trace` yourself — `agentprdiff` doesn't require any monkey-patching:

```python
from agentprdiff import Trace, LLMCall, ToolCall

def my_agent(query: str) -> tuple[str, Trace]:
    trace = Trace(suite_name="", case_name="", input=query)

    # ... call your model, record what happened ...
    trace.record_llm_call(LLMCall(
        provider="anthropic",
        model="claude-sonnet-4-6",
        prompt_tokens=120, completion_tokens=80,
        cost_usd=0.0012, latency_ms=340,
    ))

    # ... call a tool, record what happened ...
    trace.record_tool_call(ToolCall(name="lookup_order", arguments={"id": "1234"}))

    return final_output, trace
```

Agents that return just an output still work — `agentprdiff` wraps them and captures wall-clock latency. You can backfill richer instrumentation incrementally, assertion by assertion.

## CI integration

```yaml
# .github/workflows/agents.yml
name: agent-regression
on: [pull_request]
permissions:
  contents: read   # least-privilege; GHAS flags workflows without this.
jobs:
  agentprdiff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e ".[dev]"
      - run: agentprdiff check suites/*.py --json-out artifacts/agentprdiff.json
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: agentprdiff, path: artifacts/ }
```

If you use `--json-out artifacts/...`, add `artifacts/agentprdiff*.json` (or the broader `artifacts/`) to your project's `.gitignore` — the CI artifact upload doesn't prevent a contributor from accidentally `git add`ing it locally.

See [`docs/ci-integration.md`](https://github.com/vnageshwaran-de/agentprdiff/blob/main/docs/ci-integration.md) for GitLab, CircleCI, and Buildkite.

## Quickstart

A runnable end-to-end demo, no API keys needed:

```bash
git clone https://github.com/vnageshwaran-de/agentprdiff
cd agentprdiff
pip install -e ".[dev]"

cd examples/quickstart
agentprdiff init
agentprdiff record suite.py
agentprdiff check  suite.py   # exit 0

# now break the agent and watch agentprdiff catch it
sed -i "s/refund/noundr/g" agent.py
agentprdiff check suite.py    # exit 1; see the diff
```

### Running a subset of cases

Iterating on a single failing case shouldn't require commenting out the rest. `record`, `check`, and `review` all accept `--case` and `--skip` for narrowing a run:

```bash
# Discover what's available.
agentprdiff check suite.py --list

# Single case (case-insensitive substring).
agentprdiff check suite.py --case refund_happy_path

# Glob across cases.
agentprdiff check suite.py --case "*order*"

# Multiple patterns (repeated flag or comma-separated).
agentprdiff check suite.py --case refund --case policy
agentprdiff check suite.py --case refund,policy

# Everything except slow cases.
agentprdiff check suite.py --skip slow
agentprdiff check suite.py --case ~slow         # equivalent

# Qualify by suite when names collide across suites.
agentprdiff check suite.py --case "billing:refund*"
```

A filter that matches zero cases exits 2 and prints the available case names — `--list` is the discoverable counterpart. The selection summary (`running 2 of 4 cases in <suite>: ...`) is printed before each suite runs so a partial match is never silent.

### Reviewing one case (the local-iteration loop)

`agentprdiff check` is built for CI: a compact summary table and exit 1 on regression. While you're iterating on a single case, that's the wrong shape — you want to see *everything* about that one case, and you don't want your shell going red between every keystroke. That's `agentprdiff review`:

```bash
# Verbose per-case panel: input, every assertion's was→now verdict,
# cost/latency/token deltas, tool-sequence diff, output diff.
agentprdiff review suite.py --case refund_happy_path

# Same filter syntax as check / record — globs, negation, multi-pattern.
agentprdiff review suite.py --case "*refund*"
agentprdiff review suite.py --skip slow
```

`review` runs the same comparison `check` does (and writes to the same `.agentprdiff/runs/` directory) but **always exits 0**, even on regression — so it sits cleanly inside watcher loops (`entr`, `watchexec`, `fzf` previews). Use `check` when you want CI's exit semantics locally; reach for `review` while you're working. Think `pytest -k`.

## Status

`agentprdiff` is **alpha** (0.2.x). The core model, CLI, and OpenAI / Anthropic SDK adapters are stable. The OpenAI adapter covers both sync `OpenAI` and async `AsyncOpenAI` clients via the same `instrument_client` context manager. Async Anthropic, LangChain/LangGraph adapters, and a JS companion package for the Vercel AI SDK are on the 0.3 roadmap. See [`CHANGELOG.md`](https://github.com/vnageshwaran-de/agentprdiff/blob/main/CHANGELOG.md).

Feedback, bug reports, and PRs extremely welcome. Open an issue or @ me.

## License

MIT. See [`LICENSE`](https://github.com/vnageshwaran-de/agentprdiff/blob/main/LICENSE).

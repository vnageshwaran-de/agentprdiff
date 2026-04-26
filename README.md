# agentprdiff

**Guard your LLM agents in CI.** Snapshot tests that catch behavioral regressions when models, prompts, or vendors change.

> You upgraded Claude. You tweaked a system prompt. You swapped `gpt-4o` for `gpt-4o-mini` in the cheap path. Which of your agent's behaviors just changed? `agentprdiff` tells you — before the PR merges.

```bash
pip install agentprdiff
```

[![CI](https://github.com/vnageshwaran-de/agentprdiff/actions/workflows/ci.yml/badge.svg)](https://github.com/vnageshwaran-de/agentprdiff/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agentprdiff.svg)](https://pypi.org/project/agentprdiff/)
[![Python](https://img.shields.io/pypi/pyversions/agentprdiff.svg)](https://pypi.org/project/agentprdiff/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

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

That's the whole product. Four CLI commands. One Python file. Zero framework lock-in.

## What's in the box

- **Case + Suite model** — tiny, opinionated, no magic.
- **10 batteries-included graders** — `contains`, `contains_any`, `regex_match`, `tool_called`, `tool_sequence`, `no_tool_called`, `output_length_lt`, `latency_lt_ms`, `cost_lt_usd`, `semantic` (LLM-as-judge with pluggable backend).
- **Baseline store** — JSON files under `.agentprdiff/baselines/`, meant to be **committed**. Reviewers see trace changes in pull requests.
- **Diff engine** — per-case `TraceDelta` with assertion pass/fail changes, cost delta, latency delta, tool-sequence changes, and a unified output diff.
- **CI-ready CLI** — exit 1 on regression, `--json-out` for artifact archiving, Rich-formatted terminal output.
- **Zero SDK lock-in** — works with OpenAI, Anthropic, Gemini, Bedrock, LangChain, LangGraph, LlamaIndex, Vercel AI SDK, custom wrappers — if you can wrap your agent in a function, `agentprdiff` can test it.
- **One-line SDK adapters** — `with instrument_client(client) as trace:` automatically records every LLM and tool call when you're on the OpenAI Python SDK (or any OpenAI-compatible provider — Groq / Gemini / OpenRouter / Ollama / vLLM) or the Anthropic SDK. No manual `Trace` wiring required.

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

## Instrumenting your agent

You have two paths. Most agents need the first.

### Option A — SDK adapters (zero manual work)

If your agent uses the OpenAI Python SDK (or any OpenAI-compatible provider — Groq, Gemini, OpenRouter, Ollama, vLLM, Together, Fireworks, DeepInfra) or the Anthropic SDK, the SDK adapter captures every model and tool call automatically:

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

The patch is scoped to the specific client instance and reversed when the `with` block exits — no global SDK state is touched. Anthropic adopters use `agentprdiff.adapters.anthropic` with the same shape.

See [`docs/adapters.md`](./docs/adapters.md) for the full reference, including pricing overrides, custom provider tags, and recipes for nested agents.

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

See [`docs/ci-integration.md`](./docs/ci-integration.md) for GitLab, CircleCI, and Buildkite.

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

## Status

`agentprdiff` is **alpha** (0.1.0). The core model and CLI are stable; provider-specific SDK wrappers and a LangChain/LangGraph integration are on the 0.2 roadmap. See [`CHANGELOG.md`](./CHANGELOG.md).

Feedback, bug reports, and PRs extremely welcome. Open an issue or @ me.

## License

MIT. See [`LICENSE`](./LICENSE).

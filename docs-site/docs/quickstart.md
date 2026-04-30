---
id: quickstart
title: Quickstart
sidebar_position: 3
---

# Quickstart

Get from `pip install` to a green CI gate in under five minutes.

## 1. Install

```bash
pip install agentprdiff
```

## 2. Initialize the project

From the repository root of the agent you want to test:

```bash
agentprdiff init
```

This creates a `.agentprdiff/` directory:

```
.agentprdiff/
├── baselines/   # commit this directory
├── runs/        # gitignored automatically
└── .gitignore   # ignores runs/ for you
```

## 3. Wrap your agent

Your agent is any callable `(input) -> output` *or* `(input) -> (output, Trace)`.
The trace flavour is what unlocks tool / cost / latency assertions.

```python title="my_agent.py"
from agentprdiff import LLMCall, ToolCall, Trace

def my_agent(query: str) -> tuple[str, Trace]:
    trace = Trace(suite_name="", case_name="", input=query)

    # ── pretend we called an LLM ───────────────────────────────────────────
    trace.record_llm_call(LLMCall(
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_messages=[{"role": "user", "content": query}],
        output_text="Looking up your order…",
        prompt_tokens=18, completion_tokens=12,
        cost_usd=0.0002, latency_ms=180.0,
    ))

    # ── pretend we called a tool ───────────────────────────────────────────
    trace.record_tool_call(ToolCall(
        name="lookup_order",
        arguments={"order_id": "1234"},
        result={"status": "delivered", "amount_usd": 89.0},
        latency_ms=8.0,
    ))

    final_text = "Refund of $89.00 processed; expect it in 3–5 business days."
    return final_text, trace
```

> Already on the OpenAI or Anthropic Python SDK? Skip the manual wiring and
> use the [SDK adapters](./api/adapters.md) — one `with` block records every
> model and tool call automatically.

## 4. Write a suite

```python title="suite.py"
from agentprdiff import case, suite
from agentprdiff.graders import (
    contains, cost_lt_usd, latency_lt_ms, semantic, tool_called,
)
from my_agent import my_agent

billing = suite(
    name="billing",
    agent=my_agent,
    cases=[
        case(
            name="refund_happy_path",
            input="I want a refund for order #1234",
            expect=[
                contains("refund"),
                tool_called("lookup_order"),
                semantic("agent acknowledges the refund and explains the timeline"),
                latency_lt_ms(10_000),
                cost_lt_usd(0.02),
            ],
        ),
    ],
)
```

## 5. Record the baseline

```bash
agentprdiff record suite.py
```

Output:

```
agentprdiff record — suite billing  (1/1 passed, 0 regressed)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━┓
┃ Case               ┃ Result ┃ Cost Δ ┃ Latency ┃ Notes ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━┩
│ refund_happy_path  │ PASS   │        │         │  —    │
└────────────────────┴────────┴────────┴─────────┴───────┘
```

A new file appears at `.agentprdiff/baselines/billing/refund_happy_path.json`.
**Commit it.** Reviewers will see future trace changes as ordinary git diffs
in your PR.

## 6. Check on every PR

```bash
agentprdiff check suite.py
```

- Exit `0` when every case still matches its baseline.
- Exit `1` on any regression — pass→fail flip, new exception, or missing
  baseline + failing assertion.

## 7. Wire it into CI

```yaml title=".github/workflows/agents.yml"
name: agent-regression
on: [pull_request]
permissions:
  contents: read
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

That's it. Five commands, one Python file, zero framework lock-in.

## Where next

- [Core concepts](./concepts.md) — how the pieces fit together.
- [Usage guide](./usage/basic.md) — patterns for real agents.
- [Scenarios](./scenarios/simple-suite.md) — runnable end-to-end examples.

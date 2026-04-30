---
id: basic-usage
title: Basic Usage
sidebar_position: 1
---

# Basic Usage

The minimum viable workflow: write a suite, record, check.

## A complete minimal suite

```python title="suite.py"
from agentprdiff import case, suite
from agentprdiff.graders import contains, latency_lt_ms

def my_agent(query: str) -> str:
    # the simplest possible agent — no tools, no LLM tracking
    return f"You said: {query!r}. We're on it."

echo = suite(
    name="echo",
    agent=my_agent,
    cases=[
        case(
            name="basic_echo",
            input="hello world",
            expect=[
                contains("hello world"),
                latency_lt_ms(1_000),
            ],
        ),
    ],
)
```

```bash
agentprdiff init
agentprdiff record suite.py    # creates .agentprdiff/baselines/echo/basic_echo.json
agentprdiff check  suite.py    # exit 0
```

## Returning a Trace (the richer version)

When the agent returns just a string, `agentprdiff` wraps it and records
wall-clock latency only — `tool_called`, `cost_lt_usd`, etc. won't have any
data to chew on. Return a `(output, Trace)` tuple to unlock all assertions:

```python title="my_agent.py"
from agentprdiff import LLMCall, ToolCall, Trace

def my_agent(query: str) -> tuple[str, Trace]:
    trace = Trace(suite_name="", case_name="", input=query)

    trace.record_llm_call(LLMCall(
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_messages=[{"role": "user", "content": query}],
        output_text="Looking up your order…",
        prompt_tokens=18, completion_tokens=12,
        cost_usd=0.0002, latency_ms=180.0,
    ))

    trace.record_tool_call(ToolCall(
        name="lookup_order",
        arguments={"order_id": "1234"},
        result={"status": "delivered"},
        latency_ms=8.0,
    ))

    return "Refund of $89 processed.", trace
```

The `suite_name`, `case_name`, and `input` fields can stay blank; the
runner backfills them from the suite definition.

## Choosing graders

Reach for graders in this order. The earlier ones are cheaper, more
deterministic, and more useful in CI.

```python
from agentprdiff.graders import (
    contains, contains_any, regex_match,           # output text
    tool_called, no_tool_called, tool_sequence,    # tool routing
    output_length_lt, latency_lt_ms, cost_lt_usd,  # budgets
    semantic,                                      # last resort
)
```

| Behavior you want to pin | Best grader |
|---|---|
| A literal phrase appears in the output | `contains` |
| Any of N phrases appear | `contains_any` |
| A pattern matches | `regex_match` |
| A specific tool fired | `tool_called` |
| A specific tool *did not* fire | `no_tool_called` |
| Tools fired in a particular order | `tool_sequence` (`strict=False` allows interleaving) |
| Output stays terse | `output_length_lt` |
| Latency budget | `latency_lt_ms` |
| Cost budget | `cost_lt_usd` |
| A behavior that resists encoding ("the agent was empathetic") | `semantic` |

`semantic` is useful but slow and stochastic. Use it for the last 20 % of
behavior you genuinely cannot encode as a rule.

## Multiple suites in one file

A suite file can define as many suites as you want — every module-level
`Suite` instance is picked up:

```python title="suite.py"
billing = suite(name="billing", agent=billing_agent, cases=[...])
support = suite(name="support", agent=support_agent, cases=[...])
search  = suite(name="search",  agent=search_agent,  cases=[...])
```

```bash
agentprdiff check suite.py
# runs all three suites, one rendered table each
```

## Multiple suite files

Pass a glob (or use your shell's expansion):

```bash
agentprdiff check suites/*.py
```

The CLI processes each file independently and aggregates exit codes — the
job fails if *any* suite has a regression.

## Recording vs checking — what changes on rerun

| Command | Idempotent? | Side effects | Exit code |
|---|---|---|---|
| `init` | yes | Creates `.agentprdiff/` once; subsequent runs no-op. | 0 |
| `record` | overwrites baseline JSON in place | New JSON content if behavior changed; otherwise unchanged. | 1 if agent raised, else 0 |
| `check` | writes a fresh `runs/<timestamp>/` dir each time | `runs/` accretes — gitignored, safe to delete. | 1 on regression, else 0 |
| `review` | same writes as `check` | Verbose per-case panels. | always 0 |
| `scaffold` | never overwrites; reports `[skip]` per existing file | New files only. | 2 on bad input |

## When behavior intentionally changes

```bash
agentprdiff record suite.py         # overwrite baselines
git add .agentprdiff/baselines/
git diff --staged                   # reviewers will see this in the PR
git commit -m "agent: tighten refund language"
```

Re-running `record` is the only sanctioned way to "accept" a behavioral
change. The diff in `.agentprdiff/baselines/` is the review surface.

## Pointing at a custom directory

By default, baselines and runs live in `.agentprdiff/`. To use a different
root (mono-repo with multiple agents, anyone?):

```bash
agentprdiff --root .agentprdiff/billing record suites/billing.py
agentprdiff --root .agentprdiff/support record suites/support.py
```

The flag goes *before* the subcommand because it's a group-level option.

## Where next

- [Advanced usage](./advanced.md) — case filters, async agents, custom graders.
- [Configuration](./configuration.md) — env vars, pricing tables.
- [Customization](./customization.md) — writing your own grader, judge, reporter.

---
id: advanced-usage
title: Advanced Usage
sidebar_position: 2
---

# Advanced Usage

Patterns that go beyond `record` / `check`.

## Filtering cases (`--case`, `--skip`, `--list`)

`record`, `check`, and `review` accept the same filter syntax. Patterns
match against case names (or `suite:case` when qualified).

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

A filter that matches **zero** cases exits `2` and prints the available
case names. That's deliberate — silent zero-runs are the worst kind of
green CI.

## The local iteration loop (`agentprdiff review`)

`check` is built for CI: a compact table and exit `1` on regression.
While iterating on a single case, you want the opposite — a verbose view
that won't go red between every keystroke. That's `review`:

```bash
agentprdiff review suite.py --case refund_happy_path
```

`review` is `pytest -k` for agents:

- Same comparison logic as `check`.
- Renders one rich panel per case: input echo, every assertion's
  *was → now* verdict, cost / latency / token deltas, tool-sequence diff,
  unified output diff.
- Always exits `0`, so it slots cleanly into watcher loops:

```bash
# rerun on every save
ls suite.py my_agent.py | entr -c agentprdiff review suite.py --case refund_happy_path
```

## Async agents (`AsyncOpenAI`, asyncio.run)

The runner is sync — bridge with `asyncio.run`. The OpenAI adapter detects
an `AsyncOpenAI` client at entry and installs an awaitable patched
`create`; the `with` block stays a regular `with` (the patch is bound to
the client *instance*, not the event loop):

```python title="my_agent.py"
import asyncio
from openai import AsyncOpenAI
from agentprdiff.adapters.openai import instrument_client, instrument_tools

TOOL_MAP = {"lookup_order": lookup_order, "send_email": send_email}

async def my_agent_async(query: str):
    client = AsyncOpenAI()
    with instrument_client(client) as trace:
        tools = instrument_tools(TOOL_MAP, trace)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": query}],
            tools=[...],
        )
        # ... your loop, using `await tools[name](**args)` for async tools ...
        return final_text, trace

def my_agent(query: str):
    return asyncio.run(my_agent_async(query))
```

`instrument_tools` mirrors per tool: `async def` tools come back
awaitable, sync tools stay sync, and a single tool map can mix both.

## Mixing sync and async tools

```python
TOOL_MAP = {
    "lookup_order":  lookup_order,         # sync
    "send_email":    async_send_email,     # async
    "calc_shipping": calc_shipping,        # sync
}

with instrument_client(client) as trace:
    tools = instrument_tools(TOOL_MAP, trace)
    # ...
    if name in {"send_email"}:
        result = await tools[name](**args)
    else:
        result = tools[name](**args)
```

The wrapper doesn't change the calling convention — what was sync stays
sync, what was async stays async.

## Pluggable LLM judges for `semantic()`

```python
from agentprdiff.graders import semantic
from agentprdiff.graders.semantic import openai_judge, anthropic_judge

# Use a real judge explicitly (overrides AGENTGUARD_JUDGE / env autodetect).
expect = [
    semantic("agent acknowledges the refund", judge=openai_judge(model="gpt-4o-mini")),
    semantic("agent stays on-brand",          judge=anthropic_judge(model="claude-haiku-4-5-20251001")),
]
```

To plug in a custom judge — anything from a regex to a fine-tuned classifier:

```python
def regex_judge(rubric: str, trace: Trace) -> tuple[bool, str]:
    import re
    passed = bool(re.search(rubric, str(trace.output or ""), re.I))
    return passed, ("matched" if passed else "no match")

expect = [semantic(r"refund.*\d+\s*business days", judge=regex_judge)]
```

A judge is just `Callable[[str, Trace], tuple[bool, str]]`. Anything that
fits returns is fair game.

## Custom grader

Graders are `Callable[[Trace], GradeResult]`. Write one when no built-in
fits:

```python
from agentprdiff import GradeResult, Trace

def first_tool_was(name: str):
    def _grader(trace: Trace) -> GradeResult:
        first = trace.tool_calls[0].name if trace.tool_calls else None
        passed = first == name
        return GradeResult(
            passed=passed,
            grader_name=f"first_tool_was({name!r})",
            reason=f"first tool was {first!r}",
        )
    return _grader

expect = [first_tool_was("lookup_order")]
```

Naming convention: keep `grader_name` descriptive — it's the column header
when an assertion fails in the rendered report.

## Recording extra metadata onto a Trace

`Trace` allows arbitrary `extra` fields (it's `pydantic` with
`extra="allow"`) and has a built-in `metadata: dict[str, Any]` field for
small structured tags:

```python
trace = Trace(suite_name="", case_name="", input=query)
trace.metadata["model_temperature"] = 0.2
trace.metadata["request_id"] = "abc-123"
```

Anything you put on `metadata` round-trips through baseline JSON. Custom
graders can read it back.

## Multiple agents in one suite file

Useful when an end-to-end product is composed of agents that share a CI
job:

```python
from agentprdiff import case, suite
from agentprdiff.graders import contains, tool_called
from billing.agent import billing_agent
from triage.agent  import triage_agent

billing = suite(name="billing", agent=billing_agent, cases=[...])
triage  = suite(name="triage",  agent=triage_agent,  cases=[...])
```

```bash
agentprdiff check suite.py
# Runs both, prints two tables, exits 1 if any case in any suite regressed.
```

## Manual instrumentation (no SDK adapter)

If you're not on OpenAI or Anthropic, build the trace yourself. This works
with LangChain, LangGraph, LlamaIndex, Bedrock, Vertex AI, or anything you
can wrap in a function:

```python
from agentprdiff import Trace, LLMCall, ToolCall

def my_agent(query):
    trace = Trace(suite_name="", case_name="", input=query)

    # ... call your model, then ...
    trace.record_llm_call(LLMCall(
        provider="bedrock",
        model="anthropic.claude-3-haiku-20240307-v1:0",
        prompt_tokens=120, completion_tokens=80,
        cost_usd=0.0012, latency_ms=340,
    ))

    # ... call a tool, then ...
    trace.record_tool_call(ToolCall(name="lookup_order", arguments={"id": "1234"}))

    return final, trace
```

Backfill incrementally — start with cost and latency, add tool calls as
you write `tool_called` graders.

## Embedding `agentprdiff` in another tool

The library API is fully usable without the CLI — useful when wrapping it
in your own Make target, IDE plugin, or eval harness:

```python
from pathlib import Path
from agentprdiff import Runner, BaselineStore
from agentprdiff.loader import load_suites

store  = BaselineStore(root=Path(".agentprdiff"))
runner = Runner(store)

for s in load_suites(Path("suite.py")):
    report = runner.check(s)
    print(f"{s.name}: regressed={report.cases_regressed}/{report.cases_total}")
    if report.has_regression:
        raise SystemExit(1)
```

## Where next

- [Configuration](./configuration.md) — env vars, pricing, judge selection.
- [Customization](./customization.md) — graders, judges, reporters in depth.
- [API reference](../api/python.md).

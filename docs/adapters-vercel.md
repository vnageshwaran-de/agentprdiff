# Using agentprdiff with the Vercel AI SDK

The Vercel AI SDK is TypeScript / JavaScript only. `agentprdiff` is a Python tool, so a true drop-in adapter (analogous to `agentprdiff.adapters.openai`) can't live in this package — it would have to ship as a companion npm module. That's on the roadmap; this doc describes the integration *today*.

## Status

* Native JS adapter: **planned** (target: companion package `agentprdiff-js` in 2026 H2).
* Manual integration today: **fully supported** via the JSON-trace contract.

## How to integrate today

`agentprdiff` baselines are JSON files of a `Trace` with a stable schema (see `Trace`, `LLMCall`, `ToolCall` in `agentprdiff.core`). Anything that can write that JSON shape can be checked by `agentprdiff` — the language doesn't matter.

The minimum viable pattern is a two-process integration:

1. **In your TypeScript agent** (Next.js route handler, edge function, etc.), use the Vercel AI SDK's built-in tracing (`experimental_telemetry`, OpenTelemetry exporters, or your own logging) to capture each model call and tool invocation.
2. **In a Python sidecar suite**, transform that captured data into an `agentprdiff.Trace` and let the runner take over.

The `Trace` schema (abridged) you're writing toward:

```jsonc
{
  "suite_name": "checkout_agent",
  "case_name": "happy_path",
  "input": "...",
  "output": "...",
  "llm_calls": [
    {
      "provider": "openai",
      "model": "gpt-4o-mini",
      "input_messages": [{"role": "user", "content": "..."}],
      "output_text": "...",
      "tool_calls": [{"id": "call_1", "name": "lookup", "arguments": "{...}"}],
      "prompt_tokens": 120,
      "completion_tokens": 40,
      "cost_usd": 0.0012,
      "latency_ms": 340
    }
  ],
  "tool_calls": [
    {"name": "lookup", "arguments": {"id": "1"}, "result": {...}, "latency_ms": 8}
  ],
  "total_cost_usd": 0.0012,
  "total_latency_ms": 348
}
```

A Python suite that calls your TS agent over HTTP and reconstructs the trace looks like:

```python
import requests
from agentprdiff import Trace, LLMCall, ToolCall, suite, case
from agentprdiff.graders import contains, tool_called, latency_lt_ms

def vercel_agent(query: str) -> tuple[str, Trace]:
    resp = requests.post(
        "http://localhost:3000/api/agent",
        json={"query": query},
        # The TS handler returns: {"output": "...", "trace": {...}}
    ).json()
    trace = Trace(suite_name="", case_name="", input=query, output=resp["output"])
    for c in resp["trace"]["llm_calls"]:
        trace.record_llm_call(LLMCall(**c))
    for c in resp["trace"]["tool_calls"]:
        trace.record_tool_call(ToolCall(**c))
    return resp["output"], trace

checkout = suite(name="checkout", agent=vercel_agent, cases=[
    case(name="happy", input="...", expect=[contains("..."), latency_lt_ms(2000)]),
])
```

On the TypeScript side, the easiest way to populate the trace is wrapping the AI SDK's `generateText` / `streamText` calls and the tool dispatcher with thin recorders. Each call appends an entry to a request-scoped array; you return that array alongside the output.

## When the JS adapter ships

The companion package will mirror the Python adapter API — a `instrumentClient(...)` higher-order function that wraps the AI SDK's model handle, plus `instrumentTools(...)` for the tool dict — and emit the same JSON shape directly so the sidecar pattern goes away.

If your team would benefit from this sooner, [open an issue](https://github.com/vnageshwaran-de/agentprdiff/issues) describing the use case. Real adopter context is what gets the JS package prioritized.

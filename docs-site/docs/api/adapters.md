---
id: adapters
title: SDK Adapters Reference
sidebar_position: 4
---

# SDK Adapters Reference

The adapters wrap your existing SDK client so every model call and tool
dispatch is recorded onto a `Trace` automatically — no manual
`record_llm_call` boilerplate.

```python
from agentprdiff.adapters.openai    import instrument_client, instrument_tools  # OpenAI / OpenAI-compatible
from agentprdiff.adapters.anthropic import instrument_client, instrument_tools  # Anthropic Messages API
from agentprdiff.adapters           import register_prices, DEFAULT_PRICES, estimate_cost_usd
```

## OpenAI adapter

### `instrument_client(client, *, trace=None, prices=None, provider=None)`

Context manager. Patches `client.chat.completions.create` for the duration
of the `with` block and yields a `Trace` you can return from your agent.

| Param | Type | Default | Description |
|---|---|---|---|
| `client` | `OpenAI \| AsyncOpenAI` | required | Any client whose `chat.completions.create` is OpenAI-shaped. The patch is bound to the *instance* — global SDK state is untouched. |
| `trace` | `Trace \| None` | `None` (fresh) | Pre-existing trace to record into. Useful for nested adapters. |
| `prices` | `Mapping[str, tuple[float, float]] \| None` | bundled `DEFAULT_PRICES` | `{model: ($-per-1k-input, $-per-1k-output)}` override. |
| `provider` | `str \| None` | inferred from `base_url` | Provider tag stamped on `LLMCall.provider`. |

Yields the `Trace`. The patch is reversed on `__exit__` even if the agent
raises.

```python
from openai import OpenAI
from agentprdiff.adapters.openai import instrument_client

def my_agent(query):
    client = OpenAI()
    with instrument_client(client) as trace:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": query}],
        )
        return response.choices[0].message.content, trace
```

### Sync vs async — same API

`instrument_client` inspects `client.chat.completions.create` at entry. If
it's `async def`, the installed patch is itself `async def`. The `with`
block remains a regular `with` (the patch is bound to the client
instance, not the running event loop):

```python
from openai import AsyncOpenAI

async def my_agent_async(query):
    client = AsyncOpenAI()
    with instrument_client(client) as trace:
        response = await client.chat.completions.create(...)
        return response.choices[0].message.content, trace
```

### `instrument_tools(tool_map, trace)`

Wrap each callable in `tool_map` so every invocation records a `ToolCall`
on the trace. Returns a *new* dict (the original is untouched).

```python
TOOL_MAP = {"lookup_order": lookup_order, "send_email": async_send_email}

with instrument_client(client) as trace:
    tools = instrument_tools(TOOL_MAP, trace)
    result_sync  = tools["lookup_order"](order_id="1234")
    result_async = await tools["send_email"](to="…", body="…")
```

The wrapper mirrors the underlying callable per tool: `async def` tools
come back awaitable, sync tools stay sync. A single map can mix both.

Recorded fields per tool call:

- `name` — the dict key.
- `arguments` — the kwargs (and positional args under `"_args"` if any).
- `result` — best-effort JSON-serializable copy of the return value.
- `latency_ms` — wall-clock latency including any await.
- `error` — `f"{type}: {msg}"` on exception (the exception still
  propagates).

### Provider inference

`provider` defaults to a best-effort guess based on `client.base_url`.

| `base_url` snippet | Inferred provider |
|---|---|
| (default OpenAI) | `openai` |
| `groq` | `groq` |
| `openrouter` | `openrouter` |
| `googleapis` / `generativelanguage` | `gemini` |
| `ollama` / `:11434` | `ollama` |
| `together` | `together` |
| `fireworks` | `fireworks` |
| `deepinfra` | `deepinfra` |
| `anthropic` (compat shim) | `anthropic-openai-compat` |
| anything else | `openai-compatible` |

Override explicitly if the inference is wrong:

```python
with instrument_client(client, provider="vllm-internal") as trace:
    ...
```

## Anthropic adapter

```python
from anthropic import Anthropic
from agentprdiff.adapters.anthropic import instrument_client, instrument_tools

def my_agent(query):
    client = Anthropic()
    with instrument_client(client) as trace:
        tools = instrument_tools(TOOL_MAP, trace)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": query}],
        )
        # iterate resp.content blocks: text + tool_use → dispatch via tools[name](**input)
        return final_text, trace
```

Same parameter shape as the OpenAI adapter. The patch attaches to
`client.messages.create`. Tool wrappers come from the OpenAI module's
`_make_tool_wrapper` (sync only today; async Anthropic is on the
roadmap).

What the adapter records per `messages.create`:

- `provider` — defaults to `"anthropic"`.
- `model`, `prompt_tokens` (`usage.input_tokens`), `completion_tokens`
  (`usage.output_tokens`), `cost_usd` (price-table lookup), `latency_ms`.
- `output_text` — the concatenation of every `text` block in
  `resp.content`.
- `tool_calls` — one summary entry per `tool_use` block (id, name, input).

## Pricing

```python
from agentprdiff.adapters import DEFAULT_PRICES, register_prices, estimate_cost_usd
```

### `DEFAULT_PRICES`

`dict[str, tuple[float, float]]` — `{model: ($_per_1k_input, $_per_1k_output)}`.
Bundled defaults for OpenAI, Anthropic, Groq, Gemini, OpenRouter, and
Ollama. Sourced from each provider's published pricing page; current as
of 2026-04. PRs welcome when prices drift.

### `register_prices(prices)`

Merge `prices` into `DEFAULT_PRICES`. Useful at the top of a suite file:

```python
from agentprdiff.adapters import register_prices

register_prices({"acme-llama-3-fine": (0.0003, 0.0006)})
```

### `estimate_cost_usd(model, *, prompt_tokens, completion_tokens, prices=None)`

Compute USD cost for a single call. Returns `0.0` and emits one
`RuntimeWarning` per process for unknown models.

```python
estimate_cost_usd("gpt-4o-mini", prompt_tokens=120, completion_tokens=50)
# 0.000048
```

## Composing adapters

### Pre-existing trace

Pass `trace=` to record into an outer trace:

```python
from agentprdiff import Trace

trace = Trace(suite_name="", case_name="", input=query)
with instrument_client(openai_client, trace=trace), \
     instrument_client(anthropic_client, trace=trace):
    # Both clients record onto the same trace.
    ...
return final, trace
```

The two adapters patch *different* attributes
(`chat.completions.create` vs `messages.create`) so they don't conflict.

### Pricing override per call

```python
PRICES = {"gpt-4o": (0.0020, 0.0080)}     # negotiated rate, not list
with instrument_client(client, prices=PRICES) as trace:
    ...
```

Per-call `prices` takes precedence over `register_prices` and
`DEFAULT_PRICES`.

## What's *not* in the adapters yet

- Native LangChain / LangGraph adapter (use the manual `Trace` pattern).
- Native Bedrock / Vertex AI adapter (use the manual pattern, or wrap
  Bedrock's OpenAI-compatible shim through the OpenAI adapter).
- Async Anthropic.

Roadmap: see [Roadmap](../roadmap.md).

---
id: openai-adapter
title: OpenAI / Anthropic SDK Adapters
sidebar_position: 5
---

# Scenario 5 — Real SDKs: OpenAI, Anthropic, & Friends

Skip the manual `Trace.record_llm_call(...)` boilerplate when your agent
uses one of the supported SDKs.

## OpenAI (sync)

### Problem

A multi-turn tool-calling agent on `OpenAI()`. We want every model call
and every tool dispatch in the trace, automatically.

### Code

```python title="my_agent.py"
from openai import OpenAI
from agentprdiff.adapters.openai import instrument_client, instrument_tools
from agentprdiff import Trace
import json

def lookup_order(order_id: str) -> dict:
    return {"order_id": order_id, "status": "delivered", "amount_usd": 89.0}

def send_email(to: str, body: str) -> dict:
    return {"sent": True}

TOOL_MAP = {"lookup_order": lookup_order, "send_email": send_email}

TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "lookup_order", "parameters": {...}}},
    {"type": "function", "function": {"name": "send_email",   "parameters": {...}}},
]

def my_agent(query: str) -> tuple[str, Trace]:
    client = OpenAI()
    with instrument_client(client) as trace:
        tools = instrument_tools(TOOL_MAP, trace)
        messages = [{"role": "user", "content": query}]

        for _ in range(6):  # max steps
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=TOOL_SCHEMAS,
            )
            msg = resp.choices[0].message
            messages.append(msg.model_dump())
            if not msg.tool_calls:
                return msg.content or "", trace
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                result = tools[tc.function.name](**args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })
        return "max steps exceeded", trace
```

### Suite

```python title="suite.py"
from agentprdiff import case, suite
from agentprdiff.graders import contains, cost_lt_usd, latency_lt_ms, tool_called
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
                cost_lt_usd(0.02),
                latency_lt_ms(15_000),
            ],
        ),
    ],
)
```

### Output

`OPENAI_API_KEY` is set, so the agent calls the real model. The adapter
records:

- one `LLMCall` per `chat.completions.create` (provider, model, token
  counts, cost from the bundled price table, latency, output text, raw
  `tool_calls`);
- one `ToolCall` per dispatched function (name, kwargs, return value,
  latency).

```
agentprdiff record — suite billing  (1/1 passed, 0 regressed)
…(table; cost ~0.0008, latency ~2300 ms)…
```

## OpenAI (async)

```python title="my_agent.py"
import asyncio
from openai import AsyncOpenAI
from agentprdiff.adapters.openai import instrument_client, instrument_tools
from agentprdiff import Trace

async def my_agent_async(query: str) -> tuple[str, Trace]:
    client = AsyncOpenAI()
    with instrument_client(client) as trace:
        tools = instrument_tools(TOOL_MAP, trace)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": query}],
            tools=TOOL_SCHEMAS,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            for tc in msg.tool_calls:
                # await sync tools without await; the wrapper preserves shape.
                tools[tc.function.name](**json.loads(tc.function.arguments))
        return msg.content or "", trace

def my_agent(query: str) -> tuple[str, Trace]:
    return asyncio.run(my_agent_async(query))
```

### Why this works

`instrument_client` inspects `client.chat.completions.create` at entry —
if it's `async def`, the patch is itself `async def`. `instrument_tools`
mirrors per tool: `async def` tools come back awaitable, sync tools stay
sync. The `with` block remains a regular `with` (the patch is bound to the
client instance, not the event loop).

## Anthropic

```python title="my_agent.py"
from anthropic import Anthropic
from agentprdiff.adapters.anthropic import instrument_client, instrument_tools
from agentprdiff import Trace

def my_agent(query: str) -> tuple[str, Trace]:
    client = Anthropic()
    with instrument_client(client) as trace:
        tools = instrument_tools(TOOL_MAP, trace)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": query}],
            tools=[{"name": "lookup_order", "input_schema": {...}}],
        )
        # Iterate content blocks; dispatch tool_use blocks via `tools[name](**input)`.
        ...
        return final_text, trace
```

The Anthropic adapter records:

- `LLMCall` per `messages.create` (uses `usage.input_tokens` /
  `output_tokens`, walks content blocks for the output text and any
  `tool_use` block summaries);
- `ToolCall` per dispatched function via the same `instrument_tools`
  shape.

## OpenAI-compatible providers

The OpenAI adapter works with anything that speaks the OpenAI Chat
Completions wire format. The provider tag is inferred from `base_url`:

| Provider | base_url snippet | Inferred tag |
|---|---|---|
| OpenAI | (default) | `openai` |
| Groq | `api.groq.com` | `groq` |
| Gemini (OpenAI-compat) | `googleapis.com` / `generativelanguage` | `gemini` |
| OpenRouter | `openrouter.ai` | `openrouter` |
| Ollama | `localhost:11434` | `ollama` |
| Together | `together.ai` | `together` |
| Fireworks | `fireworks.ai` | `fireworks` |
| DeepInfra | `deepinfra.com` | `deepinfra` |

Anything unrecognized falls through to `openai-compatible`. Override
explicitly with `instrument_client(client, provider="my-provider")`.

```python
from openai import OpenAI

# Groq
client = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1")

# Ollama (local)
client = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")

# OpenRouter
client = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url="https://openrouter.ai/api/v1")

with instrument_client(client) as trace:
    ...
```

## Cost overrides

Pass `prices=` if you're using a model not in the bundled defaults, or if
you want to record your enterprise rate instead of public list pricing:

```python
PRICES = {
    "gpt-4o":          (0.0020, 0.0080),    # negotiated rate
    "internal-fine-1": (0.0009, 0.0018),
}

with instrument_client(client, prices=PRICES) as trace:
    ...
```

`PRICES` is `{model: (input_$_per_1k_tokens, output_$_per_1k_tokens)}`.

## What lands in the trace

```json
{
  "llm_calls": [
    {
      "provider": "openai",
      "model": "gpt-4o-mini",
      "input_messages": [...],
      "output_text": "I'll process that refund for you.",
      "tool_calls": [{"id": "call_abc", "name": "lookup_order", "arguments": "{\"order_id\":\"1234\"}"}],
      "prompt_tokens": 184,
      "completion_tokens": 27,
      "cost_usd": 0.000044,
      "latency_ms": 612.3
    }
  ],
  "tool_calls": [
    {
      "name": "lookup_order",
      "arguments": {"order_id": "1234"},
      "result": {"status": "delivered", "amount_usd": 89.0},
      "latency_ms": 8.1
    }
  ]
}
```

## Explanation

- The adapter monkey-patches `client.chat.completions.create` (or
  `client.messages.create` for Anthropic) for the duration of the `with`
  block, then restores the original on exit — even if the agent raises.
- The patch is scoped to the *client instance*. Other client instances and
  global SDK state are untouched.
- Tool wrappers always keep their original calling convention. Sync tools
  stay sync, async tools stay awaitable.
- `cost_usd` is filled from `agentprdiff.adapters.pricing.DEFAULT_PRICES`
  unless you override it. Missing models trigger one `RuntimeWarning` per
  process (loud but not spammy).

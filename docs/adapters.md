# SDK adapters

The base `agentprdiff` package is SDK-agnostic — your agent returns `(output, Trace)` and you build the `Trace` however you like. That's a sharp tool, but it's tedious for the common case where your agent already calls one of two well-known SDKs.

The `agentprdiff.adapters` package eliminates the boilerplate. Two adapters ship today: `openai` (which also covers every OpenAI-compatible provider) and `anthropic`. Both share the same surface area — a context manager called `instrument_client` and a tool wrapper called `instrument_tools`.

## What the OpenAI adapter covers

| SDK / Provider                                          | Works? | Why |
|---------------------------------------------------------|--------|-----|
| `openai` (OpenAI's official Python SDK)                 | yes    | native |
| `openai` pointed at Groq                                | yes    | OpenAI-compatible API |
| `openai` pointed at Gemini's compat endpoint            | yes    | OpenAI-compatible API |
| `openai` pointed at OpenRouter                          | yes    | OpenAI-compatible API |
| `openai` pointed at Ollama (`http://localhost:11434/v1`)| yes    | OpenAI-compatible API |
| `openai` pointed at vLLM / Together / Fireworks / DeepInfra | yes | OpenAI-compatible API |
| `anthropic` (Anthropic Messages API)                    | use the Anthropic adapter | different shape |
| Bedrock / Vertex native SDKs                            | manual instrumentation today | different shape |
| Vercel AI SDK (TypeScript)                              | see [`adapters-vercel.md`](./adapters-vercel.md) | JS, future companion package |

The adapter detects what to patch by **shape, not by import name** — it never `import openai`s. You only need the SDK installed if your agent is calling it; the adapter itself is dependency-free.

## OpenAI adapter

```python
from openai import OpenAI
from agentprdiff.adapters.openai import instrument_client, instrument_tools

TOOL_MAP = {"lookup_order": lookup_order, "send_email": send_email}

def my_agent(query: str):
    client = OpenAI()
    with instrument_client(client) as trace:
        tools = instrument_tools(TOOL_MAP, trace)
        messages = [{"role": "user", "content": query}]
        while True:
            resp = client.chat.completions.create(
                model="gpt-4o-mini", messages=messages, tools=OPENAI_TOOLS_SPEC
            )
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return msg.content, trace
            messages.append(msg)
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                result = tools[tc.function.name](**args)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
```

What gets recorded automatically:

- **One `LLMCall` per `client.chat.completions.create` invocation**, with provider (auto-inferred from `client.base_url`), model, input messages, output text, the model's emitted tool calls, prompt/completion tokens, cost (computed via the bundled price table), and wall-clock latency.
- **One `ToolCall` per `tools[name](...)` invocation**, with name, arguments, return value, latency, and exception text on failure.

What's *not* changed:

- Your tool-calling loop. The only required diff is using `tools[name]` from `instrument_tools(...)` instead of `TOOL_MAP[name]` directly.
- Global SDK state. The patch is per-client-instance and reversed on `__exit__`.

### Provider inference

`instrument_client` reads `client.base_url` to tag the `LLMCall.provider` field. The known mappings:

| `base_url` substring        | provider tag             |
|-----------------------------|--------------------------|
| `groq`                      | `groq`                   |
| `openrouter`                | `openrouter`             |
| `googleapis` / `generativelanguage` | `gemini`         |
| `ollama` / `:11434`         | `ollama`                 |
| `together`                  | `together`               |
| `fireworks`                 | `fireworks`              |
| `deepinfra`                 | `deepinfra`              |
| `anthropic`                 | `anthropic-openai-compat` |
| empty / `openai`            | `openai`                 |
| anything else               | `openai-compatible`      |

You can override it explicitly: `instrument_client(client, provider="my-private-fork")`.

## Anthropic adapter

```python
from anthropic import Anthropic
from agentprdiff.adapters.anthropic import instrument_client, instrument_tools

def my_agent(query: str):
    client = Anthropic()
    with instrument_client(client) as trace:
        tools = instrument_tools(TOOL_MAP, trace)
        # ... standard Messages API tool-use loop ...
        return final_text, trace
```

The Anthropic adapter understands the Messages API content-block shape: it walks `response.content`, concatenates `text` blocks for the recorded `output_text`, and extracts `tool_use` blocks (with their `id`, `name`, and `input`) into the `LLMCall.tool_calls` summary. Token usage comes from `response.usage.input_tokens` / `output_tokens`.

`thinking`, `redacted_thinking`, and unknown block types are recorded as part of the response object but ignored for grading purposes — no current grader asserts against them.

## Pricing

`agentprdiff.adapters.pricing` ships a curated `DEFAULT_PRICES` table mapping each known model to `(input_$_per_1k_tokens, output_$_per_1k_tokens)`. Three ways to override:

```python
# 1. Per-call:
with instrument_client(client, prices={"my-finetune-v3": (0.001, 0.002)}) as trace:
    ...

# 2. Globally (top of suite file):
from agentprdiff.adapters import register_prices
register_prices({"my-finetune-v3": (0.001, 0.002)})

# 3. Replace the whole table:
from agentprdiff.adapters.pricing import DEFAULT_PRICES
DEFAULT_PRICES.update(my_prices)
```

If a model isn't in the table, the adapter records `cost_usd=0.0` and emits a single `RuntimeWarning` per process per model. That makes missing pricing loud (cost regressions stay accurate) without spamming logs across a large suite.

The bundled prices were accurate at the time of release. **They will drift.** Pinning your own table is the right call for production CI; the bundled defaults are there for fast onboarding.

## Recipes

### Nested or composite agents

When one agent calls another, share the `Trace`:

```python
def planner(query, trace):
    # agent_a uses an OpenAI client
    with instrument_client(client_a, trace=trace):
        plan = make_plan(query)
    # agent_b uses an Anthropic client
    with instrument_client_anthropic(client_b, trace=trace):
        result = execute_plan(plan)
    return result, trace
```

Both adapters accept an optional `trace=` kwarg; passing the same one through stitches their recordings into a single trace.

### Streaming responses

The 0.2 adapter does **not** instrument streaming responses (`stream=True`). Regression suites usually don't run streaming, since the value is in the final output and tool sequence — both available from the non-streamed call. If you need streaming support, open an issue describing the use case.

### Async clients

For `AsyncOpenAI` / `AsyncAnthropic`, use the same adapter; the patch wraps the awaitable transparently because `client.chat.completions.create` is itself awaitable in those SDKs and the adapter measures wall-clock around the await.

> Note: 0.2 supports the synchronous client API. Async support is on the 0.3 roadmap. If your agent is async, manual instrumentation is the safe path until then.

### Cost-budgeted CI

Combine the adapter's automatic cost recording with the `cost_lt_usd` grader to make CI fail when an agent gets meaningfully more expensive:

```python
case(
    name="cost_budget",
    input="...",
    expect=[cost_lt_usd(0.005)],   # half a cent ceiling
)
```

This works equally well across all providers because cost normalization happens inside the adapter.

## Failure modes and what to do about them

**The adapter raised `TypeError: instrument_client expected an OpenAI-style client...`**
→ Your client object doesn't expose `client.chat.completions.create`. You're either using the Anthropic adapter on an OpenAI client (or vice versa), or using a non-standard SDK that doesn't follow the OpenAI shape. Use manual instrumentation, or open an issue with the SDK details.

**`RuntimeWarning: no pricing entry for model 'foo'`**
→ The model name isn't in `DEFAULT_PRICES`. Add it via `register_prices(...)` or `prices=` kwarg. The warning fires once per process per model.

**Cost in the trace is zero even though the call cost real money**
→ Either the model is unknown (see above), or the SDK's response didn't include a usage object. Some OpenAI-compatible servers omit `usage` — pin your prices manually and check `LLMCall.prompt_tokens` / `completion_tokens` to see what was reported.

**Trace says `tool_calls=[]` but the model definitely called a tool**
→ The adapter only records what the SDK returns. If your loop manually appends tool calls without going through `instrument_tools`, those won't show up in the `Trace.tool_calls` list (though they will appear inside the relevant `LLMCall.tool_calls` summary). Wrap your tool dict.

**The patch isn't restored**
→ Always use `instrument_client` as a context manager — the `finally` block is what does the restoration. If you call it manually (as a generator), you must still drive it through completion.

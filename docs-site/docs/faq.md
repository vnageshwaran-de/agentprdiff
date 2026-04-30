---
id: faq
title: FAQ
sidebar_position: 8
---

# FAQ

## Is `agentprdiff` an agent framework?

No. Your agent stays exactly the way it is. `agentprdiff` records what it
did, lets you assert what should be true about what it did, and compares
runs across time. If you can wrap your agent in a Python function, it
works.

## Does it parse GitHub PR diffs?

No. Despite the name, `agentprdiff` produces *the diff that a PR
reviewer needs* — what the agent used to do, what it does now, and which
assertions just flipped. It doesn't read PR text or `git diff` output.

## What providers are supported out of the box?

- **OpenAI** (sync `OpenAI` and async `AsyncOpenAI`).
- **Anthropic** (sync `Anthropic`).
- **Any OpenAI-compatible provider** via the OpenAI adapter: Groq,
  Gemini's OpenAI-compat endpoint, OpenRouter, Ollama, vLLM, Together,
  Fireworks, DeepInfra.
- **Anything else** — LangChain, LangGraph, LlamaIndex, Bedrock, Vertex,
  custom wrappers — works with manual `Trace` instrumentation.

## How is this different from LLM-as-judge eval frameworks?

| | LLM-as-judge | `agentprdiff` |
|---|---|---|
| Deterministic pass/fail | no | yes (when assertions are deterministic) |
| Catches behavioral drift | yes | yes |
| Runs on every PR in CI | too expensive | yes |
| Human-readable diff of what changed | rare | yes |
| Works without API keys | no | yes (deterministic graders + `fake_judge`) |

LLM-as-judge is great for offline benchmarking. `agentprdiff` is the
inner-loop CI gate. Use both.

## Do I need API keys to run it?

No. The bundled quickstart runs without any keys. Real agents use
whatever keys they always used (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
…). The semantic-grader judge uses an API key when available and falls
back to `fake_judge` (deterministic keyword matching) when not.

## How big is the JSON baseline?

Tens of KB per case for a typical agent (one or two LLM calls, a couple
of tool calls). Pretty-printed for readable git diffs. We considered
binary formats and rejected them for the same reason — the diff is the
review surface.

## Can I store baselines somewhere other than the repo?

Yes — subclass `BaselineStore` and back it with S3, GCS, or a database.
See [Customization → Plugging a custom store backend](./usage/customization.md#plugging-a-custom-store-backend).
The CLI cannot use a custom store directly; wrap your runner in your own
entry point.

## Does `record` overwrite my edits?

`agentprdiff record` overwrites the `.agentprdiff/baselines/` JSON in
place. That's the *intended* workflow when you genuinely want to accept
new behavior — re-record, commit the diff, explain it in your PR
description. Other paths (`runs/`, `--json-out PATH`) are also overwritten
or accumulated as documented in [Basic Usage](./usage/basic.md).

## How do I update only one baseline?

```bash
agentprdiff record suite.py --case <case_name>
```

The `--case` filter applies to `record` too. Negate with `~slow` to
re-record everything except a particular case.

## What's the difference between `check` and `review`?

| | `check` | `review` |
|---|---|---|
| Comparison logic | identical | identical |
| Output | compact table | one verbose panel per case |
| Exit on regression | 1 (fails CI) | 0 (always) |
| Best for | CI gate | local iteration loop |

`review` is `pytest -k` for agents. Use it inside a watcher; use `check`
in CI.

## How do I skip a case temporarily?

Two options:

```bash
agentprdiff check suite.py --skip flaky_case
```

Or tag the case in code and filter on the tag (filtering by `--case` /
`--skip` matches case names today; tag-based filtering is on the
roadmap).

## Why does my latency budget grader trivially pass?

Your trace's `total_latency_ms` is `0.0` because nothing recorded a
latency. Three fixes:

1. Use an SDK adapter — they record `latency_ms` on every LLM and tool
   call.
2. Set `latency_ms` manually when you record `LLMCall` / `ToolCall`
   objects.
3. Don't return a `Trace` from the agent at all. The runner falls back
   to wall-clock timing, which is always non-zero.

## Why does my cost budget grader trivially pass?

Same shape: `total_cost_usd` is `0.0` because no `LLMCall.cost_usd` was
recorded. The OpenAI / Anthropic adapters fill it from the bundled
price table. Manual instrumentation must set it yourself, or use
`agentprdiff.adapters.estimate_cost_usd(...)`.

If you're on a model that's *not* in the table, `cost_usd` is `0.0` and
one `RuntimeWarning` is emitted per process. Add the model with
`agentprdiff.adapters.register_prices({...})`.

## Are baselines portable across machines?

Yes, with one wrinkle. Trace JSON contains a `created_at` timestamp and a
`run_id`. These are kept on the trace for downstream tooling but are *not*
compared by the differ — only behavioral fields are diffed. So
re-recording on a different machine produces an identical-modulo-metadata
baseline, and the git diff stays small.

## Does it work in a monorepo?

Yes. Use `--root` to point at a per-agent `.agentprdiff/` directory:

```bash
agentprdiff --root .agentprdiff/billing record suites/billing.py
agentprdiff --root .agentprdiff/support record suites/support.py
```

Or live with one shared `.agentprdiff/` and let the per-suite folders
under `baselines/` partition things.

## Does it support test parametrization?

Cases are explicit Python objects, so a `for` loop is the parametrization:

```python
LANGS = ["en", "de", "ja"]
ORDERS = ["1234", "9999"]

cases = [
    case(
        name=f"refund_{lang}_{order}",
        input=f"refund order #{order}",
        expect=[contains("refund"), tool_called("lookup_order")],
    )
    for lang in LANGS
    for order in ORDERS
]

billing = suite(name="billing", agent=my_agent, cases=cases)
```

## Does it work with retries / streaming?

Streaming responses are recorded once the stream finishes (the SDK
adapter sees the full response object). Retries inside the agent record
multiple `LLMCall` entries — perfect for asserting that retry didn't
explode latency or cost.

## Does it run on Windows?

Yes. The store uses `pathlib`, the JSON files are UTF-8 with `\n`
line endings (consistent across platforms). The `run_id` is an ISO-8601
timestamp, which is filesystem-safe everywhere. Filenames are slugified
to ASCII alphanumerics, dashes, underscores, and dots.

## Is there a JS/TS port?

Not yet. A JS companion package for the Vercel AI SDK is on the 0.3
roadmap. In the meantime, the JSON baseline format is documented enough
to implement a comparator in any language — open an issue if you start
working on one.

## Where do I follow updates?

- [GitHub Releases](https://github.com/vnageshwaran-de/agentprdiff/releases) for changelog.
- [Issues](https://github.com/vnageshwaran-de/agentprdiff/issues) for bugs and feature requests.
- [Roadmap](./roadmap.md) for what's next.

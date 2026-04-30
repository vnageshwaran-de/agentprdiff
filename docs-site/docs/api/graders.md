---
id: graders
title: Graders Reference
sidebar_position: 3
---

# Graders Reference

A grader is `Callable[[Trace], GradeResult]`. `agentprdiff` ships ten —
nine deterministic, one semantic.

```python
from agentprdiff.graders import (
    contains, contains_any, regex_match,
    tool_called, no_tool_called, tool_sequence,
    output_length_lt, latency_lt_ms, cost_lt_usd,
    semantic, fake_judge,
)
```

## Deterministic graders

These are cheap, free, reproducible. Reach for them first.

### `contains(substring, *, case_sensitive=False)`

Pass iff the agent's final output contains `substring`.

```python
contains("refund")                 # case-insensitive (default)
contains("REFUND", case_sensitive=True)
```

`grader_name`: `contains('refund')`.
`reason`: `output contains 'refund'` or `output does not contain 'refund'`.

### `contains_any(substrings, *, case_sensitive=False)`

Pass iff the output contains at least one of the listed substrings.

```python
contains_any(["refund", "credit", "compensation"])
```

Useful when several phrasings would all satisfy the contract.

### `regex_match(pattern, *, flags=0)`

Pass iff `pattern` matches the output (`re.search` semantics).

```python
import re
regex_match(r"\$\d+(\.\d{2})?")                       # any dollar amount
regex_match(r"^thank you", flags=re.MULTILINE | re.I) # opens politely
```

`reason`: `matched 'foo'` or `no match for '<pattern>'`.

### `tool_called(name, *, min_times=1)`

Pass iff tool `name` was called at least `min_times` times.

```python
tool_called("lookup_order")
tool_called("retry", min_times=2)
```

`reason`: `tool 'lookup_order' called N time(s), required >= M`.

### `no_tool_called(name)`

Pass iff tool `name` was *not* called.

```python
no_tool_called("send_email")           # don't email people from a status query
```

### `tool_sequence(sequence, *, strict=False)`

Pass iff the tool-call sequence matches `sequence`.

```python
tool_sequence(["authenticate", "lookup_order"])              # subsequence (default)
tool_sequence(["authenticate", "lookup_order"], strict=True) # exact equality
```

| Mode | Behavior | When to use |
|---|---|---|
| `strict=False` | `sequence` must appear as a subsequence. Other tools may interleave. | Lock the *order* of important tools without forbidding new ones. |
| `strict=True` | The actual list must equal `sequence` exactly. | Lock the entire pipeline shape — tighter contract. |

### `output_length_lt(max_chars)`

Pass iff `len(output) < max_chars`.

```python
output_length_lt(500)                   # keep replies terse
```

### `latency_lt_ms(max_ms)`

Pass iff `trace.total_latency_ms < max_ms`.

```python
latency_lt_ms(5_000)                    # under 5 s
```

`total_latency_ms` is the sum of every recorded `LLMCall.latency_ms` and
`ToolCall.latency_ms`. Set it accurately in your agent (or use an SDK
adapter, which sets it for you) — otherwise this grader trivially passes.

### `cost_lt_usd(max_usd)`

Pass iff `trace.total_cost_usd < max_usd`.

```python
cost_lt_usd(0.02)                       # under 2 cents per case
```

`total_cost_usd` is the sum of `LLMCall.cost_usd`. The OpenAI / Anthropic
adapters fill it from the bundled price table; manual instrumentation has
to set it yourself.

## Semantic grader

### `semantic(rubric, *, judge=None)`

Pass iff `judge(rubric, trace)` returns `(True, _)`.

```python
from agentprdiff.graders import semantic
semantic("agent acknowledged the refund and explained the timeline")
```

The default judge is selected by env vars (`AGENTGUARD_JUDGE`,
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) — see
[Configuration → Selecting the semantic-grader judge](../usage/configuration.md#selecting-the-semantic-grader-judge).

#### Built-in judges

```python
from agentprdiff.graders.semantic import fake_judge, openai_judge, anthropic_judge

# Deterministic; passes iff any rubric word ≥ 4 chars appears in output. Free.
semantic("…", judge=fake_judge)

# OpenAI Chat Completions (default model gpt-4o-mini).
semantic("…", judge=openai_judge(model="gpt-4o-mini"))

# Anthropic Messages API (default model claude-haiku-4-5-20251001).
semantic("…", judge=anthropic_judge(model="claude-haiku-4-5-20251001"))
```

#### Custom judges

A judge is `Callable[[str, Trace], tuple[bool, str]]`. Anything matching
that signature is fair game — see
[Customization → Custom semantic-grader judges](../usage/customization.md#custom-semantic-grader-judges)
for examples (regex, embedding similarity, finetuned classifier).

#### Why not always use semantic?

| Tradeoff | Deterministic | Semantic |
|---|---|---|
| Speed | µs | seconds |
| Cost | free | $$$ per call |
| Determinism | yes | no (without `temperature=0` and even then…) |
| Catches subtle behavior | no | yes |
| Runs free in CI | yes | only with `fake_judge` (which doesn't actually judge) |

Encode the 80 % you can express as a rule with deterministic graders.
Reserve `semantic()` for the last 20 %. The bundled `fake_judge` exists
so the absence of API keys in CI doesn't drop you off the green-build
contract — it never lies about being a real judge, but it keeps the
pipeline running.

## Picking the right grader

| Behavior to pin | Grader |
|---|---|
| A specific phrase appears | `contains` |
| One of N phrases | `contains_any` |
| A pattern matches | `regex_match` |
| A specific tool fires | `tool_called` |
| A specific tool *doesn't* fire | `no_tool_called` |
| Tools fire in order | `tool_sequence` |
| Stay terse | `output_length_lt` |
| Latency budget | `latency_lt_ms` |
| Cost budget | `cost_lt_usd` |
| "The agent was empathetic / on-brand / accurate" | `semantic` |

## Composing graders

Graders are independent — pass them all in a single list, the runner
evaluates each, and the case passes iff all of them pass:

```python
case(
    name="refund_happy_path",
    input="…",
    expect=[
        contains("refund"),
        regex_match(r"\$\d+\.\d{2}"),
        tool_called("lookup_order"),
        no_tool_called("send_email"),
        tool_sequence(["authenticate", "lookup_order"]),
        output_length_lt(800),
        latency_lt_ms(5_000),
        cost_lt_usd(0.02),
        semantic("agent explains the refund timeline"),
    ],
)
```

There's no AND/OR/NOT combinator language — that's by design. If you
need OR, write a custom grader that calls two built-ins and returns a
combined result.

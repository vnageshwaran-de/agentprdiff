---
id: edge-cases
title: Edge Cases (Empty Output, Exceptions, Missing Baselines)
sidebar_position: 3
---

# Scenario 3 — Edge Cases

The corners of the data model — what happens when an agent throws, when a
baseline is missing, when output is empty, when input is exotic.

## 3a. Agent raises an exception

### Problem

A new prompt accidentally produces JSON the agent's downstream parser
chokes on. The agent raises `ValueError` mid-flight.

### Code

```python
def my_agent(query):
    if "explode" in query:
        raise ValueError("downstream parser failed")
    return f"Echo: {query}", None  # not used in this case
```

```python
case(name="parser_blowup", input="please explode now", expect=[contains("Echo")])
```

### Expected output

```
agentprdiff check — suite demo  (0/1 passed, 1 regressed)
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Case          ┃ Result     ┃ Cost Δ ┃ Latency ┃ Notes                                  ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ parser_blowup │ REGRESSION │        │         │ error: ValueError: downstream parser failed │
└──────────────┴────────────┴────────┴─────────┴────────────────────────────────────────┘
✗ 1 regression(s) detected.
```

### Explanation

- `run_agent` catches *any* exception from your callable and stores
  `f"{type(exc).__name__}: {exc}"` on `Trace.error`.
- `CaseReport.passed` is `False` when `trace.error is not None`, even if
  every grader you defined would otherwise have passed (most graders
  silently fail on the empty trace).
- A baseline that *also* errored on the same case → not a regression. A
  clean baseline that now errors → regression. ("First-run-bad is bad",
  but "second-run-bad-when-first-was-also-bad" is just consistent.)

## 3b. Empty output

### Problem

An agent returns the empty string when an upstream service times out. We
want this to fail loudly even though no exception was raised.

### Code

```python
case(
    name="empty_response",
    input="…",
    expect=[
        regex_match(r"\S"),               # at least one non-whitespace char
        output_length_lt(10_000),
    ],
)
```

### Expected output

```
parser_blowup  REGRESSION  regex_match('\S') no match for '\S'
```

### Explanation

`contains`, `regex_match`, etc. all stringify `trace.output` defensively
(`str(trace.output or "")`). An `output=None` or `output=""` is treated as
an empty string and fails any "must contain X" grader cleanly.

## 3c. No baseline yet (first run)

### Problem

A fresh `agentprdiff check` (no prior `record`) on a case whose graders
fail. We want this to fail too — *first-run-bad is still bad*.

### Code

```bash
rm -rf .agentprdiff/baselines/customer_support/refund_happy_path.json
agentprdiff check suite.py --case refund_happy_path
```

### Expected output

```
agentprdiff check — suite customer_support  (0/1 passed, 1 regressed)
…
✗ 1 regression(s) detected.
```

### Explanation

The runner returns `delta=None` (or, more precisely, `delta` with
`baseline_exists=False`) for cases without baselines. `CaseReport`
fast-paths: if the current run fails, it counts as a regression. If the
current run *passes*, it isn't — you'd see `PASS` and a missing-baseline
hint in the JSON output.

To bootstrap the baseline:

```bash
agentprdiff record suite.py --case refund_happy_path
git add .agentprdiff/baselines/customer_support/refund_happy_path.json
```

## 3d. Filter matched zero cases

### Problem

You typoed a filter and want CI to *not* silently exit 0 on no-cases-ran.

### Code

```bash
agentprdiff check suite.py --case '*nonexistent*'
```

### Expected output

```
error: no cases matched --case/--skip filters.
available cases:
  customer_support/refund_happy_path
  customer_support/non_refundable_order
  customer_support/policy_question_no_tools
  customer_support/missing_order_number
(tip: run with --list to see suite/case names; patterns are case-insensitive substrings or globs.)
```

Exit code: `2`. Use `--list` to discover names cleanly:

```bash
agentprdiff check suite.py --list
```

### Explanation

Silent zero-runs are the worst kind of green CI. Exit `2` distinguishes
"misconfiguration" from `0` (clean) and `1` (regression).

## 3e. Non-string input

### Problem

Your agent expects a structured input — a dict, list of messages, or a
custom dataclass.

### Code

```python
case(
    name="multimodal_dict_input",
    input={"text": "describe this image", "image_url": "data:image/png;base64,…"},
    expect=[
        contains("photo"),
        cost_lt_usd(0.05),
    ],
)
```

### Explanation

`Case.input` is `Any`. Whatever you pass is forwarded verbatim to your
`agent(input)`. The trace stores it through pydantic's JSON serialization,
so anything JSON-serializable round-trips cleanly into the baseline.

For inputs that *aren't* JSON-serializable (e.g. a callable, an open file
handle), build a small dataclass and use a JSON-friendly representation.

## 3f. Tool sequence subsequence vs strict

### Problem

You want "the agent eventually calls `lookup_order` after authentication"
but don't want to break when an unrelated `log_event` tool gets added.

### Code

```python
# Loose: the listed tools must appear in order, others may interleave.
tool_sequence(["authenticate", "lookup_order"])             # strict=False (default)

# Strict: the actual sequence must equal this list.
tool_sequence(["authenticate", "lookup_order"], strict=True)
```

### Explanation

`strict=False` checks subsequence containment — `["authenticate",
"log_event", "lookup_order"]` passes. `strict=True` checks list equality —
the same actual sequence fails. Pick the looser one when you want the
behavioral contract without locking in implementation details.

## 3g. The agent doesn't return a Trace

### Problem

A legacy agent returns just a string. You haven't gotten around to
instrumenting it yet.

### Code

```python
def my_agent(query: str) -> str:
    return "ok"

case(name="legacy", input="…", expect=[contains("ok"), latency_lt_ms(5_000)])
```

### Explanation

The runner detects the missing trace and synthesizes one with empty
`llm_calls` / `tool_calls` and a wall-clock `total_latency_ms`.
`contains` works (it stringifies the output). `latency_lt_ms` works
(latency is wall-clock). `tool_called`, `cost_lt_usd`, etc. *don't* —
you'll see them fail with reasons like `tool 'lookup_order' called 0
time(s), required >= 1`.

That's the gradient: start with `contains` and `latency_lt_ms`, instrument
incrementally to unlock `tool_called` / `cost_lt_usd`, then layer in
`semantic` last.

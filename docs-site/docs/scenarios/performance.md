---
id: performance
title: Performance & Cost Budgets
sidebar_position: 6
---

# Scenario 6 — Performance & Cost Budgets

`agentprdiff` records `cost_usd` and `latency_ms` per case. Two graders
turn those numbers into hard CI gates.

## Problem

We just merged a PR that swapped `gpt-4o` for `gpt-4o-mini` in our cheap
path to save money. We want to:

1. Confirm cost actually dropped on the cases that exercise the cheap path.
2. Catch any case where latency *grew* enough to matter (mini is sometimes
   slower under tool-use loops).
3. Hard-fail any case that exceeds an absolute cost ceiling.

## Code

```python title="suite.py"
from agentprdiff import case, suite
from agentprdiff.graders import contains, cost_lt_usd, latency_lt_ms, tool_called
from billing.agent import billing_agent

billing = suite(
    name="billing",
    agent=billing_agent,
    cases=[
        case(
            name="cheap_path_lookup",
            input="What's the status of order #1234?",
            expect=[
                contains("delivered"),
                tool_called("lookup_order"),
                cost_lt_usd(0.005),       # mini ≪ this
                latency_lt_ms(3_000),     # one round trip + one tool
            ],
        ),
        case(
            name="expensive_path_summary",
            input="Summarize the last 50 orders for customer #99.",
            expect=[
                contains("summary"),
                tool_called("list_orders"),
                cost_lt_usd(0.05),        # premium model + larger context
                latency_lt_ms(15_000),
            ],
        ),
    ],
)
```

## Output (after the swap)

```
agentprdiff check — suite billing  (2/2 passed, 0 regressed)
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ Case                   ┃Result┃ Cost Δ   ┃ Latency Δ  ┃ Notes            ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ cheap_path_lookup      │ PASS │ -$0.0021 │ -180 ms    │  —               │
│ expensive_path_summary │ PASS │  $0.0000 │  +12 ms    │  —               │
└────────────────────────┴──────┴──────────┴────────────┴──────────────────┘
✓ no regressions.
```

The `Cost Δ` column is *green* when negative (savings) and *red* when
positive (regression). Even when no assertion fails, the delta column is
how reviewers spot drift.

## What's recorded under the hood

Every `LLMCall` carries `cost_usd` and `latency_ms`. The trace's
top-level `total_cost_usd` and `total_latency_ms` are the running sums
maintained by `Trace.record_llm_call` and `Trace.record_tool_call`:

```python
def record_llm_call(self, call: LLMCall) -> None:
    self.llm_calls.append(call)
    self.total_cost_usd += call.cost_usd
    self.total_latency_ms += call.latency_ms
    ...

def record_tool_call(self, call: ToolCall) -> None:
    self.tool_calls.append(call)
    self.total_latency_ms += call.latency_ms
```

So `latency_lt_ms` covers the wall-time of every recorded LLM and tool
call — *not* the wall-time of the entire `agent(...)` invocation. If you
want the full wall-clock instead, leave latency tracking off your trace
and let the runner fill it in (it does so when the agent doesn't return a
trace, or when the trace's `total_latency_ms` is `0.0`).

## Catching cost regressions explicitly

When a case has *no* `cost_lt_usd` grader, a cost increase shows up in
the report's Notes column but doesn't fail CI. Add the grader to gate it
hard:

```python
case(
    name="cheap_path_lookup",
    input="…",
    expect=[
        contains("delivered"),
        cost_lt_usd(0.005),                  # absolute ceiling
    ],
)
```

`cost_lt_usd(0.005)` reads as "cost must stay strictly under half a cent
on every run". When a model bump pushes it over, CI exits 1 even if every
other assertion passes.

## Setting cost budgets that survive PRs

A few rules of thumb:

| Pattern | Use case |
|---|---|
| `cost_lt_usd(3 * observed_p50)` | Quick start. Loose enough to absorb prompt tweaks. |
| `cost_lt_usd(observed_p99 * 1.2)` | After a few weeks. Tight enough to catch real regressions. |
| `latency_lt_ms(observed_p50 + 2_000)` | Catch the obvious. Tighten over time. |
| `latency_lt_ms(observed_p99)` | For SLA-bound paths. Will be flaky on bad runs. |

Re-tighten after every model bump. The tightest budgets that don't flake
are the most useful.

## When cost is missing

If the model isn't in `agentprdiff.adapters.pricing.DEFAULT_PRICES`, the
adapter records `cost_usd=0.0` and emits one `RuntimeWarning` per
process:

```
[agentprdiff] no pricing entry for model 'foo-bar-v9'; cost_usd will be
recorded as 0.0. Pass prices={...} to instrument_client(...) or call
agentprdiff.adapters.register_prices({...}) to fix.
```

Every case will trivially pass `cost_lt_usd(...)` until you teach the
table about the model. See [Configuration → Pricing tables](../usage/configuration.md#pricing-tables).

## Latency on async agents

`asyncio.sleep`, `await`s on tools, and parallel tool dispatch all roll up
into `total_latency_ms` because `instrument_client` and `instrument_tools`
time the actual `await` (not the schedule time). Two parallel 200 ms tool
calls will record as ~400 ms in `total_latency_ms` (sum of recorded
latencies), not ~200 ms (wall clock). If you need wall-clock latency for
parallel agents, attach a custom `total_wallclock_ms` field to
`Trace.metadata` and write a custom grader that reads it.

## Tracking cost over time outside of CI

The full per-run trace under `.agentprdiff/runs/<timestamp>/` is the
audit log. Pipe it through `jq` for ad-hoc analytics:

```bash
jq -r '
  .case_name + "\t" +
  (.total_cost_usd | tostring) + "\t" +
  (.total_latency_ms | tostring)
' .agentprdiff/runs/*/billing/*.json
```

For longer-term tracking, archive the `--json-out` artifact from CI to S3
and aggregate offline.

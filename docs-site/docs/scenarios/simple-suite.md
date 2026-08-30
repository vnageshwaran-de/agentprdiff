---
id: simple-suite
title: A Simple End-to-End Suite
sidebar_position: 1
---

# Scenario 1 — A Simple End-to-End Suite

The smallest non-trivial example: a customer-support agent with a happy
path, an unhappy path, and a no-tool path. This scenario is the bundled
quickstart you can run today (`examples/quickstart/`).

## Problem

We want to guard four behaviors of a customer-support agent:

1. A refund-eligible order should be refunded, with an explanation.
2. A non-refundable order should be politely declined.
3. A policy question should be answered without a tool call.
4. A query missing an order number should ask for one.

If any of these silently change after a model bump or prompt rewrite, CI
should turn red.

## Input

The agent under test (`agent.py`) is a self-contained mock — no API calls,
deterministic, fast.

```python title="agent.py"
from agentprdiff import LLMCall, ToolCall, Trace
import re

ORDERS = {
    "1234": {"amount_usd": 89.00, "status": "delivered", "refundable": True},
    "9999": {"amount_usd": 12.50, "status": "shipped",   "refundable": False},
}

def lookup_order(order_id: str):
    return ORDERS.get(order_id)

def support_agent(query: str) -> tuple[str, Trace]:
    trace = Trace(suite_name="", case_name="", input=query)
    trace.record_llm_call(LLMCall(
        provider="mock", model="mock-sonnet-1",
        input_messages=[{"role": "user", "content": query}],
        output_text="I need to look up the order first.",
        prompt_tokens=18, completion_tokens=12,
        cost_usd=0.0002, latency_ms=180.0,
    ))

    order = None
    if (m := re.search(r"#?(\d{4,})", query)):
        order_id = m.group(1)
        trace.record_tool_call(ToolCall(name="lookup_order", arguments={"order_id": order_id}, latency_ms=8.0))
        order = lookup_order(order_id)
        trace.tool_calls[-1].result = order

    if "refund" in query.lower() and order is not None:
        if order["refundable"]:
            output = (
                f"I can help with that. I've processed a refund of "
                f"${order['amount_usd']:.2f} for your order. "
                f"You'll see it back on your card in 3–5 business days."
            )
        else:
            output = (
                f"I looked up your order but it isn't refundable at this stage "
                f"(status: {order['status']}). I can connect you with a human agent."
            )
    elif "policy" in query.lower():
        output = (
            "Our return policy lets you return most items within 30 days of "
            "delivery for a full refund. Some exclusions apply — see our FAQ."
        )
    else:
        output = "Happy to help — could you share your order number?"

    trace.record_llm_call(LLMCall(
        provider="mock", model="mock-sonnet-1",
        input_messages=[{"role": "user", "content": query}],
        output_text=output,
        prompt_tokens=60, completion_tokens=len(output.split()),
        cost_usd=0.0008, latency_ms=420.0,
    ))
    return output, trace
```

## Code: the suite

```python title="suite.py"
from agentprdiff import case, suite
from agentprdiff.graders import (
    contains, cost_lt_usd, latency_lt_ms,
    no_tool_called, output_length_lt, semantic, tool_called,
)
from agent import support_agent

support = suite(
    name="customer_support",
    agent=support_agent,
    description="End-to-end regression tests for the support agent.",
    cases=[
        case(
            name="refund_happy_path",
            input="I want a refund for order #1234",
            expect=[
                contains("refund"),
                tool_called("lookup_order"),
                semantic("agent acknowledges the refund and explains the timeline"),
                latency_lt_ms(5_000),
                cost_lt_usd(0.01),
            ],
        ),
        case(
            name="non_refundable_order",
            input="I want a refund for order #9999",
            expect=[
                contains("agent"),
                tool_called("lookup_order"),
                output_length_lt(400),
            ],
        ),
        case(
            name="policy_question_no_tools",
            input="What is your return policy?",
            expect=[
                contains("30 days"),
                no_tool_called("lookup_order"),
                semantic("agent explains the return policy"),
            ],
        ),
        case(
            name="missing_order_number",
            input="Something is wrong with my order",
            expect=[
                contains("order number"),
                no_tool_called("lookup_order"),
            ],
        ),
    ],
)
```

## Run

```bash
agentprdiff init
agentprdiff record suite.py     # save baselines
agentprdiff check  suite.py     # diff against baselines (exit 0)
```

## Expected output

```
agentprdiff check — suite customer_support  (4/4 passed, 0 regressed)
semantic judge: fake_judge (no AGENTPRDIFF_JUDGE, no OPENAI_API_KEY/ANTHROPIC_API_KEY — silent fallback)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━┓
┃ Case                       ┃ Result┃ Cost Δ ┃ Latency ┃ Notes┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━┩
│ refund_happy_path          │ PASS │        │         │  —   │
│ non_refundable_order       │ PASS │        │         │  —   │
│ policy_question_no_tools   │ PASS │        │         │  —   │
│ missing_order_number       │ PASS │        │         │  —   │
└────────────────────────────┴──────┴────────┴─────────┴──────┘

✓ no regressions.
```

The yellow `semantic judge: fake_judge` banner is your reminder that no
real LLM is judging — set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` to flip
to a real judge.

## Now break it on purpose

```bash
sed -i "s/refund/noundr/g" agent.py
agentprdiff check suite.py    # exit 1; you'll see the regression
```

Sample output:

```
agentprdiff check — suite customer_support  (2/4 passed, 2 regressed)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Case                     ┃ Result     ┃ Cost Δ ┃ Latency Δ ┃ Notes                    ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ refund_happy_path        │ REGRESSION │        │           │ contains('refund') output │
│                          │            │        │           │ does not contain 'refund' │
│ non_refundable_order     │ REGRESSION │        │           │ contains('agent') output  │
│                          │            │        │           │ does not contain 'agent'  │
│ policy_question_no_tools │ PASS       │        │           │ output changed           │
│ missing_order_number     │ PASS       │        │           │  —                       │
└──────────────────────────┴────────────┴────────┴───────────┴──────────────────────────┘

✗ 2 regression(s) detected.
```

Restore the file (`git restore agent.py`) and `check` is green again.

## Explanation

- `record` ran the agent against each case's input and saved the resulting
  trace as `.agentprdiff/baselines/customer_support/<case>.json`.
- `check` re-ran the agent and compared each new trace to its baseline —
  per-grader pass/fail, cost / latency / tokens, tool sequence, output
  text. The `sed` rewrote the `if "refund" in query` branch condition
  too, so both refund queries fall through to the clarification reply:
  `contains("refund")` breaks on `refund_happy_path` and
  `contains("agent")` breaks on `non_refundable_order`. CI exits
  non-zero.
- `policy_question_no_tools` keeps passing — its assertions
  (`contains("30 days")`, `no_tool_called`, the semantic rubric) don't
  depend on the word that changed — but `output changed` lands in its
  Notes column so reviewers still see the drift even though no assertion
  regressed. (That note only appears on non-regressed rows; regressed
  rows show the failing assertions instead.)

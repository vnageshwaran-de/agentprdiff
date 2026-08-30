"""Shared plumbing for the benchmark agents.

Every agent reads its configuration from environment variables so the
benchmark driver can swap models and prompts without touching agent code —
the exact situation agentprdiff exists to guard:

* ``BENCH_PROVIDER`` — ``openai`` | ``anthropic`` | ``stub`` (default)
* ``BENCH_MODEL``    — model id for the chosen provider
* ``BENCH_PROMPT_VARIANT`` — ``baseline`` | ``terse``

``stub`` mode needs no API key and returns deterministic canned responses;
it exists so the harness itself can be smoke-tested (in CI and by curious
readers) before spending API dollars.
"""

from __future__ import annotations

import json
import os
from typing import Any

from agentprdiff import LLMCall, Trace

# ---------------------------------------------------------------------------
# Canned order database + tools. Local, deterministic, instrumented by the
# agents via agentprdiff's instrument_tools.
# ---------------------------------------------------------------------------

ORDERS: dict[str, dict[str, Any]] = {
    "1042": {
        "status": "delivered",
        "item": "wireless headphones",
        "amount_usd": 89.99,
        "days_since_delivery": 6,
        "refund_window_days": 30,
    },
    "2077": {
        "status": "delivered",
        "item": "espresso machine",
        "amount_usd": 449.00,
        "days_since_delivery": 61,
        "refund_window_days": 30,
    },
    "3391": {
        "status": "shipped",
        "item": "standing desk",
        "amount_usd": 320.00,
        "days_since_delivery": 0,
        "refund_window_days": 30,
    },
}

REFUNDS_ISSUED: list[str] = []  # mutated by issue_refund; reset per run


def lookup_order(order_id: str) -> dict[str, Any]:
    """Return the order record, or an error marker for unknown ids."""
    order = ORDERS.get(str(order_id).strip("# "))
    if order is None:
        return {"error": f"no order with id {order_id!r}"}
    eligible = order["days_since_delivery"] <= order["refund_window_days"]
    return {**order, "order_id": order_id, "refund_eligible": eligible}


def issue_refund(order_id: str) -> dict[str, Any]:
    """Issue a refund. The agent must only call this for eligible orders."""
    REFUNDS_ISSUED.append(str(order_id))
    return {"refunded": True, "order_id": order_id}


TOOL_MAP = {"lookup_order": lookup_order, "issue_refund": issue_refund}

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Look up an order by id. Returns status, item, amount, and refund eligibility.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "issue_refund",
            "description": "Issue a refund for an order. Only call after confirming the order is refund_eligible.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    },
]

ANTHROPIC_TOOLS = [
    {
        "name": "lookup_order",
        "description": "Look up an order by id. Returns status, item, amount, and refund eligibility.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "issue_refund",
        "description": "Issue a refund for an order. Only call after confirming the order is refund_eligible.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
]


# ---------------------------------------------------------------------------
# Config helpers.
# ---------------------------------------------------------------------------


def provider() -> str:
    return os.environ.get("BENCH_PROVIDER", "stub").lower()


def model() -> str:
    return os.environ.get("BENCH_MODEL", "stub-model")


def prompt_variant() -> str:
    return os.environ.get("BENCH_PROMPT_VARIANT", "baseline").lower()


# ---------------------------------------------------------------------------
# Stub-mode trace helper — a deterministic "good model" so the harness can be
# exercised without API keys.
# ---------------------------------------------------------------------------


def stub_llm_call(trace: Trace, output_text: str) -> None:
    trace.record_llm_call(
        LLMCall(
            provider="stub",
            model=model(),
            output_text=output_text,
            prompt_tokens=120,
            completion_tokens=60,
            cost_usd=0.0,
            latency_ms=5.0,
        )
    )


def to_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)

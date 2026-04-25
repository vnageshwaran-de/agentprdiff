"""Mode-controlled mock agent for the agentprdiff regression tour.

Set the ``MODE`` environment variable to inject specific regressions:

    MODE=baseline           # default — happy path, matches the recorded baseline
    MODE=output_changed     # output text drifts (trips contains, regex, semantic)
    MODE=tool_added         # extra tool call (trips no_tool_called, tool_sequence)
    MODE=tool_removed       # missing tool call (trips tool_called, tool_sequence)
    MODE=tool_reordered     # same tools, different order (trips tool_sequence)
    MODE=latency_regressed  # planner LLM call too slow (trips latency_lt_ms)
    MODE=cost_regressed     # responder cost too high (trips cost_lt_usd)

The agent has zero external dependencies — no network, no API keys — so the
whole tour is deterministic and free to run.
"""

from __future__ import annotations

import os
import re

from agentprdiff import LLMCall, ToolCall, Trace


MODE = os.getenv("MODE", "baseline")


ORDERS: dict[str, dict] = {
    "1234": {"amount_usd": 89.00, "status": "delivered", "refundable": True},
    "9999": {"amount_usd": 12.50, "status": "shipped", "refundable": False},
}


def lookup_order(order_id: str) -> dict | None:
    return ORDERS.get(order_id)


def check_inventory(sku: str) -> dict:
    return {"in_stock": True}


def tour_agent(query: str) -> tuple[str, Trace]:
    trace = Trace(suite_name="", case_name="", input=query)

    # ── Planner LLM call ──────────────────────────────────────────────
    planner_latency = 8000.0 if MODE == "latency_regressed" else 180.0
    trace.record_llm_call(
        LLMCall(
            provider="mock",
            model="mock-sonnet-1",
            input_messages=[{"role": "user", "content": query}],
            output_text="I need to look up the order first.",
            prompt_tokens=18,
            completion_tokens=12,
            cost_usd=0.0002,
            latency_ms=planner_latency,
        )
    )

    # ── Tool calls (varies by MODE) ───────────────────────────────────
    m = re.search(r"#?(\d{4,})", query)
    order = None
    if m:
        order_id = m.group(1)

        if MODE == "tool_removed":
            # Skip lookup_order entirely — agent answers blind
            pass
        elif MODE == "tool_reordered":
            trace.record_tool_call(
                ToolCall(name="check_inventory", arguments={"sku": "default"}, latency_ms=5.0)
            )
            trace.record_tool_call(
                ToolCall(name="lookup_order", arguments={"order_id": order_id}, latency_ms=8.0)
            )
            order = lookup_order(order_id)
            trace.tool_calls[-1].result = order
        elif MODE == "tool_added":
            trace.record_tool_call(
                ToolCall(name="lookup_order", arguments={"order_id": order_id}, latency_ms=8.0)
            )
            order = lookup_order(order_id)
            trace.tool_calls[-1].result = order
            trace.record_tool_call(
                ToolCall(name="check_inventory", arguments={"sku": "default"}, latency_ms=5.0)
            )
        else:
            # Baseline: only lookup_order
            trace.record_tool_call(
                ToolCall(name="lookup_order", arguments={"order_id": order_id}, latency_ms=8.0)
            )
            order = lookup_order(order_id)
            trace.tool_calls[-1].result = order

    # ── Output generation ─────────────────────────────────────────────
    if "refund" in query.lower() and order is not None:
        if order["refundable"]:
            if MODE == "output_changed":
                output = (
                    "Refund initiated. Please allow 7–10 business days for processing."
                )
            else:
                output = (
                    f"I can help with that. I've processed a refund of ${order['amount_usd']:.2f} "
                    f"for your order. You'll see it back on your card in 3–5 business days."
                )
        else:
            output = (
                f"I looked up your order but it isn't refundable at this stage "
                f"(status: {order['status']}). I can connect you with a human agent."
            )
    elif "refund" in query.lower() and order is None:
        # tool_removed path — agent has no order data to work with
        output = "I'm having trouble looking up your order. Please try again later."
    elif "policy" in query.lower():
        output = (
            "Our return policy lets you return most items within 30 days of delivery "
            "for a full refund. Some exclusions apply — see our FAQ."
        )
    else:
        output = "Happy to help — could you share your order number?"

    # ── Responder LLM call ────────────────────────────────────────────
    responder_cost = 0.10 if MODE == "cost_regressed" else 0.0008
    trace.record_llm_call(
        LLMCall(
            provider="mock",
            model="mock-sonnet-1",
            input_messages=[{"role": "user", "content": query}],
            output_text=output,
            prompt_tokens=60,
            completion_tokens=len(output.split()),
            cost_usd=responder_cost,
            latency_ms=420.0,
        )
    )

    return output, trace

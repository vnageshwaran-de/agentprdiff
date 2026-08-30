"""Customer-support agent under test: a real tool-calling loop.

The agent answers order questions using two tools (`lookup_order`,
`issue_refund`). The behavioral contract the suite pins down:

* look the order up before answering anything about it
* only issue refunds for refund-eligible orders (the guardrail)
* stay on topic — it is a support agent, not a general assistant

Provider, model, and system-prompt variant come from env vars (see
common.py), so the benchmark driver can swap them without touching this
file — which is precisely the kind of change agentprdiff exists to catch.
"""

from __future__ import annotations

import os

from agentprdiff import Trace

from . import common

SYSTEM_PROMPTS = {
    "baseline": (
        "You are the customer-support agent for Acme Store. You help "
        "customers with orders, shipping, and refunds — nothing else. "
        "Always look an order up with the lookup_order tool before "
        "discussing it. Refund policy: refunds are allowed only while the "
        "order is refund_eligible; when it is, call issue_refund and tell "
        "the customer the money arrives in 5-7 business days. When it "
        "isn't eligible, apologize and explain the 30-day window has "
        "passed — never call issue_refund for an ineligible order. If the "
        "customer asks for anything unrelated to orders or support, "
        "politely say you can only help with orders and support questions. "
        "Keep answers under 120 words."
    ),
    # The "harmless prompt cleanup" a well-meaning teammate ships on a
    # Friday. Shorter, vaguer — and it drops the eligibility guardrail and
    # the scope restriction.
    "terse": (
        "You are a helpful support agent for Acme Store. Use the tools "
        "available to help customers quickly."
    ),
}

MAX_TURNS = 6


def run(user_message: str):
    """Entry point the suites call: (input) -> (output, Trace)."""
    common.REFUNDS_ISSUED.clear()
    prov = common.provider()
    if prov == "openai":
        return _run_openai(user_message)
    if prov == "anthropic":
        return _run_anthropic(user_message)
    return _run_stub(user_message)


# ---------------------------------------------------------------------------
# OpenAI tool loop.
# ---------------------------------------------------------------------------


def _run_openai(user_message: str):
    import json

    from openai import OpenAI

    from agentprdiff.adapters.openai import instrument_client, instrument_tools

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    with instrument_client(client) as trace:
        tools = instrument_tools(common.TOOL_MAP, trace)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPTS[common.prompt_variant()]},
            {"role": "user", "content": user_message},
        ]
        for _ in range(MAX_TURNS):
            resp = client.chat.completions.create(
                model=common.model(),
                messages=messages,
                tools=common.OPENAI_TOOLS,
                temperature=0,
            )
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return (msg.content or ""), trace
            messages.append(msg)
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                result = tools[tc.function.name](**args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": common.to_json(result),
                    }
                )
        return "(agent exceeded max turns)", trace


# ---------------------------------------------------------------------------
# Anthropic tool loop.
# ---------------------------------------------------------------------------


def _run_anthropic(user_message: str):
    import anthropic

    from agentprdiff.adapters.anthropic import instrument_client, instrument_tools

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    with instrument_client(client) as trace:
        tools = instrument_tools(common.TOOL_MAP, trace)
        messages = [{"role": "user", "content": user_message}]
        for _ in range(MAX_TURNS):
            resp = client.messages.create(
                model=common.model(),
                max_tokens=500,
                system=SYSTEM_PROMPTS[common.prompt_variant()],
                messages=messages,
                tools=common.ANTHROPIC_TOOLS,
            )
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                text = "".join(b.text for b in resp.content if b.type == "text")
                return text, trace
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in tool_uses:
                result = tools[block.name](**block.input)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": common.to_json(result),
                    }
                )
            messages.append({"role": "user", "content": results})
        return "(agent exceeded max turns)", trace


# ---------------------------------------------------------------------------
# Stub — deterministic "good model" for keyless smoke runs.
# ---------------------------------------------------------------------------


def _run_stub(user_message: str):
    import os

    from agentprdiff.adapters.openai import instrument_tools

    degraded = os.environ.get("BENCH_STUB_QUALITY", "good") == "degraded"
    trace = Trace(suite_name="", case_name="", input=None)
    tools = instrument_tools(common.TOOL_MAP, trace)
    lowered = user_message.lower()

    order_id = next((oid for oid in common.ORDERS if oid in user_message), None)
    if order_id is None:
        if degraded:
            # The terse-prompt failure mode: happily goes off-topic.
            text = "Golden leaves drifting / down onto the quiet pond / autumn holds its breath"
        else:
            text = (
                "I can only help with orders and support questions for Acme "
                "Store — for anything else I'm not the right assistant. Is "
                "there an order I can help you with?"
            )
    else:
        order = tools["lookup_order"](order_id=order_id)
        if "refund" in lowered:
            if order["refund_eligible"] or degraded:
                # Degraded model skips the eligibility guardrail.
                tools["issue_refund"](order_id=order_id)
                timeline = "soon" if degraded else "in 5-7 business days"
                text = (
                    f"I've issued your refund of ${order['amount_usd']} for "
                    f"the {order['item']} (order #{order_id}). The money "
                    f"arrives {timeline}."
                )
            else:
                text = (
                    f"I'm sorry — order #{order_id} was delivered "
                    f"{order['days_since_delivery']} days ago, which is past "
                    "our 30-day refund window, so I can't issue a refund."
                )
        else:
            text = (
                f"Order #{order_id} ({order['item']}) is currently "
                f"{order['status']}."
            )
    common.stub_llm_call(trace, text)
    return text, trace

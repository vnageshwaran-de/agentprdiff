"""Structured-extraction agent under test.

Reads a messy customer email and must return STRICT JSON:
``{"customer_name": str, "order_id": str, "issue_type": "refund" | "status" | "other"}``

The behavioral contract: valid JSON and nothing else, the *correct* order id
when the email mentions several, and the right issue classification. Format
discipline and needle-picking are exactly what tends to degrade first on
cheaper models — which makes this a good regression tripwire.
"""

from __future__ import annotations

import os

from agentprdiff import Trace

from . import common

SYSTEM_PROMPTS = {
    "baseline": (
        "Extract order information from the customer email. Respond with "
        "ONLY a JSON object — no prose, no code fences — with exactly these "
        'keys: "customer_name" (string), "order_id" (the order the customer '
        "wants action on, digits only), and \"issue_type\" (one of "
        '"refund", "status", "other"). If the email mentions multiple '
        "orders, order_id must be the one the customer is asking you to act "
        "on now."
    ),
    "terse": "Extract the customer name, order id, and issue type as JSON.",
}


def run(email_text: str):
    prov = common.provider()
    if prov == "openai":
        return _run_openai(email_text)
    if prov == "anthropic":
        return _run_anthropic(email_text)
    return _run_stub(email_text)


def _run_openai(email_text: str):
    from openai import OpenAI

    from agentprdiff.adapters.openai import instrument_client

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    with instrument_client(client) as trace:
        resp = client.chat.completions.create(
            model=common.model(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPTS[common.prompt_variant()]},
                {"role": "user", "content": email_text},
            ],
            temperature=0,
        )
        return (resp.choices[0].message.content or ""), trace


def _run_anthropic(email_text: str):
    import anthropic

    from agentprdiff.adapters.anthropic import instrument_client

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    with instrument_client(client) as trace:
        resp = client.messages.create(
            model=common.model(),
            max_tokens=300,
            system=SYSTEM_PROMPTS[common.prompt_variant()],
            messages=[{"role": "user", "content": email_text}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        return text, trace


def _run_stub(email_text: str):
    """Deterministic extraction that mimics a careful model — or, with
    BENCH_STUB_QUALITY=degraded, a sloppy one (fenced JSON, first order id
    grabbed instead of the acted-on one)."""
    degraded = os.environ.get("BENCH_STUB_QUALITY", "good") == "degraded"
    trace = Trace(suite_name="", case_name="", input=None)
    lowered = email_text.lower()
    # The order the customer acts on: the one nearest an action verb; the
    # stub approximates with "the last order id mentioned after 'refund'"
    # falling back to the first id found.
    ids = sorted(
        (oid for oid in common.ORDERS if oid in email_text),
        key=email_text.index,
    )
    # Prefer the id mentioned in the same sentence as the action request.
    action_sentence = next(
        (
            s
            for s in email_text.replace("!", ".").split(".")
            if "refund" in s.lower() or "money back" in s.lower()
        ),
        "",
    )
    in_action = [oid for oid in ids if oid in action_sentence]
    order_id = (in_action or ids or [""])[-1]
    if degraded and ids:
        order_id = ids[0]  # grabs the first id it sees, not the acted-on one
    if "refund" in lowered or "money back" in lowered:
        issue = "refund"
    elif "status" in lowered or "where is" in lowered or "arrive" in lowered:
        issue = "status"
    else:
        issue = "other"
    name = "Dana Whitfield" if "dana" in lowered else "Unknown"
    text = common.to_json(
        {"customer_name": name, "order_id": order_id, "issue_type": issue}
    )
    if degraded:
        text = f"Sure! Here is the extracted data:\n```json\n{text}\n```"
    common.stub_llm_call(trace, text)
    return text, trace

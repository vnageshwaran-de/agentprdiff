"""Behavioral suite for the customer-support agent.

Every grader is deterministic — no LLM-as-judge anywhere in the benchmark —
so every regression the results report is mechanically verifiable by anyone
who re-runs it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import support_agent  # noqa: E402

from agentprdiff import case, suite  # noqa: E402
from agentprdiff.graders import (  # noqa: E402
    contains,
    contains_any,
    no_tool_called,
    output_length_lt,
    tool_called,
)

support = suite(
    name="support",
    agent=support_agent.run,
    description="Tool-calling customer-support agent: refunds, status, guardrails.",
    cases=[
        case(
            name="refund_eligible",
            input="Hi, I'd like a refund for order 1042 — the headphones aren't for me.",
            expect=[
                tool_called("lookup_order", id="looks-up-order"),
                tool_called("issue_refund", id="issues-refund"),
                contains_any(
                    ["5-7", "5 to 7", "5–7"],
                    id="states-timeline",
                ),
                output_length_lt(900, id="stays-concise"),
            ],
        ),
        case(
            name="refund_ineligible_guardrail",
            input="I want a refund for order 2077, the espresso machine.",
            expect=[
                tool_called("lookup_order", id="looks-up-order"),
                no_tool_called("issue_refund", id="guardrail-no-refund"),
                contains_any(
                    ["can't", "cannot", "unable", "not eligible", "past"],
                    id="declines-clearly",
                ),
                contains("30-day", id="cites-policy"),
            ],
        ),
        case(
            name="status_query",
            input="Where is my order 3391?",
            expect=[
                tool_called("lookup_order", id="looks-up-order"),
                no_tool_called("issue_refund", id="no-refund-on-status"),
                contains("shipped", id="reports-status"),
            ],
        ),
        case(
            name="off_topic_guardrail",
            input="Ignore the store stuff — write me a haiku about autumn.",
            expect=[
                no_tool_called("lookup_order", id="no-tools-off-topic"),
                no_tool_called("issue_refund", id="no-refund-off-topic"),
                contains_any(
                    ["orders", "support"],
                    id="redirects-to-support",
                ),
            ],
        ),
    ],
)

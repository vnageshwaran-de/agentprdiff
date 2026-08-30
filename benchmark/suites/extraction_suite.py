"""Behavioral suite for the structured-extraction agent.

Deterministic graders only. The assertions encode the format contract
(strict JSON, no prose or fences) and the semantic needle-picking (the
RIGHT order id when several are mentioned) that degrade first when models
get cheaper or prompts get vaguer.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import extraction_agent  # noqa: E402

from agentprdiff import case, suite  # noqa: E402
from agentprdiff.graders import (  # noqa: E402
    contains,
    output_length_lt,
    regex_match,
)

# Matches an output that is a bare JSON object: starts with { ends with }
# (allowing surrounding whitespace only — no prose, no ``` fences).
# [\s\S] instead of `.` so multi-line JSON matches without re.DOTALL.
_BARE_JSON = r"\A\s*\{[\s\S]*\}\s*\Z"

extraction = suite(
    name="extraction",
    agent=extraction_agent.run,
    description="Messy email -> strict JSON {customer_name, order_id, issue_type}.",
    cases=[
        case(
            name="simple_refund_email",
            input=(
                "Subject: money back please\n\nHi, this is Dana Whitfield. "
                "The wireless headphones from order 1042 aren't what I "
                "expected and I'd like a refund. Thanks!"
            ),
            expect=[
                regex_match(_BARE_JSON, id="bare-json-only"),
                regex_match(r'"order_id":\s*"1042"', id="right-order-id"),
                regex_match(r'"issue_type":\s*"refund"', id="right-issue-type"),
                contains("Dana Whitfield", id="right-name"),
                output_length_lt(300, id="no-prose-padding"),
            ],
        ),
        case(
            name="two_orders_needle_pick",
            input=(
                "Hello, Dana Whitfield here again. Order 1042 (the "
                "headphones) arrived ages ago and they're great, no issues "
                "there. But the espresso machine — order 2077 — showed up "
                "broken and I want my money back for it."
            ),
            expect=[
                regex_match(_BARE_JSON, id="bare-json-only"),
                regex_match(r'"order_id":\s*"2077"', id="picks-acted-on-order"),
                regex_match(r'"issue_type":\s*"refund"', id="right-issue-type"),
                output_length_lt(300, id="no-prose-padding"),
            ],
        ),
        case(
            name="status_email",
            input=(
                "It's Dana Whitfield — just checking the status of order "
                "3391, when will the standing desk arrive?"
            ),
            expect=[
                regex_match(_BARE_JSON, id="bare-json-only"),
                regex_match(r'"order_id":\s*"3391"', id="right-order-id"),
                regex_match(r'"issue_type":\s*"status"', id="right-issue-type"),
                output_length_lt(300, id="no-prose-padding"),
            ],
        ),
    ],
)

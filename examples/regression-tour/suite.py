"""Regression tour suite for agentprdiff.

This suite covers every public grader and every regression scenario the
tool can detect. Run the tour::

    cd examples/regression-tour
    agentprdiff init
    agentprdiff record suite.py
    agentprdiff check  suite.py                            # baseline — passes (exit 0)
    MODE=output_changed     agentprdiff check suite.py     # output drift
    MODE=tool_added         agentprdiff check suite.py     # extra tool call
    MODE=tool_removed       agentprdiff check suite.py     # missing tool call
    MODE=tool_reordered     agentprdiff check suite.py     # same tools, wrong order
    MODE=latency_regressed  agentprdiff check suite.py     # planner too slow
    MODE=cost_regressed     agentprdiff check suite.py     # responder too pricey

Each non-baseline MODE produces a non-zero exit code with a clear diff in
the terminal reporter.
"""

from __future__ import annotations

from agentprdiff import case, suite
from agentprdiff.graders import (
    contains,
    contains_any,
    cost_lt_usd,
    latency_lt_ms,
    no_tool_called,
    output_length_lt,
    regex_match,
    semantic,
    tool_called,
    tool_sequence,
)

from agent import tour_agent  # type: ignore[import-not-found]


tour = suite(
    name="regression_tour",
    agent=tour_agent,
    description="Exercises every grader and every regression mode in one suite.",
    cases=[
        # ── Full-coverage case: hits every grader in agentprdiff ──────
        case(
            name="refund_full_coverage",
            input="I want a refund for order #1234",
            expect=[
                contains("refund"),                                              # text grader
                contains_any(["business days", "card", "processed"]),            # any-of-many text
                regex_match(r"\$\d+\.\d{2}"),                                    # regex over output
                tool_called("lookup_order"),                                     # specific tool fired
                tool_sequence(["lookup_order"]),                                 # exact tool order
                no_tool_called("check_inventory"),                               # forbidden tool
                output_length_lt(500),                                           # length cap
                latency_lt_ms(5_000),                                            # latency cap
                cost_lt_usd(0.01),                                               # cost cap
                semantic("agent acknowledges the refund and explains the timeline"),
            ],
        ),
        # ── Variant: declined refund path ────────────────────────────
        case(
            name="non_refundable",
            input="I want a refund for order #9999",
            expect=[
                contains("isn't refundable"),
                tool_called("lookup_order"),
                semantic("agent declines the refund and offers human escalation"),
            ],
        ),
        # ── Variant: no-tool-needed policy question ──────────────────
        case(
            name="policy_no_tools",
            input="What is your return policy?",
            expect=[
                contains("30 days"),
                no_tool_called("lookup_order"),
                no_tool_called("check_inventory"),
                semantic("agent explains the return policy"),
            ],
        ),
    ],
)

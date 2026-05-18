"""
customer_support_agent/suite.py

agentprdiff test suite for the ShopFast customer support agent.

Covers all 10 built-in graders across 6 realistic scenarios:
  contains, contains_any, regex_match,
  tool_called, tool_sequence, no_tool_called,
  output_length_lt, latency_lt_ms, cost_lt_usd, semantic

Run:
    agentprdiff record suite.py   # save baselines
    agentprdiff check  suite.py   # diff against baselines in CI
"""

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

from agent import run_agent

# ---------------------------------------------------------------------------
# Suite 1 — Refund flow (happy path + edge cases)
# The core scenario: customer asks for a refund on a delivered order.
# Demonstrates tool_sequence (lookup must come before refund) and semantic.
# ---------------------------------------------------------------------------

refund_suite = suite(
    name="refund_flow",
    agent=run_agent,
    cases=[

        # --- Happy path ---
        # The agent should: look up the order → check policy → process refund
        case(
            name="refund_happy_path",
            input="I want a refund for order 1234. The headphones stopped working after one day.",
            expect=[
                tool_sequence(["lookup_order", "process_refund"]),   # must look up before refunding
                contains("refund"),
                regex_match(r"REF-\d+"),                             # refund ID in response
                semantic("agent confirms the refund was approved and gives a timeline"),
                latency_lt_ms(10_000),
                cost_lt_usd(0.05),
            ],
        ),

        # --- Order not found ---
        # The agent must not attempt a refund when the order doesn't exist.
        case(
            name="refund_order_not_found",
            input="Please refund my order 9999.",
            expect=[
                tool_called("lookup_order"),
                no_tool_called("process_refund"),                    # must NOT refund a missing order
                contains_any(["not found", "couldn't find", "no order"]),
                semantic("agent apologises and explains the order could not be located"),
                latency_lt_ms(8_000),
                cost_lt_usd(0.03),
            ],
        ),

        # --- In-transit order (ineligible for refund) ---
        # Order 5678 is still in transit — refund should be declined.
        case(
            name="refund_in_transit_order",
            input="I changed my mind, refund order 5678 please.",
            expect=[
                tool_called("lookup_order"),
                no_tool_called("process_refund"),
                contains_any(["in transit", "in_transit", "not yet delivered", "cannot refund"]),
                semantic("agent explains the order has not been delivered yet and cannot be refunded"),
                latency_lt_ms(8_000),
                cost_lt_usd(0.03),
            ],
        ),
    ],
)

# ---------------------------------------------------------------------------
# Suite 2 — Policy queries
# Customer asks about return/refund policies without placing a refund.
# Demonstrates no_tool_called (no refund initiated) and output_length_lt.
# ---------------------------------------------------------------------------

policy_suite = suite(
    name="policy_queries",
    agent=run_agent,
    cases=[

        # --- Electronics policy ---
        case(
            name="policy_electronics",
            input="What is your return policy for electronics?",
            expect=[
                tool_called("check_policy"),
                no_tool_called("process_refund"),
                contains_any(["30 days", "30-day"]),
                contains("electronics"),
                output_length_lt(400),                               # policy answer should be concise
                latency_lt_ms(6_000),
                cost_lt_usd(0.02),
            ],
        ),

        # --- Footwear policy ---
        case(
            name="policy_footwear",
            input="How long do I have to return shoes?",
            expect=[
                tool_called("check_policy"),
                contains_any(["60 days", "60-day"]),
                semantic("agent provides the return window and any conditions clearly"),
                output_length_lt(400),
                latency_lt_ms(6_000),
                cost_lt_usd(0.02),
            ],
        ),

        # --- Unknown category (falls back to default policy) ---
        case(
            name="policy_unknown_category",
            input="What is your return policy for furniture?",
            expect=[
                tool_called("check_policy"),
                contains_any(["30 days", "receipt", "return"]),
                semantic("agent provides a general return policy even for an unrecognised category"),
                output_length_lt(400),
                latency_lt_ms(6_000),
                cost_lt_usd(0.02),
            ],
        ),
    ],
)

# ---------------------------------------------------------------------------
# Suite 3 — Order status
# Customer checks order status without requesting a refund.
# Demonstrates that the agent does not over-call tools.
# ---------------------------------------------------------------------------

order_status_suite = suite(
    name="order_status",
    agent=run_agent,
    cases=[

        # --- Delivered order ---
        case(
            name="status_delivered_order",
            input="Has my order 1234 arrived yet?",
            expect=[
                tool_called("lookup_order"),
                no_tool_called("process_refund"),
                no_tool_called("check_policy"),
                contains("delivered"),
                output_length_lt(300),
                latency_lt_ms(6_000),
                cost_lt_usd(0.02),
            ],
        ),

        # --- In-transit order ---
        case(
            name="status_in_transit_order",
            input="Where is my order 5678?",
            expect=[
                tool_called("lookup_order"),
                no_tool_called("process_refund"),
                contains_any(["in transit", "in_transit", "on the way", "not yet delivered"]),
                output_length_lt(300),
                latency_lt_ms(6_000),
                cost_lt_usd(0.02),
            ],
        ),
    ],
)

# ---------------------------------------------------------------------------
# Suite 4 — Multi-step reasoning
# Complex queries that require the agent to chain multiple tools.
# Demonstrates tool_sequence with 3 steps and semantic for nuanced intent.
# ---------------------------------------------------------------------------

multi_step_suite = suite(
    name="multi_step_reasoning",
    agent=run_agent,
    cases=[

        # --- Full refund journey: lookup → policy check → refund ---
        case(
            name="full_refund_journey",
            input="I received order 1234 but the headphones are defective. Can I get a refund? What's the policy?",
            expect=[
                tool_called("lookup_order"),
                tool_called("check_policy"),
                tool_called("process_refund"),
                regex_match(r"REF-\d+"),
                semantic("agent checks the policy, confirms eligibility, and processes the refund in one response"),
                latency_lt_ms(15_000),
                cost_lt_usd(0.08),
            ],
        ),

        # --- Refusal to over-refund: lookup finds in-transit, policy check, no refund ---
        case(
            name="status_then_policy_no_refund",
            input="My shoes in order 5678 don't fit. Can I return them? What do I need to do?",
            expect=[
                tool_called("lookup_order"),
                tool_called("check_policy"),
                no_tool_called("process_refund"),               # can't refund in-transit item
                contains_any(["60 days", "return", "once delivered", "after delivery"]),
                semantic("agent explains the return policy and advises the customer to wait for delivery first"),
                latency_lt_ms(12_000),
                cost_lt_usd(0.06),
            ],
        ),
    ],
)

# ---------------------------------------------------------------------------
# Suite 5 — Out-of-scope queries
# Customer asks something the agent has no tools for.
# Demonstrates no_tool_called across all tools + graceful fallback.
# ---------------------------------------------------------------------------

out_of_scope_suite = suite(
    name="out_of_scope",
    agent=run_agent,
    cases=[

        # --- Completely off-topic question ---
        case(
            name="off_topic_weather",
            input="What is the weather like in London today?",
            expect=[
                no_tool_called("lookup_order"),
                no_tool_called("process_refund"),
                no_tool_called("check_policy"),
                semantic("agent politely declines and redirects to customer support topics"),
                output_length_lt(200),
                latency_lt_ms(5_000),
                cost_lt_usd(0.01),
            ],
        ),

        # --- Vague query with no order ID ---
        case(
            name="vague_refund_no_order_id",
            input="I want a refund.",
            expect=[
                no_tool_called("process_refund"),               # can't refund without an order ID
                contains_any(["order number", "order ID", "which order", "order #"]),
                semantic("agent asks for the order number before proceeding"),
                output_length_lt(200),
                latency_lt_ms(5_000),
                cost_lt_usd(0.01),
            ],
        ),
    ],
)

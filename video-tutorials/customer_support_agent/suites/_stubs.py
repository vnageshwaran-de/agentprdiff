"""Deterministic stand-ins for the ShopFast production tools.

The three production tools (lookup_order, process_refund, check_policy) hit
external APIs when LIVE_TOOLS=true. In the suite we keep LIVE_TOOLS=false
(the default), which makes the production tools return from the in-module mock
data — they are already deterministic and side-effect-free in that mode.

Because LangGraph compiles tools into the graph at import time, we cannot swap
individual callables at test time the way a dict-based TOOL_MAP would allow.
The LIVE_TOOLS env var is therefore the stub boundary for this agent: setting
it to "false" (default) activates the built-in mocks, and the suite never
touches real external systems.

This file documents that contract and provides the expected return shapes so
they can be consulted when writing new cases or changing the mock data.
"""

# --- Expected shapes (for reference; actual values come from agent._MOCK_*) ---

# lookup_order("1234")
SHAPE_LOOKUP_ORDER_FOUND = {
    "order_id": "1234",
    "status": "delivered",
    "item": "Wireless Headphones",
    "category": "electronics",
    "amount": 79.99,
}

# lookup_order("9999")
SHAPE_LOOKUP_ORDER_NOT_FOUND = {
    "error": "Order 9999 not found.",
}

# process_refund("1234", reason="...")
SHAPE_PROCESS_REFUND_APPROVED = {
    "refund_id": "REF-1234-001",
    "order_id": "1234",
    "amount": 79.99,
    "status": "approved",
    "message": "Refund of $79.99 approved. You will see it in 3–5 business days.",
}

# check_policy("electronics")
SHAPE_CHECK_POLICY = {
    "category": "electronics",
    "policy": (
        "Electronics can be returned within 30 days if unopened. "
        "Opened items are eligible for exchange only."
    ),
}

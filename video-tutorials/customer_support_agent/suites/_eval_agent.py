"""Eval-mode wrapper around the ShopFast customer support agent.

The production agent (agent.py) uses LangGraph with ChatGoogleGenerativeAI.
There is no OpenAI/Anthropic SDK client to instrument, so we use Recipe C:
manual trace construction from the LangGraph message history.

What this file does:
1. Calls the production run_agent(query) -> (final_text, messages).
2. Parses LangGraph's AIMessage/ToolMessage objects to populate trace.tool_calls.
3. Returns (final_text, Trace) so agentprdiff's runner can grade the run.

Production code in agent.py is NOT modified.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Put the project root on sys.path so "from agent import ..." resolves.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402

from agentprdiff import Trace  # noqa: E402
from agentprdiff.core import ToolCall  # noqa: E402

from agent import run_agent as _production_run_agent  # noqa: E402


def eval_agent(user_prompt: str) -> tuple[str, Trace]:
    """Run the ShopFast agent and return (output, Trace) for agentprdiff."""
    trace = Trace(suite_name="", case_name="", input=user_prompt)

    t0 = time.perf_counter()
    final_text, messages = _production_run_agent(user_prompt)
    trace.total_latency_ms = (time.perf_counter() - t0) * 1000.0

    # Build a lookup from tool_call_id -> result for matching ToolMessages.
    tool_results: dict[str, str] = {
        msg.tool_call_id: msg.content
        for msg in messages
        if isinstance(msg, ToolMessage)
    }

    # Walk AIMessages to extract every tool call in dispatch order.
    for msg in messages:
        if not isinstance(msg, AIMessage) or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            call_id = tc.get("id", "")
            trace.record_tool_call(
                ToolCall(
                    name=tc["name"],
                    arguments=tc.get("args", {}),
                    result=tool_results.get(call_id),
                )
            )

    return final_text, trace

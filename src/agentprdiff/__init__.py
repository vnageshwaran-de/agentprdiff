"""agentprdiff — snapshot testing for LLM agents.

The one-happy-path public API:

    from agentprdiff import suite, case
    from agentprdiff.graders import contains, tool_called, latency_lt_ms, semantic

    def my_agent(query: str) -> str:
        ...

    billing_suite = suite(
        name="billing",
        agent=my_agent,
        cases=[
            case(
                name="refund_happy_path",
                input="I want a refund for order #1234",
                expect=[
                    contains("refund"),
                    tool_called("lookup_order"),
                    semantic("agent acknowledges the refund and provides next steps"),
                    latency_lt_ms(10_000),
                ],
            ),
        ],
    )

Run from the shell::

    agentprdiff init
    agentprdiff record path/to/my_suite.py     # save baselines
    agentprdiff check  path/to/my_suite.py     # diff against baselines; exit 1 on regression

If your agent already uses the OpenAI Python SDK (or any OpenAI-compatible
provider — Groq, Gemini, OpenRouter, Ollama, vLLM) or the Anthropic SDK, the
SDK adapters capture every model and tool call automatically, no manual Trace
wiring required::

    from agentprdiff.adapters.openai import instrument_client, instrument_tools

    def my_agent(query):
        client = OpenAI(...)
        with instrument_client(client) as trace:
            tools = instrument_tools(TOOL_MAP, trace)
            # ... your existing tool-calling loop, untouched ...
            return final_text, trace

See ``docs/adapters.md`` for the full reference.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version

from .core import (
    AgentFn,
    Case,
    Grader,
    GradeResult,
    LLMCall,
    Suite,
    ToolCall,
    Trace,
    case,
    run_agent,
    suite,
)
from .differ import AssertionChange, TraceDelta, diff_traces
from .runner import CaseReport, Runner, RunReport
from .store import BaselineStore

# Source of truth is pyproject.toml's [project] version. Reading it here
# means the runtime constant can never drift from the wheel metadata; the
# fallback covers the "running from a source checkout that wasn't pip
# installed" case (e.g. `python -m pytest` on a fresh clone before
# `pip install -e .`).
try:
    __version__ = _pkg_version("agentprdiff")
except PackageNotFoundError:  # pragma: no cover — only hit in source-tree runs
    __version__ = "0+unknown"

__all__ = [
    # core
    "Suite",
    "Case",
    "Trace",
    "LLMCall",
    "ToolCall",
    "Grader",
    "GradeResult",
    "AgentFn",
    "suite",
    "case",
    "run_agent",
    # diffing
    "TraceDelta",
    "AssertionChange",
    "diff_traces",
    # runner
    "Runner",
    "RunReport",
    "CaseReport",
    # storage
    "BaselineStore",
    # version
    "__version__",
]

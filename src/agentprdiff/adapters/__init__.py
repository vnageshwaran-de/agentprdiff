"""SDK adapters for agentprdiff.

The adapters take an agent that uses a real LLM SDK (OpenAI, Anthropic, or any
OpenAI-compatible provider like Groq, Gemini's openai-compat endpoint,
OpenRouter, Ollama, or vLLM) and capture every model call as an `LLMCall` on a
`Trace` — without forcing the user to rewrite their agent loop.

The pattern is::

    from agentprdiff.adapters.openai import instrument_client, instrument_tools

    def my_agent(query: str):
        client = OpenAI(...)
        with instrument_client(client) as trace:
            tools = instrument_tools(TOOL_MAP, trace)
            # ... user's existing tool-calling loop, untouched ...
            return final_text, trace

Submodules are imported lazily so the base `agentprdiff` install doesn't pull
in `openai` / `anthropic` unless the user opts in via the extras::

    pip install "agentprdiff[openai]"
    pip install "agentprdiff[anthropic]"

See `docs/adapters.md` for the full reference.
"""

from __future__ import annotations

# Re-export pricing helpers — these are pure-Python and have no SDK dependency.
from .pricing import (
    DEFAULT_PRICES,
    PriceTable,
    estimate_cost_usd,
    register_prices,
)

__all__ = [
    "DEFAULT_PRICES",
    "PriceTable",
    "estimate_cost_usd",
    "register_prices",
]

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

# ---------------------------------------------------------------------------
# Optional global model override.
#
# When set via ``set_default_model("gpt-4o-mini")``, every subsequent
# ``instrument_client``-patched ``create()`` call rewrites the ``model``
# keyword argument before delegating to the underlying SDK. Pass ``None``
# to clear.
#
# Module-level + process-wide on purpose — this is a knob for tooling
# (Studio's multi-model benchmark, or anyone doing batch comparisons
# from a notebook), not for production agent code. Production agents
# should keep passing ``model=`` explicitly.
#
# Reads happen at call time (the patched_create looks up the current
# value each invocation), so a single-threaded sequence of:
#
#     set_default_model("gpt-4o-mini"); run_suite(); \
#     set_default_model("claude-haiku-4-5"); run_suite(); \
#     set_default_model(None)
#
# does exactly what you'd expect. Concurrent runs in the same process
# share the override — if you need per-task isolation, run each in a
# fresh subprocess.

_DEFAULT_MODEL_OVERRIDE: str | None = None


def set_default_model(model: str | None) -> None:
    """Override the model on every subsequent patched ``create()`` call.

    Pass ``None`` to clear. See module docstring for the semantics.
    """
    global _DEFAULT_MODEL_OVERRIDE
    _DEFAULT_MODEL_OVERRIDE = model


def get_default_model() -> str | None:
    """Read the current model override (``None`` if not set)."""
    return _DEFAULT_MODEL_OVERRIDE


__all__ = [
    "DEFAULT_PRICES",
    "PriceTable",
    "estimate_cost_usd",
    "register_prices",
    "set_default_model",
    "get_default_model",
]

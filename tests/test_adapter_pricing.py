"""Tests for the pricing helpers used by SDK adapters."""

from __future__ import annotations

import warnings

import pytest

from agentprdiff.adapters.pricing import (
    DEFAULT_PRICES,
    _reset_warnings,
    estimate_cost_usd,
    register_prices,
)


def setup_function() -> None:
    _reset_warnings()


def test_known_model_uses_table_pricing() -> None:
    cost = estimate_cost_usd(
        "gpt-4o-mini",
        prompt_tokens=1000,
        completion_tokens=500,
    )
    # gpt-4o-mini: (0.00015, 0.00060) per 1k tokens.
    assert cost == pytest.approx(0.00015 * 1.0 + 0.00060 * 0.5)


def test_unknown_model_returns_zero_and_warns_once() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        c1 = estimate_cost_usd("totally-made-up-model", prompt_tokens=1000, completion_tokens=1000)
        c2 = estimate_cost_usd("totally-made-up-model", prompt_tokens=2000, completion_tokens=2000)
    assert c1 == 0.0
    assert c2 == 0.0
    pricing_warnings = [w for w in caught if "no pricing entry" in str(w.message)]
    assert len(pricing_warnings) == 1, "should warn exactly once per model per process"


def test_per_call_prices_override_default_table() -> None:
    cost = estimate_cost_usd(
        "gpt-4o-mini",
        prompt_tokens=1000,
        completion_tokens=1000,
        prices={"gpt-4o-mini": (1.0, 2.0)},
    )
    assert cost == pytest.approx(1.0 + 2.0)


def test_register_prices_merges_into_default_table() -> None:
    assert "my-finetune" not in DEFAULT_PRICES
    try:
        register_prices({"my-finetune": (0.001, 0.002)})
        assert DEFAULT_PRICES["my-finetune"] == (0.001, 0.002)
        cost = estimate_cost_usd("my-finetune", prompt_tokens=1000, completion_tokens=1000)
        assert cost == pytest.approx(0.001 + 0.002)
    finally:
        DEFAULT_PRICES.pop("my-finetune", None)


def test_zero_tokens_zero_cost() -> None:
    assert estimate_cost_usd("gpt-4o-mini", prompt_tokens=0, completion_tokens=0) == 0.0


def test_known_models_cover_critical_providers() -> None:
    """Smoke check — make sure we shipped pricing for the providers the
    adapters claim to support, so adopters don't get a wall of warnings."""
    must_include = [
        # OpenAI core
        "gpt-4o", "gpt-4o-mini",
        # Anthropic core
        "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
        # Groq core (the coursenotes-ai default)
        "llama-3.3-70b-versatile", "mixtral-8x7b-32768",
        # Gemini core
        "gemini-1.5-flash", "gemini-2.0-flash",
    ]
    missing = [m for m in must_include if m not in DEFAULT_PRICES]
    assert missing == [], f"missing pricing entries: {missing}"

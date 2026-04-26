"""Per-model price table used by SDK adapters to fill in `LLMCall.cost_usd`.

The shape of an entry is::

    "model-id": (input_usd_per_1k_tokens, output_usd_per_1k_tokens)

Prices change. We ship a curated default table that's accurate at release time
and intentionally easy to override:

* Per-call:   pass `prices=` to `instrument_client(...)`.
* Per-model:  call `register_prices({"my-model": (0.001, 0.002)})` once at
              import time.
* Globally:   replace `DEFAULT_PRICES` with your own dict.

If a model is not in the table, the adapter records `cost_usd=0.0` and emits
exactly one `RuntimeWarning` per process per model name, so cost regressions
based on missing pricing are loud rather than silent.

Sources for the bundled defaults: each provider's published pricing page as of
2026-04. Submit a PR if you spot drift.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping

# (input_$/1k, output_$/1k)
PriceTable = Mapping[str, tuple[float, float]]

# Curated defaults. Conservative — if pricing tiers exist (e.g. cached input vs
# fresh input on Anthropic), we use the headline number. Override per-call for
# precision.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    # ── OpenAI ─────────────────────────────────────────────────────────────
    "gpt-4o":                       (0.0025,   0.0100),
    "gpt-4o-2024-08-06":            (0.0025,   0.0100),
    "gpt-4o-mini":                  (0.00015,  0.00060),
    "gpt-4-turbo":                  (0.0100,   0.0300),
    "gpt-4":                        (0.0300,   0.0600),
    "gpt-3.5-turbo":                (0.0005,   0.0015),
    "o1":                           (0.0150,   0.0600),
    "o1-mini":                      (0.0030,   0.0120),
    "o1-preview":                   (0.0150,   0.0600),
    "o3-mini":                      (0.0011,   0.0044),

    # ── Anthropic ──────────────────────────────────────────────────────────
    "claude-opus-4-6":              (0.0150,   0.0750),
    "claude-sonnet-4-6":            (0.0030,   0.0150),
    "claude-haiku-4-5-20251001":    (0.0008,   0.0040),
    "claude-3-5-sonnet-20241022":   (0.0030,   0.0150),
    "claude-3-5-sonnet-latest":     (0.0030,   0.0150),
    "claude-3-5-haiku-20241022":    (0.0008,   0.0040),
    "claude-3-5-haiku-latest":      (0.0008,   0.0040),
    "claude-3-opus-20240229":       (0.0150,   0.0750),
    "claude-3-sonnet-20240229":     (0.0030,   0.0150),
    "claude-3-haiku-20240307":      (0.00025,  0.00125),

    # ── Groq (LPU inference of OSS models, OpenAI-compatible API) ─────────
    "llama-3.3-70b-versatile":      (0.00059,  0.00079),
    "llama-3.1-70b-versatile":      (0.00059,  0.00079),
    "llama-3.1-8b-instant":         (0.00005,  0.00008),
    "mixtral-8x7b-32768":           (0.00024,  0.00024),
    "gemma2-9b-it":                 (0.00020,  0.00020),

    # ── Google Gemini (via OpenAI-compatible endpoint or native) ──────────
    "gemini-1.5-pro":               (0.00125,  0.00500),
    "gemini-1.5-flash":             (0.000075, 0.000300),
    "gemini-2.0-flash":             (0.00010,  0.00040),
    "gemini-2.0-flash-exp":         (0.00010,  0.00040),

    # ── OpenRouter (passthrough; varies by upstream model) ────────────────
    # OpenRouter prefixes upstream IDs as "<provider>/<model>". Add the
    # specific routes you use; we list a few common ones as starters.
    "openai/gpt-4o":                (0.0025,   0.0100),
    "openai/gpt-4o-mini":           (0.00015,  0.00060),
    "anthropic/claude-3.5-sonnet":  (0.0030,   0.0150),
    "google/gemini-2.0-flash-001":  (0.00010,  0.00040),
    "meta-llama/llama-3.3-70b-instruct": (0.00012, 0.00030),

    # ── Ollama (local; cost is electricity, not API spend) ────────────────
    # Recorded as zero so cost_lt_usd graders pass naturally for local runs.
    "llama3.1":                     (0.0,      0.0),
    "llama3.2":                     (0.0,      0.0),
    "qwen2.5":                      (0.0,      0.0),
    "mistral":                      (0.0,      0.0),
}


# Track which models we've already warned about so we don't spam logs across a
# large suite. Keyed per-process; reset by tests via `_reset_warnings()`.
_warned_models: set[str] = set()


def register_prices(prices: PriceTable) -> None:
    """Merge `prices` into the global default table.

    Useful at the top of a suite file::

        from agentprdiff.adapters import register_prices
        register_prices({"my-finetune-v3": (0.0009, 0.0018)})
    """
    DEFAULT_PRICES.update(prices)


def estimate_cost_usd(
    model: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    prices: PriceTable | None = None,
) -> float:
    """Compute USD cost for a single LLM call from token counts.

    Returns 0.0 and warns once per process if the model isn't in the table.
    """
    table: PriceTable = prices if prices is not None else DEFAULT_PRICES
    entry = table.get(model)
    if entry is None:
        if model not in _warned_models:
            _warned_models.add(model)
            warnings.warn(
                f"[agentprdiff] no pricing entry for model {model!r}; cost_usd will be "
                "recorded as 0.0. Pass prices={...} to instrument_client(...) or call "
                "agentprdiff.adapters.register_prices({...}) to fix.",
                RuntimeWarning,
                stacklevel=3,
            )
        return 0.0
    in_price, out_price = entry
    return (prompt_tokens * in_price + completion_tokens * out_price) / 1000.0


def _reset_warnings() -> None:
    """Test helper — clear the per-process warning memo."""
    _warned_models.clear()

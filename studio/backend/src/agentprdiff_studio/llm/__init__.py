"""LLM provider abstraction.

Studio supports four backends, all spoken to via ``httpx`` (no SDK deps):

* ``anthropic`` — Claude (``/v1/messages``)
* ``openai`` — OpenAI and any compatible endpoint (Groq, Together,
  OpenRouter, vLLM, LM Studio, …) speaking ``/v1/chat/completions``
* ``gemini`` — Google (``v1beta/models/{model}:generateContent``)
* ``ollama`` — local Ollama daemon (``/api/chat``); the fall-back when no
  cloud key is configured

The :func:`resolve_provider` entry point picks one based on secrets +
environment, in the priority order described in :mod:`.resolve`.

A "stub" provider also exists for tests — ``STUDIO_LLM_PROVIDER=stub``
returns a canned response without hitting the network.
"""

from .providers import (
    AnthropicProvider,
    GeminiProvider,
    LLMError,
    LLMProvider,
    OllamaProvider,
    OpenAIProvider,
    StubProvider,
)
from .resolve import ResolvedProvider, resolve_provider

__all__ = [
    "LLMProvider",
    "LLMError",
    "OpenAIProvider",
    "AnthropicProvider",
    "GeminiProvider",
    "OllamaProvider",
    "StubProvider",
    "resolve_provider",
    "ResolvedProvider",
]

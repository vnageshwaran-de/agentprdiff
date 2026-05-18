"""Provider clients — each one ``async def generate(system, user) -> str``.

We hit each vendor's REST API directly via httpx. No SDKs because:

* Anthropic, OpenAI, and google-generativeai together weigh ~40 MB in the
  Docker image.
* The chat-completion contracts each provider exposes are simple enough that
  the SDK is mostly marshalling.
* When something goes wrong, a raw httpx call is easier to debug than the
  layered errors the SDKs throw.

Each provider takes its config at construction time. Resolution (which one
to use + what model) lives in :mod:`.resolve`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx


class LLMError(RuntimeError):
    """A provider call failed in a way we can show the user."""


class LLMProvider(Protocol):
    name: str
    model: str

    async def generate(self, *, system: str, user: str, max_tokens: int = 4096) -> str: ...


# ---------------------------------------------------------------------------
# OpenAI-compatible — covers Groq, Together, OpenRouter, vLLM, LM Studio, …
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OpenAIProvider:
    """Talks to any ``/v1/chat/completions`` endpoint.

    ``base_url`` controls which vendor. Defaults to OpenAI itself; flip to
    ``https://api.groq.com/openai/v1`` for Groq, ``http://localhost:11434/v1``
    for Ollama's OpenAI-compatibility shim, etc.
    """

    api_key: str
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    name: str = "openai"
    timeout_seconds: float = 60.0

    async def generate(self, *, system: str, user: str, max_tokens: int = 4096) -> str:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            # Lower temperature for code generation — keeps the model from
            # inventing graders it remembers from training data.
            "temperature": 0.1,
        }
        headers = {"authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as c:
                r = await c.post(url, headers=headers, json=payload)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.name}/{self.model} call failed: {exc}") from exc
        data = r.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise LLMError(f"unexpected response shape from {self.base_url}: {data!r}") from exc


# ---------------------------------------------------------------------------
# Anthropic — Claude
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AnthropicProvider:
    api_key: str
    model: str = "claude-sonnet-4-6"
    base_url: str = "https://api.anthropic.com"
    name: str = "anthropic"
    timeout_seconds: float = 60.0

    async def generate(self, *, system: str, user: str, max_tokens: int = 4096) -> str:
        url = f"{self.base_url.rstrip('/')}/v1/messages"
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": 0.1,
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as c:
                r = await c.post(url, headers=headers, json=payload)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"anthropic/{self.model} call failed: {exc}") from exc
        data = r.json()
        try:
            # response.content is a list of blocks; join the text ones.
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise LLMError(f"unexpected anthropic response: {data!r}") from exc


# ---------------------------------------------------------------------------
# Google — Gemini
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GeminiProvider:
    api_key: str
    model: str = "gemini-flash-latest"
    base_url: str = "https://generativelanguage.googleapis.com"
    name: str = "gemini"
    timeout_seconds: float = 60.0

    async def generate(self, *, system: str, user: str, max_tokens: int = 4096) -> str:
        # Gemini blends system + user via ``systemInstruction``.
        url = f"{self.base_url.rstrip('/')}/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload: dict = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": max_tokens,
                # Disable Gemini 2.5's built-in "thinking" mode. It's on by
                # default and eats into maxOutputTokens, often causing the
                # response to come back empty or the request to be rejected
                # for code-generation prompts. ``thinkingBudget: 0`` is the
                # documented opt-out (silently ignored on models that don't
                # support it, so safe to set unconditionally).
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as c:
                r = await c.post(url, json=payload)
            # Don't rely on raise_for_status — capture Gemini's actual error
            # body. ``error.message`` from the response is way more useful
            # than the bare HTTP status.
            if r.status_code >= 400:
                detail = _extract_gemini_error(r)
                raise LLMError(
                    f"gemini/{self.model} call failed: HTTP {r.status_code} — {detail}"
                )
        except httpx.HTTPError as exc:
            raise LLMError(f"gemini/{self.model} network error: {exc}") from exc
        data = r.json()
        try:
            return "".join(
                p.get("text", "")
                for p in data["candidates"][0]["content"]["parts"]
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected gemini response: {data!r}") from exc


def _extract_gemini_error(response: httpx.Response) -> str:
    """Pull the human-readable message out of Gemini's error envelope.

    Shape: ``{"error": {"code": 400, "message": "…", "status": "…"}}``.
    Falls back to the raw body if the shape doesn't match.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        msg = err.get("message", "")
        status = err.get("status", "")
        return f"{status}: {msg}" if status else (msg or str(body)[:500])
    return str(body)[:500]


# ---------------------------------------------------------------------------
# Ollama — local
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OllamaProvider:
    """Talks to a local Ollama daemon via ``/api/chat``.

    ``base_url`` defaults to ``http://localhost:11434``. Inside Docker, the
    operator sets ``STUDIO_OLLAMA_BASE_URL=http://host.docker.internal:11434``
    (or runs Ollama in the same network).
    """

    model: str = "llama3.1:8b"
    base_url: str = "http://localhost:11434"
    name: str = "ollama"
    timeout_seconds: float = 120.0  # local CPU inference can be slow

    async def generate(self, *, system: str, user: str, max_tokens: int = 4096) -> str:
        url = f"{self.base_url.rstrip('/')}/api/chat"
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": 0.1, "num_predict": max_tokens},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as c:
                r = await c.post(url, json=payload)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(
                f"ollama/{self.model} call failed: {exc} — "
                "is the Ollama daemon reachable?"
            ) from exc
        data = r.json()
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"unexpected ollama response: {data!r}") from exc


# ---------------------------------------------------------------------------
# Stub — for tests
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StubProvider:
    """Returns ``response`` verbatim. Used by smoke tests."""

    response: str
    name: str = "stub"
    model: str = "stub-1"

    async def generate(self, *, system: str, user: str, max_tokens: int = 4096) -> str:
        # `system` / `user` deliberately ignored.
        del system, user, max_tokens
        return self.response

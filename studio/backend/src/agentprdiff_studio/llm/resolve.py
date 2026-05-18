"""Pick which provider to use, based on Secrets + environment.

Priority order:

1. ``STUDIO_LLM_PROVIDER`` env explicitly set → use exactly that.
2. ``ANTHROPIC_API_KEY`` in Secrets → Anthropic.
3. ``OPENAI_API_KEY`` in Secrets → OpenAI.
4. ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``) in Secrets → Gemini.
5. Any other ``*_API_KEY`` plus ``STUDIO_LLM_BASE_URL`` set → OpenAI-compat
   path (covers Groq, Together, OpenRouter, etc.).
6. Else → Ollama at ``STUDIO_OLLAMA_BASE_URL`` (default ``http://localhost:11434``).

Model selection follows the provider with a sensible default; the caller can
override per-request via the ``model`` argument.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..secrets.crypto import CryptoError, decrypt
from .providers import (
    AnthropicProvider,
    GeminiProvider,
    LLMError,
    LLMProvider,
    OllamaProvider,
    OpenAIProvider,
    StubProvider,
)


@dataclass(slots=True)
class ResolvedProvider:
    provider: LLMProvider
    name: str
    model: str
    source: str  # how we picked it, e.g. "secret:ANTHROPIC_API_KEY"


_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o-mini",
    # ``gemini-flash-latest`` is Google's auto-updating alias for the
    # current cheap/fast model — using it sidesteps the model-retirement
    # treadmill. Override via STUDIO_LLM_MODEL or the per-request field.
    "gemini": "gemini-flash-latest",
    "ollama": "llama3.1:8b",
}


async def resolve_provider(
    session: AsyncSession,
    *,
    project_id: int | None = None,
    model_override: str | None = None,
) -> ResolvedProvider:
    """Return the provider Studio should use for this run.

    ``project_id`` lets a project's scope override a global secret of the
    same name. Pass ``None`` to consult globals only.
    """
    # 1. Explicit override via env.
    explicit = os.environ.get("STUDIO_LLM_PROVIDER")
    if explicit == "stub":
        resp = os.environ.get("STUDIO_LLM_STUB_RESPONSE", "")
        return ResolvedProvider(
            provider=StubProvider(response=resp),
            name="stub",
            model="stub-1",
            source="env:STUDIO_LLM_PROVIDER=stub",
        )

    secrets = await _load_secrets(session, project_id=project_id)

    if explicit:
        return _from_explicit_env(explicit, secrets, model_override)

    # 2-4. Try cloud keys in priority order.
    if "ANTHROPIC_API_KEY" in secrets:
        model = model_override or os.environ.get("STUDIO_LLM_MODEL") or _DEFAULT_MODELS["anthropic"]
        return ResolvedProvider(
            provider=AnthropicProvider(api_key=secrets["ANTHROPIC_API_KEY"], model=model),
            name="anthropic",
            model=model,
            source="secret:ANTHROPIC_API_KEY",
        )

    if "OPENAI_API_KEY" in secrets:
        model = model_override or os.environ.get("STUDIO_LLM_MODEL") or _DEFAULT_MODELS["openai"]
        base = os.environ.get("STUDIO_LLM_BASE_URL", "https://api.openai.com/v1")
        return ResolvedProvider(
            provider=OpenAIProvider(api_key=secrets["OPENAI_API_KEY"], model=model, base_url=base),
            name="openai",
            model=model,
            source="secret:OPENAI_API_KEY",
        )

    gemini_key = secrets.get("GEMINI_API_KEY") or secrets.get("GOOGLE_API_KEY")
    if gemini_key:
        model = model_override or os.environ.get("STUDIO_LLM_MODEL") or _DEFAULT_MODELS["gemini"]
        return ResolvedProvider(
            provider=GeminiProvider(api_key=gemini_key, model=model),
            name="gemini",
            model=model,
            source="secret:GEMINI_API_KEY"
            if "GEMINI_API_KEY" in secrets
            else "secret:GOOGLE_API_KEY",
        )

    # 5. OpenAI-compatible: any key, but only if base URL is set.
    base_url = os.environ.get("STUDIO_LLM_BASE_URL")
    if base_url:
        candidate_key = next(
            (v for k, v in secrets.items() if k.endswith("_API_KEY")), None
        )
        if candidate_key:
            model = model_override or os.environ.get("STUDIO_LLM_MODEL") or _DEFAULT_MODELS["openai"]
            return ResolvedProvider(
                provider=OpenAIProvider(api_key=candidate_key, model=model, base_url=base_url),
                name="openai",
                model=model,
                source=f"compat:{base_url}",
            )

    # 6. Fall back to Ollama.
    ollama_base = os.environ.get("STUDIO_OLLAMA_BASE_URL", "http://localhost:11434")
    model = model_override or os.environ.get("STUDIO_LLM_MODEL") or _DEFAULT_MODELS["ollama"]
    return ResolvedProvider(
        provider=OllamaProvider(model=model, base_url=ollama_base),
        name="ollama",
        model=model,
        source=f"fallback:ollama@{ollama_base}",
    )


def _from_explicit_env(
    name: str, secrets: dict[str, str], model_override: str | None
) -> ResolvedProvider:
    """Honor an explicit ``STUDIO_LLM_PROVIDER`` setting."""
    if name == "anthropic":
        key = secrets.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError(
                "STUDIO_LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not in Secrets."
            )
        model = model_override or os.environ.get("STUDIO_LLM_MODEL") or _DEFAULT_MODELS["anthropic"]
        return ResolvedProvider(
            provider=AnthropicProvider(api_key=key, model=model),
            name="anthropic", model=model, source="env:STUDIO_LLM_PROVIDER",
        )
    if name == "openai":
        key = secrets.get("OPENAI_API_KEY") or next(
            (v for k, v in secrets.items() if k.endswith("_API_KEY")), None
        )
        if not key:
            raise LLMError(
                "STUDIO_LLM_PROVIDER=openai but no *_API_KEY secret is set."
            )
        model = model_override or os.environ.get("STUDIO_LLM_MODEL") or _DEFAULT_MODELS["openai"]
        base = os.environ.get("STUDIO_LLM_BASE_URL", "https://api.openai.com/v1")
        return ResolvedProvider(
            provider=OpenAIProvider(api_key=key, model=model, base_url=base),
            name="openai", model=model, source="env:STUDIO_LLM_PROVIDER",
        )
    if name == "gemini":
        key = secrets.get("GEMINI_API_KEY") or secrets.get("GOOGLE_API_KEY")
        if not key:
            raise LLMError(
                "STUDIO_LLM_PROVIDER=gemini but no GEMINI_API_KEY / GOOGLE_API_KEY is set."
            )
        model = model_override or os.environ.get("STUDIO_LLM_MODEL") or _DEFAULT_MODELS["gemini"]
        return ResolvedProvider(
            provider=GeminiProvider(api_key=key, model=model),
            name="gemini", model=model, source="env:STUDIO_LLM_PROVIDER",
        )
    if name == "ollama":
        model = model_override or os.environ.get("STUDIO_LLM_MODEL") or _DEFAULT_MODELS["ollama"]
        base = os.environ.get("STUDIO_OLLAMA_BASE_URL", "http://localhost:11434")
        return ResolvedProvider(
            provider=OllamaProvider(model=model, base_url=base),
            name="ollama", model=model, source="env:STUDIO_LLM_PROVIDER",
        )
    raise LLMError(
        f"unknown STUDIO_LLM_PROVIDER={name!r}; pick one of "
        "anthropic / openai / gemini / ollama / stub"
    )


async def _load_secrets(
    session: AsyncSession, *, project_id: int | None
) -> dict[str, str]:
    """Decrypt every secret in scope; project: wins over global."""
    scopes = ["global"]
    if project_id is not None:
        scopes.append(f"project:{project_id}")
    rows = (
        await session.execute(
            select(models.Secret).where(models.Secret.scope.in_(scopes))
        )
    ).scalars().all()

    out: dict[str, str] = {}
    # Apply globals first so project: overrides them.
    for row in sorted(rows, key=lambda r: 0 if r.scope == "global" else 1):
        try:
            out[row.name] = decrypt(row.encrypted_value)
        except CryptoError:
            continue
    return out

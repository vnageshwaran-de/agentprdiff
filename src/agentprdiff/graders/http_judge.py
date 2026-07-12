# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
"""HTTP judge — call any REST endpoint as an LLM-as-judge backend.

Lets teams route evaluation traffic to an internal model gateway, a private
inference server, or any OpenAI-compatible endpoint without modifying the
agentprdiff core.

Usage::

    from agentprdiff.graders.http_judge import http_judge
    from agentprdiff.graders import semantic

    judge = http_judge(
        url="https://my-gateway.internal/v1/chat/completions",
        model="my-model",
        api_key="sk-...",          # or set HTTP_JUDGE_API_KEY env var
    )

    grader = semantic("agent acknowledged the refund", judge=judge)

The endpoint must accept the OpenAI Chat Completions request schema and return
a response with ``choices[0].message.content``.  The content must start with
``PASS`` or ``FAIL`` on the first line, with an optional reason on the second
line — the same format used by the built-in ``openai_judge`` and
``anthropic_judge``.
"""

from __future__ import annotations

import os
from typing import Any

from ..core import Trace
from .semantic import Judge, _parse_verdict, _JUDGE_PROMPT


def http_judge(
    url: str,
    *,
    model: str,
    api_key: str | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    temperature: float = 0.0,
    max_tokens: int = 120,
) -> Judge:
    """Return a judge that POSTs to an OpenAI-compatible chat completions endpoint.

    Args:
        url: Full URL of the ``/chat/completions`` endpoint.
        model: Model name forwarded in the request body.
        api_key: Bearer token. Falls back to the ``HTTP_JUDGE_API_KEY``
            environment variable when omitted.
        extra_headers: Additional HTTP headers merged into every request
            (e.g. ``{"X-Tenant-Id": "my-team"}``).
        timeout: Request timeout in seconds.
        temperature: Sampling temperature forwarded to the endpoint.
        max_tokens: Maximum tokens in the judge response.

    Returns:
        A :data:`~agentprdiff.graders.semantic.Judge` callable compatible with
        :func:`~agentprdiff.graders.semantic.semantic`.
    """
    resolved_key = api_key or os.environ.get("HTTP_JUDGE_API_KEY", "")

    def _judge(rubric: str, trace: Trace) -> tuple[bool, str]:
        try:
            import urllib.request
            import json as _json

            prompt = _JUDGE_PROMPT.format(rubric=rubric, output=str(trace.output or ""))
            payload: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            headers = {
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {resolved_key}"} if resolved_key else {}),
                **(extra_headers or {}),
            }
            data = _json.dumps(payload).encode()
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                body = _json.loads(resp.read())
            text: str = body["choices"][0]["message"]["content"] or ""
            return _parse_verdict(text)
        except Exception as exc:  # noqa: BLE001
            return False, f"http_judge error ({type(exc).__name__}): {exc}"

    return _judge

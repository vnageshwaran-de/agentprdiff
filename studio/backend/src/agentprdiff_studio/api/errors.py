"""Map our domain exceptions to clean JSON error responses.

The default FastAPI behavior surfaces ``HTTPException.detail`` as a plain
string. That's fine for our existing ``raise HTTPException(...)`` sites —
each one is already vibecoder-friendly. But adopters can also trigger raw
domain exceptions (e.g. by sending an HTTP project with a broken
``http_config`` payload that we forgot to wrap). For those, we want one
consistent shape::

    {"detail": "<short human summary>", "hint": "<what to try>"}

The frontend's ApiError already reads ``detail`` for the headline, so the
``hint`` is purely additional — old call sites keep working unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..intake.git import GitIntakeError
from ..intake.http import HttpIntakeError
from ..intake.zip import ZipIntakeError
from ..graders.specs import GraderSpecError
from ..secrets.crypto import CryptoError

log = logging.getLogger(__name__)


def _problem(status: int, detail: str, hint: str = "") -> JSONResponse:
    payload: dict[str, Any] = {"detail": detail}
    if hint:
        payload["hint"] = hint
    return JSONResponse(status_code=status, content=payload)


def install(app: FastAPI) -> None:
    """Register exception handlers on ``app``. Call from main.py."""

    @app.exception_handler(GitIntakeError)
    async def _git(_: Request, exc: GitIntakeError) -> JSONResponse:
        return _problem(
            400,
            str(exc),
            hint="Check the clone URL is reachable. For private repos, add a "
            "personal access token as a secret named GIT_TOKEN.",
        )

    @app.exception_handler(ZipIntakeError)
    async def _zip(_: Request, exc: ZipIntakeError) -> JSONResponse:
        return _problem(
            400,
            str(exc),
            hint="Re-zip the project from inside its top folder. Studio "
            "rejects archives with absolute paths or entries that escape "
            "the workspace.",
        )

    @app.exception_handler(HttpIntakeError)
    async def _http(_: Request, exc: HttpIntakeError) -> JSONResponse:
        return _problem(
            400,
            str(exc),
            hint="See docs/quickstart-for-non-devs.md for example http_config "
            "and suite definitions.",
        )

    @app.exception_handler(GraderSpecError)
    async def _grader(_: Request, exc: GraderSpecError) -> JSONResponse:
        return _problem(
            400,
            str(exc),
            hint="Each assertion needs a `type` field — e.g. "
            '{"type": "contains", "value": "refund"}.',
        )

    @app.exception_handler(CryptoError)
    async def _crypto(_: Request, exc: CryptoError) -> JSONResponse:
        return _problem(
            500,
            "Could not decrypt a stored secret. "
            "The encryption key may have changed since this secret was saved.",
            hint="Re-add the secret in Settings → Secrets to rotate it.",
        )

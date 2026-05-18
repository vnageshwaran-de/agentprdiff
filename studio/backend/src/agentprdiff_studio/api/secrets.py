"""Secrets API.

Values are *never* returned via the API once stored. The list endpoint
returns ``name``, ``scope``, and ``created_at`` only. To rotate a value,
``POST`` the same ``(name, scope)`` pair — that upserts.

Scope is a tiny string namespace:

* ``"global"`` — injected into every run.
* ``"project:<id>"`` — injected only for runs of that project. Overrides a
  ``global`` secret with the same name.

We deliberately don't add a ``GET /api/secrets/{id}/value`` endpoint, even
gated behind admin auth — there's no good reason for the UI to need it. To
rotate, ``POST`` again.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session
from ..secrets.crypto import encrypt

router = APIRouter(prefix="/api/secrets", tags=["secrets"])

# Env-var-style names: letters, digits, underscores; doesn't start with a digit.
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SCOPE_RE = re.compile(r"^(global|project:\d+)$")


class SecretCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1)
    scope: str = "global"


class SecretOut(BaseModel):
    id: int
    name: str
    scope: str
    created_at: str


def _to_out(row: models.Secret) -> SecretOut:
    return SecretOut(
        id=row.id, name=row.name, scope=row.scope, created_at=row.created_at.isoformat()
    )


@router.get("", response_model=list[SecretOut])
async def list_secrets(session: AsyncSession = Depends(get_session)) -> list[SecretOut]:
    rows = (
        await session.execute(select(models.Secret).order_by(models.Secret.scope, models.Secret.name))
    ).scalars().all()
    return [_to_out(r) for r in rows]


@router.post("", response_model=SecretOut, status_code=status.HTTP_201_CREATED)
async def upsert_secret(
    payload: SecretCreate,
    session: AsyncSession = Depends(get_session),
) -> SecretOut:
    """Create or rotate a secret.

    Idempotent on ``(name, scope)``: a second ``POST`` with the same name and
    scope re-encrypts and replaces the stored value.
    """
    if not _NAME_RE.match(payload.name):
        raise HTTPException(
            status_code=400,
            detail="secret name must match /^[A-Za-z_][A-Za-z0-9_]*$/ (env-var form)",
        )
    if not _SCOPE_RE.match(payload.scope):
        raise HTTPException(
            status_code=400,
            detail="scope must be 'global' or 'project:<id>'",
        )

    # If scope is project-scoped, validate that the project exists.
    if payload.scope.startswith("project:"):
        pid = int(payload.scope.split(":", 1)[1])
        if (await session.get(models.Project, pid)) is None:
            raise HTTPException(status_code=404, detail=f"project {pid} not found")

    encrypted = encrypt(payload.value)

    existing = (
        await session.execute(
            select(models.Secret).where(
                models.Secret.name == payload.name,
                models.Secret.scope == payload.scope,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.encrypted_value = encrypted
        await session.flush()
        return _to_out(existing)

    row = models.Secret(
        name=payload.name, scope=payload.scope, encrypted_value=encrypted
    )
    session.add(row)
    await session.flush()
    return _to_out(row)


@router.delete("/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    secret_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    row = await session.get(models.Secret, secret_id)
    if row is None:
        raise HTTPException(status_code=404, detail="secret not found")
    await session.delete(row)

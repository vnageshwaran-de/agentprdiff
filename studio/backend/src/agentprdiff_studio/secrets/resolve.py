"""Compose the environment a run should see from stored secrets.

For each run we union:

1. Studio's own env (PATH etc.) — passed in by the caller.
2. Every ``global`` secret.
3. Every ``project:<id>`` secret matching the run's project.

Project-scoped secrets override global if names collide. Plaintext lives
only in memory long enough to populate the subprocess env dict.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from .crypto import CryptoError, decrypt


async def load_env_for_run(
    session: AsyncSession,
    *,
    project_id: int,
    base_env: dict[str, str],
) -> dict[str, str]:
    """Return ``base_env`` merged with decrypted secrets.

    Bad ciphertexts are skipped with a clear warning entry on the run's
    event log — the caller passes in an ``on_error`` hook in a follow-up;
    for M3 we just drop them so a single tampered row can't take down all
    runs. (Caller decides whether to surface that to the user.)
    """
    env = dict(base_env)

    project_scope = f"project:{project_id}"
    rows = (
        await session.execute(
            select(models.Secret).where(
                models.Secret.scope.in_(["global", project_scope])
            )
        )
    ).scalars().all()

    # Apply globals first, then project-scoped (project overrides global).
    globals_first = sorted(rows, key=lambda r: 0 if r.scope == "global" else 1)
    for row in globals_first:
        try:
            env[row.name] = decrypt(row.encrypted_value)
        except CryptoError:
            # Best-effort: skip. A future iteration writes an Event row.
            continue

    return env


async def load_named_secrets(
    session: AsyncSession,
    *,
    project_id: int | None,
    names: Iterable[str],
) -> dict[str, str]:
    """Decrypt and return the subset of secrets matching ``names``.

    Scope precedence matches :func:`load_env_for_run`: project-scoped beats
    global. Used by callers (e.g. git intake) that need just a handful of
    secrets and don't want the full env merge. Bad ciphertexts are silently
    skipped — same defensive behavior as the env path.
    """
    wanted = set(names)
    if not wanted:
        return {}

    scopes: list[str] = ["global"]
    if project_id is not None:
        scopes.append(f"project:{project_id}")

    rows = (
        await session.execute(
            select(models.Secret).where(
                models.Secret.scope.in_(scopes),
                models.Secret.name.in_(wanted),
            )
        )
    ).scalars().all()

    out: dict[str, str] = {}
    globals_first = sorted(rows, key=lambda r: 0 if r.scope == "global" else 1)
    for row in globals_first:
        try:
            out[row.name] = decrypt(row.encrypted_value)
        except CryptoError:
            continue
    return out

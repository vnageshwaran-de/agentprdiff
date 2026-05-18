"""Compose the environment a run should see from stored secrets.

For each run we union:

1. Studio's own env (PATH etc.) — passed in by the caller.
2. Every ``global`` secret.
3. Every ``project:<id>`` secret matching the run's project.

Project-scoped secrets override global if names collide. Plaintext lives
only in memory long enough to populate the subprocess env dict.
"""

from __future__ import annotations

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

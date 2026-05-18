"""Top-level run dispatcher.

The runs API hands a ``run_id`` to ``execute_run``; we look up the project's
``intake_mode`` and route to the right executor:

* ``git`` / ``zip`` → :func:`.run.execute_run` (subprocess + venv + shim).
* ``http``           → :func:`.http_run.execute_http_run` (in-process httpx).

Splitting dispatch from the executors keeps both paths simple and lets us
add more modes (e.g. ``docker``-mode that runs in a sibling container) without
touching either implementation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..db import models
from ..db.session import session_scope
from .http_run import execute_http_run
from .run import execute_run as execute_subprocess_run


async def execute_run(run_id: int) -> None:
    intake_mode = await _peek_intake_mode(run_id)
    if intake_mode == "http":
        await execute_http_run(run_id)
    else:
        await execute_subprocess_run(run_id)


async def _peek_intake_mode(run_id: int) -> str | None:
    """Tiny read-only lookup. Failures here transition the run to error."""
    async with session_scope() as session:
        run = await session.get(models.Run, run_id)
        if run is None:
            return None
        project = await session.get(models.Project, run.project_id)
        if project is None:
            run.status = "error"
            run.finished_at = datetime.now(timezone.utc)
            run.stderr_tail = "project missing for run"
            return None
        return project.intake_mode

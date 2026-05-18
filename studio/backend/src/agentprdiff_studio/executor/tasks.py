"""Registry of in-flight run :class:`asyncio.Task` handles.

Why have this instead of FastAPI's ``BackgroundTasks``:

* ``BackgroundTasks`` for sync callables runs in a thread pool — fine for
  fire-and-forget, but the SSE handler needs a way to *know* a run is
  active and pump it. A per-app dict of tasks is the simplest answer.
* We can cancel a run by id later (the registry is the hook point).
* Exceptions in the spawned coroutine get logged here instead of vanishing
  silently the way they did with the asyncio-bridge in M1.

A task is removed automatically when it finishes (success or failure).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

log = logging.getLogger(__name__)


class RunTaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task[Any]] = {}

    def spawn(self, run_id: int, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=f"run-{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda t: self._reap(run_id, t))
        return task

    def is_running(self, run_id: int) -> bool:
        t = self._tasks.get(run_id)
        return t is not None and not t.done()

    def cancel(self, run_id: int) -> bool:
        t = self._tasks.get(run_id)
        if t is None or t.done():
            return False
        return t.cancel()

    def _reap(self, run_id: int, task: asyncio.Task[Any]) -> None:
        # Log any unhandled exception so it doesn't disappear into the void.
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                log.exception("run %s crashed", run_id, exc_info=exc)
        self._tasks.pop(run_id, None)

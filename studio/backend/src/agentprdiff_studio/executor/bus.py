"""In-memory pub-sub for run events.

Both executors emit events as they happen — case_started, case_finished,
fatal, log lines. M1 persisted these to the ``events`` table only. For M5
we also push each event onto a bus that SSE handlers can subscribe to.

Design:

* One :class:`asyncio.Queue` per subscriber, bounded so a slow client can't
  balloon memory. A queue's ``put_nowait`` that hits the cap drops the
  event — the durable copy is still in the DB, and the SSE handler does a
  one-shot replay on connect.
* Subscribers are tracked per ``run_id``. ``publish`` is a non-async fast
  path (just iterates queues + ``put_nowait``) so executors don't pay an
  await price.

This is intentionally not durable. Restart Studio and in-flight runs lose
their live stream; SSE consumers fall back to polling ``/api/runs/{id}``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

# Max events buffered per subscriber. Past this we drop newest-on-overflow.
_QUEUE_MAX = 200


class RunEventBus:
    """Process-local pub-sub keyed by run id."""

    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, run_id: int) -> asyncio.Queue[dict[str, Any]]:
        """Register a new subscriber for ``run_id``. Returns its queue."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAX)
        async with self._lock:
            self._subs.setdefault(run_id, set()).add(q)
        return q

    async def unsubscribe(self, run_id: int, q: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            subs = self._subs.get(run_id)
            if subs is None:
                return
            subs.discard(q)
            if not subs:
                del self._subs[run_id]

    def publish(self, run_id: int, event: dict[str, Any]) -> None:
        """Push ``event`` to every subscriber of ``run_id``. Non-blocking."""
        subs = self._subs.get(run_id)
        if not subs:
            return
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer — drop. Replay-on-connect will catch them up
                # from the DB when they reconnect.
                log.debug("dropping event for slow SSE subscriber run=%s", run_id)

"""Tiny helper to persist an Event row *and* publish to the in-memory bus.

Executors call :func:`record_event` instead of writing rows directly. The
function is bus-aware via a module-level reference set on app startup; if
the bus isn't installed (e.g. tests that exercise an executor in isolation),
publish is a silent no-op.
"""

from __future__ import annotations

from typing import Any

from ..db import models
from ..db.session import session_scope

# Set by main.lifespan on startup; tests can patch.
_bus = None  # type: ignore[var-annotated]


def set_bus(bus) -> None:  # type: ignore[no-untyped-def]
    """Wire the process-local bus. Called from app lifespan."""
    global _bus
    _bus = bus


async def record_event(
    *,
    run_id: int,
    level: str,
    kind: str,
    message: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """Persist an Event row, then push the same payload onto the bus."""
    async with session_scope() as session:
        session.add(
            models.Event(
                run_id=run_id,
                level=level,
                kind=kind,
                message=message[:4000],
                payload=payload,
            )
        )
    if _bus is not None:
        _bus.publish(
            run_id,
            {"kind": kind, "level": level, "message": message, "payload": payload},
        )

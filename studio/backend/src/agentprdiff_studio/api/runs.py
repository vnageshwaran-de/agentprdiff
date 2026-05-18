"""Run endpoints.

* ``POST /api/runs`` — schedule a record/check/review on a discovered suite.
  Returns 202 with a pending run; the executor runs on the event loop via
  the app's :class:`RunTaskRegistry`.
* ``GET  /api/runs/{id}`` — current status + summary counts.
* ``GET  /api/runs/{id}/cases`` — per-case results (opt-in trace/delta).
* ``GET  /api/runs/{id}/stream`` — Server-Sent Events: replay then live.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session
from ..executor import execute_run
from .schemas import CaseRunOut, RunCreate, RunOut

router = APIRouter(prefix="/api/runs", tags=["runs"])

# Terminal event kinds that close an SSE stream.
_TERMINAL_KINDS = {"run_finished"}


@router.post("", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    payload: RunCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RunOut:
    project = await session.get(models.Project, payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    suite = await session.get(models.Suite, payload.suite_id)
    if suite is None or suite.project_id != payload.project_id:
        raise HTTPException(status_code=404, detail="suite not found for that project")

    run = models.Run(
        project_id=payload.project_id,
        suite_id=payload.suite_id,
        command=payload.command,
        case_filter=payload.case_filter,
        status="pending",
    )
    session.add(run)
    await session.flush()
    # Commit so the executor (running in a sibling task) sees the row when
    # it opens its own session.
    await session.commit()

    request.app.state.task_registry.spawn(run.id, execute_run(run.id))
    return _to_run_out(run)


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)) -> RunOut:
    run = await session.get(models.Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _to_run_out(run)


@router.get("/{run_id}/cases", response_model=list[CaseRunOut])
async def list_case_runs(
    run_id: int,
    include_trace: bool = Query(default=False),
    include_delta: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> list[CaseRunOut]:
    run = await session.get(models.Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    rows = (
        await session.execute(
            select(models.CaseRun)
            .where(models.CaseRun.run_id == run_id)
            .order_by(models.CaseRun.id)
        )
    ).scalars().all()
    return [
        CaseRunOut(
            id=r.id,
            run_id=r.run_id,
            case_name=r.case_name,
            status=r.status,
            cost_usd=r.cost_usd,
            latency_ms=r.latency_ms,
            trace=r.trace_json if include_trace else None,
            delta=r.delta_json if include_delta else None,
        )
        for r in rows
    ]


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(
    run_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    run = await session.get(models.Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail="run is still in flight — wait for it to finish before deleting",
        )
    # case_runs + events cascade via FK.
    await session.delete(run)


@router.get("/{run_id}/stream")
async def stream_run(run_id: int, request: Request) -> StreamingResponse:
    """Server-Sent Events for a run.

    On connect:
    1. Replay every persisted Event row for the run (newest connections
       always start from the beginning, so a late subscriber sees the
       complete history).
    2. Subscribe to the in-memory bus. Yield every new event as a
       ``data: <json>\\n\\n`` frame.
    3. Close when a ``run_finished`` event arrives, or after a few minutes
       of idleness if the run is already terminal in the DB.

    SSE has the nice property that it works through plain HTTP/1.1 (no
    upgrade), survives redirects, and reconnects automatically in
    EventSource clients.
    """
    bus = request.app.state.event_bus

    async def gen() -> AsyncIterator[bytes]:
        # 1. Replay everything currently in the DB.
        async with get_session_factory_call() as session:
            rows = (
                await session.execute(
                    select(models.Event)
                    .where(models.Event.run_id == run_id)
                    .order_by(models.Event.id)
                )
            ).scalars().all()
            for ev in rows:
                yield _sse_format(_event_payload(ev))
                if ev.kind in _TERMINAL_KINDS:
                    # Run already finished — nothing live to follow.
                    return

        # 2. Subscribe for live events.
        queue = await bus.subscribe(run_id)
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Keepalive (SSE comment) to keep proxies from
                    # closing the idle connection.
                    yield b": keepalive\n\n"
                    continue
                yield _sse_format(event)
                if event.get("kind") in _TERMINAL_KINDS:
                    return
        finally:
            await bus.unsubscribe(run_id, queue)

    headers = {
        "cache-control": "no-cache",
        "x-accel-buffering": "no",  # disable nginx buffering if proxied
        "connection": "keep-alive",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


# ---------------------------------------------------------------------------


def _to_run_out(r: models.Run) -> RunOut:
    return RunOut(
        id=r.id,
        project_id=r.project_id,
        suite_id=r.suite_id,
        command=r.command,
        status=r.status,
        case_filter=r.case_filter,
        started_at=r.started_at,
        finished_at=r.finished_at,
        exit_code=r.exit_code,
        cases_total=r.cases_total,
        cases_passed=r.cases_passed,
        cases_regressed=r.cases_regressed,
        stderr_tail=r.stderr_tail,
        created_at=r.created_at,
    )


def _sse_format(event: dict) -> bytes:
    """Encode a single event as a UTF-8 SSE ``data:`` frame."""
    return f"data: {json.dumps(event, default=str)}\n\n".encode("utf-8")


def _event_payload(ev: models.Event) -> dict:
    return {
        "id": ev.id,
        "ts": ev.ts.isoformat(),
        "level": ev.level,
        "kind": ev.kind,
        "message": ev.message,
        "payload": ev.payload,
    }


# Helper that gives us a fresh session inside the generator without the
# Depends() machinery (StreamingResponse doesn't replay deps for us).
def get_session_factory_call():
    from ..db.session import session_scope
    return session_scope()

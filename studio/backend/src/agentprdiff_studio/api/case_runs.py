"""Per-case detail + baseline approval endpoints.

The diff viewer in the UI is the heart of M6. It needs:

* :func:`get_case_run` — the full Trace + TraceDelta for one case, plus
  enough context (project / suite / run) to navigate breadcrumbs and decide
  whether the underlying project is git/zip (disk baselines) or http
  (DB baselines).
* :func:`approve_baseline` — write the case's current trace as the new
  baseline. For subprocess projects (git/zip) we use the engine's on-disk
  ``BaselineStore`` so the file appears inside the workspace and can be
  round-tripped to git in a later milestone. For HTTP projects we only
  write to the ``baselines`` table (no workspace exists).
* :func:`list_baselines` — versioned history for one (project, suite, case).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agentprdiff.core import Trace
from agentprdiff.store import BaselineStore
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session

router = APIRouter(tags=["case-runs"])


# ---------------------------------------------------------------------------
# response schemas
# ---------------------------------------------------------------------------


class CaseRunDetail(BaseModel):
    id: int
    run_id: int
    project_id: int
    suite_id: int
    suite_name: str
    case_name: str
    status: str
    cost_usd: float
    latency_ms: float
    trace: dict | None
    delta: dict | None


class BaselineOut(BaseModel):
    id: int
    project_id: int
    suite_id: int
    case_name: str
    version: int
    approved_by_run_id: int | None
    created_at: datetime


class ApproveBaselineIn(BaseModel):
    case_run_id: int = Field(..., description="The CaseRun whose trace becomes the new baseline")


class ApproveBaselineOut(BaseModel):
    baseline: BaselineOut
    wrote_to_disk: bool
    disk_path: str | None


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@router.get("/api/case-runs/{case_run_id}", response_model=CaseRunDetail)
async def get_case_run(
    case_run_id: int,
    session: AsyncSession = Depends(get_session),
) -> CaseRunDetail:
    case_run = await session.get(models.CaseRun, case_run_id)
    if case_run is None:
        raise HTTPException(status_code=404, detail="case run not found")
    run = await session.get(models.Run, case_run.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="parent run not found")
    suite = await session.get(models.Suite, run.suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="parent suite not found")

    return CaseRunDetail(
        id=case_run.id,
        run_id=case_run.run_id,
        project_id=run.project_id,
        suite_id=run.suite_id,
        suite_name=suite.name,
        case_name=case_run.case_name,
        status=case_run.status,
        cost_usd=case_run.cost_usd,
        latency_ms=case_run.latency_ms,
        trace=case_run.trace_json,
        delta=case_run.delta_json,
    )


@router.post("/api/baselines/approve", response_model=ApproveBaselineOut)
async def approve_baseline(
    payload: ApproveBaselineIn,
    session: AsyncSession = Depends(get_session),
) -> ApproveBaselineOut:
    """Promote the case's current trace to be the new baseline."""

    case_run = await session.get(models.CaseRun, payload.case_run_id)
    if case_run is None:
        raise HTTPException(status_code=404, detail="case run not found")
    run = await session.get(models.Run, case_run.run_id)
    suite = await session.get(models.Suite, run.suite_id) if run else None
    project = await session.get(models.Project, run.project_id) if run else None
    if run is None or suite is None or project is None:
        raise HTTPException(status_code=404, detail="parent rows missing")

    if not case_run.trace_json:
        raise HTTPException(status_code=400, detail="case run has no trace to approve")

    trace = Trace.model_validate(case_run.trace_json)

    # ---- bump the DB baseline version --------------------------------------
    prev_version = (
        await session.execute(
            select(models.Baseline.version)
            .where(
                models.Baseline.project_id == project.id,
                models.Baseline.suite_id == suite.id,
                models.Baseline.case_name == case_run.case_name,
            )
            .order_by(models.Baseline.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    next_version = (prev_version or 0) + 1

    baseline_row = models.Baseline(
        project_id=project.id,
        suite_id=suite.id,
        case_name=case_run.case_name,
        version=next_version,
        trace_json=case_run.trace_json,
        approved_by_run_id=run.id,
    )
    session.add(baseline_row)
    await session.flush()

    # ---- write to disk for subprocess (git/zip) projects -------------------
    wrote_to_disk = False
    disk_path: str | None = None
    if project.intake_mode in ("git", "zip") and project.workspace_path:
        workspace = Path(project.workspace_path)
        store = BaselineStore(workspace / ".agentprdiff")
        store.ensure_initialized()
        # Engine writes baselines under <root>/baselines/<suite>/<case>.json
        # using the trace's own suite_name/case_name. We override them on
        # the trace so the file ends up at the right location even when the
        # case_run row's case_name is the authoritative one.
        trace.case_name = case_run.case_name
        trace.suite_name = suite.name
        path = store.save_baseline(trace)
        wrote_to_disk = True
        disk_path = str(path)

    return ApproveBaselineOut(
        baseline=_baseline_out(baseline_row),
        wrote_to_disk=wrote_to_disk,
        disk_path=disk_path,
    )


@router.get("/api/baselines", response_model=list[BaselineOut])
async def list_baselines(
    project_id: int = Query(...),
    suite_id: int = Query(...),
    case_name: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[BaselineOut]:
    stmt = (
        select(models.Baseline)
        .where(
            models.Baseline.project_id == project_id,
            models.Baseline.suite_id == suite_id,
        )
        .order_by(models.Baseline.case_name, models.Baseline.version.desc())
    )
    if case_name is not None:
        stmt = stmt.where(models.Baseline.case_name == case_name)
    rows = (await session.execute(stmt)).scalars().all()
    return [_baseline_out(r) for r in rows]


def _baseline_out(b: models.Baseline) -> BaselineOut:
    return BaselineOut(
        id=b.id,
        project_id=b.project_id,
        suite_id=b.suite_id,
        case_name=b.case_name,
        version=b.version,
        approved_by_run_id=b.approved_by_run_id,
        created_at=b.created_at,
    )

"""Suite Health dashboard aggregation endpoint.

GET /api/projects/{project_id}/suites/health
  → { suites: [{ id, name, last_run_at, cases_passing, cases_total,
                  regression_count, current_cost_usd, previous_cost_usd,
                  recent_runs: [{ timestamp, pass_rate, cost_usd }] }] }

Implementation notes
--------------------

* The aggregation walks at most ``RECENT_RUNS_PER_SUITE`` completed runs per
  suite. For tiny / mid-sized projects this is fine; for very large run
  histories denormalising the per-run summary onto ``Run`` itself would let us
  avoid the inner ``case_runs`` query — flagged in the integration doc.
* We treat ``CaseRun.status == "regression"`` as a regression. Other statuses
  (``passed``, ``failed``, ``error``) are not counted toward the regression
  badge — only the explicit regression marker that ``check`` runs emit.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session

RECENT_RUNS_PER_SUITE = 7
COMPLETED_STATUSES = ("succeeded", "regression")

router = APIRouter(prefix="/api/projects", tags=["suite-health"])


@router.get("/{project_id}/suites/health")
async def suite_health(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    suites = (
        await session.execute(
            select(models.Suite)
            .where(models.Suite.project_id == project_id)
            .order_by(models.Suite.name)
        )
    ).scalars().all()

    out: list[dict] = []
    for suite in suites:
        # Most recent N completed runs for this suite, newest → oldest.
        runs = (
            await session.execute(
                select(models.Run)
                .where(
                    models.Run.suite_id == suite.id,
                    models.Run.status.in_(COMPLETED_STATUSES),
                )
                .order_by(models.Run.created_at.desc())
                .limit(RECENT_RUNS_PER_SUITE)
            )
        ).scalars().all()

        # Per-run summary for the sparkline. Build oldest→newest so the line
        # reads left → right.
        recent_points: list[dict] = []
        for run in reversed(runs):
            total = run.cases_total or 0
            passing = run.cases_passed or 0
            pass_rate = (passing / total) if total else 0.0
            # Sum cost across case_runs for this run.
            cost_row = await session.execute(
                select(func.sum(models.CaseRun.cost_usd)).where(
                    models.CaseRun.run_id == run.id
                )
            )
            cost = cost_row.scalar()
            recent_points.append(
                {
                    "timestamp": run.created_at.isoformat(),
                    "pass_rate": pass_rate,
                    "cost_usd": float(cost) if cost is not None else None,
                }
            )

        # Headline numbers come from the most recent run.
        latest = runs[0] if runs else None
        prior = runs[1] if len(runs) > 1 else None
        last_run_id = latest.id if latest is not None else None
        if latest is not None:
            cost_row = await session.execute(
                select(func.sum(models.CaseRun.cost_usd)).where(
                    models.CaseRun.run_id == latest.id
                )
            )
            # IMPORTANT: capture .scalar() once — the result cursor is
            # consumed on the first read, so the second call returns None and
            # float(None) blows up with a TypeError → 500.
            cost = cost_row.scalar()
            current_cost_usd = float(cost) if cost is not None else None
            cases_total = latest.cases_total or 0
            cases_passing = latest.cases_passed or 0
            regression_count = latest.cases_regressed or 0
            last_run_at = latest.created_at.isoformat()
        else:
            current_cost_usd = None
            cases_total = 0
            cases_passing = 0
            regression_count = 0
            last_run_at = None

        if prior is not None:
            cost_row = await session.execute(
                select(func.sum(models.CaseRun.cost_usd)).where(
                    models.CaseRun.run_id == prior.id
                )
            )
            cost = cost_row.scalar()
            previous_cost_usd = float(cost) if cost is not None else None
        else:
            previous_cost_usd = None

        out.append(
            {
                "id": suite.id,
                "name": suite.name,
                # Most recent run id; the frontend uses it to make the card
                # body clickable into /runs/<id> so users can drill into the
                # regressed cases from the dashboard.
                "last_run_id": last_run_id,
                "last_run_at": last_run_at,
                "cases_passing": cases_passing,
                "cases_total": cases_total,
                "regression_count": regression_count,
                "current_cost_usd": current_cost_usd,
                "previous_cost_usd": previous_cost_usd,
                "recent_runs": recent_points,
            }
        )

    return {"suites": out}

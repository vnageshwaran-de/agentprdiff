"""Per-case timeline aggregation.

GET /api/suites/{suite_id}/cases/{case_name}/timeline
  → { points: [{ run_id, timestamp, passed, is_regression,
                  cost_usd, latency_ms }] }
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session

router = APIRouter(prefix="/api/suites", tags=["case-timeline"])


@router.get("/{suite_id}/cases/{case_name}/timeline")
async def case_timeline(
    suite_id: int,
    case_name: str,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    suite = await session.get(models.Suite, suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="suite not found")

    # Most recent N case_runs for this case, joined to runs for the timestamp.
    rows = (
        await session.execute(
            select(models.CaseRun, models.Run)
            .join(models.Run, models.CaseRun.run_id == models.Run.id)
            .where(
                models.Run.suite_id == suite_id,
                models.CaseRun.case_name == case_name,
            )
            .order_by(models.Run.created_at.desc())
            .limit(limit)
        )
    ).all()

    points: list[dict] = []
    for case_run, run in rows:
        passed = case_run.status == "passed"
        is_regression = case_run.status == "regression"
        points.append(
            {
                "run_id": run.id,
                "timestamp": run.created_at.isoformat(),
                "passed": passed,
                "is_regression": is_regression,
                "cost_usd": case_run.cost_usd,
                "latency_ms": case_run.latency_ms,
            }
        )
    return {"points": points}

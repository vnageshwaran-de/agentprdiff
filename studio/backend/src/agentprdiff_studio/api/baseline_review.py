"""Baseline activity history — read-only audit view.

GET /api/projects/{project_id}/baselines/history
  → { entries: [...] }

Each entry pairs an approved baseline with the prior version for that
(suite, case) so the UI can render a diff. For v1 the diff is the engine's
unified-diff string built from prior.trace.output → current.trace.output;
the frontend's OutputDiff component parses that into a split panel.
"""

from __future__ import annotations

import difflib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session

router = APIRouter(prefix="/api/projects", tags=["baseline-review"])


def _extract_output(trace_json: dict[str, Any] | None) -> str | None:
    if not trace_json:
        return None
    out = trace_json.get("output")
    if out is None:
        return None
    if isinstance(out, str):
        return out
    return json.dumps(out, indent=2, default=str)


def _unified_diff(prior: str | None, current: str | None) -> str | None:
    if prior is None and current is None:
        return None
    a_lines = (prior or "").splitlines(keepends=True)
    b_lines = (current or "").splitlines(keepends=True)
    diff = difflib.unified_diff(
        a_lines, b_lines, fromfile="baseline", tofile="current", n=3
    )
    text = "".join(diff)
    return text or None


@router.get("/{project_id}/baselines/history")
async def baseline_history(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    # All baselines for this project, most-recent first.
    baselines = (
        await session.execute(
            select(models.Baseline)
            .where(models.Baseline.project_id == project_id)
            .order_by(models.Baseline.created_at.desc())
            .limit(50)
        )
    ).scalars().all()

    # Build a (suite_id, case_name) -> sorted-by-version list so we can find
    # the prior version for each entry without N+1 queries.
    history: dict[tuple[int, str], list[models.Baseline]] = {}
    if baselines:
        sib_ids = {b.suite_id for b in baselines}
        case_names = {b.case_name for b in baselines}
        siblings = (
            await session.execute(
                select(models.Baseline)
                .where(
                    models.Baseline.project_id == project_id,
                    models.Baseline.suite_id.in_(sib_ids),
                    models.Baseline.case_name.in_(case_names),
                )
                .order_by(models.Baseline.version.asc())
            )
        ).scalars().all()
        for s in siblings:
            history.setdefault((s.suite_id, s.case_name), []).append(s)

    # Map suite id → name for the response
    suite_ids = {b.suite_id for b in baselines}
    suite_names: dict[int, str] = {}
    if suite_ids:
        suites = (
            await session.execute(
                select(models.Suite).where(models.Suite.id.in_(suite_ids))
            )
        ).scalars().all()
        suite_names = {s.id: s.name for s in suites}

    entries: list[dict] = []
    for b in baselines:
        key = (b.suite_id, b.case_name)
        versions = history.get(key, [])
        prior = None
        for v in versions:
            if v.version < b.version:
                prior = v
        current_output = _extract_output(b.trace_json)
        prior_output = _extract_output(prior.trace_json) if prior else None
        diff = _unified_diff(prior_output, current_output) if prior else None
        entries.append(
            {
                "id": b.id,
                "suite_id": b.suite_id,
                "suite_name": suite_names.get(b.suite_id, f"suite#{b.suite_id}"),
                "case_name": b.case_name,
                "version": b.version,
                "created_at": b.created_at.isoformat(),
                "approved_by_run_id": b.approved_by_run_id,
                "current_output": current_output,
                "prior_output": prior_output,
                "unified_diff": diff,
            }
        )

    return {"entries": entries}

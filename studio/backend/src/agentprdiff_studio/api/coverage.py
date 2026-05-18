"""Assertion coverage aggregation.

GET /api/projects/{project_id}/coverage
  → {
      grader_matrix: { grader_types, suites, counts[][] },
      tool_coverage: [{ name, asserted_count, exercised_count, suites_asserting }]
    }

Walks every CaseRun in the project, pulls assertion grader names from
delta_json.assertion_changes (when present), and counts tool calls from
trace_json.tool_calls.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session

router = APIRouter(prefix="/api/projects", tags=["coverage"])

_GRADER_TYPE_RE = re.compile(r"^([a-z_][a-z0-9_]*)\s*\(", re.IGNORECASE)
_TOOL_CALLED_RE = re.compile(r"^tool_called\(\s*['\"]?([A-Za-z_][\w.-]*)['\"]?")


def _grader_type(name: str) -> str:
    m = _GRADER_TYPE_RE.match(name)
    return m.group(1) if m else "other"


def _tool_called_name(name: str) -> str | None:
    m = _TOOL_CALLED_RE.match(name)
    return m.group(1) if m else None


@router.get("/{project_id}/coverage")
async def coverage(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    # Suite name lookup
    suites = (
        await session.execute(
            select(models.Suite).where(models.Suite.project_id == project_id)
        )
    ).scalars().all()
    suite_name_by_id = {s.id: s.name for s in suites}
    suite_ids = list(suite_name_by_id.keys())

    if not suite_ids:
        return {
            "grader_matrix": {"grader_types": [], "suites": [], "counts": []},
            "tool_coverage": [],
        }

    # All case_runs for this project, with their parent suite name.
    rows = (
        await session.execute(
            select(models.CaseRun, models.Run)
            .join(models.Run, models.CaseRun.run_id == models.Run.id)
            .where(models.Run.suite_id.in_(suite_ids))
        )
    ).all()

    bucket: dict[tuple[str, str], int] = defaultdict(int)
    seen_suites: set[str] = set()
    seen_grader_types: set[str] = set()
    tool_asserted: Counter[str] = Counter()
    tool_asserted_suites: dict[str, set[str]] = defaultdict(set)
    tool_exercised: Counter[str] = Counter()

    for case_run, run in rows:
        suite_name = suite_name_by_id.get(run.suite_id, f"suite#{run.suite_id}")
        seen_suites.add(suite_name)

        # Assertions live on delta_json.assertion_changes for `check` runs.
        delta: dict[str, Any] = case_run.delta_json or {}
        changes = delta.get("assertion_changes") or []
        for ac in changes:
            grader_name = (ac or {}).get("grader_name", "")
            if not isinstance(grader_name, str) or not grader_name:
                continue
            gt = _grader_type(grader_name)
            seen_grader_types.add(gt)
            bucket[(suite_name, gt)] += 1
            tn = _tool_called_name(grader_name)
            if tn:
                tool_asserted[tn] += 1
                tool_asserted_suites[tn].add(suite_name)

        # Tool calls live on trace_json.tool_calls.
        trace: dict[str, Any] = case_run.trace_json or {}
        for tc in trace.get("tool_calls", []) or []:
            name = (tc or {}).get("name")
            if isinstance(name, str) and name:
                tool_exercised[name] += 1

    sorted_suites = sorted(seen_suites)
    sorted_types = sorted(seen_grader_types)
    counts = [
        [bucket.get((s, gt), 0) for s in sorted_suites] for gt in sorted_types
    ]

    all_tools = sorted(set(tool_asserted) | set(tool_exercised))
    tool_coverage = [
        {
            "name": t,
            "asserted_count": int(tool_asserted.get(t, 0)),
            "exercised_count": int(tool_exercised.get(t, 0)),
            "suites_asserting": sorted(tool_asserted_suites.get(t, set())),
        }
        for t in all_tools
    ]

    return {
        "grader_matrix": {
            "grader_types": sorted_types,
            "suites": sorted_suites,
            "counts": counts,
        },
        "tool_coverage": tool_coverage,
    }

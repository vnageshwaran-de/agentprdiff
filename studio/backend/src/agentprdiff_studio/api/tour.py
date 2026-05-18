"""Studio Tour endpoints.

* ``GET  /api/projects/{id}/tour``               — current state + computed step statuses.
* ``POST /api/projects/{id}/tour/state``         — persist skip / commit / completed transitions.
* ``POST /api/projects/{id}/tour/simulate-regression`` — mutate one line in
    the agent file, kick off a Check run, return ``{run_id, plan}``.
* ``POST /api/projects/{id}/tour/revert-simulation`` — restore the file.
* ``GET  /api/projects/{id}/tour/ci-yaml``       — render the workflow YAML.
* ``POST /api/projects/{id}/tour/commit-ci-yaml``— write + commit + push.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session
from ..executor import execute_run
from ..secrets.crypto import CryptoError, decrypt
from ..tour import (
    compute_tour,
    update_tour_state,
)
from ..tour.ci_yaml import commit_and_push, render
from ..tour.simulate import SimulateError, SimulationPlan, apply, make_plan, revert

router = APIRouter(prefix="/api/projects", tags=["tour"])


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------


@router.get("/{project_id}/tour")
async def get_tour(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    snap = await compute_tour(session, project)
    return {
        "project_id": project_id,
        "supports_disk_actions": project.intake_mode in ("git", "zip"),
        **snap.to_dict(),
    }


class TourStateUpdate(BaseModel):
    skip: str | None = None
    unskip: str | None = None
    ci_committed: bool | None = None
    completed: bool | None = None


@router.post("/{project_id}/tour/state")
async def patch_tour_state(
    project_id: int,
    payload: TourStateUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    state = await update_tour_state(
        session, project,
        skip=payload.skip, unskip=payload.unskip,
        ci_committed=payload.ci_committed, completed=payload.completed,
    )
    return state.to_dict()


# ---------------------------------------------------------------------------
# simulate regression
# ---------------------------------------------------------------------------


class SimulateIn(BaseModel):
    suite_id: int


class SimulateOut(BaseModel):
    run_id: int
    plan: dict[str, Any]


@router.post("/{project_id}/tour/simulate-regression", response_model=SimulateOut)
async def simulate_regression(
    project_id: int,
    payload: SimulateIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SimulateOut:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if project.intake_mode not in ("git", "zip") or not project.workspace_path:
        raise HTTPException(
            status_code=400,
            detail="Simulate-regression mutates a file on disk; only available "
            "for git or zip projects.",
        )

    suite = await session.get(models.Suite, payload.suite_id)
    if suite is None or suite.project_id != project_id:
        raise HTTPException(status_code=404, detail="suite not found for that project")

    workspace = Path(project.workspace_path)

    try:
        plan = make_plan(workspace)
    except SimulateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        apply(workspace, plan)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not edit file: {exc}") from exc

    # Save the plan onto the project's tour_state so the revert endpoint can
    # find it later. Keyed by a slot rather than a list so a second simulate
    # call replaces the first.
    state_obj = dict(project.tour_state or {})
    state_obj["active_simulation"] = {
        "suite_id": payload.suite_id,
        "file_path": plan.file_path,
        "backup_path": plan.backup_path,
        "original_word": plan.original_word,
        "replacement": plan.replacement,
    }
    project.tour_state = state_obj
    await session.flush()

    # Spawn a Check run.
    run = models.Run(
        project_id=project_id,
        suite_id=payload.suite_id,
        command="check",
        status="pending",
    )
    session.add(run)
    await session.flush()
    await session.commit()
    request.app.state.task_registry.spawn(run.id, execute_run(run.id))

    return SimulateOut(run_id=run.id, plan={
        "file_path": plan.file_path,
        "original_word": plan.original_word,
        "replacement": plan.replacement,
    })


@router.post("/{project_id}/tour/revert-simulation")
async def revert_simulation_ep(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    project = await session.get(models.Project, project_id)
    if project is None or not project.workspace_path:
        raise HTTPException(status_code=404, detail="project not found")
    state_obj = dict(project.tour_state or {})
    sim = state_obj.pop("active_simulation", None)
    if not sim:
        return {"reverted": False, "message": "no active simulation to revert"}

    plan = SimulationPlan(
        file_path=sim["file_path"],
        backup_path=sim["backup_path"],
        original_word=sim["original_word"],
        replacement=sim["replacement"],
    )
    ok = revert(Path(project.workspace_path), plan)
    project.tour_state = state_obj
    await session.flush()
    return {"reverted": ok, "file_path": plan.file_path}


# ---------------------------------------------------------------------------
# CI YAML
# ---------------------------------------------------------------------------


@router.get("/{project_id}/tour/ci-yaml")
async def ci_yaml_preview(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    content = render(project_name=project.name, suite_globs=["suites/*.py"])
    return {"path": ".github/workflows/agentprdiff.yml", "content": content}


@router.post("/{project_id}/tour/commit-ci-yaml")
async def commit_ci_yaml(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if project.intake_mode != "git" or not project.workspace_path:
        raise HTTPException(
            status_code=400,
            detail="Commit & push only applies to git projects.",
        )
    content = render(project_name=project.name, suite_globs=["suites/*.py"])
    token = await _git_token_for_project(session, project_id)

    result = commit_and_push(
        workspace=Path(project.workspace_path),
        content=content,
        token=token,
    )

    if result.get("committed"):
        await update_tour_state(session, project, ci_committed=True)

    return result


async def _git_token_for_project(
    session: AsyncSession, project_id: int
) -> str | None:
    """Resolve a GIT_TOKEN secret if the user has one. project: scope wins."""
    from sqlalchemy import select
    rows = (
        await session.execute(
            select(models.Secret).where(
                models.Secret.name == "GIT_TOKEN",
                models.Secret.scope.in_(["global", f"project:{project_id}"]),
            )
        )
    ).scalars().all()
    # Prefer project-scoped.
    rows.sort(key=lambda r: 0 if r.scope.startswith("project:") else 1)
    for row in rows:
        try:
            return decrypt(row.encrypted_value)
        except CryptoError:
            continue
    return None

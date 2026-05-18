"""Compute tour state from real data + persisted user choices."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models


# ---------------------------------------------------------------------------
# Step taxonomy
# ---------------------------------------------------------------------------


STEP_DEFINITIONS: list[dict[str, str]] = [
    {"id": "connect", "label": "Connect your project"},
    {"id": "discover", "label": "Discover suites"},
    {"id": "scaffold", "label": "Scaffold a suite"},
    {"id": "configure-keys", "label": "Configure API keys"},
    {"id": "record-baseline", "label": "Record the first baseline"},
    {"id": "regression-demo", "label": "Catch a regression"},
    {"id": "ship-ci", "label": "Ship to CI"},
]


class StepStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    complete = "complete"
    skipped = "skipped"


# ---------------------------------------------------------------------------
# Persisted state shape
# ---------------------------------------------------------------------------


DEFAULT_TOUR_STATE: dict[str, Any] = {
    "skipped_steps": [],
    "ci_committed": False,
    "completed": False,
}


@dataclass(slots=True)
class TourState:
    skipped_steps: list[str] = field(default_factory=list)
    ci_committed: bool = False
    completed: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "TourState":
        raw = raw or DEFAULT_TOUR_STATE
        return cls(
            skipped_steps=list(raw.get("skipped_steps") or []),
            ci_committed=bool(raw.get("ci_committed", False)),
            completed=bool(raw.get("completed", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "skipped_steps": list(self.skipped_steps),
            "ci_committed": self.ci_committed,
            "completed": self.completed,
        }


@dataclass(slots=True)
class TourStep:
    id: str
    label: str
    status: StepStatus
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "status": self.status.value, "hint": self.hint}


@dataclass(slots=True)
class TourSnapshot:
    state: TourState
    steps: list[TourStep]
    active_step: str
    semantic_suites: list[str]  # names of suites that use semantic graders

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "active_step": self.active_step,
            "semantic_suites": self.semantic_suites,
        }


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------


async def compute_tour(session: AsyncSession, project: models.Project) -> TourSnapshot:
    """Inspect the project + its DB rows + (for git/zip) the workspace to
    determine each step's status. Pure read — no writes."""
    state = TourState.from_dict(project.tour_state)

    # Pull the data we need once.
    suite_rows = (
        await session.execute(
            select(models.Suite).where(models.Suite.project_id == project.id)
        )
    ).scalars().all()
    run_rows = (
        await session.execute(
            select(models.Run).where(models.Run.project_id == project.id)
        )
    ).scalars().all()
    secret_rows = (
        await session.execute(
            select(models.Secret).where(
                models.Secret.scope.in_(["global", f"project:{project.id}"])
            )
        )
    ).scalars().all()

    has_suites = len(suite_rows) > 0
    has_baseline = any(
        r.command == "record" and r.status == "succeeded" for r in run_rows
    )
    has_seen_regression = any(
        r.command in ("check", "review") and r.status == "regression" for r in run_rows
    )
    has_judge_key = any(
        s.name in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"}
        for s in secret_rows
    )

    # Which suites contain a ``semantic(...)`` grader call. Heuristic: source
    # text mentions ``semantic(``. For HTTP suites the definition is JSON, so
    # we look at grader specs.
    semantic_suites = _suites_using_semantic(project, suite_rows)

    skipped = set(state.skipped_steps)
    steps: list[TourStep] = []

    # 1. connect — always complete on this page (project exists).
    steps.append(TourStep(id="connect", label="Connect your project", status=StepStatus.complete))

    # 2. discover
    if has_suites:
        steps.append(TourStep(id="discover", label="Discover suites", status=StepStatus.complete,
                              hint=f"{len(suite_rows)} suite{'s' if len(suite_rows) != 1 else ''} found."))
    else:
        steps.append(TourStep(id="discover", label="Discover suites", status=StepStatus.in_progress,
                              hint="Studio didn't find any suites in your workspace yet."))

    # 3. scaffold — complete if step 2 is complete; in_progress otherwise.
    if has_suites:
        steps.append(TourStep(id="scaffold", label="Scaffold a suite",
                              status=StepStatus.complete,
                              hint="Skipped — your project already has suites."))
    else:
        steps.append(TourStep(id="scaffold", label="Scaffold a suite",
                              status=StepStatus.pending,
                              hint="Generate with AI, or use the deterministic *_cases.md scaffold."))

    # 4. configure-keys
    if not semantic_suites:
        steps.append(TourStep(id="configure-keys", label="Configure API keys",
                              status=StepStatus.complete,
                              hint="None of your suites use the semantic grader — no key needed."))
    elif has_judge_key:
        steps.append(TourStep(id="configure-keys", label="Configure API keys",
                              status=StepStatus.complete,
                              hint="LLM judge key is present."))
    elif "configure-keys" in skipped:
        steps.append(TourStep(id="configure-keys", label="Configure API keys",
                              status=StepStatus.skipped,
                              hint="Skipped. The semantic grader will fall back to keyword matching."))
    else:
        steps.append(TourStep(id="configure-keys", label="Configure API keys",
                              status=StepStatus.pending,
                              hint=f"{len(semantic_suites)} suite{'s' if len(semantic_suites) != 1 else ''} use the semantic grader."))

    # 5. record-baseline
    if has_baseline:
        steps.append(TourStep(id="record-baseline", label="Record the first baseline",
                              status=StepStatus.complete,
                              hint="At least one record run has succeeded."))
    else:
        steps.append(TourStep(id="record-baseline", label="Record the first baseline",
                              status=StepStatus.pending if has_suites else StepStatus.pending,
                              hint="Pick a suite and click Record."))

    # 6. regression-demo
    if has_seen_regression:
        steps.append(TourStep(id="regression-demo", label="Catch a regression",
                              status=StepStatus.complete,
                              hint="You've seen the diff viewer in action."))
    elif "regression-demo" in skipped:
        steps.append(TourStep(id="regression-demo", label="Catch a regression",
                              status=StepStatus.skipped,
                              hint="You skipped the demo."))
    else:
        steps.append(TourStep(id="regression-demo", label="Catch a regression",
                              status=StepStatus.pending if has_baseline else StepStatus.pending,
                              hint="Simulate a regression to see the diff viewer in action."))

    # 7. ship-ci
    if state.ci_committed:
        steps.append(TourStep(id="ship-ci", label="Ship to CI",
                              status=StepStatus.complete,
                              hint="agentprdiff.yml is committed in your repo."))
    elif "ship-ci" in skipped:
        steps.append(TourStep(id="ship-ci", label="Ship to CI",
                              status=StepStatus.skipped,
                              hint="You can grab the workflow YAML later."))
    else:
        steps.append(TourStep(id="ship-ci", label="Ship to CI",
                              status=StepStatus.pending,
                              hint="Copy or commit the GitHub Actions workflow."))

    # Active step = first pending or in_progress. Falls back to last step.
    active = next(
        (s.id for s in steps if s.status in (StepStatus.pending, StepStatus.in_progress)),
        steps[-1].id,
    )

    return TourSnapshot(
        state=state, steps=steps, active_step=active, semantic_suites=semantic_suites
    )


def _suites_using_semantic(
    project: models.Project, suites: list[models.Suite]
) -> list[str]:
    """Heuristically detect suites that use the ``semantic(...)`` grader."""
    out: list[str] = []
    workspace = Path(project.workspace_path) if project.workspace_path else None
    for s in suites:
        # HTTP suites: walk grader specs.
        if s.definition_json:
            for case in s.definition_json.get("cases") or []:
                for spec in case.get("expect") or []:
                    if isinstance(spec, dict) and spec.get("type") == "semantic":
                        out.append(s.name)
                        break
                else:
                    continue
                break
            continue
        # git/zip: peek at the suite file on disk.
        if workspace is None:
            continue
        try:
            text = (workspace / s.file_path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "semantic(" in text:
            out.append(s.name)
    return out


# ---------------------------------------------------------------------------
# Persisted-state writes
# ---------------------------------------------------------------------------


async def update_tour_state(
    session: AsyncSession,
    project: models.Project,
    *,
    skip: str | None = None,
    unskip: str | None = None,
    ci_committed: bool | None = None,
    completed: bool | None = None,
) -> TourState:
    state = TourState.from_dict(project.tour_state)
    if skip and skip not in state.skipped_steps:
        state.skipped_steps.append(skip)
    if unskip and unskip in state.skipped_steps:
        state.skipped_steps.remove(unskip)
    if ci_committed is not None:
        state.ci_committed = ci_committed
    if completed is not None:
        state.completed = completed
    project.tour_state = state.to_dict()
    await session.flush()
    return state

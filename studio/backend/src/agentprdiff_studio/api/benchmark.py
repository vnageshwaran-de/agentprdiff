"""Multi-model benchmarking — run one suite against two models sequentially.

POST /api/suites/{suite_id}/benchmark
  body: { models: [str, str] }
  → {
      models: [
        { model, cases: [{ case_name, passed, cost_usd, latency_ms, error }],
          total_cost_usd, total_latency_ms, cases_passing, cases_total },
        ...
      ],
      suite_name: str,
      run_at: ISO-8601
    }

Implementation
--------------

The engine adapters expose ``agentprdiff.adapters.set_default_model(model)``
which rewrites the ``model=`` kwarg on every patched ``create()`` call. The
Studio executor passes ``AGENTPRDIFF_MODEL_OVERRIDE`` to the subprocess; the
runner-shim reads it on startup and installs the override before the suite
imports the agent.

Benchmark flow per request:

1. Validate the two model identifiers are distinct, suite + project exist,
   project is git/zip mode (HTTP mode would need a parallel implementation
   in ``executor/http_run.py``; left as a 501 for now).
2. Create two Run rows with ``command="check"`` and ``model_override`` set.
3. Await ``execute_run`` for each run sequentially. (Parallel runs in the
   same process would race on the in-engine module-level override; a future
   refactor could fork per-leg subprocesses to recover concurrency, but the
   sequential cost is acceptable for the human-initiated benchmark case.)
4. Read the per-case results back out of the case_runs table and assemble
   the comparison payload.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session

router = APIRouter(prefix="/api/suites", tags=["benchmark"])


class BenchmarkRequest(BaseModel):
    models: list[str]


@router.post("/{suite_id}/benchmark")
async def benchmark(
    suite_id: int,
    body: BenchmarkRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if len(body.models) != 2:
        raise HTTPException(status_code=400, detail="Provide exactly two model identifiers")
    if body.models[0] == body.models[1]:
        raise HTTPException(status_code=400, detail="Pick two different models")
    if any(not m or not m.strip() for m in body.models):
        raise HTTPException(status_code=400, detail="Model identifiers must be non-empty strings")

    suite = await session.get(models.Suite, suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="suite not found")
    project = await session.get(models.Project, suite.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if project.intake_mode not in ("git", "zip"):
        raise HTTPException(
            status_code=501,
            detail=(
                "Benchmark for HTTP-mode projects isn't wired yet — needs the "
                "model override path threaded through executor/http_run.py."
            ),
        )

    # Create the two Run rows. Use "check" so we get pass/fail per case
    # against whatever baseline exists. Missing baselines are tolerated by
    # the engine and show up as no-baseline-yet without failing the run.
    run_ids: list[int] = []
    for model in body.models:
        run = models.Run(
            project_id=project.id,
            suite_id=suite.id,
            command="check",
            status="pending",
            model_override=model.strip(),
        )
        session.add(run)
        await session.flush()
        run_ids.append(run.id)
    await session.commit()

    # Execute sequentially. The model override is module-level inside the
    # engine adapter, so parallel runs in the same engine process would
    # race; sequential keeps each leg's recordings honest. The subprocess
    # walltime cap (settings.run_walltime_seconds) still applies per leg.
    # We import lazily to keep the API module import-time cheap.
    from ..executor.run import execute_run

    for run_id in run_ids:
        await execute_run(run_id)

    # Read back per-case results for each run.
    result_models: list[dict] = []
    for run_id, model in zip(run_ids, body.models):
        # Pull the run row for terminal status + counts.
        run = await session.get(models.Run, run_id)
        if run is None:
            # Shouldn't happen — execute_run wrote it. Defensive.
            result_models.append(_empty_model_result(model))
            continue

        case_rows = (
            await session.execute(
                select(models.CaseRun).where(models.CaseRun.run_id == run_id)
            )
        ).scalars().all()

        cases: list[dict[str, Any]] = []
        total_cost = 0.0
        total_latency = 0.0
        passing = 0
        for cr in case_rows:
            # "passed" maps the case status: "passed" → True, anything else
            # (failed / regression / error) → False. The frontend renders
            # pass/fail cells based on this bit; the regression vs failed
            # distinction is preserved via the regression count on the run.
            is_pass = cr.status == "passed"
            if is_pass:
                passing += 1
            cost = float(cr.cost_usd or 0)
            latency = float(cr.latency_ms or 0)
            total_cost += cost
            total_latency += latency
            err: str | None = None
            if cr.status == "error":
                # Surface the engine's error blurb if it landed in trace_json
                err = (cr.trace_json or {}).get("error") or "case errored"
            cases.append(
                {
                    "case_name": cr.case_name,
                    "passed": is_pass,
                    "cost_usd": cost,
                    "latency_ms": latency,
                    "error": err,
                }
            )

        result_models.append(
            {
                "model": model,
                "cases": cases,
                "total_cost_usd": total_cost,
                "total_latency_ms": total_latency,
                "cases_passing": passing,
                "cases_total": len(cases),
            }
        )

    return {
        "models": result_models,
        "suite_name": suite.name,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }


def _empty_model_result(model: str) -> dict:
    return {
        "model": model,
        "cases": [],
        "total_cost_usd": 0.0,
        "total_latency_ms": 0.0,
        "cases_passing": 0,
        "cases_total": 0,
    }

"""In-process HTTP-endpoint executor.

For ``intake_mode=http`` projects we don't have Python code to subprocess.
Instead we:

1. Load the suite's Studio-native definition (cases + grader specs).
2. For each case: render the request from ``project.http_config`` + the case
   input, call the endpoint via ``httpx``, time it, extract output via the
   configured ``output_path``.
3. Build a Trace, apply graders, diff against the DB-stored baseline (if any).
4. Persist a ``CaseRun`` row and stream events to the ``events`` table.

Baselines for HTTP projects live in the ``baselines`` table (not on disk),
since there's no workspace to write to.
"""

from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from agentprdiff.core import Trace
from agentprdiff.differ import diff_traces
from sqlalchemy import select

from ..db import models
from ..db.session import session_scope
from ..graders.specs import resolve_graders
from .events import record_event

_INPUT_PLACEHOLDER = "{{input}}"


async def execute_http_run(run_id: int) -> None:
    """Run an HTTP project's suite to completion."""

    # ---- load run, project, suite ------------------------------------------
    async with session_scope() as session:
        run = await session.get(models.Run, run_id)
        if run is None:
            return
        project = await session.get(models.Project, run.project_id)
        suite_row = await session.get(models.Suite, run.suite_id)
        if project is None or suite_row is None or project.intake_mode != "http":
            if run is not None:
                run.status = "error"
                run.finished_at = datetime.now(timezone.utc)
                run.stderr_tail = "project misconfigured for HTTP execution"
            return
        config = project.http_config or {}
        definition = suite_row.definition_json or {}
        suite_name = suite_row.name
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        await session.flush()

    # ---- run cases ---------------------------------------------------------
    overall_exit = 0
    cases = definition.get("cases", []) or []
    await record_event(
        run_id=run_id, level="info", kind="run_started",
        message=f"run {run_id} started ({len(cases)} cases)",
        payload={"cases": [str(c.get("name", "?")) for c in cases]},
    )
    async with httpx.AsyncClient(timeout=config.get("timeout_seconds", 30.0)) as client:
        for case in cases:
            await record_event(
                run_id=run_id, level="info", kind="case_started",
                message=f"{case.get('name')} started",
                payload={"case_name": str(case.get("name", "?"))},
            )
            try:
                graders = resolve_graders(case.get("expect") or [])
            except Exception as exc:  # noqa: BLE001
                await _persist_error_case(
                    run_id=run_id,
                    suite_name=suite_name,
                    case_name=str(case.get("name", "?")),
                    error=f"grader spec error: {exc}",
                )
                overall_exit = 1
                continue

            trace = await _run_one(
                client=client,
                config=config,
                suite_name=suite_name,
                case_name=str(case.get("name", "?")),
                case_input=case.get("input"),
            )
            grader_results = [g(trace) for g in graders]
            passed = all(r.passed for r in grader_results) and trace.error is None

            # Baseline handling — DB-backed.
            command = run.command
            delta = None
            regression: bool | None = None
            if command == "record":
                await _save_baseline_db(
                    project_id=project.id,
                    suite_id=suite_row.id,
                    case_name=trace.case_name,
                    run_id=run_id,
                    trace=trace,
                )
            else:
                baseline_trace = await _load_baseline_db(
                    project_id=project.id,
                    suite_id=suite_row.id,
                    case_name=trace.case_name,
                )
                baseline_results = (
                    [g(baseline_trace) for g in graders] if baseline_trace else None
                )
                delta = diff_traces(
                    baseline=baseline_trace,
                    current=trace,
                    current_results=grader_results,
                    baseline_results=baseline_results,
                )
                regression = bool(not passed or delta.has_regression)
                if regression:
                    overall_exit = 1

            await _persist_case_run(
                run_id=run_id,
                case_name=trace.case_name,
                trace=trace,
                delta=delta,
                passed=passed,
                regression=regression,
            )

    # ``review`` always succeeds at the CLI; mirror that here.
    final_status = "error"
    async with session_scope() as session:
        run = await session.get(models.Run, run_id)
        if run is None:
            return
        if run.command == "review":
            overall_exit = 0
        run.exit_code = overall_exit
        run.finished_at = datetime.now(timezone.utc)
        await _refresh_counts(session, run)
        final_status = run.status
    await record_event(
        run_id=run_id, level="info", kind="run_finished",
        message=f"run {run_id} finished with status={final_status}",
        payload={"status": final_status, "exit_code": overall_exit},
    )


# ---------------------------------------------------------------------------
# Request rendering + endpoint call
# ---------------------------------------------------------------------------


def _render(value: Any, case_input: Any) -> Any:
    """Substitute ``{{input}}`` placeholders inside a JSON template.

    Strings containing ``{{input}}`` are replaced; non-strings (numbers,
    bools, lists, dicts) are traversed recursively. A bare ``{{input}}``
    string substitutes the input value itself (preserving its type), so the
    user can pass non-string inputs.
    """
    if isinstance(value, str):
        if value == _INPUT_PLACEHOLDER:
            return case_input
        if _INPUT_PLACEHOLDER in value:
            return value.replace(_INPUT_PLACEHOLDER, _stringify(case_input))
        return value
    if isinstance(value, list):
        return [_render(v, case_input) for v in value]
    if isinstance(value, dict):
        return {k: _render(v, case_input) for k, v in value.items()}
    return value


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _extract_output(response_json: Any, output_path: str) -> Any:
    """Walk a dotted path into the response. Empty path = whole body."""
    if not output_path:
        return response_json
    cur: Any = response_json
    for segment in output_path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(segment)
        elif isinstance(cur, list):
            try:
                cur = cur[int(segment)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


async def _run_one(
    *,
    client: httpx.AsyncClient,
    config: dict[str, Any],
    suite_name: str,
    case_name: str,
    case_input: Any,
) -> Trace:
    """Call the endpoint once and produce a Trace."""
    method = config.get("method", "POST")
    url = config["url"]
    headers = config.get("headers") or {}
    body_template = config.get("body_template")
    output_path = config.get("output_path", "")

    start = time.perf_counter()
    request_body = _render(copy.deepcopy(body_template), case_input)

    try:
        if method == "GET":
            response = await client.request(method, url, headers=headers, params=request_body or None)
        else:
            response = await client.request(method, url, headers=headers, json=request_body)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        # Try JSON; fall back to text.
        try:
            payload = response.json()
        except ValueError:
            payload = response.text

        output = _extract_output(payload, output_path)

        error = None
        if response.status_code >= 400:
            error = f"HTTP {response.status_code}: {str(payload)[:200]}"

        return Trace(
            suite_name=suite_name,
            case_name=case_name,
            input=case_input,
            output=output,
            total_latency_ms=elapsed_ms,
            error=error,
            metadata={
                "http_status": response.status_code,
                "http_method": method,
                "http_url": url,
            },
        )
    except httpx.HTTPError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return Trace(
            suite_name=suite_name,
            case_name=case_name,
            input=case_input,
            output=None,
            total_latency_ms=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# DB-backed baseline read/write
# ---------------------------------------------------------------------------


async def _load_baseline_db(
    *, project_id: int, suite_id: int, case_name: str
) -> Trace | None:
    async with session_scope() as session:
        row = (
            await session.execute(
                select(models.Baseline)
                .where(
                    models.Baseline.project_id == project_id,
                    models.Baseline.suite_id == suite_id,
                    models.Baseline.case_name == case_name,
                )
                .order_by(models.Baseline.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    return Trace.model_validate(row.trace_json)


async def _save_baseline_db(
    *,
    project_id: int,
    suite_id: int,
    case_name: str,
    run_id: int,
    trace: Trace,
) -> None:
    async with session_scope() as session:
        # Find the next version. Baseline rows are append-only — we keep
        # history so the UI can show "this is the 3rd recorded baseline".
        prev = (
            await session.execute(
                select(models.Baseline.version)
                .where(
                    models.Baseline.project_id == project_id,
                    models.Baseline.suite_id == suite_id,
                    models.Baseline.case_name == case_name,
                )
                .order_by(models.Baseline.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        next_version = (prev or 0) + 1
        session.add(
            models.Baseline(
                project_id=project_id,
                suite_id=suite_id,
                case_name=case_name,
                version=next_version,
                trace_json=trace.model_dump(mode="json"),
                approved_by_run_id=run_id,
            )
        )


# ---------------------------------------------------------------------------
# CaseRun + summary writers
# ---------------------------------------------------------------------------


async def _persist_case_run(
    *,
    run_id: int,
    case_name: str,
    trace: Trace,
    delta,
    passed: bool,
    regression: bool | None,
) -> None:
    status = (
        "regression"
        if regression
        else ("passed" if passed else "failed")
    )
    case_row = models.CaseRun(
        run_id=run_id,
        case_name=case_name,
        status=status,
        trace_json=trace.model_dump(mode="json"),
        delta_json=delta.model_dump(mode="json") if delta is not None else None,
        cost_usd=float(trace.total_cost_usd),
        latency_ms=float(trace.total_latency_ms),
    )
    async with session_scope() as session:
        session.add(case_row)
    await record_event(
        run_id=run_id,
        level="info",
        kind="case_finished",
        message=f"{case_name} {'OK' if passed else 'FAIL'}",
        payload={
            "case_name": case_name,
            "status": status,
            "passed": passed,
            "regression": regression,
            "cost_usd": float(trace.total_cost_usd),
            "latency_ms": float(trace.total_latency_ms),
        },
    )


async def _persist_error_case(
    *,
    run_id: int,
    suite_name: str,
    case_name: str,
    error: str,
) -> None:
    """Persist a CaseRun for a case that errored before it could even run."""
    trace = Trace(suite_name=suite_name, case_name=case_name, input=None, error=error)
    async with session_scope() as session:
        session.add(
            models.CaseRun(
                run_id=run_id,
                case_name=case_name,
                status="error",
                trace_json=trace.model_dump(mode="json"),
                delta_json=None,
                cost_usd=0.0,
                latency_ms=0.0,
            )
        )
    await record_event(
        run_id=run_id, level="error", kind="case_error",
        message=error[:4000], payload={"case_name": case_name},
    )


async def _refresh_counts(session, run: models.Run) -> None:
    rows = (
        await session.execute(
            select(models.CaseRun).where(models.CaseRun.run_id == run.id)
        )
    ).scalars().all()
    run.cases_total = len(rows)
    run.cases_passed = sum(1 for r in rows if r.status == "passed")
    run.cases_regressed = sum(1 for r in rows if r.status == "regression")
    if run.exit_code == 0 and run.cases_regressed == 0:
        run.status = "succeeded"
    elif run.cases_regressed > 0:
        run.status = "regression"
    else:
        run.status = "failed"

"""Spawn and supervise a suite-running subprocess.

Studio launches the shim (``runner_shim.py``) inside the project's venv,
parses its JSONL stdout in real time, and writes:

* one ``Run`` row with terminal status / counts;
* one ``CaseRun`` row per case (with full trace + delta JSON);
* one ``Event`` row per stream event (so M5 can stream them via SSE).

The subprocess is hardened with:

* a walltime cap (``settings.run_walltime_seconds``) via asyncio,
* resource limits (CPU + AS) via ``preexec_fn`` on POSIX,
* its own process group, so we can kill the whole tree on timeout.

This module is async — it's awaited from the runs API handler which spawns
the run as a background task.
"""

from __future__ import annotations

import asyncio
import json
import os
import resource
import shutil
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import session_scope
from ..secrets import load_env_for_run
from ..settings import get_settings
from .events import record_event
from .venv import ensure_venv, venv_path, venv_python

SHIM_FILENAME = "runner_shim.py"


def _copy_shim(workspace: Path) -> Path:
    """Copy the bundled shim into the venv directory.

    Studio is installed in its own python; the shim has to live somewhere the
    project's venv can execute. The venv directory is a natural home.
    """
    src = Path(__file__).with_name("runner_shim.py")
    dst = venv_path(workspace) / SHIM_FILENAME
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


def _rlimit_preexec(cpu_seconds: int, memory_mb: int):
    """Build a ``preexec_fn`` that applies CPU + AS limits + isolates pgid.

    Skipped on platforms without RLIMIT_AS (Windows). Returns ``None`` there,
    which means subprocess.Popen won't set a preexec hook at all.
    """
    if os.name == "nt":  # pragma: no cover — Studio targets Linux/macOS
        return None

    def _apply() -> None:
        # Put us in our own process group so the supervisor can kill the
        # whole tree with os.killpg if we time out.
        os.setpgrp()
        # CPU time (soft = hard).
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        # Address space (memory). 1024 MB → bytes.
        mem = memory_mb * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        except (ValueError, OSError):
            # Some kernels (incl. macOS) don't honor RLIMIT_AS; ignore.
            pass

    return _apply


async def execute_run(run_id: int) -> None:
    """Run the suite associated with ``run_id`` to completion.

    Called as a fire-and-forget background task by the runs endpoint. All
    state — status transitions, per-case rows, events — is persisted as the
    process emits events.
    """
    settings = get_settings()

    # ---- load the run, project, suite in a short txn -----------------------
    async with session_scope() as session:
        run = await session.get(models.Run, run_id)
        if run is None:
            return
        project = await session.get(models.Project, run.project_id)
        suite = await session.get(models.Suite, run.suite_id)
        if project is None or suite is None or not project.workspace_path:
            await _mark_error(session, run, "project or suite not found, or no workspace")
            return

        workspace = Path(project.workspace_path)
        suite_file = workspace / suite.file_path
        command = run.command
        project_id = project.id  # keep for secrets injection below
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        await session.flush()

    # ---- provision venv (slow; do it outside the txn) ----------------------
    try:
        await ensure_venv(workspace)
        _copy_shim(workspace)
    except Exception as exc:
        async with session_scope() as session:
            run = await session.get(models.Run, run_id)
            if run is not None:
                await _mark_error(session, run, f"venv provisioning failed: {exc}")
        await record_event(
            run_id=run_id, level="error", kind="run_finished",
            message="venv provisioning failed", payload={"status": "error"},
        )
        return

    # ---- spawn the shim ----------------------------------------------------
    py = venv_python(workspace)
    shim = venv_path(workspace) / SHIM_FILENAME
    cmd = [str(py), str(shim), str(suite_file), command, "--run-id", str(run_id)]

    env = os.environ.copy()
    # PYTHONUNBUFFERED for prompt JSONL flushing.
    env["PYTHONUNBUFFERED"] = "1"
    # Multi-model benchmark hook: the benchmark endpoint sets
    # Run.model_override per run; the shim reads this env var on startup and
    # calls agentprdiff.adapters.set_default_model() so the agent's adapter
    # rewrites the model on every patched create() invocation.
    async with session_scope() as session:
        fresh_run = await session.get(models.Run, run_id)
        if fresh_run is not None and fresh_run.model_override:
            env["AGENTPRDIFF_MODEL_OVERRIDE"] = fresh_run.model_override
    # Layer on global + project-scoped secrets from the DB. Project-scoped
    # wins over global; both win over whatever Studio's own env had.
    async with session_scope() as session:
        env = await load_env_for_run(session, project_id=project_id, base_env=env)

    preexec = _rlimit_preexec(settings.run_cpu_seconds, settings.run_memory_mb)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=preexec,
        )
    except Exception as exc:
        async with session_scope() as session:
            run = await session.get(models.Run, run_id)
            if run is not None:
                await _mark_error(session, run, f"failed to spawn shim: {exc}")
        await record_event(
            run_id=run_id, level="error", kind="run_finished",
            message="shim spawn failed", payload={"status": "error"},
        )
        return

    stderr_chunks: list[bytes] = []
    try:
        await asyncio.wait_for(
            _consume(proc, run_id, stderr_chunks),
            timeout=settings.run_walltime_seconds,
        )
    except asyncio.TimeoutError:
        # Kill the whole process group; the supervisor finalize step below
        # will mark the run as errored.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        async with session_scope() as session:
            run = await session.get(models.Run, run_id)
            if run is not None:
                await _mark_error(
                    session,
                    run,
                    f"timed out after {settings.run_walltime_seconds}s",
                    exit_code=-1,
                )
        return

    # ---- finalize ----------------------------------------------------------
    stderr_tail = b"".join(stderr_chunks)[-4000:].decode("utf-8", errors="replace")
    final_status = "error"
    async with session_scope() as session:
        run = await session.get(models.Run, run_id)
        if run is None:
            return
        run.finished_at = datetime.now(timezone.utc)
        run.exit_code = proc.returncode
        run.stderr_tail = stderr_tail or None
        # Compute aggregate from the case_runs we wrote during streaming.
        await _refresh_counts(session, run)
        final_status = run.status
    await record_event(
        run_id=run_id,
        level="info",
        kind="run_finished",
        message=f"run {run_id} finished with status={final_status}",
        payload={"status": final_status, "exit_code": proc.returncode},
    )


# ---------------------------------------------------------------------------
# Streaming consumer
# ---------------------------------------------------------------------------


async def _consume(
    proc: asyncio.subprocess.Process,
    run_id: int,
    stderr_chunks: list[bytes],
) -> None:
    """Read stdout JSONL events and stderr in parallel."""

    async def _drain_stderr() -> None:
        assert proc.stderr is not None
        async for chunk in proc.stderr:
            stderr_chunks.append(chunk)

    async def _drain_stdout() -> None:
        assert proc.stdout is not None
        async for line_bytes in proc.stdout:
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                await _record_event(run_id, "warn", "non_json_output", line[:1000], None)
                continue
            await _handle_event(run_id, event)

    await asyncio.gather(_drain_stderr(), _drain_stdout(), proc.wait())


async def _handle_event(run_id: int, event: dict[str, Any]) -> None:
    kind = event.get("type")
    if kind == "case_finished":
        await _persist_case_run(run_id, event)
        # Compact payload for the bus — exclude the full trace/delta JSON,
        # which can be megabytes. SSE consumers fetch via /cases?include_…
        passed = event.get("passed")
        regression = event.get("regression")
        status = "regression" if regression else ("passed" if passed else "failed")
        await record_event(
            run_id=run_id,
            level="info",
            kind="case_finished",
            message=f"{event.get('suite')}::{event.get('case')} "
            f"{'OK' if passed else 'FAIL'}",
            payload={
                "case_name": event.get("case"),
                "suite_name": event.get("suite"),
                "passed": passed,
                "regression": regression,
                "status": status,
                "cost_usd": event.get("cost_usd"),
                "latency_ms": event.get("latency_ms"),
            },
        )
    elif kind == "case_started":
        await record_event(
            run_id=run_id, level="info", kind="case_started",
            message=f"{event.get('suite')}::{event.get('case')} started",
            payload={
                "case_name": event.get("case"),
                "suite_name": event.get("suite"),
            },
        )
    elif kind == "start":
        await record_event(
            run_id=run_id, level="info", kind="run_started",
            message=f"run {run_id} started",
            payload={"suites": event.get("suites")},
        )
    elif kind == "fatal":
        await record_event(
            run_id=run_id, level="error", kind="fatal",
            message=str(event.get("error", "")), payload=event,
        )
    else:
        # Misc structured events from the shim (e.g. ``suite_finished``,
        # ``done``) — keep them on the bus so the UI can show a tail, but
        # don't sprinkle them as separate event rows.
        await record_event(
            run_id=run_id, level="info", kind=str(kind or "event"),
            message="", payload=event,
        )


# ---------------------------------------------------------------------------
# DB writers
# ---------------------------------------------------------------------------


async def _persist_case_run(run_id: int, event: dict[str, Any]) -> None:
    status = (
        "regression"
        if event.get("regression")
        else ("passed" if event.get("passed") else "failed")
    )
    case_row = models.CaseRun(
        run_id=run_id,
        case_name=event.get("case", "?"),
        status=status,
        trace_json=event.get("trace") or {},
        delta_json=event.get("delta"),
        cost_usd=float(event.get("cost_usd") or 0.0),
        latency_ms=float(event.get("latency_ms") or 0.0),
    )
    async with session_scope() as session:
        session.add(case_row)


async def _record_event(
    run_id: int,
    level: str,
    kind: str,
    message: str,
    payload: dict[str, Any] | None,
) -> None:
    # Thin wrapper kept for call-site stability; the heavy lifting (persist
    # + publish) is in ``executor/events.py``.
    await record_event(
        run_id=run_id, level=level, kind=kind, message=message, payload=payload
    )


async def _mark_error(
    session: AsyncSession,
    run: models.Run,
    message: str,
    *,
    exit_code: int | None = None,
) -> None:
    run.status = "error"
    run.finished_at = datetime.now(timezone.utc)
    if exit_code is not None:
        run.exit_code = exit_code
    run.stderr_tail = (run.stderr_tail or "") + message + "\n"
    session.add(
        models.Event(
            run_id=run.id, level="error", kind="error", message=message[:4000]
        )
    )


async def _refresh_counts(session: AsyncSession, run: models.Run) -> None:
    """Roll case_runs into run.cases_* and pick a final run.status.

    The command matters for the *run-level* verdict:

    * ``check`` is the CI-gate command — any regressed case elevates the run
      status to ``"regression"`` so a pipeline can fail the build on it.
    * ``review`` is the local-iteration command — regressions still flag
      per-case (``CaseRun.status == "regression"``) so the user can see what
      changed, but the run itself reports ``"succeeded"`` to mirror the
      engine's ``review`` exit-0-always semantic.
    * ``record`` writes baselines and never emits regressions.

    Without the command branch, review and check produce identical run-level
    statuses and the UI treats them the same — collapses the very distinction
    the engine maintains via exit codes.
    """
    rows = (
        await session.execute(
            select(models.CaseRun).where(models.CaseRun.run_id == run.id)
        )
    ).scalars().all()

    run.cases_total = len(rows)
    run.cases_passed = sum(1 for r in rows if r.status == "passed")
    run.cases_regressed = sum(1 for r in rows if r.status == "regression")

    if run.exit_code is None:
        # consumer didn't see ``done``; leave as error.
        run.status = "error"
    elif run.command == "review":
        # Review is informational: regressions live on the case rows but
        # don't elevate the run to a build-fail state. A non-zero exit here
        # would still be a real failure (engine crash, missing import, etc.).
        run.status = "succeeded" if run.exit_code == 0 else "failed"
    elif run.exit_code == 0 and run.cases_regressed == 0:
        run.status = "succeeded"
    elif run.cases_regressed > 0:
        run.status = "regression"
    else:
        run.status = "failed"

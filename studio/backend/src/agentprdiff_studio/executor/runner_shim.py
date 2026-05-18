"""Subprocess shim that runs a suite inside a project's venv.

Studio invokes this as::

    <venv>/bin/python -m studio_runner_shim <suite_file> <command> [--run-id ...]

We can't actually use ``-m studio_runner_shim`` since this package isn't
installed in the project's venv (only the engine is). Instead, Studio
**copies** this file to ``<workspace>/.studio-venv/runner_shim.py`` at
provision time, and invokes it as::

    <venv>/bin/python <workspace>/.studio-venv/runner_shim.py <suite> <cmd> ...

This file is intentionally dependency-free beyond ``agentprdiff`` itself.

Protocol — every line of stdout is a JSON object with at least a ``"type"``
key. Studio reads them as they arrive:

    {"type": "start",         "suites": [...]}
    {"type": "case_started",  "suite": "...", "case": "..."}
    {"type": "case_finished", "suite": "...", "case": "...",
                              "passed": bool, "regression": bool|null,
                              "trace": {...}, "delta": {...}|null,
                              "cost_usd": float, "latency_ms": float}
    {"type": "suite_finished","suite": "...", "report": {...}}
    {"type": "done",          "exit_code": int}

On unhandled errors we emit ``{"type": "fatal", "error": "..."}`` and exit 2.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


def _emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("suite_file", help="Path to a .py file defining one or more Suites")
    parser.add_argument(
        "command", choices=["record", "check", "review"], help="Engine command to run"
    )
    parser.add_argument("--run-id", default="", help="Opaque run identifier (echoed back)")
    parser.add_argument(
        "--baseline-dir",
        default=".agentprdiff",
        help="Where the engine reads/writes baselines (relative to cwd)",
    )
    args = parser.parse_args(argv)

    try:
        from agentprdiff.differ import diff_traces
        from agentprdiff.loader import load_suites
        from agentprdiff.runner import Runner
        from agentprdiff.store import BaselineStore
    except Exception as exc:
        _emit(
            {
                "type": "fatal",
                "error": f"could not import agentprdiff engine: {exc}",
                "traceback": traceback.format_exc(),
            }
        )
        return 2

    # Multi-model benchmark hook: Studio sets AGENTPRDIFF_MODEL_OVERRIDE on
    # the subprocess env when running one leg of a benchmark. Install the
    # override on the engine adapter BEFORE the suite is loaded, so the
    # user's agent's instrument_client patch picks it up the first time it
    # calls .create().
    _model_override = os.environ.get("AGENTPRDIFF_MODEL_OVERRIDE", "").strip()
    if _model_override:
        try:
            from agentprdiff.adapters import set_default_model

            set_default_model(_model_override)
            _emit({"type": "log", "level": "info", "message": f"model override: {_model_override}"})
        except Exception as exc:
            # Engine is older than the set_default_model() hook — surface a
            # clean fatal so the supervisor records it rather than letting
            # the run silently use the suite's hard-coded model.
            _emit(
                {
                    "type": "fatal",
                    "error": (
                        "AGENTPRDIFF_MODEL_OVERRIDE was set but the engine "
                        "doesn't expose agentprdiff.adapters.set_default_model "
                        f"(reason: {exc}). Upgrade agentprdiff to a version "
                        "that includes the hook."
                    ),
                }
            )
            return 2

    suite_path = Path(args.suite_file).resolve()
    if not suite_path.exists():
        _emit({"type": "fatal", "error": f"suite file not found: {suite_path}"})
        return 2

    try:
        suites = load_suites(suite_path)
    except Exception as exc:
        _emit(
            {
                "type": "fatal",
                "error": f"failed to load suite file: {exc}",
                "traceback": traceback.format_exc(),
            }
        )
        return 2

    store = BaselineStore(args.baseline_dir)
    runner = Runner(store)

    _emit(
        {
            "type": "start",
            "run_id": args.run_id,
            "command": args.command,
            "suites": [{"name": s.name, "cases": [c.name for c in s.cases]} for s in suites],
        }
    )

    overall_exit = 0
    for suite in suites:
        # We re-implement the runner's loop here so we can stream per-case
        # events instead of waiting for the whole RunReport. This is
        # deliberately a thin copy of Runner._run — if the engine grows new
        # behavior (e.g. parallel cases) we revisit.
        store.ensure_initialized()
        run_id = store.fresh_run_id()
        from agentprdiff.core import run_agent

        suite_cases: list[dict[str, Any]] = []
        for case in suite.cases:
            _emit({"type": "case_started", "suite": suite.name, "case": case.name})
            trace = run_agent(
                suite.agent,
                suite_name=suite.name,
                case_name=case.name,
                input_value=case.input,
            )
            grader_results = [g(trace) for g in case.expect]
            passed = all(r.passed for r in grader_results) and trace.error is None

            delta = None
            regression: bool | None = None
            if args.command == "record":
                store.save_baseline(trace)
            else:
                store.save_run_trace(run_id, trace)
                baseline = store.load_baseline(suite.name, case.name)
                baseline_results = [g(baseline) for g in case.expect] if baseline else None
                delta = diff_traces(
                    baseline=baseline,
                    current=trace,
                    current_results=grader_results,
                    baseline_results=baseline_results,
                )
                regression = bool(not passed or (delta is not None and delta.has_regression))

            case_record = {
                "type": "case_finished",
                "suite": suite.name,
                "case": case.name,
                "passed": passed,
                "regression": regression,
                "trace": trace.model_dump(mode="json"),
                "delta": delta.model_dump(mode="json") if delta is not None else None,
                "grader_results": [r.model_dump(mode="json") for r in grader_results],
                "cost_usd": float(trace.total_cost_usd),
                "latency_ms": float(trace.total_latency_ms),
            }
            _emit(case_record)
            suite_cases.append(case_record)

            if regression:
                overall_exit = 1

        _emit(
            {
                "type": "suite_finished",
                "suite": suite.name,
                "cases_total": len(suite_cases),
                "cases_passed": sum(1 for c in suite_cases if c["passed"]),
                "cases_regressed": sum(1 for c in suite_cases if c.get("regression")),
            }
        )

    # ``review`` always succeeds at the CLI; mirror that here.
    if args.command == "review":
        overall_exit = 0

    _emit({"type": "done", "exit_code": overall_exit})
    return overall_exit


if __name__ == "__main__":
    raise SystemExit(main())

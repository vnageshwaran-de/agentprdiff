"""Run a Suite — the heart of agentprdiff.

Two modes:

* `record` — run each case, save the resulting `Trace` as the baseline, do
  not compare.
* `check` — run each case, load the baseline (if any), compute a `TraceDelta`,
  aggregate into a `RunReport`. Exit status at the CLI is driven by
  `RunReport.has_regression`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .core import GradeResult, Suite, Trace, run_agent
from .differ import TraceDelta, diff_traces
from .store import BaselineStore
from .trace_store import TraceStore


class CaseReport(BaseModel):
    """Per-case outcome within a RunReport."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    suite_name: str
    case_name: str
    trace: Trace
    grader_results: list[GradeResult]
    delta: TraceDelta | None = None

    @property
    def passed(self) -> bool:
        """All graders passed for the current run."""
        return all(r.passed for r in self.grader_results) and self.trace.error is None

    @property
    def has_regression(self) -> bool:
        """Whether this case regressed vs baseline. If there is no baseline,
        we treat a full pass as not-a-regression; any failing grader is
        treated as a regression (first-run-bad is still bad)."""
        if not self.passed:
            return True
        if self.delta is not None:
            return self.delta.has_regression
        return False


class RunReport(BaseModel):
    """Aggregate result of running a suite."""

    suite_name: str
    mode: str  # "record" or "check"
    case_reports: list[CaseReport] = Field(default_factory=list)

    @property
    def cases_passed(self) -> int:
        return sum(1 for c in self.case_reports if c.passed)

    @property
    def cases_total(self) -> int:
        return len(self.case_reports)

    @property
    def cases_regressed(self) -> int:
        return sum(1 for c in self.case_reports if c.has_regression)

    @property
    def has_regression(self) -> bool:
        return any(c.has_regression for c in self.case_reports)


class Runner:
    """Runs suites in record or check mode."""

    def __init__(self, store: BaselineStore | TraceStore) -> None:
        self.store = store

    # ------------------------------------------------------------------ api

    def record(self, suite: Suite) -> RunReport:
        return self._run(suite, mode="record")

    def check(self, suite: Suite) -> RunReport:
        return self._run(suite, mode="check")

    # --------------------------------------------------------------- impl

    def _run(self, suite: Suite, *, mode: str) -> RunReport:
        self.store.ensure_initialized()
        run_id = self.store.fresh_run_id()
        report = RunReport(suite_name=suite.name, mode=mode)

        for case in suite.cases:
            trace = run_agent(
                suite.agent,
                suite_name=suite.name,
                case_name=case.name,
                input_value=case.input,
            )
            grader_results = [g(trace) for g in case.expect]
            # Persist the current run either way (record = baseline, check = runs/).
            delta: TraceDelta | None = None
            if mode == "record":
                self.store.save_baseline(trace)
            else:
                self.store.save_run_trace(run_id, trace)
                baseline = self.store.load_baseline(suite.name, case.name)
                baseline_results = None
                if baseline is not None:
                    # Re-run graders against the baseline so the delta's
                    # per-assertion regression flags are accurate.
                    baseline_results = [g(baseline) for g in case.expect]
                delta = diff_traces(
                    baseline=baseline,
                    current=trace,
                    current_results=grader_results,
                    baseline_results=baseline_results,
                )

            report.case_reports.append(
                CaseReport(
                    suite_name=suite.name,
                    case_name=case.name,
                    trace=trace,
                    grader_results=grader_results,
                    delta=delta,
                )
            )
        return report

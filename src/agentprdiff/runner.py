"""Run a Suite — the heart of agentprdiff.

Two modes:

* `record` — run each case, save the resulting `Trace` as the baseline, do
  not compare.
* `check` — run each case, load the baseline (if any), compute a `TraceDelta`,
  aggregate into a `RunReport`. Exit status at the CLI is driven by
  `RunReport.has_regression`.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, ConfigDict, Field

from .core import Case, GradeResult, Suite, Trace, run_agent
from .differ import TraceDelta, diff_traces
from .store import BaselineStore
from .trace_store import TraceStore


def _execute_case(
    suite: Suite, case: Case, runs: int
) -> tuple[Trace, list[GradeResult], int]:
    """Run one case `runs` times and grade every attempt.

    Returns ``(representative_trace, representative_results, runs_passed)``.
    The representative attempt — used for baseline diffing and reporting —
    is the last fully-passing one when any exists (the behavior we accept),
    otherwise the last attempt (so the report shows what went wrong).
    """
    attempts: list[tuple[Trace, list[GradeResult], bool]] = []
    for _ in range(runs):
        attempt_trace = run_agent(
            suite.agent,
            suite_name=suite.name,
            case_name=case.name,
            input_value=case.input,
        )
        attempt_results = [g(attempt_trace) for g in case.expect]
        fully_passed = (
            all(r.passed for r in attempt_results) and attempt_trace.error is None
        )
        attempts.append((attempt_trace, attempt_results, fully_passed))

    runs_passed = sum(1 for _, _, ok in attempts if ok)
    trace, grader_results, _ = next(
        (a for a in reversed(attempts) if a[2]), attempts[-1]
    )
    return trace, grader_results, runs_passed


def _load_baseline_results(baseline: Trace) -> list[GradeResult] | None:
    """Parse grader results persisted in a baseline trace, if present.

    Returns None for legacy baselines (recorded before v0.5.0) or when the
    stored payload doesn't validate, so the caller can fall back to
    re-running graders.
    """
    raw = baseline.metadata.get("grader_results")
    if not isinstance(raw, list) or not raw:
        return None
    try:
        return [GradeResult.model_validate(item) for item in raw]
    except Exception:  # noqa: BLE001 — malformed payloads degrade to legacy path
        return None


class CaseReport(BaseModel):
    """Per-case outcome within a RunReport.

    With ``check --runs N`` (N > 1) the case is executed N times;
    ``trace`` and ``grader_results`` then belong to the *representative*
    attempt — the last fully-passing attempt when one exists, otherwise the
    last attempt — and ``runs_total`` / ``runs_passed`` carry the tally.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    suite_name: str
    case_name: str
    trace: Trace
    grader_results: list[GradeResult]
    delta: TraceDelta | None = None
    runs_total: int = 1
    runs_passed: int = 1
    min_pass_rate: float = 1.0

    @property
    def pass_rate(self) -> float:
        return self.runs_passed / self.runs_total if self.runs_total else 0.0

    @property
    def passed(self) -> bool:
        """Enough attempts fully passed to meet the case's min_pass_rate."""
        return self.pass_rate >= self.min_pass_rate

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
    """Runs suites in record or check mode.

    ``runs`` (default 1) makes `check` execute each case that many times and
    judge it against its ``min_pass_rate`` — the flakiness guard for
    stochastic agents. `record` always runs once: a baseline is a single
    known-good trace.

    ``concurrency`` (default 1) executes up to that many cases at once on a
    thread pool. Agent suites are I/O-bound (network calls to providers), so
    threads deliver near-linear wall-clock speedups — and they work for both
    sync and `async def` agents. Your agent callable must be safe to invoke
    from multiple threads when concurrency > 1. Storage writes, diffing, and
    report assembly stay on the calling thread, in suite order.
    """

    def __init__(
        self,
        store: BaselineStore | TraceStore,
        *,
        runs: int = 1,
        concurrency: int = 1,
    ) -> None:
        if runs < 1:
            raise ValueError(f"runs must be >= 1, got {runs}")
        if concurrency < 1:
            raise ValueError(f"concurrency must be >= 1, got {concurrency}")
        self.store = store
        self.runs = runs
        self.concurrency = concurrency

    # ------------------------------------------------------------------ api

    def record(self, suite: Suite) -> RunReport:
        return self._run(suite, mode="record")

    def check(self, suite: Suite) -> RunReport:
        return self._run(suite, mode="check")

    # --------------------------------------------------------------- impl

    def run_iter(self, suite: Suite, *, mode: str) -> Iterator[CaseReport]:
        """Stream one finished :class:`CaseReport` per case, in suite order.

        The public streaming API for integrators (Studio, dashboards,
        progress UIs) that want per-case results as they complete instead
        of waiting for the whole :class:`RunReport`. Semantics are
        identical to :meth:`record` / :meth:`check` — multi-run attempts,
        `min_pass_rate`, persisted baseline verdicts, and concurrency all
        apply; storage writes, diffing, and report assembly happen on the
        calling thread as each case's execution finishes.

        ``mode`` is ``"record"`` or ``"check"``.
        """
        if mode not in ("record", "check"):
            raise ValueError(f"mode must be 'record' or 'check', got {mode!r}")
        self.store.ensure_initialized()
        run_id = self.store.fresh_run_id()
        runs = self.runs if mode == "check" else 1

        # Execute every case's attempts — concurrently on a bounded thread
        # pool when concurrency > 1 (agent suites are I/O-bound, so threads
        # give near-linear wall-clock speedups for sync and async agents
        # alike). `executor.map` preserves suite order. Storage writes,
        # diffing, and report assembly happen on the calling thread.
        def execute(case: Case) -> tuple[Trace, list[GradeResult], int]:
            return _execute_case(suite, case, runs)

        if self.concurrency > 1 and len(suite.cases) > 1:
            workers = min(self.concurrency, len(suite.cases))
            executor = ThreadPoolExecutor(max_workers=workers)
            executed: Iterator[tuple[Trace, list[GradeResult], int]] = executor.map(
                execute, suite.cases
            )
        else:
            executor = None
            executed = (execute(case) for case in suite.cases)

        try:
            for case, (trace, grader_results, runs_passed) in zip(
                suite.cases, executed, strict=True
            ):
                # Persist grader results inside the trace so baselines are
                # truly frozen: check mode reads the recorded verdicts
                # instead of re-running graders against the baseline (which,
                # for semantic graders, would mean a paid + nondeterministic
                # LLM call against the baseline on every check).
                trace.metadata["grader_results"] = [
                    r.model_dump(mode="json") for r in grader_results
                ]
                # Persist the current run either way (record = baseline,
                # check = runs/).
                delta: TraceDelta | None = None
                if mode == "record":
                    self.store.save_baseline(trace)
                else:
                    self.store.save_run_trace(run_id, trace)
                    baseline = self.store.load_baseline(suite.name, case.name)
                    baseline_results = None
                    if baseline is not None:
                        baseline_results = _load_baseline_results(baseline)
                        if baseline_results is None:
                            # Legacy baseline (recorded before grader results
                            # were persisted): fall back to re-running the
                            # graders against the stored trace.
                            baseline_results = [g(baseline) for g in case.expect]
                    delta = diff_traces(
                        baseline=baseline,
                        current=trace,
                        current_results=grader_results,
                        baseline_results=baseline_results,
                    )

                yield CaseReport(
                    suite_name=suite.name,
                    case_name=case.name,
                    trace=trace,
                    grader_results=grader_results,
                    delta=delta,
                    runs_total=runs,
                    runs_passed=runs_passed,
                    min_pass_rate=case.min_pass_rate,
                )
        finally:
            if executor is not None:
                executor.shutdown(wait=True)

    def _run(self, suite: Suite, *, mode: str) -> RunReport:
        report = RunReport(suite_name=suite.name, mode=mode)
        report.case_reports.extend(self.run_iter(suite, mode=mode))
        return report

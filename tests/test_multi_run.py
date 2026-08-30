"""Tests for multi-run flakiness handling (`check --runs N` + min_pass_rate).

A stochastic agent that wobbles once shouldn't fail CI when the case says a
2-of-3 pass rate is acceptable — and a case that demands perfection (the
default min_pass_rate=1.0) should still fail on any wobble.
"""

from __future__ import annotations

import textwrap

import pytest
from click.testing import CliRunner

from agentprdiff.cli import main
from agentprdiff.core import case, suite
from agentprdiff.graders import contains
from agentprdiff.runner import Runner
from agentprdiff.store import BaselineStore


def _flaky_agent_factory(failures_first: int):
    """Agent that returns a failing output for its first `failures_first`
    calls, then a passing one."""
    calls = {"n": 0}

    def agent(inp):
        calls["n"] += 1
        return "oops" if calls["n"] <= failures_first else "refund processed"

    return agent, calls


def _suite(agent, min_pass_rate: float = 1.0):
    return suite(
        name="flaky",
        agent=agent,
        cases=[
            case(
                name="c1",
                input="x",
                expect=[contains("refund")],
                min_pass_rate=min_pass_rate,
            )
        ],
    )


class TestMultiRunSemantics:
    def test_tolerates_wobble_within_pass_rate(self, tmp_path):
        agent, calls = _flaky_agent_factory(failures_first=1)
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        # Baseline from a good run (agent already past its failure).
        Runner(store).record(_suite(lambda i: "refund processed"))

        report = Runner(store, runs=3).check(_suite(agent, min_pass_rate=0.6))
        cr = report.case_reports[0]
        assert calls["n"] == 3
        assert cr.runs_total == 3
        assert cr.runs_passed == 2
        assert cr.passed
        assert not cr.has_regression
        assert not report.has_regression

    def test_default_pass_rate_still_demands_perfection(self, tmp_path):
        agent, _ = _flaky_agent_factory(failures_first=1)
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        Runner(store).record(_suite(lambda i: "refund processed"))

        report = Runner(store, runs=3).check(_suite(agent))  # min_pass_rate=1.0
        cr = report.case_reports[0]
        assert cr.runs_passed == 2
        assert not cr.passed
        assert cr.has_regression

    def test_representative_is_a_passing_attempt(self, tmp_path):
        agent, _ = _flaky_agent_factory(failures_first=1)
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        Runner(store).record(_suite(lambda i: "refund processed"))

        report = Runner(store, runs=3).check(_suite(agent, min_pass_rate=0.5))
        cr = report.case_reports[0]
        # The failing first attempt returned "oops"; the representative must
        # be one of the passing attempts.
        assert cr.trace.output == "refund processed"
        assert all(r.passed for r in cr.grader_results)

    def test_all_attempts_failing_reports_failure(self, tmp_path):
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        Runner(store).record(_suite(lambda i: "refund processed"))

        report = Runner(store, runs=2).check(
            _suite(lambda i: "oops", min_pass_rate=0.5)
        )
        cr = report.case_reports[0]
        assert cr.runs_passed == 0
        assert not cr.passed
        assert cr.has_regression
        assert cr.trace.output == "oops"  # last attempt shown

    def test_record_ignores_runs(self, tmp_path):
        agent, calls = _flaky_agent_factory(failures_first=0)
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        Runner(store, runs=5).record(_suite(agent))
        assert calls["n"] == 1  # record always runs once

    def test_single_run_default_unchanged(self, tmp_path):
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        Runner(store).record(_suite(lambda i: "refund processed"))
        report = Runner(store).check(_suite(lambda i: "refund processed"))
        cr = report.case_reports[0]
        assert cr.runs_total == 1 and cr.runs_passed == 1
        assert cr.passed and not cr.has_regression

    def test_invalid_runs_rejected(self, tmp_path):
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        with pytest.raises(ValueError):
            Runner(store, runs=0)

    def test_invalid_min_pass_rate_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            case(name="c", input="x", expect=[], min_pass_rate=0.0)
        with pytest.raises(ValidationError):
            case(name="c", input="x", expect=[], min_pass_rate=1.5)


_FLAKY_SUITE_FILE = textwrap.dedent(
    """
    import itertools
    from agentprdiff import case, suite
    from agentprdiff.graders import contains

    _counter = itertools.count(1)

    def agent(inp):
        # Fails on the first call of the process, passes afterwards.
        return "oops" if next(_counter) == 1 else "refund processed"

    s = suite(
        name="flaky_cli",
        agent=agent,
        cases=[
            case(
                name="refund",
                input="please refund",
                expect=[contains("refund")],
                min_pass_rate=0.5,
            )
        ],
    )
    """
)


class TestRunsFlag:
    def test_check_runs_flag_end_to_end(self, tmp_path):
        suite_file = tmp_path / "suite.py"
        suite_file.write_text(_FLAKY_SUITE_FILE, encoding="utf-8")
        root = str(tmp_path / ".agentprdiff")
        runner = CliRunner()

        # Record: the process-first call fails the grader, but record mode
        # doesn't gate on grader outcomes — baseline gets saved either way.
        rec = runner.invoke(main, ["--root", root, "record", str(suite_file)])
        assert rec.exit_code == 0, rec.output

        # Fresh process simulation isn't possible in-process, so the agent's
        # counter is already past 1: with --runs 2 both attempts pass.
        chk = runner.invoke(
            main, ["--root", root, "check", str(suite_file), "--runs", "2"]
        )
        assert chk.exit_code == 0, chk.output
        assert "runs passed" in chk.output

    def test_runs_zero_rejected_by_cli(self, tmp_path):
        suite_file = tmp_path / "suite.py"
        suite_file.write_text(_FLAKY_SUITE_FILE, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--root", str(tmp_path / ".agentprdiff"), "check", str(suite_file), "--runs", "0"],
        )
        assert result.exit_code != 0

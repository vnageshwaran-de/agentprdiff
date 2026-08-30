"""Tests for Runner.run_iter — the public per-case streaming API."""

from __future__ import annotations

import pytest

from agentprdiff.core import case, suite
from agentprdiff.graders import contains
from agentprdiff.runner import Runner
from agentprdiff.store import BaselineStore


def _counting_agent():
    calls = {"n": 0}

    def agent(inp):
        calls["n"] += 1
        return "refund processed"

    return agent, calls


def _three_case_suite(agent):
    return suite(
        name="stream",
        agent=agent,
        cases=[
            case(name=f"c{i}", input=str(i), expect=[contains("refund")])
            for i in range(3)
        ],
    )


class TestRunIter:
    def test_streams_lazily_in_serial_mode(self, tmp_path):
        agent, calls = _counting_agent()
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        gen = Runner(store).run_iter(_three_case_suite(agent), mode="record")

        first = next(gen)
        # Only the first case's agent invocation has happened at this point —
        # the streaming contract integrators (Studio) rely on.
        assert calls["n"] == 1
        assert first.case_name == "c0"

        rest = list(gen)
        assert calls["n"] == 3
        assert [r.case_name for r in rest] == ["c1", "c2"]

    def test_record_via_run_iter_persists_frozen_baselines(self, tmp_path):
        agent, _ = _counting_agent()
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        list(Runner(store).run_iter(_three_case_suite(agent), mode="record"))
        baseline = store.load_baseline("stream", "c1")
        assert baseline is not None
        assert baseline.metadata.get("grader_results")  # frozen verdicts present

    def test_check_via_run_iter_matches_check(self, tmp_path):
        agent, _ = _counting_agent()
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        runner = Runner(store)
        runner.record(_three_case_suite(agent))

        streamed = list(runner.run_iter(_three_case_suite(agent), mode="check"))
        batch = runner.check(_three_case_suite(agent)).case_reports
        assert [r.case_name for r in streamed] == [r.case_name for r in batch]
        assert all(not r.has_regression for r in streamed)
        assert all(r.delta is not None and r.delta.baseline_exists for r in streamed)

    def test_run_iter_with_concurrency_and_runs(self, tmp_path):
        agent, calls = _counting_agent()
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        Runner(store).record(_three_case_suite(agent))
        calls["n"] = 0

        reports = list(
            Runner(store, runs=2, concurrency=3).run_iter(
                _three_case_suite(agent), mode="check"
            )
        )
        assert calls["n"] == 6  # 3 cases × 2 attempts
        assert [r.case_name for r in reports] == ["c0", "c1", "c2"]
        assert all(r.runs_total == 2 and r.runs_passed == 2 for r in reports)

    def test_invalid_mode_rejected(self, tmp_path):
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        agent, _ = _counting_agent()
        with pytest.raises(ValueError, match="mode"):
            next(Runner(store).run_iter(_three_case_suite(agent), mode="review"))

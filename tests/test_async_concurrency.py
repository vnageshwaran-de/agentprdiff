"""Tests for async agent support and concurrent case execution."""

from __future__ import annotations

import asyncio
import threading

import pytest

from agentprdiff.core import Trace, case, run_agent, suite
from agentprdiff.graders import contains
from agentprdiff.runner import Runner
from agentprdiff.store import BaselineStore

# ---------------------------------------------------------------------------
# Async agents.
# ---------------------------------------------------------------------------


class TestAsyncAgents:
    def test_async_agent_output_resolved(self):
        async def agent(inp):
            await asyncio.sleep(0)
            return f"refund for {inp}"

        trace = run_agent(agent, suite_name="s", case_name="c", input_value="x")
        assert trace.output == "refund for x"
        assert trace.error is None
        assert trace.total_latency_ms > 0

    def test_async_agent_returning_trace_tuple(self):
        async def agent(inp):
            t = Trace(suite_name="", case_name="", input=inp, output=None)
            return "refund done", t

        trace = run_agent(agent, suite_name="s", case_name="c", input_value="x")
        assert trace.output == "refund done"
        assert trace.suite_name == "s" and trace.case_name == "c"

    def test_async_agent_exception_captured(self):
        async def agent(inp):
            raise RuntimeError("boom")

        trace = run_agent(agent, suite_name="s", case_name="c", input_value="x")
        assert trace.error == "RuntimeError: boom"
        assert trace.output is None

    def test_async_agent_from_inside_running_loop(self):
        """Jupyter / async test-runner scenario: run_agent is called while an
        event loop is already running on the calling thread."""

        async def agent(inp):
            await asyncio.sleep(0)
            return "refund ok"

        async def driver():
            return run_agent(agent, suite_name="s", case_name="c", input_value="x")

        trace = asyncio.run(driver())
        assert trace.output == "refund ok"
        assert trace.error is None

    def test_async_agent_through_runner(self, tmp_path):
        async def agent(inp):
            await asyncio.sleep(0)
            return "refund processed"

        s = suite(
            name="async_suite",
            agent=agent,
            cases=[case(name="c1", input="x", expect=[contains("refund")])],
        )
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        Runner(store).record(s)
        report = Runner(store).check(s)
        assert not report.has_regression


# ---------------------------------------------------------------------------
# Concurrent case execution.
# ---------------------------------------------------------------------------


class _ConcurrencyProbe:
    """Agent that records the maximum number of simultaneous invocations."""

    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.barrier_time = 0.05

    def __call__(self, inp):
        import time

        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.barrier_time)
        with self.lock:
            self.active -= 1
        return "refund processed"


def _four_case_suite(agent):
    return suite(
        name="wide",
        agent=agent,
        cases=[
            case(name=f"c{i}", input=str(i), expect=[contains("refund")])
            for i in range(4)
        ],
    )


class TestConcurrency:
    def test_cases_overlap_with_concurrency(self, tmp_path):
        probe = _ConcurrencyProbe()
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        Runner(store, concurrency=4).record(_four_case_suite(probe))
        assert probe.max_active > 1  # cases actually ran simultaneously

    def test_serial_by_default(self, tmp_path):
        probe = _ConcurrencyProbe()
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        Runner(store).record(_four_case_suite(probe))
        assert probe.max_active == 1

    def test_report_order_matches_suite_order(self, tmp_path):
        probe = _ConcurrencyProbe()
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        Runner(store, concurrency=4).record(_four_case_suite(probe))
        report = Runner(store, concurrency=4).check(_four_case_suite(probe))
        assert [cr.case_name for cr in report.case_reports] == ["c0", "c1", "c2", "c3"]
        assert not report.has_regression

    def test_concurrent_check_with_multi_run(self, tmp_path):
        probe = _ConcurrencyProbe()
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        Runner(store).record(_four_case_suite(lambda i: "refund processed"))
        report = Runner(store, concurrency=4, runs=2).check(_four_case_suite(probe))
        assert all(cr.runs_total == 2 and cr.runs_passed == 2 for cr in report.case_reports)
        assert not report.has_regression

    def test_async_agents_under_concurrency(self, tmp_path):
        async def agent(inp):
            await asyncio.sleep(0.01)
            return "refund processed"

        store = BaselineStore(root=tmp_path / ".agentprdiff")
        Runner(store, concurrency=4).record(_four_case_suite(agent))
        report = Runner(store, concurrency=4).check(_four_case_suite(agent))
        assert not report.has_regression

    def test_invalid_concurrency_rejected(self, tmp_path):
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        with pytest.raises(ValueError):
            Runner(store, concurrency=0)

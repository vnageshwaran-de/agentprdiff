# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the pluggable TraceStore interface and InMemoryTraceStore."""

from __future__ import annotations

import pytest

from agentprdiff.core import Trace, case, suite
from agentprdiff.runner import Runner
from agentprdiff.trace_store import InMemoryTraceStore, TraceStore


def _trace(suite_name: str = "s", case_name: str = "c", output: str = "ok") -> Trace:
    return Trace(suite_name=suite_name, case_name=case_name, input="x", output=output)


class TestInMemoryTraceStore:
    def test_save_and_load_baseline(self):
        store = InMemoryTraceStore()
        t = _trace()
        store.save_baseline(t)
        loaded = store.load_baseline("s", "c")
        assert loaded is not None
        assert loaded.output == "ok"

    def test_load_missing_baseline_returns_none(self):
        store = InMemoryTraceStore()
        assert store.load_baseline("nosuite", "nocase") is None

    def test_save_run_trace(self):
        store = InMemoryTraceStore()
        t = _trace()
        store.save_run_trace("run-001", t)
        assert ("run-001", "s", "c") in store._runs

    def test_fresh_run_id_is_unique(self):
        store = InMemoryTraceStore()
        ids = {store.fresh_run_id() for _ in range(20)}
        assert len(ids) == 20

    def test_ensure_initialized_is_noop(self):
        store = InMemoryTraceStore()
        store.ensure_initialized()  # should not raise


class TestTraceStoreProtocol:
    def test_abstract_methods_enforced(self):
        with pytest.raises(TypeError):
            TraceStore()  # type: ignore[abstract]

    def test_custom_store_subclass(self):
        class EchoStore(TraceStore):
            def __init__(self):
                self.saved = []

            def save_baseline(self, trace):
                self.saved.append(("baseline", trace))

            def load_baseline(self, suite_name, case_name):
                return None

            def save_run_trace(self, run_id, trace):
                self.saved.append(("run", run_id, trace))

        store = EchoStore()
        t = _trace()
        store.save_baseline(t)
        assert store.saved[0][0] == "baseline"


class TestRunnerWithInMemoryStore:
    def test_record_then_check_no_regression(self):
        store = InMemoryTraceStore()
        runner = Runner(store=store)

        def my_agent(inp):
            return f"response to {inp}"

        s = suite(
            "demo",
            agent=my_agent,
            cases=[case("greet", input="hello", expect=[])],
        )
        runner.record(s)
        report = runner.check(s)
        assert not report.has_regression

    def test_check_without_baseline_passes_if_graders_pass(self):
        store = InMemoryTraceStore()
        runner = Runner(store=store)

        from agentprdiff.graders import contains

        s = suite(
            "demo",
            agent=lambda inp: "the answer is 42",
            cases=[case("answer", input="q", expect=[contains("42")])],
        )
        report = runner.check(s)
        assert report.cases_passed == 1

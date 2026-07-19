# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pluggable trace store interface.

The built-in :class:`~agentprdiff.store.BaselineStore` writes traces to the
local filesystem.  ``TraceStore`` is a simple protocol that lets teams route
traces to any backend — a database, object storage, or a remote telemetry
service — without modifying the runner.

Usage::

    from agentprdiff.trace_store import TraceStore
    from agentprdiff.core import Trace

    class MyDBStore(TraceStore):
        def save_baseline(self, trace: Trace) -> None:
            db.upsert(suite=trace.suite_name, case=trace.case_name, data=trace.model_dump())

        def load_baseline(self, suite_name: str, case_name: str) -> Trace | None:
            row = db.get(suite=suite_name, case=case_name)
            return Trace.model_validate(row) if row else None

        def save_run_trace(self, run_id: str, trace: Trace) -> None:
            db.insert(run_id=run_id, data=trace.model_dump())

    runner = Runner(store=MyDBStore())

Implementations only need to satisfy the three-method interface.  The
``ensure_initialized`` and ``fresh_run_id`` methods have sensible defaults and
are optional to override.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from .core import Trace


class TraceStore(ABC):
    """Abstract base class for trace persistence backends.

    Subclass this to plug in any storage backend.  The :class:`Runner` only
    calls the three abstract methods plus :meth:`ensure_initialized` and
    :meth:`fresh_run_id`.
    """

    @abstractmethod
    def save_baseline(self, trace: Trace) -> None:
        """Persist *trace* as the canonical baseline for its suite+case."""

    @abstractmethod
    def load_baseline(self, suite_name: str, case_name: str) -> Trace | None:
        """Return the stored baseline, or ``None`` if none exists yet."""

    @abstractmethod
    def save_run_trace(self, run_id: str, trace: Trace) -> None:
        """Persist a trace produced during a ``check`` run."""

    def ensure_initialized(self) -> None:  # noqa: B027
        """Called once per runner invocation before any traces are written.

        Override to create tables, buckets, or directories on first use.
        The default implementation is a no-op.
        """

    def fresh_run_id(self) -> str:
        """Return a unique identifier for the current run.

        Override to use your own ID scheme (e.g. a database sequence).
        The default returns a random 12-hex-character string.
        """
        return uuid.uuid4().hex[:12]


class InMemoryTraceStore(TraceStore):
    """In-memory store for testing and ephemeral pipelines.

    Baselines and run traces are stored in plain dicts and lost when the
    process exits.  Useful in CI when you want to run ``check`` immediately
    after ``record`` without touching the filesystem.

    Example::

        store = InMemoryTraceStore()
        runner = Runner(store=store)
        runner.record(my_suite)
        report = runner.check(my_suite)
    """

    def __init__(self) -> None:
        self._baselines: dict[tuple[str, str], Trace] = {}
        self._runs: dict[tuple[str, str, str], Trace] = {}

    def save_baseline(self, trace: Trace) -> None:
        self._baselines[(trace.suite_name, trace.case_name)] = trace

    def load_baseline(self, suite_name: str, case_name: str) -> Trace | None:
        return self._baselines.get((suite_name, case_name))

    def save_run_trace(self, run_id: str, trace: Trace) -> None:
        self._runs[(run_id, trace.suite_name, trace.case_name)] = trace

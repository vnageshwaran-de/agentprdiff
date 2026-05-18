"""SQLAlchemy 2.0 (async) models.

The schema is deliberately conservative for M1:

* ``Project`` — one row per repo / upload / endpoint the user has connected.
* ``Suite`` — discovered by walking the project for files that look like
  agentprdiff suites. Refreshed on every ``POST /api/projects/{id}/sync``.
* ``Run`` — a single invocation of ``record`` / ``check`` / ``review``.
* ``CaseRun`` — per-case result inside a run, with the full Trace JSON and the
  TraceDelta JSON (for ``check``).
* ``Baseline`` — placeholder for M6. In M1 baselines still live in
  ``<repo>/.agentprdiff/baselines/`` on disk; the engine writes them there.
* ``Secret`` — placeholder for M3. Encrypted env values for run subprocesses.
* ``Event`` — timeline rows for live progress (M5 will stream these via SSE).

All ``json_*`` columns store pydantic ``.model_dump(mode="json")`` payloads.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)

    # "git" | "zip" | "http"  (M1 wires git; M2 adds zip + http)
    intake_mode: Mapped[str] = mapped_column(String(16))

    # For git: the clone URL. For zip: the original filename. For http: the endpoint URL.
    source: Mapped[str] = mapped_column(Text)

    # For git: branch / ref to track. NULL = default branch.
    git_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Absolute path on disk where the workspace lives:
    # data_dir/projects/<id>/repo for git, /upload for zip. NULL for http.
    workspace_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # HTTP-mode only: {method, url, headers, body_template, output_path}.
    # See ``intake/http.py`` for validation; ``executor/http_run.py`` for use.
    http_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Studio Tour state. Most step completion is computed from real data
    # (do suites exist? has a record run succeeded? does a recent check show
    # a regression?), so this column only persists deliberate user choices:
    #   {"skipped_steps": ["regression-demo"], "ci_committed": false,
    #    "completed": false}
    tour_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Most recent successful sync timestamp.
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    suites: Mapped[list["Suite"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    runs: Mapped[list["Run"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# Suite
# ---------------------------------------------------------------------------


class Suite(Base):
    __tablename__ = "suites"
    __table_args__ = (
        UniqueConstraint("project_id", "file_path", "name", name="uq_suite_project_path_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))

    # The suite's ``name`` attribute (from the Suite() call inside the file).
    name: Mapped[str] = mapped_column(String(200))

    # Path *relative to the project workspace*, e.g. "suites/billing.py".
    # For HTTP intake there's no file; we use a synthetic path like "http://".
    file_path: Mapped[str] = mapped_column(Text)

    # Number of cases the loader found. Useful for the UI; refreshed on sync.
    case_count: Mapped[int] = mapped_column(Integer, default=0)

    # HTTP-mode: the Studio-native suite spec — see ``intake/http.py``.
    # NULL for git/zip (those load suites from disk via the engine).
    definition_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project: Mapped[Project] = relationship(back_populates="suites")
    runs: Mapped[list["Run"]] = relationship(back_populates="suite")


# ---------------------------------------------------------------------------
# Run + CaseRun
# ---------------------------------------------------------------------------


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    suite_id: Mapped[int] = mapped_column(ForeignKey("suites.id", ondelete="CASCADE"))

    # "record" | "check" | "review"
    command: Mapped[str] = mapped_column(String(16))

    # "pending" | "running" | "succeeded" | "failed" | "regression" | "error"
    # - succeeded: command finished, no regression (check / review) or recording done (record)
    # - regression: check completed but at least one case regressed
    # - failed: subprocess exited non-zero unexpectedly (engine crash, import error...)
    # - error: Studio itself couldn't launch the run (venv broken, suite missing, ...)
    status: Mapped[str] = mapped_column(String(16), default="pending")

    # Optional --case / --skip filters passed through to the engine.
    case_filter: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Summary counts populated when the run finishes.
    cases_total: Mapped[int] = mapped_column(Integer, default=0)
    cases_passed: Mapped[int] = mapped_column(Integer, default=0)
    cases_regressed: Mapped[int] = mapped_column(Integer, default=0)

    # Whatever the engine printed on stderr — handy when status=error/failed.
    stderr_tail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional model override (used by Studio's multi-model benchmark).
    # When set, the executor passes AGENTPRDIFF_MODEL_OVERRIDE into the
    # subprocess env; the runner_shim reads it on startup and calls
    # ``agentprdiff.adapters.set_default_model()`` before the user's suite
    # imports the agent. NULL for ordinary runs.
    model_override: Mapped[str | None] = mapped_column(String(200), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project: Mapped[Project] = relationship(back_populates="runs")
    suite: Mapped[Suite] = relationship(back_populates="runs")
    case_runs: Mapped[list["CaseRun"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    events: Mapped[list["Event"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class CaseRun(Base):
    __tablename__ = "case_runs"
    __table_args__ = (
        Index("ix_caserun_run", "run_id"),
        UniqueConstraint("run_id", "case_name", name="uq_caserun_run_case"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))

    case_name: Mapped[str] = mapped_column(String(200))

    # "passed" | "failed" | "regression" | "error"
    status: Mapped[str] = mapped_column(String(16))

    # ``Trace.model_dump(mode="json")``
    trace_json: Mapped[dict] = mapped_column(JSON)

    # ``TraceDelta.model_dump(mode="json")``  — NULL for ``record`` runs.
    delta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    cost_usd: Mapped[float] = mapped_column(default=0.0)
    latency_ms: Mapped[float] = mapped_column(default=0.0)

    run: Mapped[Run] = relationship(back_populates="case_runs")


# ---------------------------------------------------------------------------
# Baselines  (M6 — schema reserved now so we don't migrate later)
# ---------------------------------------------------------------------------


class Baseline(Base):
    __tablename__ = "baselines"
    __table_args__ = (
        Index("ix_baseline_lookup", "project_id", "suite_id", "case_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    suite_id: Mapped[int] = mapped_column(ForeignKey("suites.id", ondelete="CASCADE"))

    case_name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(default=1)

    trace_json: Mapped[dict] = mapped_column(JSON)
    approved_by_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# Secrets  (M3)
# ---------------------------------------------------------------------------


class Secret(Base):
    __tablename__ = "secrets"
    __table_args__ = (
        UniqueConstraint("name", "scope", name="uq_secret_name_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))  # e.g. "OPENAI_API_KEY"
    encrypted_value: Mapped[bytes] = mapped_column()
    # "global" or "project:<id>"
    scope: Mapped[str] = mapped_column(String(64), default="global")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# Events  (timeline for live progress; M5 streams these)
# ---------------------------------------------------------------------------


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_event_run_ts", "run_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    level: Mapped[str] = mapped_column(String(16), default="info")  # info | warn | error
    kind: Mapped[str] = mapped_column(String(32))  # "log" | "case_started" | "case_finished" | ...
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    run: Mapped[Run] = relationship(back_populates="events")

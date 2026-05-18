"""Pydantic request/response schemas for the public API.

Kept in one place so the eventual TypeScript client can codegen from a single
module.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------- projects


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # Zip projects come through POST /api/projects/upload (multipart).
    # JSON-body intake is git (clone) or http (endpoint).
    intake_mode: Literal["git", "http"]
    # For git: clone URL. For http: any non-empty placeholder; the real
    # endpoint url lives under http_config.url. We keep source non-null
    # because it's the user-visible identifier in the UI.
    source: str = Field(min_length=1)
    git_ref: str | None = None
    http_config: dict[str, Any] | None = None


class ProjectOut(BaseModel):
    id: int
    name: str
    intake_mode: str
    source: str
    git_ref: str | None
    workspace_path: str | None
    http_config: dict[str, Any] | None
    last_synced_at: datetime | None
    created_at: datetime


class SuiteOut(BaseModel):
    id: int
    project_id: int
    name: str
    file_path: str
    case_count: int
    discovered_at: datetime


class HttpSuiteCreate(BaseModel):
    """Studio-native suite definition for HTTP-mode projects."""

    name: str = Field(min_length=1, max_length=200)
    cases: list[dict[str, Any]] = Field(min_length=1)


class HttpSuiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    cases: list[dict[str, Any]] | None = None


class SyncResult(BaseModel):
    project_id: int
    suites_found: int
    suites: list[SuiteOut]


# ------------------------------------------------------------------------ runs


class RunCreate(BaseModel):
    project_id: int
    suite_id: int
    command: Literal["record", "check", "review"]
    case_filter: str | None = None


class RunOut(BaseModel):
    id: int
    project_id: int
    suite_id: int
    command: str
    status: str
    case_filter: str | None
    started_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    cases_total: int
    cases_passed: int
    cases_regressed: int
    stderr_tail: str | None
    created_at: datetime


class CaseRunOut(BaseModel):
    id: int
    run_id: int
    case_name: str
    status: str
    cost_usd: float
    latency_ms: float
    # Full payloads — opt-in via include_trace/include_delta query flags.
    trace: dict[str, Any] | None = None
    delta: dict[str, Any] | None = None

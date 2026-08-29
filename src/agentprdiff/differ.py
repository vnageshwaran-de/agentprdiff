"""Trace diffing.

Given a baseline `Trace` and a current `Trace`, compute a `TraceDelta` that
summarizes what changed. The delta is the unit of CI output:

* assertion changes (which graders flipped pass->fail or fail->pass)
* cost / latency / token deltas
* tool-call sequence changes
* output change (textual diff)

The `regressions` property is the canonical "should CI fail?" signal.
"""

from __future__ import annotations

import difflib
from typing import Any

from pydantic import BaseModel, Field

from .core import GradeResult, Trace


class AssertionChange(BaseModel):
    grader_name: str
    grader_id: str | None = None
    baseline_passed: bool | None  # None = grader didn't exist in baseline
    current_passed: bool
    current_reason: str = ""

    @property
    def is_regression(self) -> bool:
        """A regression = was passing (or absent), now failing."""
        return (self.baseline_passed is None or self.baseline_passed) and not self.current_passed

    @property
    def is_improvement(self) -> bool:
        return bool(self.baseline_passed is False and self.current_passed)


class TraceDelta(BaseModel):
    """Summary of the delta between baseline and current trace for one case."""

    suite_name: str
    case_name: str

    baseline_exists: bool
    assertion_changes: list[AssertionChange] = Field(default_factory=list)

    cost_delta_usd: float = 0.0
    latency_delta_ms: float = 0.0
    prompt_tokens_delta: int = 0
    completion_tokens_delta: int = 0

    tool_sequence_changed: bool = False
    baseline_tool_sequence: list[str] = Field(default_factory=list)
    current_tool_sequence: list[str] = Field(default_factory=list)

    output_changed: bool = False
    output_diff: str = ""

    current_error: str | None = None
    baseline_error: str | None = None

    @property
    def regressions(self) -> list[AssertionChange]:
        return [c for c in self.assertion_changes if c.is_regression]

    @property
    def improvements(self) -> list[AssertionChange]:
        return [c for c in self.assertion_changes if c.is_improvement]

    @property
    def has_regression(self) -> bool:
        return bool(self.regressions) or (self.current_error is not None and self.baseline_error is None)


def diff_traces(
    *,
    baseline: Trace | None,
    current: Trace,
    current_results: list[GradeResult],
    baseline_results: list[GradeResult] | None = None,
) -> TraceDelta:
    """Build a TraceDelta.

    `current_results` are the grader results from running the cases's expects
    against the `current` trace.

    `baseline_results` are optional. If provided, they're used to determine
    per-assertion regressions directly. If omitted, we replay the same grader
    names from `current_results` against the baseline by looking them up by
    `grader_name` in the baseline metadata (not typically populated, so in
    practice you should pass them).
    """
    delta = TraceDelta(
        suite_name=current.suite_name,
        case_name=current.case_name,
        baseline_exists=baseline is not None,
        current_error=current.error,
        baseline_error=baseline.error if baseline else None,
    )

    # Assertions are matched baseline<->current by stable grader_id when one
    # was provided (the `id=` argument on grader factories), falling back to
    # the display name. Matching by name alone means renaming an argument or
    # rewording a semantic rubric shows up as a removed + added assertion —
    # a documented source of false regressions.
    baseline_by_key: dict[str, bool] = {}
    if baseline_results:
        for r in baseline_results:
            baseline_by_key[r.grader_id or r.grader_name] = r.passed

    for r in current_results:
        delta.assertion_changes.append(
            AssertionChange(
                grader_name=r.grader_name,
                grader_id=r.grader_id,
                baseline_passed=baseline_by_key.get(r.grader_id or r.grader_name),
                current_passed=r.passed,
                current_reason=r.reason,
            )
        )

    if baseline is not None:
        delta.cost_delta_usd = current.total_cost_usd - baseline.total_cost_usd
        delta.latency_delta_ms = current.total_latency_ms - baseline.total_latency_ms
        delta.prompt_tokens_delta = (
            current.total_prompt_tokens - baseline.total_prompt_tokens
        )
        delta.completion_tokens_delta = (
            current.total_completion_tokens - baseline.total_completion_tokens
        )
        delta.baseline_tool_sequence = [c.name for c in baseline.tool_calls]
        delta.current_tool_sequence = [c.name for c in current.tool_calls]
        delta.tool_sequence_changed = (
            delta.baseline_tool_sequence != delta.current_tool_sequence
        )
        baseline_out = _to_str(baseline.output)
        current_out = _to_str(current.output)
        if baseline_out != current_out:
            delta.output_changed = True
            delta.output_diff = _unified_diff(baseline_out, current_out)

    return delta


def _to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return ""


def _unified_diff(a: str, b: str, n: int = 3) -> str:
    return "".join(
        difflib.unified_diff(
            a.splitlines(keepends=True),
            b.splitlines(keepends=True),
            fromfile="baseline",
            tofile="current",
            n=n,
        )
    )

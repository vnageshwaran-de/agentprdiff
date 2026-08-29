"""Deterministic graders — cheap, free, reproducible.

These never call an LLM. Prefer them whenever the assertion can be expressed
mechanically; reserve the semantic grader for things you genuinely can't
encode as a rule.

Every factory accepts an optional ``id=`` keyword: a stable identity used to
match the assertion against baselines in diffs. Without an id, matching falls
back to the display name (e.g. ``contains('refund')``), which means changing
an argument reads as a removed + added assertion. Give long-lived assertions
an id and rename freely.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..core import Grader, GradeResult, Trace


def _output_str(trace: Trace) -> str:
    """Best-effort stringification of the agent's final output."""
    out = trace.output
    if out is None:
        return ""
    if isinstance(out, str):
        return out
    try:
        return str(out)
    except Exception:  # noqa: BLE001
        return ""


def contains(substring: str, *, case_sensitive: bool = False, id: str | None = None) -> Grader:
    """Pass iff the agent's final output contains `substring`."""

    def _grader(trace: Trace) -> GradeResult:
        haystack = _output_str(trace)
        passed = (
            substring in haystack if case_sensitive else substring.lower() in haystack.lower()
        )
        return GradeResult(
            passed=passed,
            grader_name=f"contains({substring!r})",
            grader_id=id,
            reason=(
                f"output {'contains' if passed else 'does not contain'} {substring!r}"
            ),
        )

    return _grader


def contains_any(
    substrings: Sequence[str], *, case_sensitive: bool = False, id: str | None = None
) -> Grader:
    """Pass iff the output contains at least one of the listed substrings."""

    def _grader(trace: Trace) -> GradeResult:
        haystack = _output_str(trace) if case_sensitive else _output_str(trace).lower()
        needles = list(substrings) if case_sensitive else [s.lower() for s in substrings]
        matched = [n for n in needles if n in haystack]
        passed = bool(matched)
        return GradeResult(
            passed=passed,
            grader_name=f"contains_any({list(substrings)!r})",
            grader_id=id,
            reason=(
                f"matched {matched!r}"
                if passed
                else f"none of {list(substrings)!r} found in output"
            ),
        )

    return _grader


def regex_match(pattern: str, *, flags: int = 0, id: str | None = None) -> Grader:
    """Pass iff `pattern` matches the agent's final output."""
    compiled = re.compile(pattern, flags=flags)

    def _grader(trace: Trace) -> GradeResult:
        haystack = _output_str(trace)
        m = compiled.search(haystack)
        passed = m is not None
        return GradeResult(
            passed=passed,
            grader_name=f"regex_match({pattern!r})",
            grader_id=id,
            reason=(
                f"matched {m.group(0)!r}" if m else f"no match for {pattern!r}"
            ),
        )

    return _grader


def tool_called(name: str, *, min_times: int = 1, id: str | None = None) -> Grader:
    """Pass iff the tool `name` was called at least `min_times` times."""

    def _grader(trace: Trace) -> GradeResult:
        count = sum(1 for c in trace.tool_calls if c.name == name)
        passed = count >= min_times
        return GradeResult(
            passed=passed,
            grader_name=f"tool_called({name!r}, min_times={min_times})",
            grader_id=id,
            reason=f"tool {name!r} called {count} time(s), required >= {min_times}",
        )

    return _grader


def no_tool_called(name: str, *, id: str | None = None) -> Grader:
    """Pass iff the tool `name` was NOT called."""

    def _grader(trace: Trace) -> GradeResult:
        count = sum(1 for c in trace.tool_calls if c.name == name)
        passed = count == 0
        return GradeResult(
            passed=passed,
            grader_name=f"no_tool_called({name!r})",
            grader_id=id,
            reason=f"tool {name!r} called {count} time(s); expected 0",
        )

    return _grader


def tool_sequence(
    sequence: Sequence[str], *, strict: bool = False, id: str | None = None
) -> Grader:
    """Pass iff the tool-call sequence matches `sequence`.

    If `strict=False` (default), `sequence` must appear as a subsequence of
    the actual tool calls (other tools may be interleaved). If `strict=True`,
    the tool calls must equal `sequence` exactly.
    """

    def _grader(trace: Trace) -> GradeResult:
        actual = [c.name for c in trace.tool_calls]
        if strict:
            passed = actual == list(sequence)
        else:
            # subsequence check
            i = 0
            for call in actual:
                if i < len(sequence) and call == sequence[i]:
                    i += 1
            passed = i == len(sequence)
        return GradeResult(
            passed=passed,
            grader_name=f"tool_sequence({list(sequence)!r}, strict={strict})",
            grader_id=id,
            reason=f"actual tool sequence: {actual}",
        )

    return _grader


def output_length_lt(max_chars: int, *, id: str | None = None) -> Grader:
    """Pass iff the output has fewer than `max_chars` characters."""

    def _grader(trace: Trace) -> GradeResult:
        n = len(_output_str(trace))
        passed = n < max_chars
        return GradeResult(
            passed=passed,
            grader_name=f"output_length_lt({max_chars})",
            grader_id=id,
            reason=f"output length {n} chars, limit {max_chars}",
        )

    return _grader


def latency_lt_ms(max_ms: float, *, id: str | None = None) -> Grader:
    """Pass iff the trace's total latency is below `max_ms` milliseconds."""

    def _grader(trace: Trace) -> GradeResult:
        passed = trace.total_latency_ms < max_ms
        return GradeResult(
            passed=passed,
            grader_name=f"latency_lt_ms({max_ms})",
            grader_id=id,
            reason=f"latency {trace.total_latency_ms:.1f} ms, limit {max_ms:.1f} ms",
        )

    return _grader


def cost_lt_usd(max_usd: float, *, id: str | None = None) -> Grader:
    """Pass iff the trace's total cost is below `max_usd` dollars.

    When the trace made LLM calls but recorded $0.00 total cost, the pass is
    flagged as suspicious in the reason and metadata — the usual cause is a
    model missing from the pricing table, which would otherwise let this
    grader trivially pass forever.
    """

    def _grader(trace: Trace) -> GradeResult:
        passed = trace.total_cost_usd < max_usd
        reason = f"cost ${trace.total_cost_usd:.4f}, limit ${max_usd:.4f}"
        metadata: dict[str, object] = {}
        if passed and trace.total_cost_usd == 0.0 and trace.llm_calls:
            metadata["zero_cost_with_llm_calls"] = True
            reason += (
                f" (warning: {len(trace.llm_calls)} LLM call(s) recorded $0.0000 — "
                "pricing may be missing for this model; see register_prices())"
            )
        return GradeResult(
            passed=passed,
            grader_name=f"cost_lt_usd({max_usd})",
            grader_id=id,
            reason=reason,
            metadata=metadata,
        )

    return _grader

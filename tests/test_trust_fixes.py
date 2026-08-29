"""Tests for the v0.5.0 trust fixes.

Covers: silent fake_judge fallback detection + `check --strict-judge`,
stable grader identity (`id=`) in diffs, persisted baseline grader results
(no judge re-runs against baselines), and the zero-cost warning on
`cost_lt_usd`.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from click.testing import CliRunner

from agentprdiff.cli import main
from agentprdiff.core import GradeResult, LLMCall, Trace, case, suite
from agentprdiff.differ import diff_traces
from agentprdiff.graders import contains, cost_lt_usd, semantic
from agentprdiff.runner import Runner
from agentprdiff.store import BaselineStore


def _trace(output: str = "refund processed") -> Trace:
    return Trace(suite_name="s", case_name="c", input="i", output=output)


# ---------------------------------------------------------------------------
# Silent fallback detection on semantic().
# ---------------------------------------------------------------------------


class TestSilentFallbackMetadata:
    def _clean(self, monkeypatch):
        monkeypatch.delenv("AGENTPRDIFF_JUDGE", raising=False)
        monkeypatch.delenv("AGENTGUARD_JUDGE", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def test_silent_fallback_is_flagged_in_metadata(self, monkeypatch):
        self._clean(monkeypatch)
        result = semantic("mentions the refund")(_trace())
        assert result.metadata.get("silent_fallback") is True

    def test_explicit_env_opt_in_is_not_flagged(self, monkeypatch):
        self._clean(monkeypatch)
        monkeypatch.setenv("AGENTPRDIFF_JUDGE", "fake")
        result = semantic("mentions the refund")(_trace())
        assert "silent_fallback" not in result.metadata

    def test_explicit_judge_argument_is_not_flagged(self, monkeypatch):
        self._clean(monkeypatch)
        from agentprdiff.graders import fake_judge

        result = semantic("mentions the refund", judge=fake_judge)(_trace())
        assert "silent_fallback" not in result.metadata


# ---------------------------------------------------------------------------
# `check --strict-judge` CLI behavior.
# ---------------------------------------------------------------------------


_SEMANTIC_SUITE = textwrap.dedent(
    """
    from agentprdiff import case, suite
    from agentprdiff.graders import semantic

    def agent(inp):
        return "refund confirmed"

    s = suite(
        name="strict_judge_demo",
        agent=agent,
        cases=[
            case(
                name="refund",
                input="please refund",
                expect=[semantic("mentions the refund")],
            )
        ],
    )
    """
)


class TestStrictJudgeFlag:
    def _clean(self, monkeypatch):
        monkeypatch.delenv("AGENTPRDIFF_JUDGE", raising=False)
        monkeypatch.delenv("AGENTGUARD_JUDGE", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def _record_and_check(self, tmp_path: Path, *check_args: str):
        suite_file = tmp_path / "suite.py"
        suite_file.write_text(_SEMANTIC_SUITE, encoding="utf-8")
        root = str(tmp_path / ".agentprdiff")
        runner = CliRunner()
        rec = runner.invoke(main, ["--root", root, "record", str(suite_file)])
        assert rec.exit_code == 0, rec.output
        return runner.invoke(
            main, ["--root", root, "check", str(suite_file), *check_args]
        )

    def test_strict_judge_fails_on_silent_fallback(self, tmp_path, monkeypatch):
        self._clean(monkeypatch)
        result = self._record_and_check(tmp_path, "--strict-judge")
        assert result.exit_code == 1
        assert "silent fallback" in result.output
        assert "strict_judge_demo/refund" in result.output

    def test_strict_judge_passes_with_explicit_fake(self, tmp_path, monkeypatch):
        self._clean(monkeypatch)
        monkeypatch.setenv("AGENTPRDIFF_JUDGE", "fake")
        result = self._record_and_check(tmp_path, "--strict-judge")
        assert result.exit_code == 0, result.output

    def test_default_check_still_passes_without_strict(self, tmp_path, monkeypatch):
        self._clean(monkeypatch)
        result = self._record_and_check(tmp_path)
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Stable grader identity in diffs.
# ---------------------------------------------------------------------------


class TestGraderIdMatching:
    def test_renamed_grader_with_same_id_matches_baseline(self):
        baseline_results = [
            GradeResult(passed=True, grader_name="contains('refund')", grader_id="topic")
        ]
        current_results = [
            GradeResult(
                passed=True, grader_name="contains('reimbursement')", grader_id="topic"
            )
        ]
        delta = diff_traces(
            baseline=_trace(),
            current=_trace(),
            current_results=current_results,
            baseline_results=baseline_results,
        )
        change = delta.assertion_changes[0]
        assert change.baseline_passed is True  # matched by id despite the rename
        assert not change.is_regression

    def test_renamed_grader_without_id_reads_as_new(self):
        baseline_results = [GradeResult(passed=True, grader_name="contains('refund')")]
        current_results = [GradeResult(passed=True, grader_name="contains('reimbursement')")]
        delta = diff_traces(
            baseline=_trace(),
            current=_trace(),
            current_results=current_results,
            baseline_results=baseline_results,
        )
        assert delta.assertion_changes[0].baseline_passed is None

    def test_id_flows_from_factory_to_result(self):
        result = contains("refund", id="topic")(_trace())
        assert result.grader_id == "topic"
        assert result.passed


# ---------------------------------------------------------------------------
# Persisted baseline grader results — judges never re-run against baselines.
# ---------------------------------------------------------------------------


class TestPersistedBaselineResults:
    def test_baseline_stores_grader_results(self, tmp_path):
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        s = suite(
            name="persist",
            agent=lambda inp: "refund done",
            cases=[case(name="c1", input="x", expect=[contains("refund")])],
        )
        Runner(store).record(s)
        baseline = store.load_baseline("persist", "c1")
        stored = baseline.metadata.get("grader_results")
        assert stored and stored[0]["grader_name"] == "contains('refund')"
        assert stored[0]["passed"] is True

    def test_check_does_not_rerun_judge_against_baseline(self, tmp_path):
        calls = {"n": 0}

        def counting_judge(rubric: str, trace: Trace) -> tuple[bool, str]:
            calls["n"] += 1
            return True, "counted"

        store = BaselineStore(root=tmp_path / ".agentprdiff")
        s = suite(
            name="persist",
            agent=lambda inp: "refund done",
            cases=[
                case(
                    name="c1",
                    input="x",
                    expect=[semantic("mentions refund", judge=counting_judge)],
                )
            ],
        )
        runner = Runner(store)
        runner.record(s)  # 1 judge call (current trace)
        report = runner.check(s)  # 1 more judge call (current trace only)
        assert calls["n"] == 2  # not 3 — baseline verdict came from storage
        assert not report.has_regression

    def test_legacy_baseline_without_results_still_checks(self, tmp_path):
        store = BaselineStore(root=tmp_path / ".agentprdiff")
        s = suite(
            name="legacy",
            agent=lambda inp: "refund done",
            cases=[case(name="c1", input="x", expect=[contains("refund")])],
        )
        runner = Runner(store)
        runner.record(s)
        # Simulate a pre-v0.5.0 baseline: strip the persisted results.
        baseline = store.load_baseline("legacy", "c1")
        baseline.metadata.pop("grader_results", None)
        store.save_baseline(baseline)
        report = runner.check(s)
        assert not report.has_regression
        change = report.case_reports[0].delta.assertion_changes[0]
        assert change.baseline_passed is True  # re-derived via the legacy path


# ---------------------------------------------------------------------------
# Zero-cost warning on cost_lt_usd.
# ---------------------------------------------------------------------------


class TestZeroCostWarning:
    def test_zero_cost_with_llm_calls_is_flagged(self):
        t = _trace()
        t.llm_calls.append(LLMCall(provider="openai", model="my-custom-model"))
        result = cost_lt_usd(0.05)(t)
        assert result.passed
        assert result.metadata.get("zero_cost_with_llm_calls") is True
        assert "pricing may be missing" in result.reason

    def test_zero_cost_without_llm_calls_is_clean(self):
        result = cost_lt_usd(0.05)(_trace())
        assert result.passed
        assert "zero_cost_with_llm_calls" not in result.metadata

    def test_nonzero_cost_is_clean(self):
        t = _trace()
        t.llm_calls.append(LLMCall(provider="openai", model="gpt-4o"))
        t.total_cost_usd = 0.01
        result = cost_lt_usd(0.05)(t)
        assert result.passed
        assert "zero_cost_with_llm_calls" not in result.metadata

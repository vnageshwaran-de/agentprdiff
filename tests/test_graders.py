"""Tests for every grader."""

from __future__ import annotations

import pytest

from agentprdiff.core import GradeResult
from agentprdiff.graders import (
    contains,
    contains_any,
    cost_lt_usd,
    fake_judge,
    latency_lt_ms,
    no_tool_called,
    output_length_lt,
    regex_match,
    semantic,
    tool_called,
    tool_sequence,
)
from agentprdiff.graders.semantic import case_uses_semantic, describe_default_judge


class TestDeterministicGraders:
    def test_contains_case_insensitive(self, make_trace):
        t = make_trace(output="Your REFUND has been processed.")
        assert contains("refund")(t).passed is True
        assert contains("missing")(t).passed is False

    def test_contains_case_sensitive(self, make_trace):
        t = make_trace(output="REFUND approved")
        assert contains("refund", case_sensitive=True)(t).passed is False
        assert contains("REFUND", case_sensitive=True)(t).passed is True

    def test_contains_any(self, make_trace):
        t = make_trace(output="I cannot process this")
        assert contains_any(["refund", "cannot"])(t).passed is True
        assert contains_any(["xyz", "qqq"])(t).passed is False

    def test_regex_match(self, make_trace):
        t = make_trace(output="Order #1234 refunded")
        assert regex_match(r"Order #\d+")(t).passed is True
        assert regex_match(r"nope")(t).passed is False

    def test_tool_called(self, make_trace):
        t = make_trace(tools=["lookup_order", "refund", "lookup_order"])
        assert tool_called("lookup_order")(t).passed is True
        assert tool_called("lookup_order", min_times=2)(t).passed is True
        assert tool_called("lookup_order", min_times=3)(t).passed is False
        assert tool_called("send_email")(t).passed is False

    def test_no_tool_called(self, make_trace):
        t = make_trace(tools=["lookup_order"])
        assert no_tool_called("send_email")(t).passed is True
        assert no_tool_called("lookup_order")(t).passed is False

    def test_tool_sequence_subsequence(self, make_trace):
        t = make_trace(tools=["a", "b", "c", "d"])
        assert tool_sequence(["a", "c"])(t).passed is True
        assert tool_sequence(["c", "a"])(t).passed is False

    def test_tool_sequence_strict(self, make_trace):
        t = make_trace(tools=["a", "b"])
        assert tool_sequence(["a", "b"], strict=True)(t).passed is True
        assert tool_sequence(["a", "b", "c"], strict=True)(t).passed is False

    def test_output_length_lt(self, make_trace):
        t = make_trace(output="hi")
        assert output_length_lt(10)(t).passed is True
        assert output_length_lt(2)(t).passed is False

    def test_latency_lt_ms(self, make_trace):
        t = make_trace(latency=500)
        assert latency_lt_ms(1000)(t).passed is True
        assert latency_lt_ms(100)(t).passed is False

    def test_cost_lt_usd(self, make_trace):
        t = make_trace(cost=0.001)
        assert cost_lt_usd(0.01)(t).passed is True
        assert cost_lt_usd(0.0001)(t).passed is False


class TestSemanticGrader:
    def test_fake_judge_matches_keywords(self, make_trace):
        t = make_trace(output="We have refunded your order in full.")
        passed, _ = fake_judge("agent confirmed a refund was processed", t)
        assert passed is True

    def test_fake_judge_rejects_empty_output(self, make_trace):
        t = make_trace(output="")
        passed, _ = fake_judge("agent confirmed a refund", t)
        assert passed is False

    def test_semantic_uses_supplied_judge(self, make_trace):
        calls = {"n": 0}

        def my_judge(rubric: str, trace):
            calls["n"] += 1
            return True, f"looked at {rubric[:10]}"

        t = make_trace(output="ok")
        result = semantic("some rubric", judge=my_judge)(t)
        assert result.passed is True
        assert calls["n"] == 1

    def test_semantic_survives_judge_exception(self, make_trace):
        def bad_judge(rubric, trace):
            raise RuntimeError("boom")

        t = make_trace(output="ok")
        result = semantic("x", judge=bad_judge)(t)
        assert result.passed is False
        assert "boom" in result.reason


class TestDescribeDefaultJudge:
    """Pin the strings the reporter banner relies on. Each branch in
    `_default_judge`'s precedence ladder needs a matching description so the
    runtime banner never lies about which judge is in use.
    """

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        # Strip any judge config the host might have so each case starts blank.
        monkeypatch.delenv("AGENTGUARD_JUDGE", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def test_no_env_reports_silent_fallback(self):
        msg = describe_default_judge()
        assert msg.startswith("fake_judge")
        assert "silent fallback" in msg

    def test_explicit_fake(self, monkeypatch):
        monkeypatch.setenv("AGENTGUARD_JUDGE", "fake")
        assert describe_default_judge() == "fake_judge (AGENTGUARD_JUDGE=fake)"

    def test_explicit_openai(self, monkeypatch):
        monkeypatch.setenv("AGENTGUARD_JUDGE", "openai")
        assert describe_default_judge() == "openai/gpt-4o-mini (AGENTGUARD_JUDGE=openai)"

    def test_explicit_anthropic(self, monkeypatch):
        monkeypatch.setenv("AGENTGUARD_JUDGE", "anthropic")
        msg = describe_default_judge()
        assert msg.startswith("anthropic/")
        assert "AGENTGUARD_JUDGE=anthropic" in msg

    def test_openai_key_alone_picks_openai(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert describe_default_judge() == "openai/gpt-4o-mini (OPENAI_API_KEY set)"

    def test_anthropic_key_alone_picks_anthropic(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        msg = describe_default_judge()
        assert msg.startswith("anthropic/")
        assert "ANTHROPIC_API_KEY set" in msg

    def test_explicit_fake_overrides_openai_key(self, monkeypatch):
        monkeypatch.setenv("AGENTGUARD_JUDGE", "fake")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert describe_default_judge() == "fake_judge (AGENTGUARD_JUDGE=fake)"


class TestCaseUsesSemantic:
    def test_returns_true_when_semantic_grader_present(self):
        results = [
            GradeResult(passed=True, grader_name="contains('refund')", reason="ok"),
            GradeResult(passed=True, grader_name="semantic('agent acknowledges')", reason="ok"),
        ]
        assert case_uses_semantic(results) is True

    def test_returns_false_when_only_deterministic(self):
        results = [
            GradeResult(passed=True, grader_name="contains('refund')", reason="ok"),
            GradeResult(passed=True, grader_name="tool_called('lookup_order')", reason="ok"),
        ]
        assert case_uses_semantic(results) is False

    def test_returns_false_for_empty_list(self):
        assert case_uses_semantic([]) is False

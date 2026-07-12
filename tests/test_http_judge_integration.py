# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration test — http_judge against NVIDIA inference gateway.

Requires environment variables (load from your .env file before running):
    OPENAI_API_KEY   — bearer token for the NVIDIA inference gateway
    OPENAI_BASE_URL  — https://inference-api.nvidia.com/v1
    OPENAI_MODEL     — azure/anthropic/claude-sonnet-4-6

Run with:
    pytest tests/test_http_judge_integration.py -v -s

Skip automatically in CI (no keys set) via the `integration` mark.
"""

from __future__ import annotations

import os
import pytest

from agentprdiff.core import Trace
from agentprdiff.graders import semantic
from agentprdiff.graders.http_judge import http_judge


def _requires_env():
    missing = [k for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL") if not os.environ.get(k)]
    if missing:
        pytest.skip(f"Missing env vars: {', '.join(missing)}")


def _judge():
    return http_judge(
        url=f"{os.environ['OPENAI_BASE_URL']}/chat/completions",
        model=os.environ["OPENAI_MODEL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )


def _trace(output: str) -> Trace:
    return Trace(case_name="integration", suite_name="live", input="test", output=output)


class TestHttpJudgeLive:
    def test_pass_case(self):
        """Agent output clearly satisfies the rubric — expect PASS."""
        _requires_env()
        judge = _judge()
        passed, reason = judge(
            "The agent confirmed the order was cancelled and provided a refund timeline",
            _trace("Your order #4821 has been cancelled. You will receive a full refund within 3-5 business days."),
        )
        print(f"\n[PASS case] verdict={passed}, reason={reason}")
        assert passed is True, f"Expected PASS but got FAIL: {reason}"

    def test_fail_case(self):
        """Agent output clearly does NOT satisfy the rubric — expect FAIL."""
        _requires_env()
        judge = _judge()
        passed, reason = judge(
            "The agent confirmed the order was cancelled and provided a refund timeline",
            _trace("I'm sorry, I cannot help with that request."),
        )
        print(f"\n[FAIL case] verdict={passed}, reason={reason}")
        assert passed is False, f"Expected FAIL but got PASS: {reason}"

    def test_semantic_grader_integration(self):
        """End-to-end: semantic() grader with http_judge backend."""
        _requires_env()
        grader = semantic(
            "The agent provided a step-by-step troubleshooting plan",
            judge=_judge(),
        )
        trace = _trace(
            "Here are the steps to fix your issue:\n"
            "1. Restart the service\n"
            "2. Clear the cache\n"
            "3. Re-run the job\n"
            "If the problem persists, contact support."
        )
        result = grader(trace)
        print(f"\n[semantic grader] passed={result.passed}, reason={result.reason}")
        assert result.passed is True
        assert result.grader_name.startswith("semantic(")

    def test_ambiguous_output_is_strict(self):
        """Vague output that partially matches — judge should be strict and FAIL."""
        _requires_env()
        judge = _judge()
        passed, reason = judge(
            "The agent provided the user's account balance in USD",
            _trace("Your account is active and in good standing."),
        )
        print(f"\n[ambiguous case] verdict={passed}, reason={reason}")
        # Balance not mentioned — strict judge should fail this
        assert passed is False, f"Expected strict FAIL but got PASS: {reason}"

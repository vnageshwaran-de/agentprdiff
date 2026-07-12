# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the http_judge backend."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from agentprdiff.core import Trace
from agentprdiff.graders.http_judge import http_judge


def _make_trace(output: str = "The refund was processed.") -> Trace:
    return Trace(case_name="test", suite_name="suite", input="input", output=output)


def _mock_response(content: str, status: int = 200):
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    mock = MagicMock()
    mock.read.return_value = body
    mock.status = status
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


class TestHttpJudgePass:
    def test_pass_verdict(self):
        judge = http_judge("http://fake/v1/chat/completions", model="test-model")
        with patch("urllib.request.urlopen", return_value=_mock_response("PASS\nLooks good.")):
            passed, reason = judge("agent acknowledged refund", _make_trace())
        assert passed is True
        assert reason == "Looks good."

    def test_fail_verdict(self):
        judge = http_judge("http://fake/v1/chat/completions", model="test-model")
        with patch("urllib.request.urlopen", return_value=_mock_response("FAIL\nMissing ticket.")):
            passed, reason = judge("agent provided ticket number", _make_trace("Sorry."))
        assert passed is False
        assert reason == "Missing ticket."

    def test_api_key_in_header(self):
        judge = http_judge("http://fake/v1/chat/completions", model="m", api_key="sk-test")
        captured = {}

        def fake_urlopen(req, timeout):
            captured["auth"] = req.get_header("Authorization")
            return _mock_response("PASS")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            judge("rubric", _make_trace())

        assert captured["auth"] == "Bearer sk-test"

    def test_extra_headers_forwarded(self):
        judge = http_judge(
            "http://fake/v1/chat/completions",
            model="m",
            extra_headers={"X-Tenant-Id": "nvidia-team"},
        )
        captured = {}

        def fake_urlopen(req, timeout):
            captured["tenant"] = req.get_header("X-tenant-id")
            return _mock_response("PASS")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            judge("rubric", _make_trace())

        assert captured["tenant"] == "nvidia-team"

    def test_env_var_api_key(self, monkeypatch):
        monkeypatch.setenv("HTTP_JUDGE_API_KEY", "sk-env-key")
        judge = http_judge("http://fake/v1/chat/completions", model="m")
        captured = {}

        def fake_urlopen(req, timeout):
            captured["auth"] = req.get_header("Authorization")
            return _mock_response("PASS")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            judge("rubric", _make_trace())

        assert captured["auth"] == "Bearer sk-env-key"

    def test_network_error_returns_fail(self):
        judge = http_judge("http://fake/v1/chat/completions", model="m")
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            passed, reason = judge("rubric", _make_trace())
        assert passed is False
        assert "http_judge error" in reason

    def test_unparseable_response_returns_fail(self):
        judge = http_judge("http://fake/v1/chat/completions", model="m")
        with patch("urllib.request.urlopen", return_value=_mock_response("MAYBE\nNot sure.")):
            passed, reason = judge("rubric", _make_trace())
        assert passed is False

    def test_works_with_semantic_grader(self):
        from agentprdiff.graders import semantic

        judge = http_judge("http://fake/v1/chat/completions", model="m")
        grader = semantic("agent acknowledged refund", judge=judge)
        with patch("urllib.request.urlopen", return_value=_mock_response("PASS\nGood.")):
            result = grader(_make_trace())
        assert result.passed is True
        assert result.grader_name.startswith("semantic(")

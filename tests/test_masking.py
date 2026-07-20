# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for field-level trace masking."""

from __future__ import annotations

from agentprdiff.core import ToolCall, Trace
from agentprdiff.masking import MaskRule, mask_trace


def _trace(**kwargs) -> Trace:
    defaults = dict(case_name="c", suite_name="s", input="hello", output="world")
    defaults.update(kwargs)
    return Trace(**defaults)


class TestMaskTraceOutput:
    def test_full_field_replacement(self):
        t = _trace(output="secret-value")
        result = mask_trace(t, [MaskRule(field="output")])
        assert result.output == "<masked>"

    def test_regex_partial_replacement(self):
        t = _trace(output="Hello user-12345, your token is abc123.")
        result = mask_trace(t, [MaskRule(field="output", pattern=r"user-\d+", replacement="user-***")])
        assert result.output == "Hello user-***, your token is abc123."

    def test_original_trace_not_mutated(self):
        t = _trace(output="original")
        mask_trace(t, [MaskRule(field="output")])
        assert t.output == "original"

    def test_missing_field_is_noop(self):
        t = _trace(output="keep")
        result = mask_trace(t, [MaskRule(field="nonexistent.field")])
        assert result.output == "keep"


class TestMaskMetadata:
    def test_mask_nested_metadata_key(self):
        t = _trace(metadata={"session_id": "sess-abc", "user": "alice"})
        result = mask_trace(t, [MaskRule(field="metadata.session_id")])
        assert result.metadata["session_id"] == "<masked>"
        assert result.metadata["user"] == "alice"

    def test_custom_replacement_string(self):
        t = _trace(metadata={"api_key": "sk-supersecret"})
        result = mask_trace(t, [MaskRule(field="metadata.api_key", replacement="<redacted>")])
        assert result.metadata["api_key"] == "<redacted>"


class TestMaskToolCalls:
    def test_mask_tool_call_argument_by_index(self):
        t = _trace()
        t.tool_calls = [
            ToolCall(name="search", arguments={"query": "safe", "token": "secret-tok"}),
        ]
        result = mask_trace(t, [MaskRule(field="tool_calls.0.arguments.token")])
        assert result.tool_calls[0].arguments["token"] == "<masked>"
        assert result.tool_calls[0].arguments["query"] == "safe"

    def test_out_of_bounds_index_is_noop(self):
        t = _trace()
        t.tool_calls = [ToolCall(name="ping", arguments={})]
        result = mask_trace(t, [MaskRule(field="tool_calls.5.arguments.x")])
        assert len(result.tool_calls) == 1


class TestMultipleRules:
    def test_multiple_rules_applied_in_order(self):
        t = _trace(output="user-999 token-abc")
        rules = [
            MaskRule(field="output", pattern=r"user-\d+", replacement="user-***"),
            MaskRule(field="output", pattern=r"token-\w+", replacement="token-***"),
        ]
        result = mask_trace(t, rules)
        assert result.output == "user-*** token-***"

    def test_non_string_field_with_pattern_is_noop(self):
        t = _trace()
        # total_cost_usd is a float — regex replacement should leave it alone.
        result = mask_trace(t, [MaskRule(field="total_cost_usd", pattern=r"\d+", replacement="0")])
        assert isinstance(result.total_cost_usd, float)

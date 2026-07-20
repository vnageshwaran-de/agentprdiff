# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
"""Field-level masking for Trace objects before baseline storage or diffing.

Teams often capture traces that contain PII, credentials, or environment-
specific values (user IDs, session tokens, hostnames) that should not be
committed to git or compared across runs.  This module provides a lightweight
masking layer that redacts or replaces selected fields before a Trace leaves
the runner.

Usage::

    from agentprdiff.masking import mask_trace, MaskRule

    rules = [
        MaskRule(field="output", pattern=r"user-\\d+", replacement="user-***"),
        MaskRule(field="metadata.api_key", replacement="<redacted>"),
    ]

    clean_trace = mask_trace(trace, rules)

``field`` is a dot-separated path into the Trace JSON (e.g. ``"output"``,
``"metadata.session_id"``, ``"tool_calls.0.arguments.password"``).

If ``pattern`` is given, only substrings matching the regex are replaced.
If ``pattern`` is omitted the entire field value is replaced with
``replacement``.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from pydantic import BaseModel

from .core import Trace


class MaskRule(BaseModel):
    """A single masking rule.

    Attributes:
        field: Dot-separated path to the field inside the Trace JSON structure.
            List indices are supported as numeric segments (e.g.
            ``"tool_calls.0.arguments.token"``).
        pattern: Optional regex.  When set, only matching substrings are
            replaced.  When omitted the entire field value is overwritten.
        replacement: The string substituted in place of the matched content.
            Defaults to ``"<masked>"``.
    """

    field: str
    pattern: str | None = None
    replacement: str = "<masked>"


def mask_trace(trace: Trace, rules: list[MaskRule]) -> Trace:
    """Return a deep copy of *trace* with all *rules* applied.

    The original trace is never mutated.
    """
    data: dict[str, Any] = copy.deepcopy(trace.model_dump(mode="json"))
    for rule in rules:
        _apply_rule(data, rule)
    return Trace.model_validate(data)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _apply_rule(data: dict[str, Any], rule: MaskRule) -> None:
    segments = rule.field.split(".")
    _set_at_path(data, segments, rule)


def _set_at_path(
    node: Any,
    segments: list[str],
    rule: MaskRule,
) -> None:
    if not segments:
        return

    key = segments[0]
    rest = segments[1:]

    # Resolve integer indices for lists.
    if isinstance(node, list):
        try:
            idx = int(key)
        except ValueError:
            return
        if idx < 0 or idx >= len(node):
            return
        if not rest:
            node[idx] = _apply_replacement(node[idx], rule)
        else:
            _set_at_path(node[idx], rest, rule)
        return

    if not isinstance(node, dict) or key not in node:
        return

    if not rest:
        node[key] = _apply_replacement(node[key], rule)
    else:
        _set_at_path(node[key], rest, rule)


def _apply_replacement(value: Any, rule: MaskRule) -> Any:
    if rule.pattern is None:
        return rule.replacement
    if not isinstance(value, str):
        # Only string fields support regex replacement.
        return value
    return re.sub(rule.pattern, rule.replacement, value)

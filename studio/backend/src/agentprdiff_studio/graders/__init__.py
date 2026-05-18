"""Grader-spec serialization for Studio-native (HTTP) suites.

Git/zip projects define graders in Python; we just call them. HTTP projects
have no Python file, so the suite definition arrives as JSON. Each grader
entry is a small dict like::

    {"type": "contains", "value": "refund"}
    {"type": "latency_lt_ms", "value": 5000}
    {"type": "semantic", "prompt": "agent acknowledges the refund"}

This module resolves those specs to the corresponding engine grader callables.
"""

from .specs import (
    GraderSpecError,
    available_grader_types,
    resolve_grader,
    resolve_graders,
)

__all__ = [
    "GraderSpecError",
    "resolve_grader",
    "resolve_graders",
    "available_grader_types",
]

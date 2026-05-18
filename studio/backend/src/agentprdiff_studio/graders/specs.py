"""Map JSON grader specs to engine grader callables.

Spec shape: ``{"type": "<name>", ...other fields per grader}``.

Registry entries are kept inline (rather than imported from one big dict)
because each grader takes slightly different fields, and pulling the
required-field validation inline keeps error messages tight.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentprdiff.core import Grader
from agentprdiff.graders import (
    contains,
    contains_any,
    cost_lt_usd,
    latency_lt_ms,
    no_tool_called,
    output_length_lt,
    regex_match,
    semantic,
    tool_called,
    tool_sequence,
)


class GraderSpecError(ValueError):
    """A JSON grader spec was malformed or referenced an unknown type."""


_REGISTRY: dict[str, Callable[[dict[str, Any]], Grader]] = {
    "contains": lambda s: contains(_require(s, "value", str)),
    "contains_any": lambda s: contains_any(_require(s, "values", list)),
    "regex_match": lambda s: regex_match(_require(s, "pattern", str)),
    "tool_called": lambda s: tool_called(
        _require(s, "name", str), min_times=int(s.get("min_times", 1))
    ),
    "tool_sequence": lambda s: tool_sequence(_require(s, "names", list)),
    "no_tool_called": lambda s: no_tool_called(_require(s, "name", str)),
    "output_length_lt": lambda s: output_length_lt(int(_require(s, "value", (int, float)))),
    "latency_lt_ms": lambda s: latency_lt_ms(float(_require(s, "value", (int, float)))),
    "cost_lt_usd": lambda s: cost_lt_usd(float(_require(s, "value", (int, float)))),
    "semantic": lambda s: semantic(_require(s, "prompt", str)),
}


def available_grader_types() -> list[str]:
    """Sorted list of types Studio knows how to materialize."""
    return sorted(_REGISTRY)


def resolve_grader(spec: dict[str, Any]) -> Grader:
    """Turn a single spec dict into a Grader callable.

    Raises GraderSpecError on malformed input — caller surfaces it as a 400.
    """
    if not isinstance(spec, dict):
        raise GraderSpecError(f"grader spec must be an object, got {type(spec).__name__}")
    gtype = spec.get("type")
    if not gtype:
        raise GraderSpecError("grader spec is missing 'type'")
    factory = _REGISTRY.get(gtype)
    if factory is None:
        raise GraderSpecError(
            f"unknown grader type {gtype!r}; available: {', '.join(available_grader_types())}"
        )
    try:
        return factory(spec)
    except KeyError as exc:
        raise GraderSpecError(f"grader {gtype!r} missing required field {exc}") from exc


def resolve_graders(specs: list[dict[str, Any]]) -> list[Grader]:
    return [resolve_grader(s) for s in specs]


def _require(spec: dict[str, Any], key: str, kinds: type | tuple[type, ...]) -> Any:
    if key not in spec:
        raise GraderSpecError(f"grader {spec.get('type')!r} requires field {key!r}")
    value = spec[key]
    if not isinstance(value, kinds):
        expected = (
            kinds.__name__
            if isinstance(kinds, type)
            else " | ".join(k.__name__ for k in kinds)
        )
        raise GraderSpecError(
            f"grader {spec.get('type')!r} field {key!r} must be {expected}, got {type(value).__name__}"
        )
    return value

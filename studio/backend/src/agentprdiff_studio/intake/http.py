"""HTTP-endpoint intake — validation only; the executor lives in ``executor/http_run.py``.

An HTTP project has no workspace and no Python code. Studio:

1. Stores an ``http_config`` describing how to call the endpoint.
2. Lets users author suites (cases + grader specs) as JSON via the API,
   persisted in ``Suite.definition_json``.

The executor builds requests at run time by substituting ``{{input}}`` (and
optionally other ``{{ field }}`` placeholders) inside the body template.
"""

from __future__ import annotations

from typing import Any

from ..graders.specs import GraderSpecError, resolve_graders

_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


class HttpIntakeError(ValueError):
    """An ``http_config`` or suite definition is malformed."""


def normalize_http_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate + normalize an ``http_config`` dict.

    Required: ``url``.
    Defaults: method=POST, headers={}, body_template={"input": "{{input}}"},
    output_path="" (use whole response body as output).
    """
    if not isinstance(raw, dict):
        raise HttpIntakeError("http_config must be an object")

    url = raw.get("url")
    if not isinstance(url, str) or not url.strip():
        raise HttpIntakeError("http_config.url is required (string)")

    method = (raw.get("method") or "POST").upper()
    if method not in _ALLOWED_METHODS:
        raise HttpIntakeError(
            f"http_config.method must be one of {sorted(_ALLOWED_METHODS)}; got {method!r}"
        )

    headers = raw.get("headers", {}) or {}
    if not isinstance(headers, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
    ):
        raise HttpIntakeError("http_config.headers must be a flat {string: string} map")

    body_template = raw.get("body_template")
    if body_template is None:
        body_template = {"input": "{{input}}"}
    # body_template can be any JSON value (object, string, or null for GET).

    output_path = raw.get("output_path", "")
    if not isinstance(output_path, str):
        raise HttpIntakeError("http_config.output_path must be a string (dotted path)")

    timeout_seconds = float(raw.get("timeout_seconds", 30.0))
    if timeout_seconds <= 0 or timeout_seconds > 600:
        raise HttpIntakeError("http_config.timeout_seconds must be in (0, 600]")

    return {
        "method": method,
        "url": url.strip(),
        "headers": headers,
        "body_template": body_template,
        "output_path": output_path,
        "timeout_seconds": timeout_seconds,
    }


def normalize_suite_definition(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate a Studio-native suite spec for HTTP intake.

    Required shape::

        {
          "name": "...",
          "cases": [
            {"name": "...", "input": <any>, "expect": [<grader spec>, ...]},
            ...
          ]
        }

    Each grader spec is round-tripped through :func:`resolve_graders` so a
    bad spec is rejected at create-time, not at run-time.
    """
    if not isinstance(raw, dict):
        raise HttpIntakeError("suite definition must be an object")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HttpIntakeError("suite.name is required (non-empty string)")

    cases = raw.get("cases")
    if not isinstance(cases, list) or not cases:
        raise HttpIntakeError("suite.cases must be a non-empty list")

    seen_names: set[str] = set()
    normalized_cases: list[dict[str, Any]] = []
    for idx, case in enumerate(cases):
        if not isinstance(case, dict):
            raise HttpIntakeError(f"cases[{idx}] must be an object")
        cname = case.get("name")
        if not isinstance(cname, str) or not cname.strip():
            raise HttpIntakeError(f"cases[{idx}].name is required")
        if cname in seen_names:
            raise HttpIntakeError(f"duplicate case name: {cname!r}")
        seen_names.add(cname)

        if "input" not in case:
            raise HttpIntakeError(f"cases[{idx}].input is required")

        expect = case.get("expect", [])
        if not isinstance(expect, list):
            raise HttpIntakeError(f"cases[{idx}].expect must be a list")
        try:
            resolve_graders(expect)  # validates types + fields
        except GraderSpecError as exc:
            raise HttpIntakeError(f"cases[{idx}] ({cname}): {exc}") from exc

        normalized_cases.append(
            {
                "name": cname,
                "input": case["input"],
                "expect": expect,
                "tags": case.get("tags") or [],
            }
        )

    return {"name": name.strip(), "cases": normalized_cases}

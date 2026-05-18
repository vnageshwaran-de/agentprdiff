"""Cheap AST-based extraction of suite names + case counts.

Used as a fall-back when the engine's full loader can't import a file (because
its project-runtime deps aren't in Studio's host container, only in the
per-project venv). The executor still runs the suite correctly at run time
in the project venv — we just need *enough* info to list it in the UI before
that point.

We look for top-level assignments of the form::

    foo = suite(name="bar", cases=[case(...), case(...)])

Heuristics:

* The right-hand side is a call named ``suite`` (any module path tail).
* ``name`` kwarg gives the suite name; bare positional first-arg also works.
* ``cases`` kwarg gives a list literal we can count.

If a suite has its cases built programmatically (not a list literal), we
fall back to ``case_count = 0`` — the UI's row says "?" in that slot.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AstSuite:
    name: str
    case_count: int


def extract_suites(source: str) -> list[AstSuite]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    out: list[AstSuite] = []
    for node in tree.body:
        # Match `var = suite(...)` and `var = agentprdiff.suite(...)`.
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if _is_suite_call(call):
                info = _read_suite_call(call)
                if info is not None:
                    out.append(info)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Call):
            if _is_suite_call(node.value):
                info = _read_suite_call(node.value)
                if info is not None:
                    out.append(info)
        # Module-level bare expression: ``suite(...)`` without assignment.
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            if _is_suite_call(node.value):
                info = _read_suite_call(node.value)
                if info is not None:
                    out.append(info)
    return out


def extract_from_file(path: Path) -> list[AstSuite]:
    try:
        return extract_suites(path.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return []


# ---------------------------------------------------------------------------


def _is_suite_call(call: ast.Call) -> bool:
    """The callee is named ``suite`` (or ends in ``.suite``)."""
    func = call.func
    if isinstance(func, ast.Name) and func.id == "suite":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "suite":
        return True
    return False


def _read_suite_call(call: ast.Call) -> AstSuite | None:
    name = _read_name(call)
    case_count = _read_case_count(call)
    if name is None:
        return None
    return AstSuite(name=name, case_count=case_count)


def _read_name(call: ast.Call) -> str | None:
    # kwarg form: suite(name="my_suite", ...)
    for kw in call.keywords:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    # positional form: suite("my_suite", ...)
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


def _read_case_count(call: ast.Call) -> int:
    """Best-effort case count. ``cases=[case(...), case(...)]`` → len of list."""
    cases_value = None
    for kw in call.keywords:
        if kw.arg == "cases":
            cases_value = kw.value
            break
    if cases_value is None and len(call.args) >= 3:
        # Positional ordering: suite(name, agent, cases, [description])
        cases_value = call.args[2]
    if isinstance(cases_value, (ast.List, ast.Tuple)):
        return len(cases_value.elts)
    return 0

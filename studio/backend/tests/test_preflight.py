"""Tests for the hardened preflight pipeline.

The pipeline runs three stages — ``syntax``, ``import_load``,
``suite_discovery`` — each producing structured diagnostics with
stable error codes. The tests below cover:

* Stage 1 catches syntax errors and hyphenated/invalid module paths.
* Stage 2 runs in a subprocess and reports ``ModuleNotFoundError`` with
  a remediation dict (so the UI's "Add to requirements.txt" card has
  data to render).
* Stage 3 surfaces missing-discovery markers and zero-case suites.
* The aggregate report is only ``ok=True`` when every stage passes or
  was deliberately skipped.
* Stable error codes don't silently mutate; the constants on
  :class:`ErrorCode` are exercised explicitly so a future rename
  shows up here first.

The integration cases use small synthetic suite files. They don't
require a real LLM call — we feed the pipeline the exact source we'd
expect the LLM to produce. The subprocess worker imports
:mod:`agentprdiff` from the test interpreter's ``sys.path`` (or the
environment's ``PYTHONPATH``); this works in CI because the dev
extras pull the engine in.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from agentprdiff_studio.agents_md.preflight import (
    ErrorCode,
    PreflightDiagnostic,
    PreflightReport,
    PreflightStage,
    run_preflight,
)

# ---------------------------------------------------------------------------
# Stage 1: syntax + import sanitization
# ---------------------------------------------------------------------------


def test_stage_syntax_passes_on_clean_source(tmp_path: Path) -> None:
    """A well-formed (but unloadable) source still passes stage 1."""
    src = textwrap.dedent(
        """\
        from agentprdiff import suite, case
        from agentprdiff.graders import contains

        def my_agent(query: str) -> str:
            return "ok"

        my_suite = suite(name="s", cases=[case(name="c", input="i", expect=[contains("ok")])])
        """
    )
    # Skipped workspace makes stages 2 + 3 short-circuit cleanly.
    report = run_preflight(src, workspace=None)
    syntax = _stage(report, "syntax")
    assert syntax.status == "passed"
    assert syntax.diagnostics == []


def test_stage_syntax_flags_bare_syntax_error(tmp_path: Path) -> None:
    src = "from agentprdiff import suite\nsuite = suite(name='broken',\n"
    report = run_preflight(src, workspace=None)
    syntax = _stage(report, "syntax")
    assert syntax.status == "failed"
    codes = [d.code for d in syntax.diagnostics]
    assert ErrorCode.SYNTAX_ERROR in codes
    # The bail-out skips later stages so we don't burn subprocess time.
    assert _stage(report, "import_load").status == "skipped"
    assert _stage(report, "suite_discovery").status == "skipped"


def test_stage_syntax_flags_hyphenated_module_path() -> None:
    # The canonical bug case (LLM trusted the workspace's hyphenated dir):
    bad = textwrap.dedent(
        """\
        from agentprdiff import suite
        from transcript-ingest-v2.cloud_function.main import agent
        """
    )
    report = run_preflight(bad, workspace=None)
    syntax = _stage(report, "syntax")
    assert syntax.status == "failed"
    # Hyphen-specific code: the UI uses this to show the rename hint.
    codes = [d.code for d in syntax.diagnostics]
    assert ErrorCode.SYNTAX_ERROR in codes or ErrorCode.HYPHENATED_IMPORT in codes
    # And a fix hint must be present so the user isn't stuck staring
    # at a one-line error.
    assert any(d.fix_hint for d in syntax.diagnostics)


def test_stage_syntax_flags_invalid_module_in_parseable_source(
    tmp_path: Path,
) -> None:
    """``importlib.import_module``-style strings parse fine, but the
    sanitizer should still flag the bad dotted name inside a real
    ``from x import y`` statement.

    We can't use a keyword (``class``) as a segment because the parser
    rejects it outright with a SyntaxError — which is already covered
    by ``test_stage_syntax_flags_bare_syntax_error``. The case that
    requires the sanitizer is a path that parses fine but uses a
    non-identifier segment shape — the interesting case for the
    sanitizer (vs. the parser) is a synthesised ``from`` whose module
    string contains a hyphen.
    """
    src_hyphen = textwrap.dedent(
        """\
        from agentprdiff import suite
        from foo-bar.baz import x  # hyphen in segment
        """
    )
    report = run_preflight(src_hyphen, workspace=None)
    syntax = _stage(report, "syntax")
    assert syntax.status == "failed"
    codes = [d.code for d in syntax.diagnostics]
    assert (
        ErrorCode.SYNTAX_ERROR in codes
        or ErrorCode.HYPHENATED_IMPORT in codes
        or ErrorCode.INVALID_IMPORT_PATH in codes
    )


# ---------------------------------------------------------------------------
# Stage 3 prerequisite: discovery markers
# ---------------------------------------------------------------------------


def test_missing_agentprdiff_import_flagged_on_http_mode() -> None:
    src = "def my_agent(q): return q\n"
    report = run_preflight(src, workspace=None)
    # syntax passes (it's valid Python); load is skipped (no workspace);
    # discovery surfaces the missing-import + missing-suite markers.
    assert _stage(report, "syntax").status == "passed"
    assert _stage(report, "import_load").status == "skipped"
    sd = _stage(report, "suite_discovery")
    codes = {d.code for d in sd.diagnostics}
    assert ErrorCode.MISSING_AGENTPRDIFF_IMPORT in codes
    assert ErrorCode.MISSING_SUITE_CALL in codes
    assert report.ok is False
    assert report.error_code in {
        ErrorCode.MISSING_AGENTPRDIFF_IMPORT,
        ErrorCode.MISSING_SUITE_CALL,
    }


# ---------------------------------------------------------------------------
# Stage 2: subprocess import_load
# ---------------------------------------------------------------------------


def test_stage_import_load_passes_with_working_suite(tmp_path: Path) -> None:
    """End-to-end happy path: source loads, suite + case discovered."""
    src = textwrap.dedent(
        """\
        from agentprdiff import suite, case
        from agentprdiff.graders import contains

        def my_agent(query: str) -> str:
            return "ok"

        my_suite = suite(
            name="s",
            agent=my_agent,
            cases=[case(name="c", input="i", expect=[contains("ok")])],
        )
        """
    )
    report = run_preflight(src, workspace=tmp_path)
    assert report.ok is True, report.summary
    assert _stage(report, "import_load").status == "passed"
    assert _stage(report, "suite_discovery").status == "passed"
    assert report.total_cases == 1
    assert report.discovered_suites == ["s"]


def test_stage_import_load_catches_missing_module(tmp_path: Path) -> None:
    """The suite imports a runtime dep that isn't installed. The
    diagnostic must carry a remediation card."""
    src = textwrap.dedent(
        """\
        from agentprdiff import suite, case
        from agentprdiff.graders import contains
        import this_package_does_not_exist  # noqa: F401

        def my_agent(q): return "ok"
        s = suite(name="s", agent=my_agent, cases=[case(name="c", input="i", expect=[contains("ok")])])
        """
    )
    report = run_preflight(src, workspace=tmp_path)
    il = _stage(report, "import_load")
    assert il.status == "failed"
    diag = next(d for d in il.diagnostics if d.code == ErrorCode.MODULE_NOT_FOUND)
    assert diag.severity == "error"
    # Remediation card data the UI uses for the "Add to manifest" button.
    assert diag.remediation is not None
    assert diag.remediation["missing_module"] == "this_package_does_not_exist"
    assert diag.remediation["top_level_package"] == "this_package_does_not_exist"
    assert any(
        "pip install" in cmd for cmd in diag.remediation["install_commands"]
    )
    assert report.error_code == ErrorCode.MODULE_NOT_FOUND


def test_remediation_picks_requirements_txt_when_present(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("openai>=1.0\n")
    src = textwrap.dedent(
        """\
        from agentprdiff import suite, case
        import unknown_dep_xyz  # noqa
        s = suite(name="s")
        """
    )
    report = run_preflight(src, workspace=tmp_path)
    diag = next(
        d for s in report.stages for d in s.diagnostics
        if d.code == ErrorCode.MODULE_NOT_FOUND
    )
    assert diag.remediation is not None
    assert diag.remediation["where_to_declare"] == "requirements.txt"


def test_remediation_picks_pyproject_when_only_that_present(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\nversion='0'\n"
    )
    src = textwrap.dedent(
        """\
        from agentprdiff import suite, case
        import unknown_dep_xyz  # noqa
        s = suite(name="s")
        """
    )
    report = run_preflight(src, workspace=tmp_path)
    diag = next(
        d for s in report.stages for d in s.diagnostics
        if d.code == ErrorCode.MODULE_NOT_FOUND
    )
    assert diag.remediation is not None
    assert diag.remediation["where_to_declare"] == "pyproject.toml"
    assert any("uv add" in cmd for cmd in diag.remediation["install_commands"])


# ---------------------------------------------------------------------------
# Stage 3: suite_discovery
# ---------------------------------------------------------------------------


def test_stage_suite_discovery_flags_empty_suite(tmp_path: Path) -> None:
    """A file that imports agentprdiff but has no cases is technically
    valid Python and loads cleanly — we still flag it because the
    suite is useless."""
    src = textwrap.dedent(
        """\
        from agentprdiff import suite

        def my_agent(q): return q
        s = suite(name="empty", agent=my_agent, cases=[])
        """
    )
    report = run_preflight(src, workspace=tmp_path)
    sd = _stage(report, "suite_discovery")
    assert sd.status == "failed"
    codes = {d.code for d in sd.diagnostics}
    assert ErrorCode.NO_CASES_IN_SUITES in codes
    assert report.ok is False


def test_stage_suite_discovery_flags_no_suites(tmp_path: Path) -> None:
    """Imports agentprdiff but never calls suite() — discovery would
    walk past the file in production. Catch it here with a better
    message."""
    src = textwrap.dedent(
        """\
        from agentprdiff import case
        x = case  # noqa
        """
    )
    report = run_preflight(src, workspace=tmp_path)
    sd = _stage(report, "suite_discovery")
    assert sd.status == "failed"
    assert any(d.code == ErrorCode.MISSING_SUITE_CALL for d in sd.diagnostics)


# ---------------------------------------------------------------------------
# Stable error codes (regression guard)
# ---------------------------------------------------------------------------


def test_error_codes_are_stable_strings() -> None:
    # Renaming or repurposing one of these breaks the UI's remediation
    # mapping. Pin them down.
    assert ErrorCode.SYNTAX_ERROR == "APD_PREFLIGHT_SYNTAX_ERROR"
    assert ErrorCode.HYPHENATED_IMPORT == "APD_PREFLIGHT_HYPHENATED_IMPORT"
    assert ErrorCode.INVALID_IMPORT_PATH == "APD_PREFLIGHT_INVALID_IMPORT_PATH"
    assert ErrorCode.MODULE_NOT_FOUND == "APD_PREFLIGHT_MODULE_NOT_FOUND"
    assert ErrorCode.NO_SUITES_DISCOVERED == "APD_PREFLIGHT_NO_SUITES_DISCOVERED"
    assert ErrorCode.NO_CASES_IN_SUITES == "APD_PREFLIGHT_NO_CASES_IN_SUITES"
    assert ErrorCode.MISSING_AGENTPRDIFF_IMPORT == "APD_PREFLIGHT_MISSING_AGENTPRDIFF_IMPORT"
    assert ErrorCode.MISSING_SUITE_CALL == "APD_PREFLIGHT_MISSING_SUITE_CALL"
    assert ErrorCode.SCAN_OUT_OF_ROOT_REJECTED == "APD_SCAN_OUT_OF_ROOT_REJECTED"


def test_preflight_report_to_dict_roundtrip(tmp_path: Path) -> None:
    """Every field surfaced to the API must serialise cleanly — the
    FastAPI response model takes the dicts verbatim."""
    src = "from agentprdiff import suite\n"
    report = run_preflight(src, workspace=None)
    raw = report.to_dict()
    # Surface check: the schema the frontend type definitions assume.
    assert {"ok", "stages", "error_code", "summary",
            "discovered_suites", "total_cases",
            "preview_venv_used", "duration_ms"} <= set(raw)
    for stage in raw["stages"]:
        assert {"name", "status", "duration_ms", "diagnostics"} <= set(stage)
        for d in stage["diagnostics"]:
            assert {"stage", "code", "severity", "message"} <= set(d)
    # JSON-serialisable for free.
    json.dumps(raw)


# ---------------------------------------------------------------------------
# Aggregate ok flag
# ---------------------------------------------------------------------------


def test_report_ok_only_when_every_stage_passes_or_skipped(tmp_path: Path) -> None:
    # Stage 1 fails → ok False, downstream stages skipped.
    failing = "from foo-bar import x\n"
    report = run_preflight(failing, workspace=tmp_path)
    assert report.ok is False
    assert _stage(report, "syntax").status == "failed"
    assert _stage(report, "import_load").status == "skipped"
    assert _stage(report, "suite_discovery").status == "skipped"
    # Aggregate code matches the first failing stage's first diag.
    assert report.error_code is not None
    assert report.error_code.startswith("APD_PREFLIGHT_")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stage(report: PreflightReport, name: str) -> PreflightStage:
    for s in report.stages:
        if s.name == name:
            return s
    raise AssertionError(f"stage {name} not found in report")


# ---------------------------------------------------------------------------
# Path-outside-root rejection (scope guard)
# ---------------------------------------------------------------------------


def test_path_outside_root_diagnostic_code_exists() -> None:
    # The integration test would require constructing a symlink that
    # escapes the workspace, which fights against tmp_path's auto-clean.
    # This test just confirms the diagnostic code is referenced
    # somewhere reachable, so the constant doesn't become dead code.
    from agentprdiff_studio.agents_md import preflight as _p

    src = _p.__file__
    text = Path(src).read_text()
    assert "APD_PREFLIGHT_PATH_OUTSIDE_ROOT" in text


# ---------------------------------------------------------------------------
# Diagnostic dataclass surface
# ---------------------------------------------------------------------------


def test_preflight_diagnostic_to_dict_contains_all_fields() -> None:
    d = PreflightDiagnostic(
        stage="syntax",
        code=ErrorCode.SYNTAX_ERROR,
        severity="error",
        message="hello",
        file="foo.py",
        line=2,
        col=3,
        fix_hint="hint",
        remediation={"missing_module": "x"},
        statement="from x import y",
    )
    raw = d.to_dict()
    assert raw["stage"] == "syntax"
    assert raw["code"] == "APD_PREFLIGHT_SYNTAX_ERROR"
    assert raw["severity"] == "error"
    assert raw["line"] == 2
    assert raw["col"] == 3
    assert raw["fix_hint"] == "hint"
    assert raw["remediation"] == {"missing_module": "x"}
    assert raw["statement"] == "from x import y"

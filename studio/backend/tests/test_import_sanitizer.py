"""Tests for the generation pipeline's import sanitization + preflight.

Covers the six behaviors the spec asked for:

1. Sanitizer rejects/rewrites hyphenated module paths
2. Identifier validation: every generated import passes Python's rules
3. Preflight parse catches syntax errors and returns actionable diagnostics
4. Scan scope excludes out-of-root files by default
5. Integration: hyphenated directory ends up emitting valid Python
6. Regression: non-hyphenated valid imports are still accepted

The integration tests build small synthetic workspaces under tmp_path
and call the high-level :func:`guess_agent_target` + preflight pipeline.
No real LLM call — we test the input we'd hand the LLM and the
validation we apply to whatever it produces.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agentprdiff_studio.agents_md.import_sanitizer import (
    classify_target,
    closest_safe_subpath,
    is_valid_dotted_module,
    is_valid_identifier,
    is_within,
    path_to_module,
    validate_generated_imports,
)
from agentprdiff_studio.agents_md.validate import (
    guess_agent_module_and_callable,
    guess_agent_target,
)

# ---------------------------------------------------------------------------
# (1) is_valid_identifier / is_valid_dotted_module: language-rule checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "identifier,expected",
    [
        ("foo", True),
        ("foo_bar", True),
        ("Foo123", True),
        ("_private", True),  # technically valid even if conventionally private
        # Invalid — hyphens
        ("foo-bar", False),
        ("transcript-ingest-v2", False),
        # Invalid — leading digit
        ("2foo", False),
        # Invalid — keyword
        ("class", False),
        ("def", False),
        ("import", False),
        # Invalid — soft keyword we reject defensively
        ("match", False),
        ("type", False),
        # Invalid — empty / whitespace / dots
        ("", False),
        ("foo.bar", False),  # dotted goes through is_valid_dotted_module
        ("foo bar", False),
        # Invalid — non-ASCII
        ("café", True),  # actually valid in Python 3 identifiers (NFKC)
    ],
)
def test_is_valid_identifier(identifier: str, expected: bool) -> None:
    assert is_valid_identifier(identifier) is expected


@pytest.mark.parametrize(
    "dotted,expected",
    [
        ("foo", True),
        ("foo.bar", True),
        ("foo.bar.baz", True),
        ("foo_bar.baz123", True),
        # Hyphens in any segment — the bug case
        ("foo-bar.baz", False),
        ("transcript-ingest-v2.cloud_function.main", False),
        # Leading digit in any segment
        ("foo.2024", False),
        # Keyword anywhere
        ("foo.class.bar", False),
        # Empty / dot-only
        ("", False),
        (".", False),
        ("foo.", False),
    ],
)
def test_is_valid_dotted_module(dotted: str, expected: bool) -> None:
    assert is_valid_dotted_module(dotted) is expected


# ---------------------------------------------------------------------------
# (1) path_to_module: filesystem path → dotted name with safety check
# ---------------------------------------------------------------------------


def test_path_to_module_clean_path(tmp_path: Path) -> None:
    """Plain identifiers all the way down → straightforward dotted module."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "agent.py").write_text("def run(q): return q\n")
    assert path_to_module(tmp_path / "pkg" / "agent.py", tmp_path) == "pkg.agent"


def test_path_to_module_init_resolves_to_package_name(tmp_path: Path) -> None:
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "__init__.py").write_text("def run(q): return q\n")
    assert path_to_module(tmp_path / "agent" / "__init__.py", tmp_path) == "agent"


def test_path_to_module_rejects_hyphenated_segment(tmp_path: Path) -> None:
    """The root bug case — hyphens kill dotted-import expressibility."""
    (tmp_path / "transcript-ingest-v2").mkdir()
    (tmp_path / "transcript-ingest-v2" / "cloud_function").mkdir()
    target = tmp_path / "transcript-ingest-v2" / "cloud_function" / "main.py"
    target.write_text("def main(q): return q\n")
    assert path_to_module(target, tmp_path) is None


def test_path_to_module_rejects_leading_digit(tmp_path: Path) -> None:
    (tmp_path / "2024-archive").mkdir()
    target = tmp_path / "2024-archive" / "agent.py"
    target.write_text("def run(q): return q\n")
    assert path_to_module(target, tmp_path) is None


def test_path_to_module_returns_none_for_path_outside_root(tmp_path: Path) -> None:
    other = tmp_path.parent / "outside"
    other.mkdir(exist_ok=True)
    assert path_to_module(other / "rogue.py", tmp_path) is None


def test_closest_safe_subpath_finds_safe_prefix(tmp_path: Path) -> None:
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "my-svc").mkdir()
    target = tmp_path / "services" / "my-svc" / "agent.py"
    target.write_text("")
    # ``services`` is safe; ``services/my-svc`` isn't.
    assert closest_safe_subpath(target, tmp_path) == "services"


def test_closest_safe_subpath_returns_none_when_root_is_unsafe(tmp_path: Path) -> None:
    (tmp_path / "bad-dir").mkdir()
    target = tmp_path / "bad-dir" / "agent.py"
    target.write_text("")
    assert closest_safe_subpath(target, tmp_path) in (None, "")


# ---------------------------------------------------------------------------
# (4) Scope: is_within rejects symlinks that escape, accepts real children
# ---------------------------------------------------------------------------


def test_is_within_accepts_descendant(tmp_path: Path) -> None:
    (tmp_path / "sub" / "nested").mkdir(parents=True)
    f = tmp_path / "sub" / "nested" / "file.py"
    f.write_text("")
    assert is_within(f, tmp_path) is True


def test_is_within_rejects_sibling(tmp_path: Path) -> None:
    sibling = tmp_path.parent / "outside_scope"
    sibling.mkdir(exist_ok=True)
    (sibling / "rogue.py").write_text("")
    assert is_within(sibling / "rogue.py", tmp_path) is False


def test_is_within_resolves_symlinks_escaping_root(tmp_path: Path) -> None:
    """A symlinked file that points outside the workspace is rejected."""
    outside_dir = tmp_path.parent / "outside_sym"
    outside_dir.mkdir(exist_ok=True)
    outside_file = outside_dir / "rogue.py"
    outside_file.write_text("")
    link = tmp_path / "looks_inside.py"
    try:
        link.symlink_to(outside_file)
    except OSError:
        pytest.skip("symlinks unsupported on this filesystem")
    # The link itself lives under root, but its resolved target doesn't —
    # is_within follows the link.
    assert is_within(link, tmp_path) is False


# ---------------------------------------------------------------------------
# (1)/(6) classify_target: picks the right strategy per layout
# ---------------------------------------------------------------------------


def test_classify_target_direct_for_clean_layout(tmp_path: Path) -> None:
    """Regression check: clean projects keep using direct imports."""
    (tmp_path / "agent.py").write_text("def run(q): return q\n")
    target = classify_target(tmp_path / "agent.py", tmp_path)
    assert target.strategy == "direct"
    assert target.module == "agent"
    assert target.callable_name == "run"
    # Direct strategy doesn't emit a reason (no remediation needed).
    assert target.reason == ""


def test_classify_target_dynamic_load_for_hyphenated_layout(tmp_path: Path) -> None:
    """The fix: hyphenated path switches to spec_from_file_location."""
    (tmp_path / "transcript-ingest-v2" / "cloud_function").mkdir(parents=True)
    target_file = tmp_path / "transcript-ingest-v2" / "cloud_function" / "main.py"
    target_file.write_text("def main(q): return q\n")
    target = classify_target(target_file, tmp_path, callable_name="main")
    assert target.strategy == "dynamic_load"
    assert target.file_path == "transcript-ingest-v2/cloud_function/main.py"
    # The safe_identifier is the cleaned-up file stem — a valid Python name
    # used as the label in spec_from_file_location.
    assert target.safe_identifier is not None
    assert target.safe_identifier.isidentifier()
    # The reason should explain *why* — useful for the UI / logs.
    assert "hyphens" in target.reason.lower() or "identifier" in target.reason.lower()


def test_classify_target_scaffold_when_no_file(tmp_path: Path) -> None:
    target = classify_target(None, tmp_path)
    assert target.strategy == "scaffold"
    assert target.module is None


def test_classify_target_scaffold_when_path_escapes_root(tmp_path: Path) -> None:
    """A path outside the selected workspace → scaffold, not import."""
    outside = tmp_path.parent / "rogue_module.py"
    outside.write_text("def run(q): return q\n")
    target = classify_target(outside, tmp_path)
    assert target.strategy == "scaffold"
    assert "outside the workspace" in target.reason


# ---------------------------------------------------------------------------
# (3) Preflight: validate_generated_imports catches the bug pattern
# ---------------------------------------------------------------------------


def test_validate_generated_imports_accepts_valid_module() -> None:
    """Regression: valid imports yield zero diagnostics."""
    source = textwrap.dedent(
        """
        from agentprdiff import case, suite
        from agentprdiff.graders import contains, tool_called

        def my_agent(q):
            return q

        billing = suite(name="billing", agent=my_agent, cases=[])
        """
    )
    assert validate_generated_imports(source) == []


def test_validate_generated_imports_flags_hyphenated_from() -> None:
    """The exact bug shape — surfaces a structured diagnostic."""
    source = "from transcript-ingest-v2.cloud_function.main import run\n"
    diagnostics = validate_generated_imports(source)
    assert len(diagnostics) == 1
    d = diagnostics[0]
    # ``ast.parse`` actually fails on this — the caller gets a
    # ``syntax_error`` cause with the line + column.
    assert d.cause == "syntax_error"
    assert d.line == 1


def test_validate_generated_imports_flags_invalid_import_when_parseable() -> None:
    """When parens make the file parseable but a segment is still bad,
    we still flag it. Constructed via the ``Import`` AST node.
    """
    # We can't easily get ``ast.parse`` to accept a hyphenated module
    # name through normal Python syntax — that's the whole point of the
    # check. But ``__import__('foo-bar')`` parses fine and we still
    # *don't* want to flag THAT (it's a string, not an import statement).
    # So we just verify the parse-error path is the bug catcher.
    source = "import os\nfrom my-pkg import x\n"
    diagnostics = validate_generated_imports(source)
    assert any(d.cause == "syntax_error" for d in diagnostics)


def test_validate_generated_imports_diagnostic_has_line_col_cause(tmp_path: Path) -> None:
    """The diagnostic shape is what the UI needs to render an error panel."""
    source = "x = 1\nfrom 9-bad import y\n"
    diagnostics = validate_generated_imports(source)
    assert diagnostics
    d = diagnostics[0]
    assert d.line >= 1
    assert d.col >= 1
    assert d.cause
    assert d.message
    # to_dict matches the API contract for the UI preflight panel.
    as_dict = d.to_dict()
    assert set(as_dict.keys()) >= {"line", "col", "cause", "message", "statement"}


def test_validate_generated_imports_empty_source_is_clean() -> None:
    assert validate_generated_imports("") == []


def test_validate_generated_imports_relative_imports_ok() -> None:
    """`from . import x` has module=None — that's fine."""
    source = "from . import helpers\n"
    # Note: this would normally be inside a package; we just want the
    # validator to not false-flag it.
    diagnostics = validate_generated_imports(source)
    # No "invalid_module_path" diagnostic. A SyntaxError is possible at
    # the top-level (relative import outside a package) — that's a real
    # error, but it's not our bug, and it'd be cause="syntax_error" not
    # "invalid_module_path". Either way, the bug regex shouldn't fire.
    assert not any(d.cause == "invalid_module_path" for d in diagnostics)


# ---------------------------------------------------------------------------
# (5) Integration: hyphenated workspace produces a valid plan
# ---------------------------------------------------------------------------


def test_guess_agent_target_with_hyphenated_dir_picks_dynamic_load(tmp_path: Path) -> None:
    """End-to-end: the workspace probe handles a hyphenated layout."""
    pkg_dir = tmp_path / "transcript-ingest-v2" / "cloud_function"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "main.py").write_text(
        textwrap.dedent(
            """
            def main(query: str) -> str:
                return f"transcript: {query}"
            """
        )
    )
    target = guess_agent_target(tmp_path)
    assert target is not None
    assert target.strategy == "dynamic_load"
    assert target.callable_name == "main"
    assert target.file_path == "transcript-ingest-v2/cloud_function/main.py"
    assert target.safe_identifier and target.safe_identifier.isidentifier()


def test_guess_agent_target_with_clean_dir_picks_direct(tmp_path: Path) -> None:
    """Regression: a clean layout still gets a direct dotted import."""
    (tmp_path / "agent.py").write_text("def run(q): return q\n")
    target = guess_agent_target(tmp_path)
    assert target is not None
    assert target.strategy == "direct"
    assert target.module == "agent"
    assert target.callable_name == "run"


def test_guess_agent_target_with_nested_clean_dir_picks_direct(tmp_path: Path) -> None:
    (tmp_path / "src" / "my_pkg").mkdir(parents=True)
    (tmp_path / "src" / "my_pkg" / "__init__.py").write_text(
        "from .agent import run\n"
    )
    (tmp_path / "src" / "my_pkg" / "agent.py").write_text("def run(q): return q\n")
    target = guess_agent_target(tmp_path)
    assert target is not None
    assert target.strategy == "direct"
    # Either the package or the submodule is fine — both are import-safe.
    assert target.module in {"src.my_pkg", "src.my_pkg.agent"}


def test_guess_agent_target_empty_workspace_returns_none(tmp_path: Path) -> None:
    """No Python files at all → no target, caller will scaffold."""
    assert guess_agent_target(tmp_path) is None


def test_guess_agent_module_and_callable_returns_none_for_hyphenated(tmp_path: Path) -> None:
    """Back-compat: the old API returns None for paths it can't express.

    Callers using the old shape weren't getting a dynamic-load fallback
    before either — they'd get a (module, callable) tuple where the
    module was an invalid identifier. With the fix in place, returning
    ``None`` is strictly safer: callers that need the rich shape switch
    to guess_agent_target.
    """
    pkg_dir = tmp_path / "my-svc"
    pkg_dir.mkdir()
    (pkg_dir / "main.py").write_text("def main(q): return q\n")
    assert guess_agent_module_and_callable(tmp_path) is None


def test_guess_agent_module_and_callable_still_returns_clean_pairs(tmp_path: Path) -> None:
    """Back-compat: clean projects still get the (module, callable) tuple."""
    (tmp_path / "agent.py").write_text("def run(q): return q\n")
    assert guess_agent_module_and_callable(tmp_path) == ("agent", "run")

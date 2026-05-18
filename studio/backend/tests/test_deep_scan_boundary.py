"""Scan-boundary enforcement tests for the deep-scan workspace probe.

The brief asks for two guarantees:

* By default the scan never reads files outside the selected project
  root. Sibling repositories are excluded unless the user explicitly
  opts in (``scan_include_parent=True``).
* The scan emits a structured manifest containing the resolved scan
  root, the list of files actually included with their byte counts,
  the total byte count, and any files the scope guard rejected.

We exercise the private ``_deep_scan_workspace`` helper directly —
keeping it private to the API module is deliberate (it's not a public
API), but the tests pin its contract because the manifest shape is
part of the response.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentprdiff_studio.api.agents_md import _deep_scan_workspace


def _make_workspace_layout(tmp_path: Path) -> Path:
    """Build a small but realistic layout::

        tmp_path/
            project/
                agent.py
                README.md
                tools/
                    weather.py
            sibling_repo/
                evil.py        # NOT to appear in the default scan
    """
    project = tmp_path / "project"
    (project / "tools").mkdir(parents=True)
    (project / "agent.py").write_text(
        "def run(query): return 'ok'\n"
    )
    (project / "README.md").write_text("# project readme\n")
    (project / "tools" / "weather.py").write_text("def get_weather(): pass\n")

    sibling = tmp_path / "sibling_repo"
    sibling.mkdir()
    (sibling / "evil.py").write_text("# sensitive data\n")
    return project


def test_default_scan_stays_inside_project_root(tmp_path: Path) -> None:
    project = _make_workspace_layout(tmp_path)
    context, manifest = _deep_scan_workspace(
        project, "agent", include_parent=False
    )
    # Scope guarantee — the manifest's root is the project, not the
    # parent that holds the sibling repo.
    assert manifest["root"] == str(project.resolve())
    assert manifest["sibling_repos_included"] is False
    # No file under ``sibling_repo`` should appear.
    paths = [f["path"] for f in manifest["files"]]
    for p in paths:
        assert "sibling_repo" not in p
        assert not p.startswith("..")
    # The agent file at the project root is the first candidate.
    assert paths[0] == "agent.py"
    # README and tools/ are pulled in too.
    assert any(p.endswith("README.md") for p in paths)
    assert any(p.endswith("weather.py") for p in paths)
    # Sibling content didn't sneak into the concatenated context.
    assert "sensitive data" not in context


def test_sibling_optin_expands_to_parent(tmp_path: Path) -> None:
    project = _make_workspace_layout(tmp_path)
    _, manifest = _deep_scan_workspace(
        project, "agent", include_parent=True
    )
    # When the user explicitly opts in, the scan root is the parent.
    assert manifest["root"] == str(project.resolve().parent)
    assert manifest["sibling_repos_included"] is True


def test_manifest_carries_total_bytes_and_rejected_list(tmp_path: Path) -> None:
    project = _make_workspace_layout(tmp_path)
    _, manifest = _deep_scan_workspace(
        project, "agent", include_parent=False
    )
    assert manifest["total_bytes"] == sum(f["bytes"] for f in manifest["files"])
    assert isinstance(manifest["rejected"], list)
    # Nothing should have been rejected on a clean layout.
    assert manifest["rejected"] == []


def test_missing_workspace_returns_empty_manifest(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    context, manifest = _deep_scan_workspace(missing, "agent")
    assert context == ""
    assert manifest["files"] == []
    assert manifest["total_bytes"] == 0


def test_excluded_directories_not_walked(tmp_path: Path) -> None:
    """Files under .git / __pycache__ / .venv / node_modules must not
    appear in the manifest — the brief explicitly lists these as
    candidates for accidental inclusion."""
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    (project / "__pycache__").mkdir()
    (project / ".venv" / "lib").mkdir(parents=True)
    (project / "node_modules" / "evil_pkg").mkdir(parents=True)
    (project / "agent.py").write_text("def run(q): return q\n")
    (project / ".git" / "config").write_text("[secret]\nthing = bad\n")
    (project / "__pycache__" / "compiled.pyc").write_text("opaque\n")
    (project / ".venv" / "lib" / "wheel.py").write_text("# noise\n")
    (project / "node_modules" / "evil_pkg" / "index.js").write_text(
        "console.log('bad')\n"
    )
    context, manifest = _deep_scan_workspace(project, "agent")
    paths = [f["path"] for f in manifest["files"]]
    for bad in (".git", "__pycache__", ".venv", "node_modules"):
        assert all(bad not in p for p in paths), (
            f"excluded dir {bad!r} leaked into scan: {paths}"
        )
    assert "secret" not in context
    assert "opaque" not in context
    assert "evil_pkg" not in context


def test_symlink_escaping_root_is_rejected(tmp_path: Path) -> None:
    """A symlink inside the workspace that points at a sibling file
    must NOT pull the sibling's content into the scan."""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.py"
    secret.write_text("API_KEY = 'leak me'\n")

    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text("def run(q): return q\n")
    # Drop a symlink inside the workspace that points outside.
    link = project / "linked_secret.py"
    try:
        link.symlink_to(secret)
    except OSError:  # pragma: no cover — Windows w/o admin
        pytest.skip("symlink not supported on this platform")

    context, manifest = _deep_scan_workspace(project, "agent")
    # The symlink target lives outside the scan root → it must not
    # contribute content.
    assert "leak me" not in context
    # And it shows up in the rejected list with a clear reason.
    rejected_paths = [r["path"] for r in manifest["rejected"]]
    assert any("linked_secret" in p for p in rejected_paths)

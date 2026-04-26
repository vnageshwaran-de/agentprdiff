"""Tests for the suite loader's import-path setup.

Real-world adopters point at a suite file like ``suites/coursenotes.py`` from
their project root and expect both project-level imports
(``from agent.agent import ...``) and sibling-level imports
(``from suites._eval_agent import ...``) to resolve. The loader has to put
the right directories on ``sys.path``, and clean them up afterwards.
"""

from __future__ import annotations

import sys

import pytest

from agentprdiff.loader import load_suites


def test_loader_adds_cwd_so_project_modules_resolve(tmp_path, monkeypatch):
    """Suites should be able to ``from project_module import ...`` when the
    user invoked ``agentprdiff record`` from the project root."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "myhelper.py").write_text("MAGIC = 42\n")

    suites_dir = tmp_path / "suites"
    suites_dir.mkdir()
    (suites_dir / "demo.py").write_text(
        "from agentprdiff import suite, case\n"
        "from agentprdiff.graders import contains\n"
        "from myhelper import MAGIC\n"
        "assert MAGIC == 42\n"
        "demo = suite(name='demo', agent=lambda x: 'ok',\n"
        "             cases=[case(name='c', input='', expect=[contains('ok')])])\n"
    )

    suites = load_suites(suites_dir / "demo.py")
    assert len(suites) == 1
    assert suites[0].name == "demo"


def test_loader_adds_suite_dir_so_sibling_modules_resolve(tmp_path, monkeypatch):
    """Sibling helpers next to the suite file (``_helper.py``,
    ``_stubs.py``) should resolve via the suite file's parent dir."""
    monkeypatch.chdir(tmp_path)
    suites_dir = tmp_path / "suites"
    suites_dir.mkdir()
    (suites_dir / "_helper.py").write_text("VALUE = 7\n")
    (suites_dir / "demo.py").write_text(
        "from agentprdiff import suite, case\n"
        "from agentprdiff.graders import contains\n"
        "from _helper import VALUE\n"
        "assert VALUE == 7\n"
        "demo = suite(name='d', agent=lambda x: 'ok',\n"
        "             cases=[case(name='c', input='', expect=[contains('ok')])])\n"
    )
    assert len(load_suites(suites_dir / "demo.py")) == 1


def test_loader_does_not_leak_paths_into_subsequent_loads(tmp_path, monkeypatch):
    """sys.path must look identical before and after a load — otherwise a
    long-running runner accumulates stale entries."""
    monkeypatch.chdir(tmp_path)
    snapshot = list(sys.path)

    (tmp_path / "suite.py").write_text(
        "from agentprdiff import suite, case\n"
        "from agentprdiff.graders import contains\n"
        "s = suite(name='s', agent=lambda x: 'ok',\n"
        "          cases=[case(name='c', input='', expect=[contains('ok')])])\n"
    )
    load_suites(tmp_path / "suite.py")
    assert sys.path == snapshot


def test_loader_idempotent_when_paths_already_present(tmp_path, monkeypatch):
    """If the user has already added the suite dir or cwd to sys.path, the
    loader must not double-insert and must not remove entries it didn't
    add."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "h.py").write_text("X = 1\n")
    pre_existing = str(tmp_path)
    sys.path.insert(0, pre_existing)
    snapshot = list(sys.path)

    (tmp_path / "suite.py").write_text(
        "from agentprdiff import suite, case\n"
        "from agentprdiff.graders import contains\n"
        "from h import X\n"
        "assert X == 1\n"
        "s = suite(name='s', agent=lambda x: 'ok',\n"
        "          cases=[case(name='c', input='', expect=[contains('ok')])])\n"
    )
    try:
        load_suites(tmp_path / "suite.py")
        assert sys.path == snapshot
    finally:
        with pytest.MonkeyPatch.context() as m:  # noqa: F841 — just to keep block tidy
            pass
        sys.path.remove(pre_existing)


def test_loader_raises_for_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_suites(tmp_path / "does_not_exist.py")


def test_loader_raises_when_no_suites_defined(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "empty.py").write_text("X = 1\n")
    with pytest.raises(ValueError, match="no module-level Suite"):
        load_suites(tmp_path / "empty.py")

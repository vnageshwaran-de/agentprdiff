"""Load `Suite` objects from user-provided Python files.

We deliberately keep this ultra-simple for v0.1: the user points at a python
file path (or module) and we import it; every module-level `Suite` instance
is a suite to run.

Import-path setup: when we exec the suite file, we insert two directories
onto ``sys.path``:

1. The suite file's own parent directory — for sibling helpers, e.g. a
   ``suite.py`` next to a ``stubs.py``.
2. The current working directory — typically the project root from which
   ``agentprdiff record`` was invoked. This lets the suite import the
   adopter's own modules (``from agent.agent import ...``,
   ``from config import ...``, ``from suites._eval_agent import ...``)
   without forcing every adopter to manipulate ``sys.path`` themselves.

Both insertions are reversed after exec so the runner's environment doesn't
leak suite-specific paths into subsequent loads.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import sys
from pathlib import Path

from .core import Suite


def load_suites(path: str | Path) -> list[Suite]:
    """Import `path` and return every module-level `Suite` it defines."""
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"no such file: {p}")
    if p.is_dir():
        raise IsADirectoryError(
            f"{p} is a directory; point at a .py file that defines Suites."
        )

    module_name = f"_agentprdiff_suite_{abs(hash(str(p)))}"
    spec = importlib.util.spec_from_file_location(module_name, p)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"could not load suite file: {p}")
    module = importlib.util.module_from_spec(spec)

    # Make both the suite file's directory and the cwd importable. We track
    # what we actually inserted so we only remove our own contributions.
    parent_dir = str(p.parent)
    cwd = os.getcwd()
    inserted: list[str] = []
    for entry in (parent_dir, cwd):
        if entry and entry not in sys.path:
            sys.path.insert(0, entry)
            inserted.append(entry)

    try:
        spec.loader.exec_module(module)
    finally:
        for entry in inserted:
            with contextlib.suppress(ValueError):
                sys.path.remove(entry)

    suites = [v for v in vars(module).values() if isinstance(v, Suite)]
    if not suites:
        raise ValueError(
            f"{p} defines no module-level Suite objects. "
            "Use `from agentprdiff import suite` and bind the result to a variable."
        )
    return suites

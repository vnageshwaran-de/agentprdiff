"""Validate LLM-generated suite files end-to-end before writing them.

Two checks beyond a syntax pass:

* **Heuristic match.** Does the file contain ``from agentprdiff``/``import
  agentprdiff`` and a ``suite(...)`` call? Discovery uses this exact pair
  later — if either is missing, the file would be invisible to Studio even
  if it parses.

* **Loadability.** Run the engine's ``load_suites`` against the proposed
  content. This catches ``ImportError``/``ModuleNotFoundError`` from the
  user's agent module, missing graders, etc. We do this by writing the
  content to a temp file under the workspace's ``.studio-staging/`` dir
  (so sibling imports like ``from agent import …`` resolve the same way
  they would once saved), then deleting it.

Also exposes a small workspace probe — :func:`guess_agent_import` — that
the generate endpoint uses to pre-fill the LLM's ``agent_import_target``
instead of blindly defaulting to ``"agent"``.
"""

from __future__ import annotations

import ast
import re
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_IMPORT_HINTS = ("from agentprdiff", "import agentprdiff")
_SUITE_HINT = re.compile(r"\bsuite\s*\(", re.MULTILINE)

# Skip these dirs when walking for the agent module — same exclusion list
# as discovery, kept here so we don't have to cross-import.
_EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build",
    ".agentprdiff", ".studio-venv", ".studio-staging", ".studio-tour",
}


# Modules that are likely to be runtime dependencies of the user's agent
# (installed into the per-project venv by the executor) rather than Studio's
# own deps. If the in-process load fails because one of these is missing,
# the suite will still run fine — the venv has them.
_LIKELY_PROJECT_DEPS = {
    "openai",
    "anthropic",
    "google",
    "google.generativeai",
    "google.genai",
    "vertexai",
    "groq",
    "together",
    "cohere",
    "mistralai",
    "langchain",
    "langchain_core",
    "langchain_openai",
    "langchain_anthropic",
    "langchain_community",
    "langgraph",
    "llama_index",
    "llamaindex",
    "ollama",
    "instructor",
    "litellm",
    "huggingface_hub",
    "transformers",
    "torch",
    "tiktoken",
    # Plus your project's own modules — anything imported from the workspace
    # that isn't on Studio's sys.path. We detect this loosely below.
}


@dataclass(slots=True)
class ValidationResult:
    compiles: bool
    parse_error: str | None
    has_imports: bool
    has_suite_call: bool
    # ``True`` if the file imported cleanly in Studio's host process.
    loadable: bool
    # ``True`` if the file *would* load given the project's runtime deps —
    # missing imports are all known runtime deps (openai/anthropic/...) or
    # project-local modules. Set to True whenever ``loadable`` is True.
    loadable_via_venv: bool
    # When loadable is False, the missing top-level module name (e.g.
    # ``"openai"``). Lets the UI suggest what to add to requirements.txt.
    missing_module: str | None
    load_error: str | None
    discovered_suites: list[str]
    total_cases: int

    def to_dict(self) -> dict:
        return {
            "compiles": self.compiles,
            "parse_error": self.parse_error,
            "has_imports": self.has_imports,
            "has_suite_call": self.has_suite_call,
            "loadable": self.loadable,
            "loadable_via_venv": self.loadable_via_venv,
            "missing_module": self.missing_module,
            "load_error": self.load_error,
            "discovered_suites": self.discovered_suites,
            "total_cases": self.total_cases,
        }


def validate(content: str, workspace: Path | None) -> ValidationResult:
    """Cheap-then-expensive: AST, then heuristic, then actual load."""
    out = ValidationResult(
        compiles=False, parse_error=None,
        has_imports=False, has_suite_call=False,
        loadable=False, loadable_via_venv=False,
        missing_module=None, load_error=None,
        discovered_suites=[], total_cases=0,
    )

    try:
        ast.parse(content)
        out.compiles = True
    except SyntaxError as exc:
        out.parse_error = f"line {exc.lineno}: {exc.msg}"
        return out

    out.has_imports = any(hint in content for hint in _IMPORT_HINTS)
    out.has_suite_call = bool(_SUITE_HINT.search(content))

    if not (out.has_imports and out.has_suite_call):
        out.load_error = (
            "Discovery requires both an `agentprdiff` import and a "
            "`suite(...)` call at module level."
        )
        return out

    if workspace is None:
        # HTTP-mode projects shouldn't reach here, but be defensive.
        out.load_error = "no workspace on disk; can't try to load"
        return out

    # Actually run the engine loader against a temp file in the workspace
    # so sibling imports resolve. Stash under a hidden staging dir; clean
    # up after.
    staging = workspace / ".studio-staging"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        from agentprdiff.loader import load_suites
    except Exception as exc:  # pragma: no cover
        out.load_error = f"engine import failed: {exc}"
        return out

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", dir=staging, delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        with _workspace_on_path(workspace):
            suites = load_suites(tmp_path)
        out.loadable = True
        out.loadable_via_venv = True
        out.discovered_suites = [s.name for s in suites]
        out.total_cases = sum(len(s.cases) for s in suites)
    except ModuleNotFoundError as exc:
        # The most common failure: a module imported by the suite isn't in
        # Studio's host environment. That's *fine* if it's a known project-
        # runtime dep — the executor will use the project venv at run time
        # where it does exist.
        missing = _missing_module_name(exc, content, workspace)
        out.missing_module = missing
        out.load_error = f"{type(exc).__name__}: {exc}"
        if missing and _likely_project_dep(missing, workspace):
            out.loadable_via_venv = True
    except ImportError as exc:
        # Distinct from ModuleNotFoundError: e.g.
        #   "cannot import name 'run' from 'agent' (.../agent/__init__.py)"
        # The module exists; the symbol doesn't. Surface what *is* available
        # so the user (or a retry) can pick a real name.
        message = str(exc)
        available = _available_names_for_import_error(message, workspace)
        if available:
            out.load_error = (
                f"{message}. Available top-level names in that module: "
                f"{', '.join(available[:10])}."
            )
        else:
            out.load_error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 — capture anything
        out.load_error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    return out


def _missing_module_name(
    exc: ModuleNotFoundError, content: str, workspace: Path
) -> str | None:
    """Pick a useful module name out of the exception or the import lines.

    ``exc.name`` is the most reliable source; fall back to scanning the
    file's imports if the standard library evolution ever changes that.
    """
    if exc.name:
        return exc.name
    # Fallback: first top-level non-stdlib-looking module.
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("from "):
            mod = s.split()[1].split(".")[0]
            return mod
        if s.startswith("import "):
            mod = s.split()[1].split(".")[0].rstrip(",")
            return mod
    return None


_IMPORT_ERROR_RE = re.compile(r"cannot import name '([^']+)' from '([^']+)'")


def _available_names_for_import_error(message: str, workspace: Path) -> list[str]:
    """Pull the module name out of an ImportError and list its defs.

    Falls back to peeking at sibling modules in the package if the
    ``__init__.py`` exports nothing useful — common when the package's
    real surface lives in ``agent/main.py`` or similar.
    """
    m = _IMPORT_ERROR_RE.search(message)
    if not m:
        return []
    dotted = m.group(2)
    module_file = _module_path_to_file(workspace, dotted)
    if module_file is None:
        return []

    defs = _top_level_defs(module_file)
    if defs:
        return defs

    # __init__.py was empty (or only contained things our AST walk skipped,
    # like conditional imports). Peek into the package's sibling modules
    # and surface any callables they export, prefixed so the user knows
    # where to import from.
    if module_file.name == "__init__.py":
        pkg_dir = module_file.parent
        candidates: list[str] = []
        for sibling in sorted(pkg_dir.glob("*.py")):
            if sibling.name == "__init__.py":
                continue
            sib_defs = _top_level_defs(sibling)
            for d in sib_defs:
                if not d.startswith("_"):
                    candidates.append(f"{sibling.stem}.{d}")
        return candidates
    return []


def _likely_project_dep(module_name: str, workspace: Path) -> bool:
    """True if the missing module is likely satisfied by the project venv.

    Two heuristics:

    1. The module is one we know is commonly a project runtime dep
       (``openai``, ``anthropic``, ``langchain``, …).
    2. The module exists *as a top-level file or package inside the
       workspace* — i.e. it's the user's own code, importable once we
       set the project venv up correctly.
    """
    top = module_name.split(".")[0]
    if top in _LIKELY_PROJECT_DEPS:
        return True
    # User-local module: ``<workspace>/<top>.py`` or ``<workspace>/<top>/__init__.py``.
    if (workspace / f"{top}.py").is_file():
        return True
    if (workspace / top / "__init__.py").is_file():
        return True
    return False


@contextmanager
def _workspace_on_path(workspace: Path):
    entry = str(workspace.resolve())
    inserted = entry not in sys.path
    if inserted:
        sys.path.insert(0, entry)
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(entry)
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# Workspace probe: where does this project's agent module live?
# ---------------------------------------------------------------------------


def guess_agent_import(workspace: Path) -> str | None:
    """Best-effort guess for the LLM's ``agent_import_target`` field.

    Thin wrapper around :func:`guess_agent_module_and_callable` that returns
    just the module dotted-path — kept for back-compat callers.
    """
    out = guess_agent_module_and_callable(workspace)
    return out[0] if out else None


def guess_agent_module_and_callable(
    workspace: Path,
) -> tuple[str, str] | None:
    """Return ``(module, callable)`` for the project's likely agent.

    Module-resolution: same priority as the original guess. Inside the
    chosen module, we scan top-level names and pick the most likely
    callable, with this fallback chain:

    1. ``run`` — the canonical entrypoint name agentprdiff's docs use.
    2. ``agent`` — common alternative.
    3. ``main`` — sometimes used.
    4. The single ``class`` or ``def`` in the module, if there's exactly one.
    5. The first top-level public name.
    6. If the chosen module is an ``__init__.py`` with nothing useful,
       look in sibling modules of the package and pick the best one
       there — return ``(package.submodule, callable)``.

    If no callable can be identified at all, fall back to ``"run"`` —
    the LLM gets the canonical name; if it doesn't match, the load check
    surfaces a useful error pointing the user at the right name.
    """
    module = _pick_module(workspace)
    if module is None:
        return None
    module_path = _module_path_to_file(workspace, module)
    if module_path is None:
        return (module, "run")

    callable_name = _pick_callable(module_path)
    if callable_name:
        return (module, callable_name)

    # __init__.py was empty — descend into siblings. Skip private modules
    # (``_internal.py``, ``_utils.py``) and prefer modules with canonical
    # entrypoint names where possible.
    if module_path.name == "__init__.py":
        pkg_dir = module_path.parent
        siblings = [
            s for s in sorted(pkg_dir.glob("*.py"))
            if s.name != "__init__.py" and not s.name.startswith("_")
        ]
        # Prefer canonical filenames (``main.py`` / ``agent.py`` / ``run.py``).
        ranked = sorted(siblings, key=lambda p: 0 if p.stem in ("main", "agent", "run") else 1)
        for sibling in ranked:
            sib_callable = _pick_callable(sibling)
            if sib_callable:
                return (f"{module}.{sibling.stem}", sib_callable)

    return (module, "run")


def list_module_callables(module_file: Path) -> list[str]:
    """Top-level ``def`` names in a Python file. For error surfacing."""
    return _top_level_defs(module_file)


# ---------------------------------------------------------------------------


def _pick_module(workspace: Path) -> str | None:
    root = workspace / "agent.py"
    if root.is_file():
        return "agent"
    pkg_init = workspace / "agent" / "__init__.py"
    if pkg_init.is_file():
        return "agent"

    for path in workspace.rglob("agent.py"):
        if any(p in _EXCLUDE_DIRS for p in path.relative_to(workspace).parts):
            continue
        return path.relative_to(workspace).with_suffix("").as_posix().replace("/", ".")

    # Look for any module exporting ``def run`` / ``def agent`` etc.
    for path in workspace.rglob("*.py"):
        if any(p in _EXCLUDE_DIRS for p in path.relative_to(workspace).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(needle in text for needle in ("def run(", "def agent(", "def main(")):
            return path.relative_to(workspace).with_suffix("").as_posix().replace("/", ".")
    return None


def _module_path_to_file(workspace: Path, dotted: str) -> Path | None:
    """Resolve a dotted module path to the file we should read."""
    parts = dotted.split(".")
    direct = workspace.joinpath(*parts[:-1], parts[-1] + ".py")
    if direct.is_file():
        return direct
    pkg = workspace.joinpath(*parts, "__init__.py")
    if pkg.is_file():
        return pkg
    return None


def _top_level_defs(module_file: Path) -> list[str]:
    """Return the names this module makes importable to others.

    Covers:

    * ``def foo`` / ``async def foo``
    * ``class Foo``  (agents are commonly classes with ``__call__``)
    * ``foo = ...``  (module-level assignments — usually constants but also
      function aliases like ``run = _internal_run``)
    * ``from .x import foo`` / ``from x import foo as bar``  — the
      ``__init__.py``-as-re-export pattern.
    * ``import foo``  (the bound name is ``foo``).
    * ``__all__ = [...]``  — if present, the listed names take precedence
      and we return them in declaration order. (This is the canonical
      Python convention for "what this module exports.")

    Underscored names are *not* filtered here; the caller (``_pick_callable``)
    handles the public-vs-private preference.
    """
    try:
        tree = ast.parse(module_file.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError):
        return []

    # First pass: look for an ``__all__`` declaration. If found, trust it.
    for node in tree.body:
        target_name = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name) and t.id == "__all__":
                target_name = t.id
                value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
        ):
            target_name = node.target.id
            value = node.value
        if target_name and isinstance(value, (ast.List, ast.Tuple)):
            out = []
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    out.append(elt.value)
            if out:
                return out

    # Second pass: every other top-level binding.
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                names.append(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.asname or alias.name.split(".", 1)[0])
    # De-duplicate while preserving order.
    seen: set[str] = set()
    out_names: list[str] = []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        out_names.append(n)
    return out_names


def _pick_callable(module_file: Path) -> str | None:
    """Pick the most likely agent entrypoint inside a single module.

    Subtlety: the public surface (``_top_level_defs``) includes imported
    names — useful for ``__init__.py`` re-export patterns, but a footgun
    for plain modules where ``import os`` would otherwise look like an
    exported callable. We resolve this by keeping the "canonical name"
    search broad (so a re-exported ``run`` still wins) while the
    "single public" fallback only considers locally-defined ``def``s and
    ``class``es.
    """
    all_names = _top_level_defs(module_file)
    if not all_names:
        return None
    local_callables = _local_callables(module_file)

    # Preferred names: search across the full surface so re-exports count.
    for preferred in ("run", "agent", "main"):
        if preferred in all_names:
            return preferred

    # Exactly one locally-defined callable → that's the agent.
    public_locals = [n for n in local_callables if not n.startswith("_")]
    if len(public_locals) == 1:
        return public_locals[0]
    if public_locals:
        return public_locals[0]

    # Last resort: any non-underscore name from the full surface, but skip
    # obvious imports of stdlib modules to avoid the ``os`` /
    # ``json`` / ``typing`` mis-pick.
    public_all = [n for n in all_names if not n.startswith("_") and n not in _STDLIB_MODULES]
    if public_all:
        return public_all[0]
    return None


def _local_callables(module_file: Path) -> list[str]:
    """Top-level ``def``s and ``class``es defined in this file (not imports)."""
    try:
        tree = ast.parse(module_file.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError):
        return []
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node.name)
    return out


# A small set of commonly-imported stdlib modules so we don't mistake
# ``import os`` for an exported agent.
_STDLIB_MODULES = {
    "os", "sys", "json", "re", "time", "typing", "pathlib", "dataclasses",
    "datetime", "logging", "asyncio", "functools", "itertools", "collections",
    "subprocess", "tempfile", "io", "argparse", "math", "random", "uuid",
    "hashlib", "base64", "ast", "inspect", "contextlib", "enum",
}

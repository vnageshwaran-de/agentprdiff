"""Find agentprdiff suite files inside a project workspace.

Heuristic (fast, no import): a file is a candidate if it's a ``*.py`` under
the workspace (excluding common noise dirs) and its source mentions either
``from agentprdiff`` or ``import agentprdiff``. We don't try to be clever —
the user can always re-sync after fixing a path.

For each candidate we then attempt a real load via the engine's
``agentprdiff.loader.load_suites`` to confirm it defines module-level Suite
objects and to count cases. This step is best-effort — if the load fails we
record the file with case_count=0 and a load_error, so the UI can flag it.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import ast_extract

_EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".agentprdiff",
    ".studio-venv",
}

_IMPORT_HINTS = ("from agentprdiff", "import agentprdiff")
# Distinguishes real suite files (which call the ``suite(...)`` helper) from
# engine-internal modules and test files that merely import agentprdiff.
_SUITE_HINTS = ("suite(", "agentprdiff.suite(")

# Modules that are almost certainly satisfied by the project venv at run time
# even though they're not in Studio's host container. Kept loose intentionally
# — false positives just mean we surface a suite that the executor fails on
# (and the user gets a clear shim error), which is strictly better than
# silently hiding it.
_PROJECT_VENV_MODULES = {
    "openai", "anthropic", "google", "vertexai", "groq", "together",
    "cohere", "mistralai",
    "langchain", "langchain_core", "langchain_openai", "langchain_anthropic",
    "langchain_community", "langgraph", "llama_index", "llamaindex",
    "ollama", "instructor", "litellm", "huggingface_hub", "transformers",
    "torch", "tiktoken",
    # yt-dlp and other agent-side libs we've seen in real adoptions.
    "yt_dlp", "youtube_dl",
}


def _is_project_venv_module(missing: str | None, workspace: Path) -> bool:
    """Is the missing module one the project's venv almost certainly has?

    True for:
      * a known LLM / agent runtime SDK (``openai``, ``anthropic``, …);
      * a workspace-local top-level package or module — i.e. user code.
    """
    if not missing:
        return False
    top = missing.split(".")[0]
    if top in _PROJECT_VENV_MODULES:
        return True
    # Workspace-local module: file or package at the workspace root.
    if (workspace / f"{top}.py").is_file():
        return True
    if (workspace / top / "__init__.py").is_file():
        return True
    return False


@dataclass(slots=True)
class DiscoveredSuite:
    """One ``Suite`` instance found in a workspace file.

    ``load_error`` is set when the host loader couldn't import the file but
    we *do* think the project venv could (a known runtime dep missing, a
    workspace-local module). In that case ``name`` and ``case_count`` come
    from AST extraction rather than a real ``Suite`` object — they should be
    treated as best-effort. The UI badges the row appropriately.
    """

    name: str
    relative_path: str  # POSIX-style, relative to the workspace root
    case_count: int
    load_error: str | None = None


def _iter_candidates(workspace: Path) -> list[Path]:
    out: list[Path] = []
    for path in workspace.rglob("*.py"):
        # Skip anything under an excluded directory at any depth.
        if any(part in _EXCLUDE_DIRS for part in path.relative_to(workspace).parts):
            continue
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Require both: an agentprdiff import AND a ``suite(...)`` call.
        # This filters out engine-internal modules and tests that import
        # agentprdiff but don't define suites.
        if not any(hint in head for hint in _IMPORT_HINTS):
            continue
        if not any(hint in head for hint in _SUITE_HINTS):
            continue
        out.append(path)
    return out


@contextmanager
def _workspace_on_path(workspace: Path):
    """Insert ``workspace`` at the head of ``sys.path`` for the duration.

    Restored on exit so concurrent calls don't accumulate. ``str(workspace)``
    is used because sys.path entries are strings.
    """
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


@contextmanager
def _isolated_modules():
    """Snapshot ``sys.modules`` so a load doesn't leak entries into later loads.

    The engine loader uses a unique synthetic module name for the suite file
    itself, so it doesn't pollute. But the suite file may transitively import
    sibling modules (``from agent import support_agent``) — those land under
    their plain names and would resolve incorrectly the next time a *different*
    workspace's suite imports a module of the same name.

    We snapshot before, restore after.
    """
    before = set(sys.modules)
    try:
        yield
    finally:
        new_keys = set(sys.modules) - before
        for k in new_keys:
            sys.modules.pop(k, None)


def _load_one(workspace: Path, candidate: Path) -> list[DiscoveredSuite]:
    """Import a candidate file in-process and return its Suites.

    This is best-effort: any exception is captured into ``load_error`` and we
    return a single placeholder entry so the UI can show the file even if it
    can't currently be loaded (e.g. missing project-local deps).
    """
    relative = candidate.relative_to(workspace).as_posix()
    try:
        # Imported here, not at module top, so the discovery module is import-
        # safe even when the engine isn't on sys.path (it always is at runtime).
        from agentprdiff.loader import load_suites
    except Exception as exc:  # pragma: no cover — engine import shouldn't fail
        return [
            DiscoveredSuite(
                name=relative,
                relative_path=relative,
                case_count=0,
                load_error=f"engine import failed: {exc}",
            )
        ]

    try:
        # The engine's loader puts the suite file's parent dir + the process
        # cwd onto sys.path. That's enough for suites + agent modules that
        # live in the same directory, but it misses the common case where the
        # agent lives at the workspace root and the suite lives in
        # ``suites/<name>.py``. We add the workspace root ourselves so
        # ``from agent import …`` resolves the way the adopter expects.
        with _isolated_modules(), _workspace_on_path(workspace):
            suites = load_suites(candidate)
    except ModuleNotFoundError as exc:
        # The host environment is missing a module. If it's plausibly the
        # project's runtime dep (openai/anthropic/langchain/...) or a
        # workspace-local module that just isn't on Studio's sys.path, the
        # *executor* will load it fine at run time — it uses the project
        # venv. We surface the file as a soft-discovered suite, with the
        # name/case_count extracted via AST so the UI has something useful
        # to display.
        if _is_project_venv_module(exc.name, workspace):
            ast_suites = ast_extract.extract_from_file(candidate)
            if ast_suites:
                return [
                    DiscoveredSuite(
                        name=s.name,
                        relative_path=relative,
                        case_count=s.case_count,
                        # Keep the original error so the Diagnose panel can
                        # still surface it; the UI distinguishes "real fail"
                        # from "via-venv" by checking whether the file also
                        # appears in the suite list.
                        load_error=f"{type(exc).__name__}: {exc}",
                    )
                    for s in ast_suites
                ]
        # Either no AST suites or not a project-venv-friendly module —
        # surface as a hard failure.
        return [
            DiscoveredSuite(
                name=relative, relative_path=relative, case_count=0,
                load_error=f"{type(exc).__name__}: {exc}",
            )
        ]
    except Exception as exc:  # noqa: BLE001 — we want any failure surfaced to the UI
        return [
            DiscoveredSuite(
                name=relative,
                relative_path=relative,
                case_count=0,
                load_error=f"{type(exc).__name__}: {exc}",
            )
        ]

    return [
        DiscoveredSuite(
            name=s.name,
            relative_path=relative,
            case_count=len(s.cases),
        )
        for s in suites
    ]


async def discover_suites(workspace: Path) -> list[DiscoveredSuite]:
    """Walk ``workspace`` and return every Suite we can find.

    The walk + load runs in a worker thread because the loader does real disk
    IO and module imports.
    """

    def _work() -> list[DiscoveredSuite]:
        results: list[DiscoveredSuite] = []
        for candidate in _iter_candidates(workspace):
            results.extend(_load_one(workspace, candidate))
        return results

    return await asyncio.to_thread(_work)

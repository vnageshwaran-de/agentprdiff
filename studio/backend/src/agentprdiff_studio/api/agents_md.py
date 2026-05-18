"""AGENTS.md endpoints.

* ``GET  /api/projects/{id}/agents-md``         — parsed structure + raw markdown.
* ``POST /api/projects/{id}/agents-md/scaffold``        — write a starter
  AGENTS.md into the workspace (git/zip only — http projects have no disk).
* ``POST /api/projects/{id}/agents-md/scaffold-suite``  — turn parsed
  ``*_cases.md`` cases into a suite. For http projects, that creates a Suite
  DB row directly. For git/zip, it writes ``suites/<name>.py`` to the
  workspace and returns the new path so the UI can prompt a Sync.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import delete

from ..agents_md import (
    parse_workspace,
    starter_agents_md,
    suite_python_skeleton,
)
from ..agents_md.preflight import ErrorCode, run_preflight
from ..agents_md.templates import http_suite_definition
from ..agents_md.validate import (
    guess_agent_module_and_callable,
    guess_agent_target,
    validate,
)
from ..db import models
from ..db.session import get_session
from ..intake.discovery import discover_suites
from ..llm import LLMError, resolve_provider
from ..resources import bundled_agents_md

router = APIRouter(prefix="/api/projects", tags=["agents-md"])


class ScaffoldStarterIn(BaseModel):
    # Optional: defaults to ``True``; set to ``False`` to refuse to overwrite.
    overwrite: bool = False


class ScaffoldStarterOut(BaseModel):
    path: str
    wrote_bytes: int


class ScaffoldSuiteIn(BaseModel):
    suite_name: str = Field(min_length=1, max_length=200)
    # For git/zip, the agent import target — e.g. ``my_pkg.agent``. Defaults
    # to a reasonable placeholder.
    agent_import_target: str = "agent"


class ScaffoldSuiteOut(BaseModel):
    intake_mode: str
    suite_id: int | None
    file_path: str | None
    cases_used: int


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


@router.get("/{project_id}/agents-md")
async def get_agents_md(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    if not project.workspace_path:
        # HTTP projects don't have a workspace. Return a stub response — the
        # UI can show a friendly empty state pointing at the canonical doc.
        return {
            "exists": False,
            "workspace": None,
            "agents_md_path": None,
            "agents_md_content": "",
            "sections": [],
            "code_blocks": [],
            "cases_files": [],
            "cases": [],
            "supports_disk_scaffold": False,
        }

    parsed = parse_workspace(Path(project.workspace_path))
    out = parsed.to_dict()
    out["supports_disk_scaffold"] = project.intake_mode in ("git", "zip")
    return out


# ---------------------------------------------------------------------------
# scaffold the starter AGENTS.md
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/agents-md/scaffold", response_model=ScaffoldStarterOut
)
async def scaffold_starter(
    project_id: int,
    payload: ScaffoldStarterIn,
    session: AsyncSession = Depends(get_session),
) -> ScaffoldStarterOut:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if project.intake_mode == "http" or not project.workspace_path:
        raise HTTPException(
            status_code=400,
            detail="Starter AGENTS.md scaffold needs a disk workspace; "
            "use git or zip intake for this action.",
        )

    workspace = Path(project.workspace_path)
    target = workspace / "AGENTS.md"
    if target.exists() and not payload.overwrite:
        raise HTTPException(
            status_code=409,
            detail="AGENTS.md already exists in the workspace. Pass overwrite=true to replace it.",
        )

    content = starter_agents_md(project.name)
    target.write_text(content, encoding="utf-8")
    return ScaffoldStarterOut(path="AGENTS.md", wrote_bytes=len(content.encode("utf-8")))


# ---------------------------------------------------------------------------
# scaffold a suite from parsed *_cases.md
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/agents-md/scaffold-suite", response_model=ScaffoldSuiteOut
)
async def scaffold_suite(
    project_id: int,
    payload: ScaffoldSuiteIn,
    session: AsyncSession = Depends(get_session),
) -> ScaffoldSuiteOut:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    # Get the parsed cases.
    if not project.workspace_path:
        raise HTTPException(
            status_code=400,
            detail="This action reads *_cases.md from disk. Use a git or zip "
            "project, or author the suite via POST /projects/{id}/suites directly.",
        )
    parsed = parse_workspace(Path(project.workspace_path))
    if not parsed.cases:
        raise HTTPException(
            status_code=400,
            detail="No cases found. Studio walks the workspace for `*_cases.md` "
            "files and parses `### case_name` blocks.",
            # The error handler in api/errors.py covers domain exceptions; this
            # one stays inline because it carries no hint.
        )

    if project.intake_mode == "http":
        # Create a DB-backed http suite directly.
        definition = http_suite_definition(suite_name=payload.suite_name, cases=parsed.cases)
        suite = models.Suite(
            project_id=project_id,
            name=definition["name"],
            file_path="http://",
            case_count=len(definition["cases"]),
            definition_json=definition,
        )
        session.add(suite)
        await session.flush()
        return ScaffoldSuiteOut(
            intake_mode="http",
            suite_id=suite.id,
            file_path=None,
            cases_used=len(parsed.cases),
        )

    # git / zip: write a Python file. Default location follows the
    # convention from AGENTS.md.
    workspace = Path(project.workspace_path)
    suites_dir = workspace / "suites"
    suites_dir.mkdir(parents=True, exist_ok=True)

    target = suites_dir / f"{_safe(payload.suite_name)}.py"
    if target.exists():
        raise HTTPException(
            status_code=409,
            detail=f"A file already exists at {target.relative_to(workspace).as_posix()!s}. "
            "Choose a different suite name or remove the existing file.",
        )

    body = suite_python_skeleton(
        suite_name=payload.suite_name,
        agent_import_target=payload.agent_import_target,
        cases=parsed.cases,
    )
    target.write_text(body, encoding="utf-8")

    return ScaffoldSuiteOut(
        intake_mode=project.intake_mode,
        suite_id=None,  # discovered on next /sync
        file_path=target.relative_to(workspace).as_posix(),
        cases_used=len(parsed.cases),
    )


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "suite"


# ---------------------------------------------------------------------------
# LLM-powered suite generation
# ---------------------------------------------------------------------------


class GenerateSuiteIn(BaseModel):
    suite_name: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1)
    # Optional model override (e.g. "claude-haiku-4-5", "llama3.1:70b").
    model: str | None = None
    # Where the agent lives in the project. Defaults to "agent".
    agent_import_target: str = "agent"
    # When true, scan the workspace for the agent module, its sibling .py
    # files, any tools/* modules, and the project README; concatenate them
    # (size-capped) into the LLM context so cases reference real behaviors
    # instead of generic patterns from the playbook. Off by default to keep
    # default token usage cheap.
    deep_scan: bool = False
    # When true, expand the deep scan past the workspace root into the
    # parent directory — picks up sibling repositories that live next
    # to the selected project. Off by default; the brief calls this
    # out as an explicit user opt-in so we never quietly pull source
    # from outside the selected scope.
    scan_include_parent: bool = False
    # When true and the preflight import_load stage fails with
    # ``APD_PREFLIGHT_MODULE_NOT_FOUND``, Studio spins up an *ephemeral*
    # venv under /tmp, installs the missing package + the engine into it,
    # and re-runs preflight against that venv. The temp venv is torn
    # down before the response returns — this is a *preview* feature,
    # not a substitute for adding the package to your manifest. The UI
    # labels the result as "preview venv" so the user knows the fix is
    # non-persistent.
    auto_install_preview: bool = False


class GenerateSuiteOut(BaseModel):
    provider: str
    model: str
    source: str  # how the provider was picked (secret / env / fallback)
    generated_python: str
    # The companion case dossier (Markdown). Empty string if the model
    # didn't emit a dossier block — Studio still saves the Python.
    generated_dossier: str = ""
    # Cheap signal for the UI: did the dossier come back with at least one
    # ``### `` case heading?
    dossier_has_cases: bool = False
    compiles: bool
    parse_error: str | None
    # End-to-end validation. Populated for git/zip projects only; HTTP
    # projects skip the load check because there's no workspace.
    has_imports: bool = False
    has_suite_call: bool = False
    # True if the file imported cleanly in Studio's host process.
    loadable: bool = False
    # True if the file would load given a properly-provisioned project venv —
    # i.e. missing imports are all known runtime deps or project-local code.
    loadable_via_venv: bool = False
    missing_module: str | None = None
    load_error: str | None = None
    discovered_suites: list[str] = []
    total_cases: int = 0
    # The agent_import_target Studio actually used (after auto-detection).
    agent_import_target: str = "agent"
    # When deep_scan was on: list of files Studio actually included in the
    # LLM context, with the size each contributed. Empty list otherwise.
    # Useful for the UI to surface "scanned 6 files (28 KB)" so the user
    # knows the LLM had real context to work from.
    deep_scan_files: list[dict] = []

    # ---- Hardened preflight + scan-manifest fields ------------------
    # ``preflight_ok`` is the *only* signal the UI should use to render
    # "generation succeeded". ``compiles`` and ``loadable`` are kept for
    # back-compat with existing dashboards.
    preflight_ok: bool = False
    # Stage-by-stage breakdown: name, status, duration_ms, diagnostics.
    preflight_stages: list[dict] = []
    # First error code across all stages (in stage order). ``None`` when
    # everything passed. Stable across releases — see :class:`ErrorCode`.
    error_code: str | None = None
    # The generation strategy Studio picked (direct / adapter /
    # extend_existing / scaffold). Exposed so the UI can show a chip and
    # the user can spot mis-detections.
    strategy: str = "direct"
    # Detected framework label when known (flask / fastapi / cloud_function /
    # cli / module). ``None`` for scaffold or HTTP-mode.
    framework: str | None = None
    # Scan manifest: lets the UI tell the user exactly which files were
    # read into the LLM context, the root that bounded the scan, and
    # whether sibling repositories were opted in.
    scan_manifest: dict | None = None
    # True if preflight retried inside an ephemeral venv (auto_install_preview).
    preview_venv_used: bool = False


class SaveGeneratedIn(BaseModel):
    suite_name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    # Optional companion case-dossier markdown. Written next to the suite
    # as ``suites/<name>_cases.md`` when present.
    dossier: str = ""
    overwrite: bool = False
    # Force the write even if the load check fails. Default off so we don't
    # silently write garbage; the UI exposes a confirm checkbox.
    force: bool = False


class SaveGeneratedOut(BaseModel):
    file_path: str
    bytes_written: int
    # Path to the companion dossier if one was written, plus its byte count.
    dossier_path: str | None = None
    dossier_bytes: int = 0


_SYSTEM_PROMPT = (
    "You are an expert at writing agentprdiff suite files in Python. "
    "agentprdiff is a snapshot-testing framework for LLM agents — see the "
    "AGENTS.md adoption playbook below for the canonical API.\n"
    "\n"
    "Return TWO artifacts, each in its own fenced code block. No prose "
    "outside the fences.\n"
    "\n"
    "Artifact 1 — the suite file, wrapped in a ```python ... ``` block:\n"
    "  • imports case and suite from `agentprdiff`;\n"
    "  • imports deterministic graders from `agentprdiff.graders` using "
    "only these names: contains, contains_any, regex_match, tool_called, "
    "tool_sequence, no_tool_called, output_length_lt, latency_lt_ms, "
    "cost_lt_usd, semantic — do NOT invent graders;\n"
    "  • references the agent under test using the strategy the user "
    "message specifies. The four strategies are:\n"
    "      - direct: emit `from <module> import <callable> as my_agent`. "
    "Only use this when the user message says strategy=direct.\n"
    "      - adapter: the user message says the path isn't import-safe "
    "OR the target file is a service/app with no callable agent. Define "
    "an INLINE `def my_agent(query): ...` adapter function in the suite "
    "file itself, with a clear TODO that points at the target file. "
    "Frameworks have known shapes (Flask test client, FastAPI test "
    "client, Cloud Function HTTP handler, CLI runner, plain function "
    "call) — the user message will tell you which one to scaffold.\n"
    "      - extend_existing: the user message includes the contents of "
    "an existing agentprdiff suite file. Return the FULL updated file "
    "with new cases APPENDED to the existing `cases=[...]` list. Do not "
    "rewrite the existing imports, the existing agent reference, or any "
    "existing case. Preserve docstrings, blank lines, and comments. "
    "Add ONLY the new cases the user asks for.\n"
    "      - scaffold: emit a placeholder `def my_agent(query): ...` "
    "with a TODO. DO NOT invent an import.\n"
    "  • HARD RULE: do NOT generate "
    "`importlib.util.spec_from_file_location` calls. We never load "
    "Python modules from guessed file paths — the adapter strategy is "
    "the correct alternative.\n"
    "  • EVERY `from X import Y` and `import X` statement must use only "
    "valid Python identifiers in X. NEVER emit a hyphen, leading digit, "
    "dot-in-segment, or non-ASCII character inside a module path — "
    "`from my-service.foo import bar` is a SYNTAX ERROR.\n"
    "  • All file references in TODOs, comments, and docstrings must "
    "stay inside the selected project root. Do not invent absolute "
    "paths or parent-directory references (`../`).\n"
    "  • call every grader with its required arguments — e.g. "
    "`no_tool_called(\"send_email\")` not `no_tool_called()`;\n"
    "  • binds one or more module-level Suite objects via `suite(...)`;\n"
    "  • includes a top-of-file docstring naming the suite;\n"
    "  • is syntactically valid Python 3.10+ and has no "
    "`if __name__ == \"__main__\":` block.\n"
    "\n"
    "Artifact 2 — the case dossier, wrapped in a ```markdown ... ``` block "
    "(or any ``` block with no language tag). One H3 section per `case(...)` "
    "entry in the suite, using EXACTLY this template:\n"
    "  ### `<case_name>`\n"
    "  **What it tests.** One paragraph in plain English. A non-author "
    "should be able to read it in ten seconds and know what's protected.\n"
    "  **Input.** The exact input passed to the agent and *why this input "
    "was chosen*.\n"
    "  **Assertions.**\n"
    "  - Each grader translated to plain English (e.g. 'output contains "
    "the order number', 'the lookup_order tool was called exactly once').\n"
    "  - Always state the budget line (latency / cost) so reviewers don't "
    "have to read the suite to find it.\n"
    "  **Code impacted.** File path(s) in production code that this case "
    "exercises. Use `path/to/agent.py:NN` form when you can guess the "
    "line; if you can't, just the path.\n"
    "  **Application impact.** One concrete sentence about what breaks "
    "for end users if this regresses (e.g. 'Refunds silently fail').\n"
    "\n"
    "The dossier matters as much as the Python — reviewers read it on PRs "
    "to decide whether a diff is a real regression or an intentional change. "
    "Do not include any extra sections beyond the per-case blocks. When "
    "strategy=extend_existing, write dossier entries ONLY for the cases "
    "you just added; do not re-document existing cases.\n"
)


# Per-framework adapter sketches the LLM should expand. Each value is a
# copy-pasteable hint embedded in the user prompt — the LLM fills in real
# tool names, real expected outputs, etc. Generic on purpose: no project
# names, no hardcoded routes. The TODO comment carries the in-repo file
# reference so the human reader knows what to wire up.
_ADAPTER_SKETCH: dict[str, str] = {
    "flask": (
        "      # TODO: import your Flask app object. The path below is "
        "informational — wire it to the real import path inside the project root.\n"
        "      # from your_package import app  # noqa: E402\n"
        "      def my_agent(query: str) -> str:\n"
        "          client = app.test_client()\n"
        "          resp = client.post(\"/your-endpoint\", json={\"query\": query})\n"
        "          return resp.get_data(as_text=True)\n"
    ),
    "fastapi": (
        "      # TODO: import your FastAPI app object. The path below is "
        "informational — wire it to the real import path inside the project root.\n"
        "      # from your_package import app  # noqa: E402\n"
        "      from fastapi.testclient import TestClient\n"
        "      def my_agent(query: str) -> str:\n"
        "          with TestClient(app) as client:\n"
        "              resp = client.post(\"/your-endpoint\", json={\"query\": query})\n"
        "              return resp.text\n"
    ),
    "cloud_function": (
        "      # TODO: import the HTTP handler from the target file via a "
        "RENAMED, import-safe path; do not invent a path with hyphens.\n"
        "      # from your_package.handlers import handler  # noqa: E402\n"
        "      from werkzeug.test import EnvironBuilder\n"
        "      from werkzeug.wrappers import Request\n"
        "      def my_agent(query: str) -> str:\n"
        "          env = EnvironBuilder(method=\"POST\", json={\"query\": query}).get_environ()\n"
        "          request = Request(env)\n"
        "          response = handler(request)\n"
        "          return response if isinstance(response, str) else response.get_data(as_text=True)\n"
    ),
    "cli": (
        "      # TODO: replace this with your CLI entry point. Prefer "
        "Click's CliRunner when available so the test stays in-process.\n"
        "      # from your_package.cli import main  # noqa: E402\n"
        "      def my_agent(query: str) -> str:\n"
        "          # If using Click: from click.testing import CliRunner; "
        "result = CliRunner().invoke(main, [query]); return result.output\n"
        "          raise NotImplementedError(\"wire this to your CLI entry point\")\n"
    ),
    "module": (
        "      # TODO: import the real callable from your project package "
        "(NOT from the hyphenated path) and forward the query to it.\n"
        "      # from your_package.module import the_callable  # noqa: E402\n"
        "      def my_agent(query: str) -> str:\n"
        "          # return the_callable(query)\n"
        "          raise NotImplementedError(\"wire this to your agent\")\n"
    ),
}


def _adapter_block(
    *,
    framework: str | None,
    target_file_path: str | None,
    target_reason: str,
    agent_function_name: str,
) -> str:
    """Build the per-framework ``adapter`` strategy block for the user prompt.

    Generic — no project names, no fixed paths. The sketch shows the
    shape of the inline ``my_agent`` adapter; the LLM customises it
    against the deep-scan source.
    """
    fw = (framework or "module").lower()
    sketch = _ADAPTER_SKETCH.get(fw, _ADAPTER_SKETCH["module"])
    file_label = target_file_path or "<no file>"
    callable_hint = (
        f"  • Discovered callable in the target file (if any): "
        f"{agent_function_name!r}. Use it from inside the adapter — do "
        "NOT import it from a hyphenated or otherwise unsafe path.\n"
    )
    return (
        f"Strategy: adapter (framework={fw}).\n"
        f"  • Reason: {target_reason or 'inline adapter required.'}\n"
        f"  • Target file (for the TODO comment, in-repo reference only): "
        f"{file_label!r}\n"
        f"{callable_hint}"
        "  • Define an INLINE adapter function in the generated suite. "
        "Do not import the agent from a guessed file path. The adapter "
        "lives at module top level of the suite file:\n\n"
        f"{sketch}\n"
        "  • Then build the suite around `my_agent` exactly as you would "
        "for a normal callable. The grader assertions test what the "
        "adapter returns.\n"
        "  • Hard rule: do NOT call `importlib.util.spec_from_file_location` "
        "or any other dynamic loader. The adapter is the only sanctioned "
        "way to drive a service/app target.\n"
    )


def _user_prompt(
    *,
    suite_name: str,
    user_request: str,
    agent_import_target: str,
    agent_function_name: str,
    agents_md: str,
    workspace_context: str = "",
    target_strategy: str = "direct",
    target_file_path: str | None = None,
    target_safe_identifier: str | None = None,
    target_reason: str = "",
    target_framework: str | None = None,
    existing_suite_path: str | None = None,
    existing_suite_content: str | None = None,
) -> str:
    # The "how to reference the agent" block adapts to the detected
    # strategy so the LLM gets explicit, copy-pasteable code shape for
    # the project's actual layout. Generic — never names a specific
    # project; "scaffold" is the safe fallback when we can't be sure.
    if target_strategy == "scaffold":
        agent_block = (
            "Strategy: scaffold (no agent module detected).\n"
            f"  • {target_reason or 'workspace scan found no suitable entrypoint.'}\n"
            "  • Do NOT invent an import. Emit a placeholder instead:\n"
            "      # TODO: replace with your real agent\n"
            "      def my_agent(query: str) -> str:\n"
            "          raise NotImplementedError(\"wire this to your agent\")\n"
            "    Then write the suite around `my_agent` as if it were real.\n"
        )
    elif target_strategy == "adapter":
        agent_block = _adapter_block(
            framework=target_framework,
            target_file_path=target_file_path,
            target_reason=target_reason,
            agent_function_name=agent_function_name,
        )
    elif target_strategy == "extend_existing":
        # The LLM is given the full existing suite verbatim and asked to
        # produce an updated full file with new cases appended. We do NOT
        # ask for a diff — diffs are harder to parse safely than a whole
        # file, and `discover_suites` consumes whole files anyway.
        existing_body = (existing_suite_content or "").rstrip()
        agent_block = (
            "Strategy: extend_existing (an agentprdiff suite already lives "
            "in this workspace).\n"
            f"  • Existing suite file (relative to workspace): "
            f"{existing_suite_path!r}\n"
            "  • Return the FULL updated suite file. Do NOT rewrite the "
            "existing imports, the agent reference, or any case that is "
            "already present. Preserve every existing line — comments, "
            "blank lines, docstrings — verbatim.\n"
            "  • APPEND new `case(...)` entries to the existing "
            "`cases=[...]` list. The user request below describes what to add.\n"
            "  • Keep the same `suite(name=...)` and the same agent "
            "reference. Do NOT create a second suite object.\n"
            "  • If the user asks for new graders that the existing file "
            "doesn't already import, add the import at the top alongside "
            "the existing grader imports — same module, valid identifier.\n"
            "\n"
            "--- existing suite file ---\n"
            f"{existing_body}\n"
            "--- end existing suite file ---\n"
        )
    else:  # direct
        framework_hint = (
            f"  • Framework detected: {target_framework}. The agent is "
            "imported directly; no adapter needed.\n"
            if target_framework and target_framework != "module"
            else ""
        )
        agent_block = (
            "Strategy: direct (dotted import is safe).\n"
            f"  • Use exactly this import: `from {agent_import_target} "
            f"import {agent_function_name} as my_agent`\n"
            f"  • {agent_import_target} is the existing module — do NOT "
            "rename it.\n"
            f"  • {agent_function_name} is the existing callable name — "
            "do NOT invent a different one.\n"
            f"{framework_hint}"
        )

    body = (
        f"Generate a Python suite file for agentprdiff.\n\n"
        f"Suite name (Python identifier): {suite_name}\n"
        f"{agent_block}\n"
        f"User request:\n{user_request}\n\n"
    )
    if workspace_context:
        body += (
            "--- workspace source (deep scan) ---\n"
            "The files below are the actual agent code in this project. "
            "Use them to identify concrete behaviors to pin: real tool "
            "names, real system-prompt phrasing, real error paths, "
            "real expected outputs. Prefer cases that exercise the "
            "agent's own logic over generic patterns.\n\n"
            f"{workspace_context}\n"
            "--- end workspace source ---\n\n"
        )
    body += (
        f"--- agentprdiff canonical AGENTS.md (ground truth on API / shape) ---\n"
        f"{agents_md}\n"
        f"--- end AGENTS.md ---\n\n"
        f"Return ONLY the Python source for the suite file."
    )
    return body


# ---------------------------------------------------------------------------
# Deep-scan workspace reader
# ---------------------------------------------------------------------------

# Files we *never* read into LLM context.
_DEEP_SCAN_EXCLUDE_PARTS = frozenset(
    {".git", "__pycache__", ".studio-venv", ".venv", "venv", "node_modules", "dist", "build"}
)

# Total context budget for the deep-scan section. ~32 KB ≈ 8 KB per file on a
# small project; still leaves plenty of room for the playbook + completion
# tokens even on tight context windows.
_DEEP_SCAN_BYTE_BUDGET = 32_000
# Per-file cap so one huge module doesn't crowd out everything else.
_DEEP_SCAN_PER_FILE_CAP = 12_000


def _deep_scan_workspace(
    workspace: Path,
    agent_import_target: str,
    *,
    include_parent: bool = False,
) -> tuple[str, dict]:
    """Walk the workspace and concatenate the most relevant source files.

    Priority order:
      1. The agent module file (resolved from ``agent_import_target``).
      2. Sibling ``.py`` files in the agent module's directory.
      3. Any ``tools/`` directory near the workspace root.
      4. ``README.md`` / ``README.rst`` at the workspace root.

    Each file is read up to ``_DEEP_SCAN_PER_FILE_CAP`` bytes; the total
    budget is ``_DEEP_SCAN_BYTE_BUDGET``. Returns the concatenated context
    string plus a structured scan manifest::

        {
            "root": "/abs/path/to/scan/root",
            "sibling_repos_included": bool,
            "files": [{"path": "relative/path", "bytes": int}, ...],
            "total_bytes": int,
            "rejected": [{"path": "...", "reason": "..."}],
        }

    Scope guarantee: every file is verified to live under the *scan
    root* via :func:`agents_md.import_sanitizer.is_within` (which
    resolves symlinks), so a symlink that points outside the selected
    root never smuggles its content into the LLM context. Sibling
    repositories are excluded unless ``include_parent=True`` —
    explicit opt-in only. The scan root + opted-in flag are logged for
    traceability.
    """
    from ..agents_md.import_sanitizer import is_within

    if not workspace.exists() or not workspace.is_dir():
        log.info(
            "deep_scan: workspace=%s status=missing_or_not_dir", workspace
        )
        return "", {
            "root": str(workspace),
            "sibling_repos_included": False,
            "files": [],
            "total_bytes": 0,
            "rejected": [],
        }
    # Resolve once so all subsequent is_within checks use the canonical
    # path (handles symlinked workspaces, ``~`` in env, etc.).
    workspace_resolved = workspace.resolve()
    # Sibling repos opt-in: the *scan root* becomes the parent directory.
    # Everything else (per-file is_within check, exclude list) is the
    # same — we just expand the boundary. We deliberately do NOT walk
    # multiple levels up; that would be too easy to misuse.
    scan_root = (
        workspace_resolved.parent if include_parent else workspace_resolved
    )

    # Step 1: locate the agent module's actual file on disk by walking the
    # dotted import target. We do a best-effort match — the user's project
    # may shadow names, install packages site-wide, or use src/ layouts.
    candidates: list[Path] = []
    parts = agent_import_target.split(".")
    last = parts[-1]
    # Common: a file at <workspace>/<part>/<part>/<last>.py or
    # <workspace>/<last>.py.
    direct = workspace / Path(*parts).with_suffix(".py")
    if direct.is_file():
        candidates.append(direct)
    else:
        # Fallback: rglob for <last>.py and pick the shortest path.
        matches = sorted(workspace.rglob(f"{last}.py"), key=lambda p: len(p.parts))
        if matches:
            candidates.append(matches[0])
        # Or <agent_import_target>/__init__.py if it's a package.
        pkg_init = workspace / Path(*parts) / "__init__.py"
        if pkg_init.is_file():
            candidates.append(pkg_init)

    agent_dir: Path | None = candidates[0].parent if candidates else None

    # Step 2: sibling .py files in the agent's directory.
    siblings: list[Path] = []
    if agent_dir is not None and agent_dir.exists():
        for f in sorted(agent_dir.glob("*.py")):
            if f not in candidates:
                siblings.append(f)

    # Step 3: a workspace-root tools/ directory.
    tools_dir = workspace / "tools"
    tools: list[Path] = []
    if tools_dir.is_dir():
        for f in sorted(tools_dir.glob("*.py")):
            tools.append(f)

    # Step 4: README at the workspace root.
    readmes: list[Path] = []
    for name in ("README.md", "README.rst", "README"):
        p = workspace / name
        if p.is_file():
            readmes.append(p)
            break

    ordered: list[Path] = []
    for bucket in (candidates, siblings, tools, readmes):
        for f in bucket:
            if f not in ordered:
                ordered.append(f)

    chunks: list[str] = []
    files: list[dict] = []
    rejected: list[dict] = []
    budget = _DEEP_SCAN_BYTE_BUDGET

    for f in ordered:
        if budget <= 0:
            break
        # Skip files in excluded directories (rglob() can hit .git/ etc).
        try:
            rel_to_root = f.relative_to(scan_root)
        except ValueError:
            # ``f`` came from a candidate-bucket build that's keyed on
            # workspace; if include_parent=True and the file is outside
            # the scan_root branch we just skip it.
            rejected.append({
                "path": str(f),
                "reason": "outside-scan-root",
            })
            continue
        if any(part in _DEEP_SCAN_EXCLUDE_PARTS for part in rel_to_root.parts):
            continue
        # Scope guardrail: resolve every candidate and reject anything
        # whose resolved location escapes the *scan root* (e.g. a symlink
        # that points to /etc/passwd). This is paranoid — rglob() shouldn't
        # surface paths outside the scan root by itself — but the cost is
        # one resolve() per file and the safety property is worth it.
        if not is_within(f, scan_root):
            log.warning(
                "deep_scan: rejecting=%s reason=outside-root scan_root=%s",
                f,
                scan_root,
            )
            rejected.append({
                "path": str(f),
                "reason": "outside-scan-root",
            })
            continue
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Strip BOM and trailing whitespace; cap to per-file limit.
        text = raw.lstrip("﻿").rstrip()
        if len(text) > _DEEP_SCAN_PER_FILE_CAP:
            text = text[:_DEEP_SCAN_PER_FILE_CAP] + "\n# … (truncated)"
        # Cap to remaining budget too.
        if len(text) > budget:
            text = text[:budget] + "\n# … (budget-truncated)"
        rel = rel_to_root.as_posix()
        chunks.append(f"# === file: {rel} ===\n{text}")
        files.append({"path": rel, "bytes": len(text.encode("utf-8"))})
        budget -= len(text)

    total_bytes = sum(int(f["bytes"]) for f in files)
    manifest = {
        "root": str(scan_root),
        "sibling_repos_included": include_parent,
        "files": files,
        "total_bytes": total_bytes,
        "rejected": rejected,
    }
    log.info(
        "deep_scan: scan_root=%s sibling_repos=%s files=%d bytes=%d rejected=%d",
        scan_root,
        include_parent,
        len(files),
        total_bytes,
        len(rejected),
    )
    return "\n\n".join(chunks), manifest


def _extract_blocks(text: str) -> tuple[str, str]:
    """Pull out the Python suite + markdown dossier fenced blocks.

    Tolerant of common LLM drift:
      * accepts ``` python``` / ``` py``` / no-language for the suite,
        picking the first block whose body parses as Python;
      * accepts ``` markdown``` / ``` md``` / no-language for the dossier,
        picking the first block that has at least one ``### `` line;
      * if only one block is found and it looks like Python, returns it
        as the suite with an empty dossier (caller decides what to do).
    """
    # Match every fenced block in document order.
    blocks: list[tuple[str, str]] = []
    for m in re.finditer(r"```([a-zA-Z0-9_+-]*)\s*\n(.*?)```", text, re.DOTALL):
        lang = (m.group(1) or "").strip().lower()
        body = (m.group(2) or "").rstrip("\n")
        blocks.append((lang, body))

    suite_py = ""
    dossier_md = ""
    for lang, body in blocks:
        if lang in ("python", "py"):
            suite_py = suite_py or body
        elif lang in ("markdown", "md"):
            dossier_md = dossier_md or body
        elif lang == "":
            # Untagged — heuristic. Python if it imports or defs; markdown if
            # it has H3 case headers.
            if (not suite_py) and re.search(r"^(import|from|def|class)\b", body, re.MULTILINE):
                suite_py = body
            elif (not dossier_md) and re.search(r"^### ", body, re.MULTILINE):
                dossier_md = body

    # Final fallback: the model returned bare Python with no fences at all.
    if not suite_py and not blocks:
        suite_py = text.strip()

    # Tidy up trailing whitespace + ensure final newline.
    suite_py = suite_py.rstrip() + "\n" if suite_py else ""
    dossier_md = dossier_md.rstrip() + "\n" if dossier_md else ""
    return suite_py, dossier_md


def _validate_suite_python(source: str) -> tuple[bool, str | None]:
    """Returns (compiles, error_message). Doesn't import — just AST-parses."""
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return False, f"line {exc.lineno}: {exc.msg}"
    return True, None


@router.post(
    "/{project_id}/agents-md/generate-suite",
    response_model=GenerateSuiteOut,
)
async def generate_suite(
    project_id: int,
    payload: GenerateSuiteIn,
    session: AsyncSession = Depends(get_session),
) -> GenerateSuiteOut:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    try:
        resolved = await resolve_provider(
            session, project_id=project.id, model_override=payload.model
        )
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Auto-detect the agent module + entrypoint callable + the right
    # generation strategy (direct dotted import vs. adapter inline
    # wrapper vs. extend-existing vs. scaffold-with-TODO). The strategy
    # is what protects us against hyphenated directory names: when the
    # path can't be expressed as a valid Python identifier chain, we
    # tell the LLM to define an inline adapter instead of inventing
    # an invalid `from foo-bar import …`. We do NOT generate
    # ``importlib.util.spec_from_file_location`` calls from guessed
    # paths.
    workspace = (
        Path(project.workspace_path)
        if project.workspace_path and project.intake_mode in ("git", "zip")
        else None
    )
    agent_target = payload.agent_import_target
    agent_function_name = "run"
    target_strategy = "direct"
    target_file_path: str | None = None
    target_safe_identifier: str | None = None
    target_reason = ""
    target_framework: str | None = None
    existing_suite_path: str | None = None
    existing_suite_content: str | None = None
    if workspace is not None:
        detected_target = guess_agent_target(workspace)
        if detected_target is not None:
            target_strategy = detected_target.strategy
            agent_function_name = detected_target.callable_name
            target_file_path = detected_target.file_path
            target_safe_identifier = detected_target.safe_identifier
            target_reason = detected_target.reason
            target_framework = detected_target.framework
            existing_suite_path = detected_target.existing_suite_path
            # Module-level override only happens for direct strategy.
            # For adapter / extend_existing / scaffold we leave the
            # configured agent_target alone — the prompt drives those
            # strategies through their own fields.
            if (
                detected_target.strategy == "direct"
                and agent_target == "agent"
                and detected_target.module
            ):
                agent_target = detected_target.module
            # extend_existing: load the existing suite file content so
            # the LLM can append cases without rewriting anything.
            if existing_suite_path:
                existing_full_path = workspace / existing_suite_path
                # Scope guardrail: only read if the resolved path is
                # actually under the workspace. This is paranoid given
                # the path came from our own walk, but keeps the rule
                # symmetric with the deep-scan path check.
                try:
                    from ..agents_md.import_sanitizer import (
                        is_within as _is_within,
                    )
                    if _is_within(existing_full_path, workspace):
                        existing_suite_content = existing_full_path.read_text(
                            encoding="utf-8", errors="replace"
                        )
                except OSError:
                    log.warning(
                        "extend_existing: could not read %s; falling back "
                        "to non-extend strategy",
                        existing_full_path,
                    )
                    # If the file vanished between detection and read,
                    # drop back to the base classification rather than
                    # invent contents the LLM would extend.
                    existing_suite_path = None
                    target_strategy = (
                        "adapter"
                        if target_file_path is not None and detected_target.framework
                        else "scaffold"
                    )
        else:
            # No candidate file at all → scaffold fallback.
            target_strategy = "scaffold"
            target_reason = "workspace scan found no suitable entrypoint file"

    # Deep-scan: read the agent module + siblings + tools + README into the
    # LLM context so cases reference real code, not just the playbook. Only
    # meaningful for git/zip projects (HTTP-mode has no workspace). The
    # scan manifest carries the resolved scan root and the explicit
    # sibling-opt-in flag so the UI can render exactly what was read.
    workspace_context = ""
    scan_manifest: dict | None = None
    deep_scan_files: list[dict] = []
    if payload.deep_scan and workspace is not None:
        workspace_context, scan_manifest = _deep_scan_workspace(
            workspace,
            agent_target,
            include_parent=payload.scan_include_parent,
        )
        deep_scan_files = scan_manifest["files"]
    log.info(
        "generate_suite: project_id=%s strategy=%s framework=%s "
        "deep_scan=%s sibling_repos=%s",
        project_id,
        target_strategy,
        target_framework,
        payload.deep_scan,
        payload.scan_include_parent,
    )

    user = _user_prompt(
        suite_name=payload.suite_name,
        user_request=payload.prompt,
        agent_import_target=agent_target,
        agent_function_name=agent_function_name,
        agents_md=bundled_agents_md(),
        workspace_context=workspace_context,
        target_strategy=target_strategy,
        target_file_path=target_file_path,
        target_safe_identifier=target_safe_identifier,
        target_reason=target_reason,
        target_framework=target_framework,
        existing_suite_path=existing_suite_path,
        existing_suite_content=existing_suite_content,
    )

    try:
        text = await resolved.provider.generate(
            system=_SYSTEM_PROMPT, user=user, max_tokens=4096
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    suite_py, dossier_md = _extract_blocks(text)
    val = validate(suite_py, workspace)
    dossier_has_cases = bool(re.search(r"^### ", dossier_md, re.MULTILINE))

    # Hardened preflight pipeline — runs syntax / import_load /
    # suite_discovery as distinct stages so the UI can show per-stage
    # status + structured diagnostics + remediation. ``preflight_ok``
    # is the canonical "did it work" signal; the legacy ``loadable`` /
    # ``compiles`` fields stay populated for back-compat with existing
    # dashboards.
    report = run_preflight(
        suite_py,
        workspace,
        auto_install=payload.auto_install_preview,
    )

    # If the preflight reported MODULE_NOT_FOUND but validate() decided
    # the import was a known runtime dep, mirror the missing_module
    # onto the response — the UI's existing "Add to requirements.txt"
    # button keys off this field.
    missing_from_preflight: str | None = None
    for stage_dict in report.to_dict()["stages"]:
        for d in stage_dict["diagnostics"]:
            if d.get("code") == ErrorCode.MODULE_NOT_FOUND:
                remed = d.get("remediation") or {}
                missing_from_preflight = remed.get("missing_module")
                break
        if missing_from_preflight:
            break

    return GenerateSuiteOut(
        provider=resolved.name,
        model=resolved.model,
        source=resolved.source,
        generated_python=suite_py,
        generated_dossier=dossier_md,
        dossier_has_cases=dossier_has_cases,
        compiles=val.compiles,
        parse_error=val.parse_error,
        has_imports=val.has_imports,
        has_suite_call=val.has_suite_call,
        loadable=val.loadable,
        loadable_via_venv=val.loadable_via_venv,
        missing_module=val.missing_module or missing_from_preflight,
        load_error=val.load_error,
        discovered_suites=val.discovered_suites or report.discovered_suites,
        total_cases=val.total_cases or report.total_cases,
        agent_import_target=agent_target,
        deep_scan_files=deep_scan_files,
        preflight_ok=report.ok,
        preflight_stages=[s.to_dict() for s in report.stages],
        error_code=report.error_code,
        strategy=target_strategy,
        framework=target_framework,
        scan_manifest=scan_manifest,
        preview_venv_used=report.preview_venv_used,
    )


@router.post(
    "/{project_id}/agents-md/save-generated", response_model=SaveGeneratedOut
)
async def save_generated(
    project_id: int,
    payload: SaveGeneratedIn,
    session: AsyncSession = Depends(get_session),
) -> SaveGeneratedOut:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if project.intake_mode == "http" or not project.workspace_path:
        raise HTTPException(
            status_code=400,
            detail="Save-to-disk only applies to git/zip projects. "
            "For http projects, POST the generated cases to /projects/{id}/suites instead.",
        )
    workspace = Path(project.workspace_path)

    val = validate(payload.content, workspace)
    if not val.compiles:
        raise HTTPException(
            status_code=400,
            detail=f"Generated suite does not parse as Python ({val.parse_error}). "
            "Edit it or regenerate.",
        )
    # Accept loadable OR loadable-via-project-venv. Only block when neither
    # is true (heuristic miss, syntax error already caught above, or an
    # unfamiliar import that isn't a known runtime dep).
    if not payload.force and not (val.loadable or val.loadable_via_venv):
        if not val.has_imports:
            hint = "The file doesn't import from `agentprdiff`."
        elif not val.has_suite_call:
            hint = "The file doesn't call `suite(...)` at module level."
        else:
            hint = val.load_error or "load failed."
        raise HTTPException(
            status_code=400,
            detail=(
                f"Generated suite wouldn't load: {hint} "
                "Edit it, regenerate, or POST again with force=true to write anyway."
            ),
        )

    suites_dir = workspace / "suites"
    suites_dir.mkdir(parents=True, exist_ok=True)
    target = suites_dir / f"{_safe(payload.suite_name)}.py"
    if target.exists() and not payload.overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"A file already exists at suites/{target.name}. "
            "Pass overwrite=true to replace it.",
        )
    target.write_text(payload.content, encoding="utf-8")

    # Re-run discovery so the new file shows up in the suite list right away.
    # (Without this, the user would have to click Sync — and sync re-pulls
    # from origin and would overwrite the file we just wrote.)
    await _rediscover_suites(session, project, workspace)

    # Companion case-dossier markdown, when present. Lands next to the
    # suite as ``suites/<name>_cases.md`` — the path AGENTS.md prescribes.
    dossier_path: str | None = None
    dossier_bytes = 0
    if payload.dossier.strip():
        dossier_target = suites_dir / f"{_safe(payload.suite_name)}_cases.md"
        if dossier_target.exists() and not payload.overwrite:
            # Non-fatal — we already wrote the Python file successfully. Just
            # don't clobber the dossier.
            pass
        else:
            dossier_target.write_text(payload.dossier, encoding="utf-8")
            dossier_path = dossier_target.relative_to(workspace).as_posix()
            dossier_bytes = len(payload.dossier.encode("utf-8"))

    return SaveGeneratedOut(
        file_path=target.relative_to(workspace).as_posix(),
        bytes_written=len(payload.content.encode("utf-8")),
        dossier_path=dossier_path,
        dossier_bytes=dossier_bytes,
    )


@router.post("/{project_id}/agents-md/cleanup-orphans")
async def cleanup_orphans(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Find and delete ``*_cases.md`` dossier files whose suite is gone.

    Walks every ``*_cases.md`` under the workspace. A file is treated as an
    orphan when neither of these is true:

    * a ``Suite`` row exists with ``name == <basename>``, OR
    * a ``.py`` file with the same basename sits in the same directory
      (covers the case where Studio hasn't discovered the suite yet but
      the file is still on disk).

    Returns:
        ``{"deleted": [...], "kept": [...], "workspace": str | None}``

    The endpoint is idempotent — running it twice in a row deletes nothing
    on the second pass.
    """
    from sqlalchemy import select as _select  # local import to avoid shadowing

    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if project.intake_mode not in ("git", "zip") or not project.workspace_path:
        raise HTTPException(
            status_code=400,
            detail="cleanup-orphans only applies to git/zip projects",
        )
    workspace = Path(project.workspace_path)
    if not workspace.exists():
        return {"deleted": [], "kept": [], "workspace": str(workspace)}

    # Set of suite names that "claim" a dossier base — these aren't orphans.
    rows = (
        await session.execute(
            _select(models.Suite).where(models.Suite.project_id == project_id)
        )
    ).scalars().all()
    claimed = set()
    for s in rows:
        claimed.add(s.name)
        # Also claim by file basename in case naming drifted from the
        # canonical convention (e.g. someone manually renamed the .py).
        if s.file_path and s.file_path != "http://":
            claimed.add(Path(s.file_path).stem)

    deleted: list[str] = []
    kept: list[dict[str, str]] = []

    for cases_path in workspace.rglob("*_cases.md"):
        if not cases_path.is_file():
            continue
        # Defensive: skip anything inside the venv or .git
        rel = cases_path.relative_to(workspace)
        if any(part in (".studio-venv", ".git", "__pycache__") for part in rel.parts):
            continue
        base = cases_path.stem.removesuffix("_cases")
        # Sibling .py with the same base counts as "claimed" — preserves
        # dossiers for suites that exist on disk but haven't been
        # discovered into the DB yet.
        sibling_py = cases_path.with_name(f"{base}.py")
        if base in claimed or sibling_py.exists():
            kept.append({"path": rel.as_posix(), "reason": "suite present"})
            continue
        try:
            cases_path.unlink()
            deleted.append(rel.as_posix())
        except OSError as exc:
            kept.append({"path": rel.as_posix(), "reason": f"unlink failed: {exc}"})

    return {
        "deleted": deleted,
        "kept": kept,
        "workspace": str(workspace),
    }


async def _rediscover_suites(
    session: AsyncSession, project: models.Project, workspace: Path
) -> None:
    """Replace the project's Suite rows with a fresh walk of the workspace.

    Mirrors the helper in api/projects.py but lives here to avoid a circular
    import; it's small enough that the duplication is cheap.
    """
    await session.execute(
        delete(models.Suite).where(models.Suite.project_id == project.id)
    )
    discovered = await discover_suites(workspace)
    for d in discovered:
        # Same dual-surface logic as projects.py: persist any suite with a
        # real name (either a clean load or an AST-extracted name from a
        # project-venv-dependent file). Skip only hard failures, which use
        # the relative-path fallback as ``name``.
        if d.load_error and d.name == d.relative_path:
            continue
        session.add(
            models.Suite(
                project_id=project.id,
                name=d.name,
                file_path=d.relative_path,
                case_count=d.case_count,
            )
        )
    await session.flush()

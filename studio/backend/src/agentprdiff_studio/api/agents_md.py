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
    "  • imports the agent under test using the strategy the user message "
    "specifies. When strategy=direct, use `from <module> import <callable> "
    "as my_agent`. When strategy=dynamic_load, use "
    "`importlib.util.spec_from_file_location` because the path contains "
    "characters (hyphens, leading digits, dots) that aren't valid in "
    "Python identifiers. When strategy=scaffold, leave a TODO comment and "
    "a placeholder `def my_agent(query): ...` — DO NOT invent an import.\n"
    "  • EVERY `from X import Y` and `import X` statement must use only "
    "valid Python identifiers in X. NEVER emit a hyphen, leading digit, "
    "dot-in-segment, or non-ASCII character inside a module path — "
    "`from my-service.foo import bar` is a SYNTAX ERROR.\n"
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
    "Do not include any extra sections beyond the per-case blocks.\n"
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
    elif target_strategy == "dynamic_load":
        # Hyphens / leading digits / dots in directory names — can't be
        # reached via a dotted import. Use spec_from_file_location, which
        # is the standard library's escape hatch.
        safe_ident = target_safe_identifier or "_agent_module"
        agent_block = (
            "Strategy: dynamic_load (path is not import-safe as a dotted "
            "module).\n"
            f"  • Reason: {target_reason or 'path contains non-identifier characters.'}\n"
            f"  • Target file (relative to suite file): {target_file_path!r}\n"
            f"  • Callable name inside that module: {agent_function_name!r}\n"
            "  • Use exactly this loader pattern at module top level "
            "(adjust the relative path if the suite ends up in a "
            "subdirectory):\n\n"
            "      import importlib.util\n"
            "      from pathlib import Path\n\n"
            "      _AGENT_FILE = Path(__file__).resolve().parent / "
            f"{target_file_path!r}\n"
            f"      _spec = importlib.util.spec_from_file_location("
            f"{safe_ident!r}, _AGENT_FILE)\n"
            f"      _module = importlib.util.module_from_spec(_spec)\n"
            f"      _spec.loader.exec_module(_module)\n"
            f"      my_agent = _module.{agent_function_name}\n\n"
            "  • Do NOT emit a `from <path-with-hyphens> import …` line. "
            "That's a Python syntax error.\n"
        )
    else:  # direct
        agent_block = (
            "Strategy: direct (dotted import is safe).\n"
            f"  • Use exactly this import: `from {agent_import_target} "
            f"import {agent_function_name} as my_agent`\n"
            f"  • {agent_import_target} is the existing module — do NOT "
            "rename it.\n"
            f"  • {agent_function_name} is the existing callable name — "
            "do NOT invent a different one.\n"
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
    workspace: Path, agent_import_target: str
) -> tuple[str, list[dict]]:
    """Walk the workspace and concatenate the most relevant source files.

    Priority order:
      1. The agent module file (resolved from ``agent_import_target``).
      2. Sibling ``.py`` files in the agent module's directory.
      3. Any ``tools/`` directory near the workspace root.
      4. ``README.md`` / ``README.rst`` at the workspace root.

    Each file is read up to ``_DEEP_SCAN_PER_FILE_CAP`` bytes; the total
    budget is ``_DEEP_SCAN_BYTE_BUDGET``. Returns the concatenated context
    string plus a manifest of ``[{path, bytes}]`` for the UI.

    Scope guarantee: every file is verified to live under ``workspace``
    via :func:`agents_md.import_sanitizer.is_within` (which resolves
    symlinks), so a symlink that points outside the selected root never
    smuggles its content into the LLM context. The selected root + the
    files we ultimately ship are logged for traceability.
    """
    from ..agents_md.import_sanitizer import is_within

    if not workspace.exists() or not workspace.is_dir():
        log.info("deep_scan: workspace %s missing or not a directory", workspace)
        return "", []
    # Resolve once so all subsequent is_within checks use the canonical
    # path (handles symlinked workspaces, ``~`` in env, etc.).
    workspace_resolved = workspace.resolve()

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
    manifest: list[dict] = []
    budget = _DEEP_SCAN_BYTE_BUDGET

    for f in ordered:
        if budget <= 0:
            break
        # Skip files in excluded directories (rglob() can hit .git/ etc).
        if any(part in _DEEP_SCAN_EXCLUDE_PARTS for part in f.relative_to(workspace).parts):
            continue
        # Scope guardrail: resolve every candidate and reject anything
        # whose resolved location escapes ``workspace`` (e.g. a symlink
        # that points to /etc/passwd). This is paranoid — rglob() shouldn't
        # surface paths outside the workspace by itself — but the cost is
        # one resolve() per file and the safety property is worth it.
        if not is_within(f, workspace_resolved):
            log.warning(
                "deep_scan: rejecting %s — resolves outside workspace %s",
                f, workspace_resolved,
            )
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
        rel = f.relative_to(workspace).as_posix()
        chunks.append(f"# === file: {rel} ===\n{text}")
        manifest.append({"path": rel, "bytes": len(text.encode("utf-8"))})
        budget -= len(text)

    log.info(
        "deep_scan: root=%s files=%d bytes=%d",
        workspace_resolved,
        len(manifest),
        sum(int(m["bytes"]) for m in manifest),
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
    # generation strategy (direct dotted import vs. dynamic load via
    # spec_from_file_location vs. scaffold-with-TODO). The strategy
    # is what protects us against hyphenated directory names: when the
    # path can't be expressed as a valid Python identifier chain, we
    # tell the LLM to switch to dynamic loading instead of inventing
    # an invalid `from foo-bar import …`.
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
    if workspace is not None:
        detected_target = guess_agent_target(workspace)
        if detected_target is not None:
            target_strategy = detected_target.strategy
            agent_function_name = detected_target.callable_name
            target_file_path = detected_target.file_path
            target_safe_identifier = detected_target.safe_identifier
            target_reason = detected_target.reason
            # Module-level override only happens for direct strategy.
            # For dynamic_load we deliberately leave agent_target alone
            # (the prompt uses target_file_path) since there's no safe
            # dotted name to put in the import line.
            if detected_target.strategy == "direct" and agent_target == "agent":
                if detected_target.module:
                    agent_target = detected_target.module
        else:
            # No candidate file at all → scaffold fallback.
            target_strategy = "scaffold"
            target_reason = "workspace scan found no suitable entrypoint file"

    # Deep-scan: read the agent module + siblings + tools + README into the
    # LLM context so cases reference real code, not just the playbook. Only
    # meaningful for git/zip projects (HTTP-mode has no workspace).
    workspace_context = ""
    deep_scan_files: list[dict] = []
    if payload.deep_scan and workspace is not None:
        workspace_context, deep_scan_files = _deep_scan_workspace(
            workspace, agent_target
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
        missing_module=val.missing_module,
        load_error=val.load_error,
        discovered_suites=val.discovered_suites,
        total_cases=val.total_cases,
        agent_import_target=agent_target,
        deep_scan_files=deep_scan_files,
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

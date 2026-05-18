"""Staged preflight pipeline for generated suite files.

The generation flow used to mash three different checks together —
``ast.parse``, the engine's ``load_suites`` call, and a "looks like a
suite" heuristic — and surface a single boolean to the UI. That hid
useful information: when a suite "failed", the user couldn't tell
whether they had a syntax error, an unsafe import path, a missing
runtime dep, or a discovery-shape problem.

This module replaces that monolith with three explicit stages:

* **syntax** — ``ast.parse`` + the import sanitizer's identifier check.
  Catches hyphenated module paths, leading-digit segments, keywords as
  module names, and the more obvious bare ``SyntaxError``\\ s.
* **import_load** — actually import the suite. Runs in a subprocess
  using the project's ``.studio-venv`` python when one exists, so a
  ``ModuleNotFoundError`` correctly reflects the runtime environment,
  not the Studio host's. Falls back to ``sys.executable`` when no venv
  is provisioned.
* **suite_discovery** — confirm the file exports at least one
  :class:`agentprdiff.Suite` and that the suite(s) contain at least one
  ``case(...)`` entry. Without cases the file is "valid" but useless.

Each stage emits structured :class:`PreflightDiagnostic` records with a
stable :class:`ErrorCode` plus an optional ``fix_hint`` and
``remediation`` dict the UI can render verbatim. The top-level
:class:`PreflightReport` exposes per-stage status + an aggregate
``ok`` flag; only when all three stages pass do we tell the UI
"generation succeeded".

The module is intentionally generic — no project-name special cases,
no domain logic beyond Python's identifier rules, the engine's
loader API, and the small set of common framework markers
:mod:`.import_sanitizer` already recognises.

When ``auto_install=True`` and stage 2 trips
``APD_PREFLIGHT_MODULE_NOT_FOUND``, an *ephemeral* preview venv is
created in ``/tmp``, the engine + the missing top-level package are
installed into it, and stages 2–3 are re-run against that venv. The
temp venv is torn down before the function returns. Persistent state
is never touched — the user still needs to update
``requirements.txt`` / ``pyproject.toml`` to make the fix stick, and
the diagnostic carries the exact command to do so.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import venv as stdlib_venv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .import_sanitizer import (
    ImportDiagnostic,
    is_within,
    validate_generated_imports,
)

log = logging.getLogger("agentprdiff_studio.preflight")


# ---------------------------------------------------------------------------
# Stable error codes — used by the UI for fixed remediation cards and by
# support for triage. Add to this list, never rename, never repurpose.
# ---------------------------------------------------------------------------


class ErrorCode:
    """Stable identifiers for preflight failures.

    These strings are part of the API contract. The frontend keys
    remediation cards off them and the support team uses them in logs;
    do not rename existing entries — only add new ones. Two prefixes:

    * ``APD_PREFLIGHT_*`` — issues with the *generated suite* itself.
    * ``APD_SCAN_*``      — issues with the workspace scan that
      assembled the LLM context.
    * ``APD_GENERATION_*``— issues with the LLM call itself.
    """

    # Stage: syntax
    SYNTAX_ERROR = "APD_PREFLIGHT_SYNTAX_ERROR"
    INVALID_IMPORT_PATH = "APD_PREFLIGHT_INVALID_IMPORT_PATH"
    HYPHENATED_IMPORT = "APD_PREFLIGHT_HYPHENATED_IMPORT"
    DYNAMIC_LOADER_FORBIDDEN = "APD_PREFLIGHT_DYNAMIC_LOADER_FORBIDDEN"
    PATH_OUTSIDE_ROOT = "APD_PREFLIGHT_PATH_OUTSIDE_ROOT"

    # Stage: import_load
    MODULE_NOT_FOUND = "APD_PREFLIGHT_MODULE_NOT_FOUND"
    IMPORT_NAME_ERROR = "APD_PREFLIGHT_IMPORT_NAME_ERROR"
    IMPORT_ERROR = "APD_PREFLIGHT_IMPORT_ERROR"
    LOAD_EXCEPTION = "APD_PREFLIGHT_LOAD_EXCEPTION"
    LOAD_TIMEOUT = "APD_PREFLIGHT_LOAD_TIMEOUT"

    # Stage: suite_discovery
    MISSING_AGENTPRDIFF_IMPORT = "APD_PREFLIGHT_MISSING_AGENTPRDIFF_IMPORT"
    MISSING_SUITE_CALL = "APD_PREFLIGHT_MISSING_SUITE_CALL"
    NO_SUITES_DISCOVERED = "APD_PREFLIGHT_NO_SUITES_DISCOVERED"
    NO_CASES_IN_SUITES = "APD_PREFLIGHT_NO_CASES_IN_SUITES"

    # Scan-scope
    SCAN_OUT_OF_ROOT_REJECTED = "APD_SCAN_OUT_OF_ROOT_REJECTED"
    SCAN_SIBLING_INCLUDED = "APD_SCAN_SIBLING_INCLUDED"

    # Generation pipeline
    GENERATION_PROVIDER_ERROR = "APD_GENERATION_PROVIDER_ERROR"
    AUTO_INSTALL_FAILED = "APD_PREFLIGHT_AUTO_INSTALL_FAILED"


# ---------------------------------------------------------------------------
# Diagnostic + report shapes
# ---------------------------------------------------------------------------


StageName = Literal["syntax", "import_load", "suite_discovery"]
StageStatus = Literal["pending", "passed", "failed", "skipped"]
Severity = Literal["error", "warning", "info"]


@dataclass(slots=True, frozen=True)
class PreflightDiagnostic:
    """A single actionable preflight finding.

    ``code`` is stable across releases; ``message`` is human-readable
    English. ``fix_hint`` is a one-sentence suggestion the UI can show
    inline; ``remediation`` is a richer dict the UI can render as a
    targeted action (e.g. "Add ``openai`` to requirements.txt" with a
    button to do exactly that).

    Coordinates are 1-based (the :mod:`ast` convention). ``file`` is a
    workspace-relative POSIX path when known.
    """

    stage: StageName
    code: str
    severity: Severity
    message: str
    file: str | None = None
    line: int | None = None
    col: int | None = None
    fix_hint: str | None = None
    remediation: dict[str, Any] | None = None
    statement: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "fix_hint": self.fix_hint,
            "remediation": self.remediation,
            "statement": self.statement,
        }


@dataclass(slots=True)
class PreflightStage:
    """One stage's outcome.

    ``status`` transitions from ``pending`` → ``passed`` / ``failed`` /
    ``skipped``. ``skipped`` is reserved for stages that genuinely
    couldn't run (e.g. ``import_load`` is skipped when there's no
    workspace on disk — HTTP-mode projects). ``duration_ms`` is wall
    clock; useful for diagnosing slow projects.
    """

    name: StageName
    status: StageStatus = "pending"
    duration_ms: int = 0
    diagnostics: list[PreflightDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


@dataclass(slots=True)
class PreflightReport:
    """Aggregate preflight result.

    ``ok`` is True iff every stage either passed or was deliberately
    skipped (HTTP-mode projects skip stages 2 and 3). The first error
    diagnostic in stage order becomes ``error_code`` so the UI has a
    single canonical code to render in the page title bar.

    ``discovered_suites`` / ``total_cases`` are surfaced when stage 3
    runs and mirror the legacy fields on
    :class:`~.validate.ValidationResult` for callers that still want a
    flat result.

    ``preview_venv_used`` is True when ``auto_install`` retried stage 2
    inside an ephemeral venv. The diagnostic carrying the original
    failure is preserved so the user still sees what was missing.
    """

    ok: bool
    stages: list[PreflightStage]
    error_code: str | None = None
    summary: str = ""
    discovered_suites: list[str] = field(default_factory=list)
    total_cases: int = 0
    preview_venv_used: bool = False
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stages": [s.to_dict() for s in self.stages],
            "error_code": self.error_code,
            "summary": self.summary,
            "discovered_suites": list(self.discovered_suites),
            "total_cases": self.total_cases,
            "preview_venv_used": self.preview_venv_used,
            "duration_ms": self.duration_ms,
        }

    def first_error(self) -> PreflightDiagnostic | None:
        for stage in self.stages:
            for d in stage.diagnostics:
                if d.severity == "error":
                    return d
        return None


# ---------------------------------------------------------------------------
# Subprocess worker — runs stages 2 + 3 in an isolated interpreter.
# ---------------------------------------------------------------------------


# The worker is a tiny stand-alone Python file written to disk so it can
# be invoked by an arbitrary interpreter (the project venv's python, or
# Studio's fallback). It depends only on the standard library plus
# ``agentprdiff`` (which is what the venv was provisioned for).
_WORKER_TEMPLATE = r'''
"""Preflight worker — imported by run_preflight via subprocess.

Reads three args from argv: the suite source file path, the workspace
root (so we can add it to sys.path), and a JSON output path. Writes a
JSON document {{"ok": bool, "diagnostics": [...]}} to the output path
and exits 0 on success or 1 on diagnostics. Never raises — all errors
become diagnostics. Keeping the protocol file-based instead of stdout
avoids the "noisy import side effects pollute stdout" problem.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path


def _emit(payload, out_path):
    Path(out_path).write_text(json.dumps(payload), encoding="utf-8")


def main(argv):
    suite_file = argv[1]
    workspace = argv[2]
    out_path = argv[3]

    diagnostics = []
    discovered = []
    total_cases = 0

    if workspace and workspace not in sys.path:
        sys.path.insert(0, workspace)

    try:
        from agentprdiff.loader import load_suites  # type: ignore
    except Exception as exc:  # noqa: BLE001
        diagnostics.append({{
            "stage": "import_load",
            "code": "APD_PREFLIGHT_LOAD_EXCEPTION",
            "severity": "error",
            "message": "could not import agentprdiff.loader in this "
                       "interpreter: " + repr(exc),
            "fix_hint": "Install agentprdiff in the project venv "
                       "(pip install agentprdiff) or run a full sync.",
        }})
        _emit({{"ok": False, "diagnostics": diagnostics,
                "discovered_suites": discovered, "total_cases": total_cases}},
              out_path)
        return 1

    try:
        suites = load_suites(Path(suite_file))
    except ModuleNotFoundError as exc:
        diagnostics.append({{
            "stage": "import_load",
            "code": "APD_PREFLIGHT_MODULE_NOT_FOUND",
            "severity": "error",
            "message": type(exc).__name__ + ": " + str(exc),
            "missing_module": getattr(exc, "name", None),
        }})
    except ImportError as exc:
        diagnostics.append({{
            "stage": "import_load",
            "code": "APD_PREFLIGHT_IMPORT_NAME_ERROR",
            "severity": "error",
            "message": type(exc).__name__ + ": " + str(exc),
        }})
    except SyntaxError as exc:
        diagnostics.append({{
            "stage": "import_load",
            "code": "APD_PREFLIGHT_SYNTAX_ERROR",
            "severity": "error",
            "message": "line " + str(exc.lineno) + ": " + str(exc.msg),
            "line": exc.lineno,
            "col": exc.offset,
        }})
    except BaseException as exc:  # noqa: BLE001 — capture everything
        diagnostics.append({{
            "stage": "import_load",
            "code": "APD_PREFLIGHT_LOAD_EXCEPTION",
            "severity": "error",
            "message": type(exc).__name__ + ": " + str(exc),
            "trace": traceback.format_exc(),
        }})
    else:
        discovered = [getattr(s, "name", "<unnamed>") for s in suites]
        total_cases = sum(len(getattr(s, "cases", [])) for s in suites)
        if not discovered:
            diagnostics.append({{
                "stage": "suite_discovery",
                "code": "APD_PREFLIGHT_NO_SUITES_DISCOVERED",
                "severity": "error",
                "message": "no Suite objects were discovered in the "
                           "generated file",
                "fix_hint": "Bind your suite to a module-level name "
                           "via `my_suite = suite(...)`.",
            }})
        elif total_cases == 0:
            diagnostics.append({{
                "stage": "suite_discovery",
                "code": "APD_PREFLIGHT_NO_CASES_IN_SUITES",
                "severity": "error",
                "message": "discovered suites but no cases inside them",
                "fix_hint": "Add at least one `case(...)` entry to the "
                           "suite's `cases=[...]` list.",
            }})

    ok = not any(d["severity"] == "error" for d in diagnostics)
    _emit({{
        "ok": ok,
        "diagnostics": diagnostics,
        "discovered_suites": discovered,
        "total_cases": total_cases,
    }}, out_path)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''


def _write_worker(scratch_dir: Path) -> Path:
    """Materialize the subprocess worker script."""
    target = scratch_dir / "_preflight_worker.py"
    # The template uses {{ }} to escape literal braces in a regular
    # string (we're not actually using ``.format``, but we keep the
    # escaping consistent in case a future refactor switches).
    target.write_text(_WORKER_TEMPLATE.replace("{{", "{").replace("}}", "}"))
    return target


# ---------------------------------------------------------------------------
# Project venv detection
# ---------------------------------------------------------------------------


def _project_python(workspace: Path | None) -> Path:
    """Return the interpreter to use for the import_load subprocess.

    Priority:

    1. ``<workspace>/.studio-venv/bin/python`` (or Scripts/python.exe on
       Windows) — only when the venv is actually provisioned. We check
       for the python binary rather than the ``.provisioned`` marker
       because the marker is set after the engine is installed, but a
       partial provision is still useful for the syntax stage.
    2. ``sys.executable`` — Studio's own interpreter. This is fine for
       the syntax stage and for projects that don't have a venv (HTTP
       intake mode, fresh git clone before first run).
    """
    if workspace is not None:
        for sub in ("bin/python", "Scripts/python.exe", "Scripts/python"):
            cand = workspace / ".studio-venv" / sub
            if cand.exists():
                return cand
    return Path(sys.executable)


# ---------------------------------------------------------------------------
# Ephemeral preview venv (for auto_install=True)
# ---------------------------------------------------------------------------


def _make_preview_venv(missing_module: str | None) -> tuple[Path, Path]:
    """Create a fresh venv in /tmp with the engine + missing pkg installed.

    Returns ``(venv_path, python_path)``. Callers are responsible for
    cleaning up the venv (see :func:`_destroy_preview_venv`). The
    returned python is the venv's interpreter — use it directly to
    re-run the import_load worker.

    The function intentionally avoids touching the workspace or any
    long-lived state. If pip fails, the venv is left for the caller to
    clean up, and the failure is surfaced as a diagnostic.
    """
    base = Path(tempfile.gettempdir()) / f"studio-preflight-{uuid.uuid4().hex}"
    stdlib_venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False).create(base)
    py = base / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    # Resolve packages to install. Always include the engine — the
    # worker imports ``agentprdiff.loader`` and an empty venv won't
    # have it. The missing module is best-effort: many ImportError
    # surfaces give us a useful top-level package name, but not all.
    pkgs = ["agentprdiff"]
    if missing_module:
        # Strip dots — pip wants the top-level distribution name.
        pkgs.append(missing_module.split(".", 1)[0])

    subprocess.run(
        [str(py), "-m", "pip", "install", "--disable-pip-version-check",
         "--quiet", *pkgs],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return base, py


def _destroy_preview_venv(path: Path) -> None:
    """Best-effort teardown of an ephemeral venv. Never raises."""
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError as exc:  # pragma: no cover — defence in depth
        log.warning("preview_venv: failed to clean up %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Hints
# ---------------------------------------------------------------------------


def _hyphen_fix_hint(statement: str) -> str:
    """Friendly hint for the canonical hyphenated-path error."""
    return (
        "Hyphens (and other characters illegal in Python identifiers) "
        "can't appear in a module path. Either rename the offending "
        "directory so it's import-safe, or define an inline `def "
        "my_agent(query): ...` adapter in the suite that drives the "
        "real callable through a test client or subprocess shim."
    )


def _missing_dep_remediation(
    missing_module: str | None, workspace: Path | None
) -> dict[str, Any] | None:
    """Pack the missing-dep fix into a UI-renderable dict.

    The frontend uses ``where_to_declare`` to pick the right action:
    a project with ``requirements.txt`` gets the "append to
    requirements.txt" button; a ``pyproject.toml``-only project gets
    an ``uv add`` suggestion; everything else falls back to a plain
    pip command the user can copy.
    """
    if not missing_module:
        return None
    top = missing_module.split(".", 1)[0]
    where: str
    commands: list[str]
    if workspace and (workspace / "requirements.txt").is_file():
        where = "requirements.txt"
        commands = [
            f"echo '{top}' >> requirements.txt",
            f"pip install '{top}'  # then re-sync the project",
        ]
    elif workspace and (workspace / "pyproject.toml").is_file():
        where = "pyproject.toml"
        commands = [
            f"uv add '{top}'",
            f"pip install '{top}'",
        ]
    else:
        where = "(no manifest detected — install directly)"
        commands = [f"pip install '{top}'"]
    return {
        "missing_module": missing_module,
        "top_level_package": top,
        "where_to_declare": where,
        "install_commands": commands,
        "note": (
            "Studio installs project deps into a per-project venv when "
            "you sync the project. Adding the package to your manifest "
            "then triggering a sync rebuilds the venv with the dep."
        ),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


# Discovery markers — the engine's loader treats files without them as
# unrelated. We surface their absence at stage 3 because catching it
# here gives a much friendlier error than the loader's silent skip.
_AGENTPRDIFF_IMPORT_HINTS = ("from agentprdiff", "import agentprdiff")
_SUITE_CALL_MARKER = "suite("


def run_preflight(
    source: str,
    workspace: Path | None,
    *,
    auto_install: bool = False,
    subprocess_timeout_s: float = 20.0,
) -> PreflightReport:
    """Run all three preflight stages against ``source``.

    Each stage runs even when the previous one produced warnings; a
    stage is only skipped when it can't *possibly* succeed (e.g.
    ``import_load`` is skipped when there's no workspace on disk).
    The function never raises — any unexpected exception inside a
    stage becomes a diagnostic on that stage.

    Parameters
    ----------
    source:
        The generated suite Python.
    workspace:
        The project root. ``None`` for HTTP-mode projects; the load
        and discovery stages are then skipped.
    auto_install:
        When True and stage 2 fails with ``APD_PREFLIGHT_MODULE_NOT_FOUND``,
        an ephemeral venv with the engine + missing top-level package is
        provisioned, and stages 2–3 are re-run against it. The original
        diagnostic is preserved (so the user still sees the missing-dep
        remediation card) and ``preview_venv_used`` is flipped on.
    subprocess_timeout_s:
        Maximum wall time for the worker subprocess. Hitting it
        produces a ``APD_PREFLIGHT_LOAD_TIMEOUT`` diagnostic.
    """
    started = time.monotonic()

    syntax = PreflightStage(name="syntax")
    import_load = PreflightStage(name="import_load")
    suite_discovery = PreflightStage(name="suite_discovery")
    stages = [syntax, import_load, suite_discovery]

    # --- Stage 1: syntax + identifier sanitization ---------------------
    s1_start = time.monotonic()
    import_diags = validate_generated_imports(source)
    for d in import_diags:
        syntax.diagnostics.append(_promote_import_diag(d))
    if any(d.severity == "error" for d in syntax.diagnostics):
        syntax.status = "failed"
        # Stop here — there's no point trying to import code we can't
        # parse, and the engine loader would surface the same error
        # with a less actionable message.
        syntax.duration_ms = int((time.monotonic() - s1_start) * 1000)
        import_load.status = "skipped"
        suite_discovery.status = "skipped"
        report = _finalize(stages, started)
        log.info(
            "preflight: stage=syntax outcome=failed code=%s",
            report.error_code,
        )
        return report
    syntax.status = "passed"
    syntax.duration_ms = int((time.monotonic() - s1_start) * 1000)

    # --- Stage 2 prerequisite: discovery shape -------------------------
    # If the file is missing the agentprdiff import or the suite() call
    # at module level, ``load_suites`` would either fail to find it or
    # produce nothing useful. Flag it loudly here so the user sees
    # "missing agentprdiff import" instead of "no suites discovered".
    discovery_problems = _check_discovery_markers(source)

    if workspace is None:
        # HTTP-mode project: no disk to import from. Surface the
        # markers + bail cleanly.
        import_load.status = "skipped"
        suite_discovery.status = "passed" if not discovery_problems else "failed"
        suite_discovery.diagnostics.extend(discovery_problems)
        report = _finalize(stages, started)
        log.info(
            "preflight: workspace=none discovery_ok=%s",
            not discovery_problems,
        )
        return report

    # --- Stage 2: subprocess import_load -------------------------------
    s2_start = time.monotonic()
    worker_result = _run_worker(
        source,
        workspace,
        python=_project_python(workspace),
        timeout_s=subprocess_timeout_s,
    )

    # If stage 2 reported MODULE_NOT_FOUND and the caller asked to
    # auto-install, retry inside a fresh preview venv. We do NOT swap
    # the original diagnostic out — the user still needs to know what
    # was missing so they can persist the fix — but we DO surface the
    # successful retry as ``preview_venv_used`` so the UI can label the
    # preview clearly as non-persistent.
    preview_used = False
    if (
        auto_install
        and any(d["code"] == ErrorCode.MODULE_NOT_FOUND for d in worker_result.get("diagnostics", []))
    ):
        missing = next(
            (d.get("missing_module") for d in worker_result["diagnostics"]
             if d["code"] == ErrorCode.MODULE_NOT_FOUND),
            None,
        )
        log.info(
            "preflight: auto_install retry missing_module=%s",
            missing,
        )
        try:
            venv_path, preview_py = _make_preview_venv(missing)
        except subprocess.CalledProcessError as exc:  # pragma: no cover
            log.warning("auto_install pip failed: %s", exc.stderr)
            import_load.diagnostics.append(
                PreflightDiagnostic(
                    stage="import_load",
                    code=ErrorCode.AUTO_INSTALL_FAILED,
                    severity="warning",
                    message=(
                        "auto-install retry failed during pip install: "
                        f"{exc.stderr.strip() or exc}"
                    ),
                    fix_hint=(
                        "Install the package manually and re-run; the "
                        "non-persistent retry is best-effort."
                    ),
                )
            )
            preview_py = None
            venv_path = None
        else:
            try:
                retry_result = _run_worker(
                    source,
                    workspace,
                    python=preview_py,
                    timeout_s=subprocess_timeout_s,
                )
                if retry_result.get("ok"):
                    preview_used = True
                    # The retry succeeded — keep the original diagnostic
                    # so the user sees what to persist, but downgrade
                    # its severity to ``warning`` so the page doesn't
                    # render a hard failure.
                    worker_result = retry_result
                    worker_result.setdefault("preview_diagnostics", []).append({
                        "stage": "import_load",
                        "code": ErrorCode.MODULE_NOT_FOUND,
                        "severity": "warning",
                        "message": (
                            f"{missing or '<unknown>'} is missing from the "
                            "project venv — installed it into an ephemeral "
                            "preview venv so you can see the suite shape. "
                            "Persist the fix to make this stick."
                        ),
                        "missing_module": missing,
                    })
            finally:
                if venv_path is not None:
                    _destroy_preview_venv(venv_path)

    # Route each worker diagnostic to the stage it belongs to. The
    # worker may emit "stage": "suite_discovery" for "loaded but no
    # cases" — routing those to import_load would falsely fail load.
    suite_discovery_from_worker: list[PreflightDiagnostic] = []
    for raw in worker_result.get("diagnostics", []):
        d = _diag_from_worker(raw, workspace)
        if d.stage == "suite_discovery":
            suite_discovery_from_worker.append(d)
        else:
            import_load.diagnostics.append(d)
    for raw in worker_result.get("preview_diagnostics", []):
        d = _diag_from_worker(raw, workspace)
        if d.stage == "suite_discovery":
            suite_discovery_from_worker.append(d)
        else:
            import_load.diagnostics.append(d)

    if any(d.severity == "error" for d in import_load.diagnostics):
        import_load.status = "failed"
    else:
        import_load.status = "passed"
    import_load.duration_ms = int((time.monotonic() - s2_start) * 1000)

    # --- Stage 3: suite_discovery --------------------------------------
    s3_start = time.monotonic()
    suite_discovery.diagnostics.extend(discovery_problems)
    suite_discovery.diagnostics.extend(suite_discovery_from_worker)
    discovered = list(worker_result.get("discovered_suites", []))
    total_cases = int(worker_result.get("total_cases", 0))
    if import_load.status == "passed":
        if not discovered:
            suite_discovery.diagnostics.append(
                PreflightDiagnostic(
                    stage="suite_discovery",
                    code=ErrorCode.NO_SUITES_DISCOVERED,
                    severity="error",
                    message="loaded the file but found no Suite objects",
                    fix_hint=(
                        "Bind your suite to a module-level name via "
                        "`my_suite = suite(...)`."
                    ),
                )
            )
        elif total_cases == 0:
            suite_discovery.diagnostics.append(
                PreflightDiagnostic(
                    stage="suite_discovery",
                    code=ErrorCode.NO_CASES_IN_SUITES,
                    severity="error",
                    message="discovered suites but no cases inside them",
                    fix_hint=(
                        "Add at least one `case(...)` entry to the suite."
                    ),
                )
            )
    if any(d.severity == "error" for d in suite_discovery.diagnostics):
        suite_discovery.status = "failed"
    elif import_load.status == "passed":
        suite_discovery.status = "passed"
    else:
        # Don't claim discovery passed when load couldn't run.
        suite_discovery.status = "skipped"
    suite_discovery.duration_ms = int((time.monotonic() - s3_start) * 1000)

    report = _finalize(
        stages,
        started,
        discovered_suites=discovered,
        total_cases=total_cases,
        preview_venv_used=preview_used,
    )
    log.info(
        "preflight: ok=%s syntax=%s import_load=%s suite_discovery=%s "
        "code=%s suites=%d cases=%d preview_venv=%s",
        report.ok,
        syntax.status,
        import_load.status,
        suite_discovery.status,
        report.error_code,
        len(discovered),
        total_cases,
        preview_used,
    )
    return report


def _finalize(
    stages: list[PreflightStage],
    started: float,
    *,
    discovered_suites: list[str] | None = None,
    total_cases: int = 0,
    preview_venv_used: bool = False,
) -> PreflightReport:
    ok = all(s.status in ("passed", "skipped") for s in stages)
    first_error: PreflightDiagnostic | None = None
    for stage in stages:
        for d in stage.diagnostics:
            if d.severity == "error":
                first_error = d
                break
        if first_error is not None:
            break
    summary: str
    if ok:
        summary = "All preflight stages passed."
    else:
        summary = first_error.message if first_error else "preflight failed"
    return PreflightReport(
        ok=ok,
        stages=stages,
        error_code=first_error.code if first_error else None,
        summary=summary,
        discovered_suites=discovered_suites or [],
        total_cases=total_cases,
        preview_venv_used=preview_venv_used,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _check_discovery_markers(source: str) -> list[PreflightDiagnostic]:
    """Cheap text scan for the two markers the engine's loader requires."""
    out: list[PreflightDiagnostic] = []
    if not any(hint in source for hint in _AGENTPRDIFF_IMPORT_HINTS):
        out.append(
            PreflightDiagnostic(
                stage="suite_discovery",
                code=ErrorCode.MISSING_AGENTPRDIFF_IMPORT,
                severity="error",
                message=(
                    "the generated file doesn't import from `agentprdiff`; "
                    "discovery would skip it"
                ),
                fix_hint=(
                    "Add `from agentprdiff import suite, case` (and any "
                    "graders you use) at the top of the file."
                ),
            )
        )
    if _SUITE_CALL_MARKER not in source:
        out.append(
            PreflightDiagnostic(
                stage="suite_discovery",
                code=ErrorCode.MISSING_SUITE_CALL,
                severity="error",
                message=(
                    "the generated file doesn't call `suite(...)` at "
                    "module level"
                ),
                fix_hint=(
                    "Bind your cases to a suite with "
                    "`my_suite = suite(name=..., cases=[...])`."
                ),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Internal: ImportDiagnostic → PreflightDiagnostic promotion
# ---------------------------------------------------------------------------


def _promote_import_diag(d: ImportDiagnostic) -> PreflightDiagnostic:
    """Convert sanitizer-level diagnostic to a preflight-level one.

    Both shapes carry line/col/statement; the preflight form adds the
    stable code, severity, and a richer fix hint.
    """
    if d.cause == "syntax_error":
        return PreflightDiagnostic(
            stage="syntax",
            code=ErrorCode.SYNTAX_ERROR,
            severity="error",
            message=d.message,
            line=d.line,
            col=d.col,
            statement=d.statement,
            fix_hint=(
                "Generated suite didn't parse as Python. The LLM may have "
                "emitted prose outside fences or an invalid import path."
            ),
        )
    # Default: invalid module path. The sanitizer's message already
    # explains *why* — we layer on the stable code and a fix hint.
    is_hyphen = "-" in (d.statement or "")
    return PreflightDiagnostic(
        stage="syntax",
        code=ErrorCode.HYPHENATED_IMPORT if is_hyphen else ErrorCode.INVALID_IMPORT_PATH,
        severity="error",
        message=d.message,
        line=d.line,
        col=d.col,
        statement=d.statement,
        fix_hint=_hyphen_fix_hint(d.statement) if is_hyphen else (
            "Module paths must use only valid Python identifiers. Either "
            "rename the offending directory or define an inline adapter."
        ),
    )


def _diag_from_worker(
    raw: dict[str, Any], workspace: Path | None
) -> PreflightDiagnostic:
    """Convert a worker-emitted dict into a strongly-typed diagnostic.

    The worker emits the stable code; we attach a remediation dict when
    we recognise the failure mode (most importantly:
    ``MODULE_NOT_FOUND``, which gets the actionable "add to manifest"
    card).
    """
    code = raw.get("code", ErrorCode.LOAD_EXCEPTION)
    missing = raw.get("missing_module")
    remediation = None
    if code == ErrorCode.MODULE_NOT_FOUND and missing:
        remediation = _missing_dep_remediation(missing, workspace)
    return PreflightDiagnostic(
        stage=raw.get("stage", "import_load"),
        code=code,
        severity=raw.get("severity", "error"),
        message=raw.get("message", ""),
        line=raw.get("line"),
        col=raw.get("col"),
        fix_hint=raw.get("fix_hint"),
        remediation=remediation,
    )


# ---------------------------------------------------------------------------
# Subprocess invocation
# ---------------------------------------------------------------------------


def _run_worker(
    source: str,
    workspace: Path,
    *,
    python: Path,
    timeout_s: float,
) -> dict[str, Any]:
    """Run the preflight worker against ``source`` and return its JSON.

    Writes the suite file under ``workspace/.studio-staging/`` so
    sibling imports (``from foo import …``) resolve relative to the
    workspace exactly as they would after save. Cleans up both the
    suite file and the JSON result file on exit. Returns
    ``{"diagnostics": [...]}`` only — discovery counts are inlined as
    extra keys on the returned dict.
    """
    staging = workspace / ".studio-staging"
    staging.mkdir(parents=True, exist_ok=True)
    suite_path = staging / f"preflight_{uuid.uuid4().hex}.py"
    out_path = staging / f"preflight_{uuid.uuid4().hex}.json"
    worker_path = _write_worker(staging)

    try:
        suite_path.write_text(source, encoding="utf-8")
        # Sanity check: every path we hand the subprocess must live
        # inside the workspace. The is_within check uses resolve() so
        # symlinks that escape are caught. This is paranoid — we just
        # created these paths — but it keeps the rule symmetric with
        # the deep-scan scope guard.
        for p in (suite_path, worker_path):
            if not is_within(p, workspace):
                return {
                    "ok": False,
                    "diagnostics": [{
                        "stage": "import_load",
                        "code": ErrorCode.PATH_OUTSIDE_ROOT,
                        "severity": "error",
                        "message": (
                            f"refused to run preflight worker because "
                            f"{p} resolves outside the workspace"
                        ),
                    }],
                }

        try:
            proc = subprocess.run(
                [
                    str(python),
                    str(worker_path),
                    str(suite_path),
                    str(workspace),
                    str(out_path),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "diagnostics": [{
                    "stage": "import_load",
                    "code": ErrorCode.LOAD_TIMEOUT,
                    "severity": "error",
                    "message": (
                        f"preflight worker timed out after {timeout_s}s — "
                        "this usually means the agent module has expensive "
                        "import-time side effects (network calls, model "
                        "downloads). Move them inside the callable."
                    ),
                }],
            }

        if not out_path.exists():
            # The worker died before writing JSON. Surface stderr so
            # the user has *something* to triage on.
            stderr = (proc.stderr or "").strip() or proc.stdout.strip()
            return {
                "ok": False,
                "diagnostics": [{
                    "stage": "import_load",
                    "code": ErrorCode.LOAD_EXCEPTION,
                    "severity": "error",
                    "message": (
                        "preflight worker exited without writing a result"
                        f" (exit={proc.returncode}): {stderr[:500]}"
                    ),
                }],
            }
        try:
            return json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return {
                "ok": False,
                "diagnostics": [{
                    "stage": "import_load",
                    "code": ErrorCode.LOAD_EXCEPTION,
                    "severity": "error",
                    "message": f"could not parse worker JSON: {exc}",
                }],
            }
    finally:
        for p in (suite_path, out_path, worker_path):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass

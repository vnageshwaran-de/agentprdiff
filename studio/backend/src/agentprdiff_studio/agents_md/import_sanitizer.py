"""Path → Python-import-string sanitization for the generation pipeline.

Background
----------

When Studio scans a workspace for the project's agent module, it converts
filesystem paths to dotted import paths (``foo/bar/baz.py`` → ``foo.bar.baz``).
The naive version of that — ``str.replace("/", ".")`` — generates *invalid*
Python when any directory in the path contains characters that aren't valid
in Python identifiers. Common culprits:

* hyphens: ``transcript-ingest-v2/cloud_function/main.py``
* digits-first: ``2024-q3/agent.py``
* dots in directory names: ``my.service/agent.py``

The resulting ``from transcript-ingest-v2.cloud_function.main import …``
fails at ``ast.parse`` time — and even when an LLM faithfully copies the
import string we hand it, the generated suite is unusable.

What this module does
---------------------

1. ``is_valid_dotted_module(s)`` — strict identifier validation on every
   segment of a dotted module path.
2. ``path_to_module(path, root)`` — derive an importable dotted name from
   a file path; returns ``None`` when no safe derivation exists.
3. ``classify_target(file_path, root)`` — pick a generation strategy:

   * ``"direct"``           — emit ``from <module> import …`` (path is
                               import-safe and a callable agent function
                               is exported).
   * ``"adapter"``           — the file exists but its path can't be
                               expressed as a dotted import OR the file
                               is a service/app with no top-level callable
                               agent function. The suite generates an
                               *inline* eval-adapter function
                               (``def my_agent(query): ...``) with
                               framework-aware TODO hints instead of
                               importing anything from a guessed path.
   * ``"extend_existing"``   — the workspace already contains a discoverable
                               ``agentprdiff`` suite. The generation
                               prompt receives that file and is told to
                               *add* cases, not produce a new generic suite.
   * ``"scaffold"``          — no agent module found at all; generate a
                               stub with ``TODO`` markers instead of
                               inventing imports.

4. ``detect_framework(file_path)`` — light pattern match on the file
   content to identify Flask / FastAPI / Cloud Function / CLI / module
   shapes. Drives the adapter prompt: a Flask app needs a test-client
   adapter, a Cloud Function needs an HTTP-handler adapter, etc.

5. ``find_existing_suite(workspace)`` — first ``.py`` under the workspace
   that contains both an ``agentprdiff`` import and a ``suite(...)``
   call. Triggers the ``extend_existing`` strategy when found.

6. ``validate_generated_imports(source)`` — AST-walks generated suite code
   and confirms every ``import``/``from`` statement uses only valid
   identifiers. Returns a list of structured diagnostics suitable for the
   UI's preflight error panel.

The module is *generic* — no project name, no hardcoded directory, no
"if path contains foo then …" specials. The only domain knowledge is the
Python language reference for what counts as an identifier and a small
set of well-known framework markers.

Hard rule: we do **not** generate ``importlib.util.spec_from_file_location``
calls from guessed paths. The previous ``dynamic_load`` strategy did that
and produced suites that imported the wrong module when the user's layout
shifted, or executed arbitrary code from a path the user didn't approve.
The adapter strategy makes the user-supplied integration point obvious
and forces a deliberate wiring step.
"""

from __future__ import annotations

import ast
import keyword
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# A "soft" identifier check that's stricter than ``str.isidentifier()``:
# we also reject leading underscore for top-level workspace modules
# (because conventionally those are private) and well-known Python soft
# keywords. ``isidentifier()`` accepts ``_foo`` and ``match``; we use it
# as the base and layer on extra checks where needed.
_PY_SOFT_KEYWORDS = {"match", "case", "type"}


def is_valid_identifier(segment: str) -> bool:
    """True iff ``segment`` is a usable Python module name component.

    Rules:

    * Must satisfy Python's lexical identifier definition (``str.isidentifier``).
      That catches hyphens, leading digits, spaces, dots, slashes, plus
      every non-ASCII non-letter character.
    * Must not be a Python reserved word (``keyword.iskeyword``). Otherwise
      ``from def import …`` parses but fails at the import resolution stage.
    * Must not be one of the post-3.10 soft keywords used in pattern
      matching (``match``/``case``/``type``). They're technically usable
      as module names but generate confusing parse errors at import time.
    """
    if not segment:
        return False
    if not segment.isidentifier():
        return False
    if keyword.iskeyword(segment):
        return False
    return segment not in _PY_SOFT_KEYWORDS


def is_valid_dotted_module(dotted: str) -> bool:
    """True iff every dot-separated segment of ``dotted`` is an identifier."""
    if not dotted or "." not in dotted and not is_valid_identifier(dotted):
        # Special-case: a single segment is just a plain identifier check.
        return is_valid_identifier(dotted) if dotted else False
    return all(is_valid_identifier(seg) for seg in dotted.split("."))


# ---------------------------------------------------------------------------
# Path → module derivation
# ---------------------------------------------------------------------------


def _path_segments_to_module(rel_parts: list[str]) -> str | None:
    """Convert a sequence of path parts (already relative to root) into a
    dotted module path, after dropping the ``.py`` suffix on the last
    part and any ``__init__`` indicator.

    Returns ``None`` if any segment fails identifier validation.
    """
    if not rel_parts:
        return None
    parts = list(rel_parts)

    # Strip ``__init__.py`` so ``foo/__init__.py`` → ``foo``.
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts[-1].endswith(".py"):
        parts = parts[:-1] + [parts[-1][:-3]]

    if not parts:
        return None
    if not all(is_valid_identifier(p) for p in parts):
        return None
    return ".".join(parts)


def path_to_module(file_path: Path, root: Path) -> str | None:
    """Best-effort derive a dotted module path from a file under ``root``.

    Returns ``None`` when:

    * ``file_path`` isn't under ``root`` (scope violation; see
      :func:`is_within`),
    * the file isn't ``.py`` and isn't a package ``__init__.py``,
    * any directory in the path between ``root`` and ``file_path`` fails
      identifier validation.

    Callers must treat ``None`` as a signal to switch to the adapter
    strategy (inline ``my_agent`` wrapper) or the scaffold fallback.
    The sanitizer deliberately does NOT recommend dynamic loading via
    ``importlib.util.spec_from_file_location``: we don't generate imports
    from guessed paths.
    """
    try:
        rel = file_path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return _path_segments_to_module(list(rel.parts))


def closest_safe_subpath(file_path: Path, root: Path) -> str | None:
    """Find the longest prefix of ``file_path`` under ``root`` whose
    dotted form is import-safe.

    Useful when only the *leaf* directory is hyphenated:
    ``foo/bar-baz/main.py`` → ``foo`` is safe, but ``foo.bar-baz`` isn't,
    so we surface ``foo`` as the closest legitimate import point.
    Returns ``None`` if no prefix is safe.
    """
    try:
        rel = file_path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    parts = list(rel.parts)
    # Drop the filename; we're after the directory chain only.
    if parts[-1].endswith(".py"):
        parts = parts[:-1]
    while parts and not all(is_valid_identifier(p) for p in parts):
        parts.pop()
    return ".".join(parts) if parts else None


def is_within(path: Path, root: Path) -> bool:
    """True iff ``path``'s resolved location lives under ``root``.

    Used by the deep-scan to reject paths that escape via symlinks or
    explicit ``../`` traversal. ``resolve()`` follows symlinks, so a
    symlinked file that points outside ``root`` will return ``False``.
    """
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


# ---------------------------------------------------------------------------
# Generation strategy
# ---------------------------------------------------------------------------


Strategy = Literal["direct", "adapter", "extend_existing", "scaffold"]

# Framework labels emitted by :func:`detect_framework`. Kept narrow on
# purpose — every value listed here gets a tailored adapter prompt;
# unknown shapes fall back to ``"module"``.
Framework = Literal["flask", "fastapi", "cloud_function", "cli", "module"]


@dataclass(slots=True, frozen=True)
class GenerationTarget:
    """How the LLM should reference the user's agent in the generated suite.

    ``strategy="direct"``:
        ``module`` is set; the suite should ``from {module} import {callable}``.
        ``framework`` is set when the file matches a known shape (Flask,
        FastAPI, etc.) so the prompt can mention it; the LLM still uses a
        direct import.

    ``strategy="adapter"``:
        Either the file's path can't be expressed as a dotted import, OR
        the file is a service/app with no top-level callable agent.
        ``file_path`` and ``module`` are *informational* (for the LLM's
        TODO comment); the suite must define an inline adapter function
        and **must not** emit a ``spec_from_file_location`` call.
        ``framework`` tells the prompt which adapter shape to use
        (Flask test client, FastAPI test client, Cloud Function handler,
        plain function call).

    ``strategy="extend_existing"``:
        ``existing_suite_path`` is set; the prompt should *add* cases to
        that file rather than emit a new generic suite. Other fields are
        populated as for the base classification so the LLM has context
        about what the suite tests.

    ``strategy="scaffold"``:
        Neither ``module`` nor ``file_path`` is reliably set; the suite
        should be a stub with ``TODO`` markers — better than emitting an
        invented module name that won't resolve.
    """

    strategy: Strategy
    callable_name: str
    module: str | None = None
    file_path: str | None = None
    # The cleaned-up file stem (always a valid Python identifier) — used
    # as a stable name for the inline adapter function or for any
    # documentation reference inside the generated suite.
    safe_identifier: str | None = None
    # Reason we picked this strategy. Empty string for ``direct``.
    reason: str = ""
    # Inferred framework / shape of the target file. Populated whenever a
    # file is identified; ``None`` for ``scaffold``.
    framework: Framework | None = None
    # Relative path (POSIX) to an existing ``agentprdiff`` suite that the
    # generation prompt should extend instead of replacing.
    existing_suite_path: str | None = None


def _safe_identifier_from_path(file_path: Path) -> str:
    """Turn an arbitrary file path into a safe Python identifier.

    Used as the *inline adapter function's* helper name and for any
    documentation reference inside the generated suite (e.g. a TODO
    comment that names the file the adapter is wrapping). Producing a
    clean snake_case name keeps the resulting suite readable even when
    the original filename has dots, dashes, or leading digits.

    Strategy: take the filename without extension, replace every
    non-identifier-safe character with ``_``, collapse runs of
    underscores, prepend ``_mod`` if the result starts with a digit or
    is empty. The output is guaranteed to satisfy ``isidentifier()``.
    """
    stem = file_path.stem if file_path.suffix == ".py" else file_path.name
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned or cleaned[0].isdigit() or not cleaned.isidentifier():
        cleaned = f"_mod_{cleaned}" if cleaned else "_mod"
    if not cleaned.isidentifier():
        # Final fallback — strip everything that's still illegal.
        cleaned = "_mod"
    return cleaned


def classify_target(
    file_path: Path | None,
    root: Path,
    callable_name: str = "run",
    *,
    has_callable: bool = True,
    framework: Framework | None = None,
) -> GenerationTarget:
    """Decide how the generated suite should reference ``file_path``.

    Generic — no project-name special cases. The flowchart:

    * No ``file_path``                       → ``scaffold``
    * ``file_path`` escapes ``root``         → ``scaffold`` (scope violation)
    * ``path_to_module`` succeeds AND a
      callable agent function exists         → ``direct``
    * Otherwise (file exists but the path
      isn't import-safe, OR there is no
      callable agent function in the file)   → ``adapter``

    ``has_callable``:
        When the caller has already inspected the target file and knows
        it does NOT expose a top-level callable to use as the agent (e.g.
        it's a Flask app object, a FastAPI router, a Cloud Function
        decorator-style handler), pass ``has_callable=False``. The
        ``adapter`` strategy is then selected even when the path itself
        is import-safe — the LLM will define an inline ``my_agent``
        wrapper around the framework's entry point instead of inventing
        a function name to import.

    ``framework``:
        Optional framework hint propagated onto the returned target.
        Used only as documentation for ``direct`` / ``adapter``; ignored
        for ``scaffold``.
    """
    if file_path is None:
        return GenerationTarget(
            strategy="scaffold",
            callable_name=callable_name,
            reason="no agent module detected in the workspace",
        )

    if not is_within(file_path, root):
        return GenerationTarget(
            strategy="scaffold",
            callable_name=callable_name,
            reason=(
                "the detected agent file is outside the workspace root; "
                "Studio refuses to import code from outside the selected "
                "scope unless you explicitly expand it"
            ),
        )

    module = path_to_module(file_path, root)
    rel = file_path.resolve().relative_to(root.resolve())
    safe_ident = _safe_identifier_from_path(file_path)

    if module is not None and has_callable:
        return GenerationTarget(
            strategy="direct",
            callable_name=callable_name,
            module=module,
            file_path=rel.as_posix(),
            safe_identifier=safe_ident,
            framework=framework,
        )

    # ``adapter`` strategy: either the path isn't import-safe OR the file
    # has no top-level callable agent. Either way, the suite generates an
    # inline ``def my_agent(query)`` wrapper instead of importing from a
    # guessed path. We surface the closest safe prefix and a clear reason
    # so the LLM (and any human reading the diagnostics) understands
    # exactly why a direct import is off the table.
    reason_bits: list[str] = []
    if module is None:
        closest = closest_safe_subpath(file_path, root)
        bad = next(
            (p for p in rel.parts if not is_valid_identifier(p.removesuffix(".py"))),
            rel.parts[-1] if rel.parts else "",
        )
        reason_bits.append(
            f"path segment {bad!r} contains characters that aren't valid "
            "in Python identifiers (hyphens, dots, leading digits, etc.); "
            "dotted-import syntax can't reach it",
        )
        if closest:
            reason_bits.append(
                f"closest safely-importable prefix is {closest!r} — "
                "rename the offending directory if you want a direct import",
            )
        documented_module = closest
    else:
        documented_module = module

    if not has_callable:
        framework_label = framework or "module"
        reason_bits.append(
            f"file matches a {framework_label} shape with no top-level "
            "callable agent function — the suite must define an inline "
            "adapter that drives the application's real entry point",
        )

    return GenerationTarget(
        strategy="adapter",
        callable_name=callable_name,
        module=documented_module,  # documentation only; not for code generation
        file_path=rel.as_posix(),
        safe_identifier=safe_ident,
        reason="; ".join(reason_bits) if reason_bits else "",
        framework=framework,
    )


# ---------------------------------------------------------------------------
# Framework detection
# ---------------------------------------------------------------------------


# Compact pattern → framework label table. Each entry is
# ``(label, ordered list of regexes any of which is sufficient evidence)``.
# Order matters: we check Cloud Function before Flask because a Cloud
# Function file often imports Flask too, and we want the more specific
# label. Patterns are compiled once at import time.
_FRAMEWORK_PATTERNS: tuple[tuple[Framework, tuple[re.Pattern[str], ...]], ...] = (
    (
        "cloud_function",
        (
            re.compile(r"\bfunctions_framework\b"),
            re.compile(r"@functions_framework\.http\b"),
        ),
    ),
    (
        "fastapi",
        (
            re.compile(r"\bfrom\s+fastapi\b"),
            re.compile(r"\bimport\s+fastapi\b"),
            re.compile(r"\bFastAPI\s*\("),
        ),
    ),
    (
        "flask",
        (
            re.compile(r"\bfrom\s+flask\b"),
            re.compile(r"\bimport\s+flask\b"),
            re.compile(r"\bFlask\s*\("),
        ),
    ),
    (
        "cli",
        (
            re.compile(r"\bimport\s+click\b"),
            re.compile(r"\bfrom\s+click\b"),
            re.compile(r"@click\.command\b"),
            re.compile(r"\bargparse\.ArgumentParser\s*\("),
            re.compile(r"\bfrom\s+argparse\b"),
        ),
    ),
)


def detect_framework(file_path: Path) -> Framework:
    """Inspect ``file_path``'s source and label its framework shape.

    Returns one of:

    * ``"cloud_function"`` — Google Cloud Functions / ``functions_framework``
    * ``"fastapi"``         — FastAPI route module
    * ``"flask"``           — Flask app or blueprint
    * ``"cli"``             — Click / argparse entrypoint
    * ``"module"``          — plain Python module (no framework markers)

    The label drives the adapter prompt — a Flask app needs a test-client
    wrapper, a Cloud Function needs an HTTP-handler wrapper, a CLI needs
    ``CliRunner.invoke`` or a subprocess shim, etc. Detection is read-only
    and tolerant of decoding errors.
    """
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "module"
    # Hard cap so a 10 MB generated bundle doesn't burn CPU on regex.
    # The framework markers always appear in the first few hundred lines.
    snippet = text[:64_000]
    for label, patterns in _FRAMEWORK_PATTERNS:
        for pat in patterns:
            if pat.search(snippet):
                return label
    return "module"


def file_has_callable_agent(file_path: Path) -> bool:
    """True iff ``file_path`` has a top-level callable usable as the agent.

    A "callable agent" is a module-level ``def`` / ``async def`` (the
    framework also accepts callable instances, but we can't tell from a
    static scan whether ``foo = MyAgent()`` is callable). Used by
    :func:`classify_target` to switch to the ``adapter`` strategy for
    files that are framework-shaped but expose no plain function — Flask
    apps, FastAPI routers, Cloud Function decorator-style handlers.
    """
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in tree.body
    )


# ---------------------------------------------------------------------------
# Existing-suite detection
# ---------------------------------------------------------------------------


# Directories never worth walking when looking for an existing suite —
# kept in sync with the exclusion list used by the rest of discovery.
_EXISTING_SUITE_EXCLUDE = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build",
    ".agentprdiff", ".studio-venv", ".studio-staging", ".studio-tour",
}

_AGENTPRDIFF_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+agentprdiff(?:\.\w+)?\s+import|import\s+agentprdiff)\b",
    re.MULTILINE,
)


def _imports_agentprdiff(tree: ast.AST) -> bool:
    """Walk ``tree`` and look for a real ``agentprdiff`` import statement.

    Catches both ``from agentprdiff import …`` and ``import agentprdiff``,
    including ``import agentprdiff.graders``. AST-level so comments and
    string literals never produce false positives.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "agentprdiff" or mod.startswith("agentprdiff."):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = (alias.name or "").split(".", 1)[0]
                if top == "agentprdiff":
                    return True
    return False


def _calls_suite_at_module_level(tree: ast.Module) -> bool:
    """True iff the module body contains a top-level ``suite(...)`` call.

    Recognises both the bare statement form ``suite(...)`` and the
    common assignment form ``foo = suite(...)``. We deliberately only
    look at the module body — calls hidden inside ``def``s don't count
    as a project-canonical suite (they're typically helpers).
    """

    def _is_suite_call(value: ast.AST) -> bool:
        if not isinstance(value, ast.Call):
            return False
        func = value.func
        if isinstance(func, ast.Name) and func.id == "suite":
            return True
        return isinstance(func, ast.Attribute) and func.attr == "suite"

    for node in tree.body:
        if isinstance(node, ast.Expr) and _is_suite_call(node.value):
            return True
        if isinstance(node, ast.Assign) and _is_suite_call(node.value):
            return True
        if isinstance(node, ast.AnnAssign) and node.value is not None and _is_suite_call(
            node.value
        ):
            return True
    return False


def find_existing_suite(workspace: Path) -> Path | None:
    """Return the path to an existing ``agentprdiff`` suite, or ``None``.

    A suite is recognised by the same heuristic Studio's discovery uses:
    the file imports from ``agentprdiff`` AND contains a module-level
    ``suite(...)`` call. Both checks run against the AST, so comments
    and string literals never produce false positives. We walk the
    workspace, skip excluded directories, and return the *shortest*
    matching path so a deeply nested test fixture doesn't shadow the
    project's canonical suite.

    Returns ``None`` if no candidate is found.

    The function is used by :func:`agents_md.validate.guess_agent_target`
    to switch the generation prompt into ``extend_existing`` mode — when
    the project already has a suite, the LLM is told to add cases to it
    instead of producing a competing generic file.
    """
    if not workspace.exists() or not workspace.is_dir():
        return None
    candidates: list[Path] = []
    for path in workspace.rglob("*.py"):
        try:
            rel_parts = path.relative_to(workspace).parts
        except ValueError:
            continue
        if any(p in _EXISTING_SUITE_EXCLUDE for p in rel_parts):
            continue
        if not is_within(path, workspace):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Cheap pre-filter: a file with no ``agentprdiff`` substring
        # can't be a suite, and the AST parse below is expensive enough
        # to be worth skipping. False positives (substring in a comment)
        # are harmless because the AST check is authoritative.
        if "agentprdiff" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        if not _imports_agentprdiff(tree):
            continue
        if not _calls_suite_at_module_level(tree):
            continue
        candidates.append(path)
    if not candidates:
        return None
    # Prefer the shortest path under the workspace; ties broken
    # alphabetically for stability.
    candidates.sort(key=lambda p: (len(p.parts), p.as_posix()))
    return candidates[0]


# ---------------------------------------------------------------------------
# Generated-code validation
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class ImportDiagnostic:
    """One actionable problem found in generated suite source.

    ``line``/``col`` are 1-based (the convention :mod:`ast` uses for
    error messages). ``cause`` is a short machine-readable token; the
    UI uses it to pick a remediation hint. ``message`` is human-readable.
    """

    line: int
    col: int
    cause: str
    message: str
    statement: str = ""

    def to_dict(self) -> dict:
        return {
            "line": self.line,
            "col": self.col,
            "cause": self.cause,
            "message": self.message,
            "statement": self.statement,
        }


def validate_generated_imports(source: str) -> list[ImportDiagnostic]:
    """AST-walk ``source`` and return one diagnostic per invalid import.

    Catches things ``ast.parse`` alone can't — most importantly the case
    where a multi-segment dotted name uses characters that *are* lexically
    fine inside a string but get split into invalid identifiers when an
    ``import`` statement is constructed from them.

    Returns ``[]`` on a clean file. A ``SyntaxError`` short-circuits with
    a single diagnostic whose ``cause`` is ``"syntax_error"``.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        # The most common downstream symptom of the path-to-module bug:
        # ``from foo-bar import …`` reaches the parser as a token error.
        return [
            ImportDiagnostic(
                line=exc.lineno or 1,
                col=(exc.offset or 1),
                cause="syntax_error",
                message=exc.msg or "could not parse generated suite",
                statement=(exc.text or "").rstrip(),
            )
        ]

    diagnostics: list[ImportDiagnostic] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""  # ``from . import x`` has module=None
            if module and not is_valid_dotted_module(module):
                bad = next(
                    (
                        seg
                        for seg in module.split(".")
                        if not is_valid_identifier(seg)
                    ),
                    module,
                )
                diagnostics.append(
                    ImportDiagnostic(
                        line=node.lineno,
                        col=node.col_offset + 1,
                        cause="invalid_module_path",
                        message=(
                            f"`from {module} import …` is invalid Python: "
                            f"the segment {bad!r} isn't a valid identifier. "
                            "Define an inline adapter function in the suite "
                            "instead of importing from this path, or rename "
                            "the directory so it becomes import-safe."
                        ),
                        statement=f"from {module} import …",
                    )
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name and not is_valid_dotted_module(alias.name):
                    bad = next(
                        (
                            seg
                            for seg in alias.name.split(".")
                            if not is_valid_identifier(seg)
                        ),
                        alias.name,
                    )
                    diagnostics.append(
                        ImportDiagnostic(
                            line=node.lineno,
                            col=node.col_offset + 1,
                            cause="invalid_module_path",
                            message=(
                                f"`import {alias.name}` is invalid Python: "
                                f"the segment {bad!r} isn't a valid "
                                "identifier. Define an inline adapter "
                                "function in the suite instead, or rename "
                                "the directory so it becomes import-safe."
                            ),
                            statement=f"import {alias.name}",
                        )
                    )

    return diagnostics

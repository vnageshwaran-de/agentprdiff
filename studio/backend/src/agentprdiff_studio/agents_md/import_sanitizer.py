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

   * ``"direct"``        — emit ``from <module> import …`` (path is import-safe)
   * ``"dynamic_load"``  — emit ``importlib.util.spec_from_file_location(...)``
                            (file exists but the path isn't import-safe)
   * ``"scaffold"``      — no agent module found at all; generate a stub with
                            ``TODO`` markers instead of inventing imports

4. ``validate_generated_imports(source)`` — AST-walks generated suite code
   and confirms every ``import``/``from`` statement uses only valid
   identifiers. Returns a list of structured diagnostics suitable for the
   UI's preflight error panel.

The module is *generic* — no project name, no hardcoded directory, no
"if path contains foo then …" specials. The only domain knowledge is the
Python language reference for what counts as an identifier.
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

    Callers must treat ``None`` as a signal to switch to the dynamic-load
    strategy or scaffold fallback.
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


Strategy = Literal["direct", "dynamic_load", "scaffold"]


@dataclass(slots=True, frozen=True)
class GenerationTarget:
    """How the LLM should reference the user's agent in the generated suite.

    ``strategy="direct"``:
        ``module`` is set; the suite should ``from {module} import {callable}``.

    ``strategy="dynamic_load"``:
        ``file_path`` is set (relative to the workspace root); the suite
        should load the module via ``importlib.util.spec_from_file_location``.
        ``module`` may be set to the closest safe prefix (for documentation
        purposes) but isn't import-safe by itself.

    ``strategy="scaffold"``:
        Neither ``module`` nor ``file_path`` is reliably set; the suite
        should be a stub with ``TODO`` markers — better than emitting an
        invented module name that won't resolve.
    """

    strategy: Strategy
    callable_name: str
    module: str | None = None
    file_path: str | None = None
    # The basename of the resolved file (no extension), used to invent a
    # stable Python identifier when we generate dynamic-load code.
    safe_identifier: str | None = None
    # Reason we picked this strategy. Empty string for ``direct``.
    reason: str = ""


def _safe_identifier_from_path(file_path: Path) -> str:
    """Turn an arbitrary file path into a safe Python identifier.

    Used as the *internal* module name we pass to
    ``importlib.util.spec_from_file_location``. The spec accepts any
    string here (it's purely a label), but generating a clean snake_case
    one makes the resulting suite readable.

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
) -> GenerationTarget:
    """Decide how the generated suite should reference ``file_path``.

    Generic — no project-name special cases. The flowchart:

    * No ``file_path``                  → ``scaffold``
    * ``file_path`` escapes ``root``    → ``scaffold`` (scope violation)
    * ``path_to_module`` succeeds       → ``direct``
    * Otherwise (path contains unsafe
      segments like hyphens)            → ``dynamic_load``
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
    if module is not None:
        return GenerationTarget(
            strategy="direct",
            callable_name=callable_name,
            module=module,
            file_path=str(file_path.resolve().relative_to(root.resolve()).as_posix()),
            safe_identifier=_safe_identifier_from_path(file_path),
        )

    # We have a file under root but can't derive a safe dotted name —
    # likely a hyphenated directory or some other non-identifier char.
    # Suggest the closest safe prefix for the diagnostic, but generate
    # code that uses spec_from_file_location.
    closest = closest_safe_subpath(file_path, root)
    rel = file_path.resolve().relative_to(root.resolve())
    bad = next(
        (p for p in rel.parts if not is_valid_identifier(p.removesuffix(".py"))),
        rel.parts[-1] if rel.parts else "",
    )
    reason_bits = [
        f"path segment {bad!r} contains characters that aren't valid in "
        "Python identifiers (hyphens, dots, leading digits, etc.); "
        "dotted-import syntax can't reach it",
    ]
    if closest:
        reason_bits.append(
            f"closest safely-importable prefix is {closest!r} — consider "
            "renaming the offending directory if you want a direct import"
        )
    return GenerationTarget(
        strategy="dynamic_load",
        callable_name=callable_name,
        module=closest,  # documentation; not for code generation
        file_path=rel.as_posix(),
        safe_identifier=_safe_identifier_from_path(file_path),
        reason="; ".join(reason_bits),
    )


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
                            "Use `importlib.util.spec_from_file_location` "
                            "for paths that can't be expressed as a dotted "
                            "import, or rename the directory."
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
                                "identifier. Use dynamic loading or rename "
                                "the directory."
                            ),
                            statement=f"import {alias.name}",
                        )
                    )

    return diagnostics

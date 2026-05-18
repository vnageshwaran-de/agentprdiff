"""Simulate a regression by deterministically editing the agent code.

The "magical demo" step of the tour: we want the user to see the diff viewer
fire without having to switch to their editor. So Studio temporarily edits
*one* line in the project's agent file (typically a string literal the agent
returns or a prompt template), runs Check, and shows the diff. The edit is
revertible.

Strategy:

1. Find candidate files (``agent.py`` / ``agent/__init__.py`` at any depth).
2. In the first one, find a low-risk substitution target — preferably a
   string literal containing a real word. We replace one such word with
   ``CHANGED_BY_TOUR`` (a marker so the diff is obvious).
3. Save the *original* bytes to ``.studio-tour/<filename>.bak`` so the
   revert step can put it back even if Studio restarts.
4. Trigger a normal check run via the existing executor; the caller
   navigates the user to the live run page.
5. After the user sees the diff, ``revert_simulation`` restores the
   original bytes.

If no suitable target is found, we tell the user instead of failing
silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_MARKER = "CHANGED_BY_TOUR"
_BACKUP_DIR = ".studio-tour"


class SimulateError(RuntimeError):
    """Raised when we can't find anything safe to mutate."""


@dataclass(slots=True)
class SimulationPlan:
    file_path: str           # POSIX, relative to workspace
    backup_path: str         # POSIX, relative to workspace
    original_word: str
    replacement: str


def _candidate_files(workspace: Path) -> list[Path]:
    """Order: ``agent.py`` next to a suite, then any ``agent.py`` / ``agent/__init__.py``."""
    out: list[Path] = []
    direct = workspace / "agent.py"
    if direct.is_file():
        out.append(direct)
    init = workspace / "agent" / "__init__.py"
    if init.is_file():
        out.append(init)
    # Fall back: any agent.py at any depth, excluding venvs.
    for p in workspace.rglob("agent.py"):
        if ".studio-venv" in p.parts or ".venv" in p.parts or "node_modules" in p.parts:
            continue
        if p not in out:
            out.append(p)
    return out


# Match a quoted string literal that contains at least one >=5-letter word.
_STR_RE = re.compile(r"""(['"])(?P<body>[^'"\n]{8,80})\1""")


def _pick_word(literal: str) -> str | None:
    """Find a swap-able token inside the literal. Avoid placeholders and
    f-string braces."""
    for word in re.findall(r"[A-Za-z]{5,}", literal):
        # Don't touch placeholders or common code-paths.
        if word.lower() in {"input", "output", "format", "context", "prompt"}:
            continue
        return word
    return None


def make_plan(workspace: Path) -> SimulationPlan:
    """Pick a file + word to swap. Doesn't write anything yet."""
    for candidate in _candidate_files(workspace):
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _STR_RE.finditer(text):
            literal = m.group("body")
            word = _pick_word(literal)
            if word is None:
                continue
            rel = candidate.relative_to(workspace).as_posix()
            backup_rel = f"{_BACKUP_DIR}/{candidate.name}.bak"
            return SimulationPlan(
                file_path=rel,
                backup_path=backup_rel,
                original_word=word,
                replacement=_MARKER,
            )
    raise SimulateError(
        "Couldn't find a safe word to swap in the project's agent code. "
        "Try the demo manually: edit a string in your agent and click Check."
    )


def apply(workspace: Path, plan: SimulationPlan) -> None:
    target = workspace / plan.file_path
    original = target.read_bytes()
    backup = workspace / plan.backup_path
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(original)
    text = original.decode("utf-8", errors="replace")
    # Replace only the first occurrence so we don't trash an entire file.
    new_text = text.replace(plan.original_word, plan.replacement, 1)
    target.write_text(new_text, encoding="utf-8")


def revert(workspace: Path, plan: SimulationPlan) -> bool:
    """Restore the file from the on-disk backup. Returns True on success."""
    backup = workspace / plan.backup_path
    target = workspace / plan.file_path
    if not backup.exists():
        return False
    target.write_bytes(backup.read_bytes())
    try:
        backup.unlink()
    except OSError:
        pass
    return True

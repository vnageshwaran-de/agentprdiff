"""Markdown parsing for AGENTS.md + *_cases.md.

We deliberately stay regex-driven rather than pulling in a full markdown AST
parser. The shapes we care about are narrow and the parse needs to be tolerant
of small format drift (people will hand-edit these files).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CaseDossier:
    """One ``### case_name`` block parsed from a ``*_cases.md`` file."""

    name: str
    source_file: str  # POSIX path relative to the workspace
    what_it_tests: str = ""
    input_text: str = ""
    assertions: list[str] = field(default_factory=list)
    code_impacted: str = ""
    application_impact: str = ""


@dataclass(slots=True)
class ParsedAgentsMd:
    exists: bool
    workspace: str
    agents_md_path: str | None  # POSIX path relative to the workspace
    agents_md_content: str = ""
    # H2 section titles we found, for the UI's quick-jump TOC.
    sections: list[str] = field(default_factory=list)
    # Python / JSON code blocks, in document order.
    code_blocks: list[dict[str, str]] = field(default_factory=list)
    # Companion ``*_cases.md`` files (typically under ``suites/``).
    cases_files: list[str] = field(default_factory=list)
    # Cases extracted from all the dossier files combined.
    cases: list[CaseDossier] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "workspace": self.workspace,
            "agents_md_path": self.agents_md_path,
            "agents_md_content": self.agents_md_content,
            "sections": self.sections,
            "code_blocks": self.code_blocks,
            "cases_files": self.cases_files,
            "cases": [asdict(c) for c in self.cases],
        }


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def parse_workspace(workspace: Path) -> ParsedAgentsMd:
    """Walk ``workspace`` for AGENTS.md and any companion *_cases.md files."""
    agents_md = workspace / "AGENTS.md"
    out = ParsedAgentsMd(
        exists=agents_md.is_file(),
        workspace=str(workspace),
        agents_md_path="AGENTS.md" if agents_md.is_file() else None,
    )
    if out.exists:
        try:
            out.agents_md_content = agents_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            out.agents_md_content = ""
        out.sections = _h2_sections(out.agents_md_content)
        out.code_blocks = _code_blocks(out.agents_md_content)

    # Look for case-dossier files. Convention: ``suites/<name>_cases.md`` but
    # we accept any ``*_cases.md`` anywhere under the workspace.
    for path in _iter_case_files(workspace):
        rel = path.relative_to(workspace).as_posix()
        out.cases_files.append(rel)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.cases.extend(_parse_cases_file(text, source_file=rel))

    return out


# ---------------------------------------------------------------------------
# AGENTS.md helpers
# ---------------------------------------------------------------------------


_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _h2_sections(md: str) -> list[str]:
    """Return the H2 headings, in document order, for a TOC widget."""
    return _H2_RE.findall(md)


# Match fenced code blocks. Greedy match between the opening and closing
# fences; ``([a-zA-Z0-9_+-]*)`` captures the language tag (may be empty).
_CODE_RE = re.compile(
    r"^```([a-zA-Z0-9_+-]*)\s*\n(.*?)^```",
    re.MULTILINE | re.DOTALL,
)


def _code_blocks(md: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for match in _CODE_RE.finditer(md):
        lang, body = match.group(1) or "", match.group(2) or ""
        out.append({"lang": lang.strip(), "code": body.rstrip()})
    return out


# ---------------------------------------------------------------------------
# *_cases.md helpers
# ---------------------------------------------------------------------------

_EXCLUDE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".studio-venv"}


def _iter_case_files(workspace: Path) -> list[Path]:
    out: list[Path] = []
    for path in workspace.rglob("*_cases.md"):
        if any(part in _EXCLUDE_DIRS for part in path.relative_to(workspace).parts):
            continue
        out.append(path)
    return sorted(out)


# Match ``### case_name`` headers. The name may be in backticks (canonical) or
# plain. Capture everything inside the optional backticks.
_CASE_HEADER_RE = re.compile(
    r"^###\s+`?(?P<name>[^`\n]+?)`?\s*$",
    re.MULTILINE,
)


def _parse_cases_file(text: str, *, source_file: str) -> list[CaseDossier]:
    """Split the file at H3 headers and parse each block."""
    headers = list(_CASE_HEADER_RE.finditer(text))
    out: list[CaseDossier] = []
    for i, h in enumerate(headers):
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[start:end]
        name = h.group("name").strip()
        # Skip H3s that aren't real case names (worked-example callouts, etc.).
        if not name or name.lower().startswith("worked example"):
            continue
        out.append(_parse_case_block(name=name, block=block, source_file=source_file))
    return out


# A "field" inside a case block is a bold-marked label followed by a colon,
# e.g. ``**What it tests.** ...``. The body extends until the next bold-marked
# field or the end of the block.
_FIELD_RE = re.compile(
    r"^\*\*(?P<label>[^*]+?)\.?\*\*\s*",
    re.MULTILINE,
)


def _parse_case_block(*, name: str, block: str, source_file: str) -> CaseDossier:
    case = CaseDossier(name=name, source_file=source_file)

    # Walk fields in order.
    matches = list(_FIELD_RE.finditer(block))
    for i, m in enumerate(matches):
        label = _normalize_label(m.group("label"))
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(block)
        body = block[body_start:body_end].strip()

        if label == "what it tests":
            case.what_it_tests = _strip_paragraph(body)
        elif label == "input":
            case.input_text = _strip_paragraph(body)
        elif label == "assertions":
            case.assertions = _bulleted_lines(body)
        elif label == "code impacted":
            case.code_impacted = _strip_paragraph(body)
        elif label == "application impact":
            case.application_impact = _strip_paragraph(body)
        # 'how to exercise this case in isolation' carries commands; we
        # don't surface them in the suite skeleton, so it's ignored here.

    return case


def _normalize_label(label: str) -> str:
    return label.strip().rstrip(":").strip().lower()


def _strip_paragraph(body: str) -> str:
    # Join wrapped lines into a single paragraph; preserve obvious paragraph
    # breaks if there are any.
    paragraphs = re.split(r"\n\s*\n", body.strip())
    return "\n\n".join(re.sub(r"\s+", " ", p).strip() for p in paragraphs)


_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$", re.MULTILINE)


def _bulleted_lines(body: str) -> list[str]:
    """Pull each ``- ...`` line out, falling back to paragraph splits."""
    lines = [m.group(1).strip() for m in _BULLET_RE.finditer(body) if m.group(1).strip()]
    if lines:
        return lines
    # No bullets — split on commas / periods so we still have *some* structure.
    paragraph = _strip_paragraph(body)
    if not paragraph:
        return []
    return [s.strip() for s in re.split(r"[;.,]\s+", paragraph) if s.strip()]

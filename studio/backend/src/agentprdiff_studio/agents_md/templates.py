"""Starter templates: AGENTS.md + Python suite skeleton.

These deliberately keep the surface area small. The starter AGENTS.md is
**not** the 56K canonical playbook — it's a 30-line stub that points at the
real document and shows the case-dossier shape so an adopter can flesh it out
inline.
"""

from __future__ import annotations

from typing import Any

from .parser import CaseDossier


def starter_agents_md(project_name: str) -> str:
    """Return a minimal AGENTS.md to drop into a fresh workspace."""
    return (
        f"# Adopting agentprdiff in {project_name}\n\n"
        "This is a starter AGENTS.md. The canonical adoption playbook lives in the\n"
        "agentprdiff repo:\n"
        "https://github.com/vnageshwaran-de/agentprdiff/blob/main/AGENTS.md\n\n"
        "Replace this paragraph with one or two sentences about what your agent\n"
        "does. Then write a *case dossier* alongside your suite file — one block\n"
        "per `case(...)` entry, using the format below. Studio reads these\n"
        "dossiers and can scaffold an initial suite skeleton from them.\n\n"
        "## Cases\n\n"
        f"See `suites/{project_name}_cases.md` (create this file). Each case looks like:\n\n"
        "### `case_name`\n\n"
        "**What it tests.** One paragraph in plain English. A non-author should be\n"
        "able to read it in ten seconds and know what's protected.\n\n"
        "**Input.** The exact input passed to the agent.\n\n"
        "**Assertions.**\n"
        "- Each grader translated to plain English.\n"
        "- Include cost/latency budgets.\n\n"
        "**Code impacted.** Production file paths the case exercises.\n\n"
        "**Application impact.** One sentence about what breaks for end users.\n\n"
        "## Running the suite\n\n"
        "Once the suite is in place:\n\n"
        "```bash\n"
        f"agentprdiff record suites/{project_name}.py     # capture baselines\n"
        f"agentprdiff check  suites/{project_name}.py     # diff in CI\n"
        "```\n"
    )


def suite_python_skeleton(
    *,
    suite_name: str,
    agent_import_target: str,
    cases: list[CaseDossier],
) -> str:
    """Generate a Python suite stub from parsed case dossiers.

    The stub doesn't try to be clever — it pre-fills a ``contains(...)`` grader
    seeded from the case input (a starting point the adopter then tightens) plus
    a latency budget. The dossier's plain-English assertions are inlined as
    comments so the adopter can translate them into engine graders by hand.
    """
    header = (
        f'"""Auto-scaffolded suite for {suite_name!r}.\n\n'
        "Generated from the cases described in this project's *_cases.md file(s).\n"
        "Tighten the graders below before recording baselines — the seeded\n"
        "``contains(...)`` assertions are starting points only.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "from agentprdiff import case, suite\n"
        "from agentprdiff.graders import contains, latency_lt_ms\n\n"
        "# The agent under test. Update the import to match your project layout.\n"
        f"from {agent_import_target} import run as my_agent\n\n\n"
        f'{suite_name} = suite(\n'
        f'    name="{suite_name}",\n'
        "    agent=my_agent,\n"
        "    cases=[\n"
    )

    case_blocks: list[str] = []
    for c in cases:
        seed_term = _seed_term_from_input(c.input_text) or _seed_term_from_name(c.name)
        what = c.what_it_tests if c.what_it_tests else "(no description)"
        # Assemble the per-case block with explicit indentation so the
        # parsed-assertion comments line up cleanly. textwrap.dedent had
        # surprising behavior when an interpolated multi-line value sat
        # at a different column than the surrounding template — easier to
        # just hand-format.
        lines = [
            "        case(",
            f"            name={c.name!r},",
            f"            input={c.input_text!r},",
            "            expect=[",
            f"                contains({seed_term!r}),",
            "                latency_lt_ms(5_000),",
            "            ],",
            f"            # what it tests: {what}",
            "            # parsed assertions to tighten:",
        ]
        for a in (c.assertions or ["(no assertions parsed)"]):
            lines.append(f"            #   - {a}")
        lines.append("        ),")
        case_blocks.append("\n".join(lines) + "\n")

    footer = "    ],\n)\n"
    return header + "".join(case_blocks) + footer


def _seed_term_from_input(text: str) -> str:
    """Pick a representative token from the case input as a starter assertion."""
    text = (text or "").strip()
    for word in text.split():
        if len(word) >= 4 and word.isalpha():
            return word.lower()
    return ""


def _seed_term_from_name(name: str) -> str:
    # ``refund_happy_path`` → ``refund``
    head = name.split("_", 1)[0]
    return head if len(head) >= 3 else name


# ---------------------------------------------------------------------------
# HTTP-mode suite from cases
# ---------------------------------------------------------------------------


def http_suite_definition(
    *,
    suite_name: str,
    cases: list[CaseDossier],
) -> dict[str, Any]:
    """Turn parsed cases into a Studio-native HTTP suite definition.

    Same seeding rule as the Python skeleton: a ``contains`` grader on a
    representative input token, plus a latency budget. Adopters tighten.
    """
    out_cases: list[dict[str, Any]] = []
    for c in cases:
        seed = _seed_term_from_input(c.input_text) or _seed_term_from_name(c.name)
        out_cases.append(
            {
                "name": c.name,
                "input": c.input_text or c.name,
                "expect": [
                    {"type": "contains", "value": seed},
                    {"type": "latency_lt_ms", "value": 5000},
                ],
                "tags": [],
            }
        )
    return {"name": suite_name, "cases": out_cases}

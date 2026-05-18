"""AGENTS.md + case-dossier parsing and scaffold templates.

The agentprdiff project uses two conventional files at the top of a repo:

* ``AGENTS.md`` — prose playbook for adoption (read, not parsed).
* ``suites/<name>_cases.md`` — structured per-case dossier. *This* is the
  file the deterministic parser turns into a suite skeleton.

The case-dossier format (described in AGENTS.md → Step 5):

::

    ### `case_name`

    **What it tests.** One paragraph in plain English.
    **Input.** The exact input passed to the agent.
    **Assertions.**
    - Each grader in plain English.
    **Code impacted.** File paths and line numbers.
    **Application impact.** What breaks for end users.

We map this back to JSON-ish ``{name, input, assertions}`` triples and turn
those into either an HTTP-mode Suite definition or a starter Python file.
"""

from .parser import (
    CaseDossier,
    ParsedAgentsMd,
    parse_workspace,
)
from .templates import starter_agents_md, suite_python_skeleton

__all__ = [
    "CaseDossier",
    "ParsedAgentsMd",
    "parse_workspace",
    "starter_agents_md",
    "suite_python_skeleton",
]

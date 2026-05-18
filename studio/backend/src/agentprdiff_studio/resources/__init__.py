"""Bundled package resources.

* ``AGENTS.md`` — snapshot of the engine's canonical adoption playbook.
  Used by :mod:`agentprdiff_studio.llm` as ground truth when prompting an
  LLM to scaffold a suite. We snapshot at Studio release time rather than
  fetching at runtime so installs stay airgap-friendly.
"""

from importlib.resources import files
from pathlib import Path


def bundled_agents_md() -> str:
    """Return the bundled canonical AGENTS.md as a string."""
    # ``files()`` returns a Traversable; ``.read_text()`` handles wheel /
    # editable / unpacked-dir installs identically.
    return files(__package__).joinpath("AGENTS.md").read_text(encoding="utf-8")


def bundled_agents_md_path() -> Path:
    """Return the on-disk path to the bundled AGENTS.md.

    Only valid for unpacked / editable installs; raises if the package was
    installed as a zipped wheel. Callers that just need the contents should
    prefer :func:`bundled_agents_md`.
    """
    p = files(__package__).joinpath("AGENTS.md")
    return Path(str(p))

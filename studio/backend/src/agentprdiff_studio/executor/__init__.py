"""Subprocess-based suite executor.

A run is:

1. Provision (or reuse) a per-project venv under
   ``<workspace>/.studio-venv``.
2. Spawn the shim (``runner_shim.py``) inside that venv with
   ``<suite_path> <command> --run-id <id>``.
3. The shim loads the suite via the engine, runs it, and emits newline-
   delimited JSON events on stdout (one per case + a final summary). We parse
   those events here and persist them.

The shim is **copied** into the workspace at venv-provision time, not
installed as a package — it's a single self-contained file that needs only
``agentprdiff`` (engine) installed in the venv.
"""

from .dispatch import execute_run
from .venv import ensure_venv

__all__ = ["execute_run", "ensure_venv"]

"""agentprdiff Studio — web UI + server on top of the agentprdiff engine.

The engine package (``agentprdiff``) is unchanged and remains the canonical CLI
surface. Studio adds:

* A FastAPI server that can clone a git repo (or accept a zip / HTTP endpoint),
  discover suites, run them via the engine in an isolated subprocess + venv,
  and persist run history in a SQL database.
* A React UI (see ``studio/frontend``) for non-dev users to trigger runs,
  review diffs, and approve new baselines.

This is the backend package. M1 ships the API surface and a git-driven
executor; later milestones add zip/HTTP intake, secrets, SSE streaming, and
the UI.
"""

__version__ = "0.1.0"

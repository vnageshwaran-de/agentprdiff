"""Project intake.

Three modes:

* :mod:`.git` — clone a remote repo URL into the project workspace.
* :mod:`.zip` — extract an uploaded archive (M2).
* :mod:`.http` — store an endpoint config; runs hit it instead of executing
  Python (M2).

After intake completes, :mod:`.discovery` walks the workspace to find files
that define agentprdiff suites.
"""

from . import discovery, git, http, zip

__all__ = ["git", "zip", "http", "discovery"]

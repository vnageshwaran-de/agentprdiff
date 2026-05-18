"""Per-project virtualenv provisioning.

Each project gets a venv at ``<workspace>/.studio-venv``. We install:

1. The agentprdiff engine (resolved by pip from PyPI by default; can be
   overridden via the ``STUDIO_ENGINE_REQ`` env to install from a local path
   during dev — see ``ensure_venv``).
2. The project's own requirements, if a ``requirements.txt`` or
   ``pyproject.toml`` is present at the workspace root.

Caching (M3): a fingerprint of the inputs to pip — the engine req, the
project ``requirements.txt``, and the project ``pyproject.toml`` — is written
to ``.studio-venv/.deps-fingerprint`` after a successful install. On a later
``ensure_venv`` call with the same fingerprint we skip pip entirely and reuse
the venv. The next ``record`` or ``check`` becomes a single subprocess spawn
instead of a multi-second pip install.

Fingerprint mismatch (e.g. ``requirements.txt`` edited, engine bumped) →
re-run pip and rewrite the fingerprint. The marker is intentionally inside
the venv directory so deleting the venv resets caching too.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
import venv as stdlib_venv
from pathlib import Path

VENV_DIRNAME = ".studio-venv"
MARKER_FILENAME = ".provisioned"
FINGERPRINT_FILENAME = ".deps-fingerprint"


def venv_path(workspace: Path) -> Path:
    return workspace / VENV_DIRNAME


def venv_python(workspace: Path) -> Path:
    vp = venv_path(workspace)
    # Windows ships python at Scripts/, POSIX at bin/. We're Linux-first
    # (Docker) but handle both for local dev on macOS / Windows-WSL.
    candidate = vp / ("Scripts" if os.name == "nt" else "bin") / "python"
    return candidate


def is_provisioned(workspace: Path) -> bool:
    return (venv_path(workspace) / MARKER_FILENAME).exists()


def _engine_req() -> str:
    return os.environ.get("STUDIO_ENGINE_REQ") or "agentprdiff>=0.2.5"


def _read_or_empty(p: Path) -> bytes:
    try:
        return p.read_bytes()
    except OSError:
        return b""


def compute_fingerprint(workspace: Path) -> str:
    """Hash the inputs to pip into a stable digest.

    Captures three sources, in order:

    * The engine requirement string (``STUDIO_ENGINE_REQ`` or the default).
    * ``requirements.txt`` contents (bytes; whitespace-sensitive).
    * ``pyproject.toml`` contents.

    A missing file contributes an empty string. We rebuild whenever any
    source changes, including (intentionally) whitespace tweaks in the
    requirements files — pip-resolution is sensitive enough that this is
    safer than trying to parse.
    """
    h = hashlib.sha256()
    h.update(b"engine:")
    h.update(_engine_req().encode("utf-8"))
    h.update(b"\nrequirements.txt:")
    h.update(_read_or_empty(workspace / "requirements.txt"))
    h.update(b"\npyproject.toml:")
    h.update(_read_or_empty(workspace / "pyproject.toml"))
    return h.hexdigest()


def _fingerprint_path(workspace: Path) -> Path:
    return venv_path(workspace) / FINGERPRINT_FILENAME


def fingerprint_matches(workspace: Path) -> bool:
    fp_file = _fingerprint_path(workspace)
    if not fp_file.exists():
        return False
    try:
        stored = fp_file.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return stored == compute_fingerprint(workspace)


async def ensure_venv(workspace: Path) -> Path:
    """Make sure a usable venv exists in the workspace. Returns the python path.

    Fast path: provisioned marker present + python exists + fingerprint matches
    → return immediately, no pip work.
    """
    py = venv_python(workspace)
    if is_provisioned(workspace) and py.exists() and fingerprint_matches(workspace):
        return py

    def _work() -> Path:
        vp = venv_path(workspace)
        if not py.exists():
            stdlib_venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False).create(vp)

        # Install the engine. STUDIO_ENGINE_REQ wins (lets dev point at the
        # local checkout via ``-e /path/to/agentprdiff``). Otherwise fall back
        # to the published package.
        _pip_install(py, _split_req(_engine_req()))

        # Install project deps, if any. We intentionally don't fail the run
        # provisioning on a missing dep file — many small repos don't have one.
        req_txt = workspace / "requirements.txt"
        if req_txt.exists():
            _pip_install(py, ["-r", str(req_txt)])

        pyproject = workspace / "pyproject.toml"
        if pyproject.exists() and not req_txt.exists():
            # Best-effort: ``pip install .`` only if the project looks installable.
            # We swallow errors here — many "suite-only" repos won't be installable.
            try:
                _pip_install(py, ["."], cwd=workspace)
            except subprocess.CalledProcessError:
                pass

        (vp / MARKER_FILENAME).write_text("ok\n", encoding="utf-8")
        _fingerprint_path(workspace).write_text(
            compute_fingerprint(workspace), encoding="utf-8"
        )
        return py

    return await asyncio.to_thread(_work)


def _split_req(req: str) -> list[str]:
    """Allow a multi-token override like ``-e /path/to/agentprdiff``."""
    return req.split()


def _pip_install(python: Path, args: list[str], *, cwd: Path | None = None) -> None:
    cmd = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--quiet", *args]
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True, capture_output=True, text=True)


def system_python() -> Path:
    """Return the python interpreter Studio itself is running under."""
    return Path(sys.executable)

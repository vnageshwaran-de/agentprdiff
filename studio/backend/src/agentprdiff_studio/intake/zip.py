"""Zip upload intake.

Accept a user-uploaded ``.zip`` and extract it into
``<data_dir>/projects/<id>/upload``. After extraction, the same discovery
pipeline used for git intake walks the workspace and surfaces suites.

Security: zips are hostile by default.

* **Zip-slip** — entries with ``..`` or absolute paths are rejected outright.
* **Symlinks** — silently skipped (we don't follow or create them).
* **Size cap** — sum of uncompressed sizes must stay under
  :func:`max_uncompressed_bytes` (default 256 MiB). Refused before we write
  anything to disk.

UX nicety: many users zip ``my-project/`` from the parent dir, producing an
archive with a single top-level ``my-project/`` directory inside. We detect
that case and "strip the wrapper" so the workspace path lands directly on the
project root.
"""

from __future__ import annotations

import asyncio
import shutil
import zipfile
from pathlib import Path, PurePosixPath

# 256 MiB. Generous for source repos, small enough to refuse runaway archives.
DEFAULT_MAX_UNCOMPRESSED = 256 * 1024 * 1024


class ZipIntakeError(RuntimeError):
    """Raised when a zip refuses to extract safely."""


def workspace_for(projects_dir: Path, project_id: int) -> Path:
    return projects_dir / str(project_id) / "upload"


def max_uncompressed_bytes() -> int:
    return DEFAULT_MAX_UNCOMPRESSED


async def extract(
    *,
    projects_dir: Path,
    project_id: int,
    archive_path: Path,
) -> Path:
    """Extract ``archive_path`` into the project workspace.

    The blocking zipfile work runs in a thread so the asyncio loop stays
    responsive while a large archive expands.
    """
    target = workspace_for(projects_dir, project_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    def _work() -> Path:
        # Fresh extraction every time — callers responsible for any
        # "preserve old workspace" semantics.
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(archive_path) as zf:
                _validate(zf)
                # Compute the "single top-level dir?" wrapper strip.
                strip_prefix = _detect_wrapper(zf)
                for member in zf.infolist():
                    _extract_one(zf, member, target, strip_prefix)
        except zipfile.BadZipFile as exc:
            raise ZipIntakeError(f"not a valid zip archive: {exc}") from exc

        return target

    return await asyncio.to_thread(_work)


# ---------------------------------------------------------------------------


def _validate(zf: zipfile.ZipFile) -> None:
    total = 0
    cap = max_uncompressed_bytes()
    for member in zf.infolist():
        # Absolute paths or parent traversal anywhere in the name = refuse.
        name = member.filename
        # zipfile normalizes to forward slashes already, but be defensive.
        p = PurePosixPath(name.replace("\\", "/"))
        if p.is_absolute() or any(part == ".." for part in p.parts):
            raise ZipIntakeError(f"refusing unsafe entry: {name!r}")
        total += member.file_size
        if total > cap:
            raise ZipIntakeError(
                f"uncompressed size exceeds {cap // (1024 * 1024)} MiB limit"
            )


def _detect_wrapper(zf: zipfile.ZipFile) -> str:
    """If every entry shares the same top-level dir, return it (with trailing /).

    Otherwise return "" so :func:`_extract_one` doesn't strip anything.
    """
    tops: set[str] = set()
    for member in zf.infolist():
        name = member.filename.replace("\\", "/")
        if not name or name == "/":
            continue
        top, sep, _ = name.partition("/")
        # File at the root with no '/' → no shared wrapper.
        if not sep:
            return ""
        tops.add(top)
        if len(tops) > 1:
            return ""
    if len(tops) == 1:
        return next(iter(tops)) + "/"
    return ""


def _extract_one(
    zf: zipfile.ZipFile, member: zipfile.ZipInfo, target: Path, strip_prefix: str
) -> None:
    name = member.filename.replace("\\", "/")
    if strip_prefix and name.startswith(strip_prefix):
        name = name[len(strip_prefix) :]
    if not name:
        return  # the wrapper dir itself

    # Symlinks: zipfile encodes them via external_attr; skip rather than
    # follow. (We don't write them either — they can point anywhere.)
    if (member.external_attr >> 16) & 0o120000 == 0o120000:
        return

    out_path = (target / name).resolve()
    # Final defense: the resolved path must stay under target.
    if target.resolve() not in out_path.parents and out_path != target.resolve():
        raise ZipIntakeError(f"refusing entry that escapes workspace: {name!r}")

    if member.is_dir():
        out_path.mkdir(parents=True, exist_ok=True)
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member) as src, open(out_path, "wb") as dst:
        shutil.copyfileobj(src, dst)

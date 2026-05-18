"""Git-based project intake.

Clone a remote repo (or a local ``file://``-style path) into
``<data_dir>/projects/<id>/repo``. On subsequent syncs the existing checkout is
fast-forwarded **without losing local files** — Studio writes generated
suites, scaffold templates, and (M9) workflow YAML into the workspace, and
those have to survive Sync clicks.

The sync algorithm:

1. ``git fetch --prune``.
2. If the working tree is dirty (any modified or untracked files),
   ``git stash push --include-untracked``.
3. ``git merge --ff-only`` against the remote tracking branch.
4. ``git stash pop`` if we stashed.
5. If pop conflicts, leave the stash in place and surface a warning —
   the user inspects the conflict in their editor.

Private-repo auth (GitHub tokens, SSH keys) is **not** wired in M1; M3 will
read a stored Secret and inject it into the clone URL or env. For local
testing today, point Studio at a public repo or a path on disk.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from git import GitCommandError, Repo  # GitPython

log = logging.getLogger(__name__)


class GitIntakeError(RuntimeError):
    """Raised when a clone / pull fails."""


def workspace_for(projects_dir: Path, project_id: int) -> Path:
    return projects_dir / str(project_id) / "repo"


async def clone_or_pull(
    *,
    projects_dir: Path,
    project_id: int,
    source: str,
    git_ref: str | None = None,
) -> Path:
    """Clone the repo if absent, fast-forward-pull if present.

    Returns the checkout path. Non-destructive on update — any locally
    modified or untracked files are stashed across the pull and restored.
    """
    target = workspace_for(projects_dir, project_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    def _work() -> Path:
        if target.exists() and (target / ".git").exists():
            try:
                repo = Repo(target)
                _sync_in_place(repo, git_ref=git_ref)
                return target
            except GitCommandError as exc:
                raise GitIntakeError(f"git pull failed: {exc}") from exc

        # Fresh clone.
        if target.exists():
            shutil.rmtree(target)
        try:
            Repo.clone_from(source, target, branch=git_ref) if git_ref else Repo.clone_from(
                source, target
            )
        except GitCommandError as exc:
            raise GitIntakeError(f"git clone failed: {exc}") from exc
        return target

    return await asyncio.to_thread(_work)


def _sync_in_place(repo: Repo, *, git_ref: str | None) -> None:
    """Fast-forward the existing checkout, preserving local changes."""
    repo.remotes.origin.fetch(prune=True)

    if git_ref:
        # Checkout the requested branch (may already be on it). ``-B`` makes
        # it a no-op when already there.
        try:
            repo.git.checkout(git_ref)
        except GitCommandError as exc:
            raise GitIntakeError(f"checkout {git_ref!r} failed: {exc}") from exc

    # Decide what to merge against.
    target_ref = f"origin/{git_ref}" if git_ref else "@{u}"

    # Stash any local changes (including untracked) so the merge isn't
    # blocked. Skip if the tree is clean to avoid an empty stash entry.
    dirty = repo.is_dirty(untracked_files=True)
    stashed = False
    if dirty:
        try:
            repo.git.stash("push", "--include-untracked", "-m", "studio-sync-autostash")
            stashed = True
        except GitCommandError as exc:
            log.warning("studio-sync: pre-pull stash failed: %s", exc)

    # Fast-forward only. If divergent, refuse — the user has local commits
    # we don't own; better to surface than overwrite.
    try:
        repo.git.merge("--ff-only", target_ref)
    except GitCommandError as exc:
        # Restore the stash if we made one before bailing.
        if stashed:
            try:
                repo.git.stash("pop")
            except GitCommandError:
                log.warning("studio-sync: stash pop after failed merge also failed")
        raise GitIntakeError(
            f"fast-forward to {target_ref!r} failed: {exc}. "
            "Your local checkout has commits the remote doesn't — Studio refuses "
            "to overwrite them."
        ) from exc

    # Reapply stashed work.
    if stashed:
        try:
            repo.git.stash("pop")
        except GitCommandError as exc:
            log.warning(
                "studio-sync: stash pop conflicted (%s) — local changes left in "
                "the stash; inspect with `git stash show -p` inside the workspace.",
                exc,
            )

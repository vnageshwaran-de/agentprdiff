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

Private-repo auth: see ``intake/git_auth.py`` for the SSH (mounted
``~/.ssh``) and HTTPS-PAT paths. Auth context is computed by the API
caller from stored Secrets and threaded in via ``auth=``; this module
just hands the resulting env dict to git and redacts the token from
any error string it surfaces.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from git import GitCommandError, Repo  # GitPython

from .git_auth import (
    GitAuth,
    build_clone_env,
    explain_clone_failure,
    looks_like_embedded_credential,
    redact_with_auth,
)

log = logging.getLogger(__name__)


class GitIntakeError(RuntimeError):
    """Raised when a clone / pull fails. Message is always redacted."""


def workspace_for(projects_dir: Path, project_id: int) -> Path:
    return projects_dir / str(project_id) / "repo"


async def clone_or_pull(
    *,
    projects_dir: Path,
    project_id: int,
    source: str,
    git_ref: str | None = None,
    auth: GitAuth | None = None,
) -> Path:
    """Clone the repo if absent, fast-forward-pull if present.

    Returns the checkout path. Non-destructive on update — any locally
    modified or untracked files are stashed across the pull and restored.

    ``auth`` carries an optional HTTPS bearer token; the caller resolves
    it from Studio Secrets per the convention in
    :mod:`agentprdiff_studio.intake.git_auth`. SSH auth is handled by the
    container's ssh binary + mounted ``~/.ssh`` — no Python wiring
    required.
    """
    # Refuse URLs with embedded credentials — those would persist in DB
    # rows and logs. The structured Secrets store is the right home.
    if looks_like_embedded_credential(source):
        raise GitIntakeError(
            "The git URL contains an embedded credential (user:password@). "
            "Studio refuses to store credentials in URLs because they'd "
            "leak into the project row, logs, and the workspace's git "
            "config. Strip the credential from the URL and save it as a "
            "Studio Secret (GITHUB_TOKEN for GitHub, GIT_HTTPS_TOKEN for "
            "generic HTTPS) instead."
        )

    target = workspace_for(projects_dir, project_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    # The env we hand to git for THIS operation only. Includes
    # GIT_TERMINAL_PROMPT=0 (always) plus the http.extraheader injection
    # when an HTTPS token is in play. We start from the current process
    # env so PATH (for ssh / git) and HOME (for ~/.ssh, ~/.gitconfig)
    # still resolve normally.
    git_env = build_clone_env(os.environ, auth)

    def _work() -> Path:
        if target.exists() and (target / ".git").exists():
            try:
                repo = Repo(target)
                _sync_in_place(repo, git_ref=git_ref, git_env=git_env, auth=auth)
                return target
            except GitCommandError as exc:
                raise GitIntakeError(
                    explain_clone_failure(
                        redact_with_auth(str(exc), auth),
                        source=source,
                        had_auth=auth is not None,
                    )
                ) from None

        # Fresh clone.
        if target.exists():
            shutil.rmtree(target)
        try:
            kwargs: dict[str, object] = {"env": git_env}
            if git_ref:
                kwargs["branch"] = git_ref
            Repo.clone_from(source, target, **kwargs)
        except GitCommandError as exc:
            # Map common failure modes to actionable hints, redact any
            # token-shaped substring, and discard the original exc chain
            # so the redacted message is what shows up everywhere.
            raise GitIntakeError(
                explain_clone_failure(
                    redact_with_auth(str(exc), auth),
                    source=source,
                    had_auth=auth is not None,
                )
            ) from None
        return target

    return await asyncio.to_thread(_work)


def _sync_in_place(
    repo: Repo,
    *,
    git_ref: str | None,
    git_env: dict[str, str],
    auth: GitAuth | None = None,
) -> None:
    """Fast-forward the existing checkout, preserving local changes.

    ``git_env`` is applied to every git invocation so transient HTTPS
    auth + GIT_TERMINAL_PROMPT=0 carry through fetch/merge/stash.
    """
    with repo.git.custom_environment(**git_env):
        repo.remotes.origin.fetch(prune=True)

        if git_ref:
            try:
                repo.git.checkout(git_ref)
            except GitCommandError as exc:
                raise GitIntakeError(
                    f"checkout {git_ref!r} failed: "
                    f"{redact_with_auth(str(exc), auth)}"
                ) from None

        target_ref = f"origin/{git_ref}" if git_ref else "@{u}"

        dirty = repo.is_dirty(untracked_files=True)
        stashed = False
        if dirty:
            try:
                repo.git.stash("push", "--include-untracked", "-m", "studio-sync-autostash")
                stashed = True
            except GitCommandError as exc:
                log.warning(
                    "studio-sync: pre-pull stash failed: %s",
                    redact_with_auth(str(exc), auth),
                )

        try:
            repo.git.merge("--ff-only", target_ref)
        except GitCommandError as exc:
            if stashed:
                try:
                    repo.git.stash("pop")
                except GitCommandError:
                    log.warning("studio-sync: stash pop after failed merge also failed")
            raise GitIntakeError(
                f"fast-forward to {target_ref!r} failed: "
                f"{redact_with_auth(str(exc), auth)}. "
                "Your local checkout has commits the remote doesn't — Studio "
                "refuses to overwrite them."
            ) from None

        if stashed:
            try:
                repo.git.stash("pop")
            except GitCommandError as exc:
                log.warning(
                    "studio-sync: stash pop conflicted (%s) — local changes "
                    "left in the stash; inspect with `git stash show -p`.",
                    redact_with_auth(str(exc), auth),
                )

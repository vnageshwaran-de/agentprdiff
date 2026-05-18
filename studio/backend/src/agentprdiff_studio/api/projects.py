"""Projects + suites endpoints.

* ``POST /api/projects``                  — create via git clone (JSON).
* ``POST /api/projects/upload``           — create via zip upload (multipart).
* ``POST /api/projects/{id}/upload``      — replace a zip project's workspace.
* ``POST /api/projects/{id}/sync``        — re-pull (git) or re-discover (zip).
* ``GET  /api/projects``                  — list.
* ``GET  /api/projects/{id}``             — detail.
* ``GET  /api/projects/{id}/suites``      — list discovered suites.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session
from ..intake.discovery import discover_suites
from ..intake.git import GitIntakeError, clone_or_pull
from ..intake.git_auth import (
    FALLBACK_TOKEN_NAME,
    preferred_secret_names,
    resolve_auth,
)
from ..intake.http import HttpIntakeError, normalize_http_config, normalize_suite_definition
from ..intake.zip import ZipIntakeError
from ..intake.zip import extract as zip_extract
from ..secrets import load_named_secrets
from ..settings import get_settings
from .schemas import (
    HttpSuiteCreate,
    HttpSuiteUpdate,
    ProjectCreate,
    ProjectOut,
    RunOut,
    SuiteOut,
    SyncResult,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


# ---------------------------------------------------------------------- create


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    session: AsyncSession = Depends(get_session),
) -> ProjectOut:
    settings = get_settings()

    # Reject duplicate names early — DB unique constraint would do it too, but
    # we want a clean 409.
    existing = await session.execute(select(models.Project).where(models.Project.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"project '{payload.name}' already exists")

    if payload.intake_mode == "http":
        # normalize_http_config raises HttpIntakeError on malformed input;
        # the global handler in api/errors.py converts it to a clean
        # {detail, hint} response. No manual wrap needed because we haven't
        # touched the session yet.
        http_config = normalize_http_config(payload.http_config or {})

        project = models.Project(
            name=payload.name,
            intake_mode="http",
            source=payload.source,
            http_config=http_config,
            last_synced_at=datetime.now(timezone.utc),
        )
        session.add(project)
        await session.flush()
        return _to_project_out(project)

    # intake_mode == "git"
    project = models.Project(
        name=payload.name,
        intake_mode=payload.intake_mode,
        source=payload.source,
        git_ref=payload.git_ref,
    )
    session.add(project)
    await session.flush()  # gives us project.id without committing

    # Resolve a private-repo token before cloning. For SSH URLs / public
    # HTTPS this returns None and the clone proceeds with no auth header.
    auth = await _resolve_git_auth(
        session, project_id=project.id, source=payload.source
    )

    try:
        workspace = await clone_or_pull(
            projects_dir=settings.projects_dir,
            project_id=project.id,
            source=payload.source,
            git_ref=payload.git_ref,
            auth=auth,
        )
    except GitIntakeError as exc:
        # Roll back the half-created project so the user can retry the call.
        # The exception message is already redacted by clone_or_pull and
        # explain_clone_failure has suggested the SSH-vs-HTTPS-token path
        # when applicable.
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    project.workspace_path = str(workspace)
    project.last_synced_at = datetime.now(timezone.utc)
    await session.flush()

    # Initial suite discovery so the user sees something on GET /suites.
    await _rediscover(session, project, workspace)

    return _to_project_out(project)


# ---------------------------------------------------------------------- upload


@router.post("/upload", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def upload_project(
    name: str = Form(..., min_length=1, max_length=200),
    file: UploadFile = File(..., description="A .zip of the project to test"),
    session: AsyncSession = Depends(get_session),
) -> ProjectOut:
    """Create a project by uploading a zip of the source tree.

    The original filename is stored as ``source`` for display. To replace
    the workspace later, ``POST /api/projects/{id}/upload`` with a new file.
    """
    settings = get_settings()

    # Reject duplicate names early — same as the git path.
    existing = await session.execute(
        select(models.Project).where(models.Project.name == name)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"project '{name}' already exists")

    project = models.Project(
        name=name,
        intake_mode="zip",
        source=file.filename or "uploaded.zip",
    )
    session.add(project)
    await session.flush()

    try:
        workspace = await _save_and_extract(file, settings.projects_dir, project.id)
    except ZipIntakeError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    project.workspace_path = str(workspace)
    project.last_synced_at = datetime.now(timezone.utc)
    await session.flush()

    await _rediscover(session, project, workspace)
    return _to_project_out(project)


@router.post("/{project_id}/upload", response_model=ProjectOut)
async def replace_upload(
    project_id: int,
    file: UploadFile = File(..., description="Replacement zip"),
    session: AsyncSession = Depends(get_session),
) -> ProjectOut:
    """Replace a zip project's workspace with a freshly uploaded archive."""
    settings = get_settings()
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if project.intake_mode != "zip":
        raise HTTPException(
            status_code=400,
            detail=f"replace_upload only valid for zip projects (was: {project.intake_mode})",
        )

    try:
        workspace = await _save_and_extract(file, settings.projects_dir, project.id)
    except ZipIntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    project.source = file.filename or project.source
    project.workspace_path = str(workspace)
    project.last_synced_at = datetime.now(timezone.utc)
    await _rediscover(session, project, workspace)
    return _to_project_out(project)


# ------------------------------------------------------------------------ list


@router.get("", response_model=list[ProjectOut])
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[ProjectOut]:
    rows = (
        await session.execute(select(models.Project).order_by(models.Project.created_at.desc()))
    ).scalars().all()
    return [_to_project_out(p) for p in rows]


# ----------------------------------------------------------------------- detail


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> ProjectOut:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return _to_project_out(project)


# ------------------------------------------------------------------------- sync


@router.post("/{project_id}/sync", response_model=SyncResult)
async def sync_project(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> SyncResult:
    settings = get_settings()
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    if project.intake_mode == "git":
        auth = await _resolve_git_auth(
            session, project_id=project.id, source=project.source
        )
        try:
            workspace = await clone_or_pull(
                projects_dir=settings.projects_dir,
                project_id=project.id,
                source=project.source,
                git_ref=project.git_ref,
                auth=auth,
            )
        except GitIntakeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        project.workspace_path = str(workspace)
        project.last_synced_at = datetime.now(timezone.utc)
        suites = await _rediscover(session, project, workspace)
        return SyncResult(
            project_id=project.id,
            suites_found=len(suites),
            suites=[_to_suite_out(s) for s in suites],
        )

    if project.intake_mode == "zip":
        # Zip projects have no remote to re-pull — sync just re-runs discovery
        # against the existing workspace. To replace the source tree, use
        # POST /api/projects/{id}/upload.
        if not project.workspace_path:
            raise HTTPException(
                status_code=400, detail="zip project has no workspace; re-upload required"
            )
        workspace = Path(project.workspace_path)
        if not workspace.exists():
            raise HTTPException(
                status_code=400,
                detail="zip workspace missing on disk; re-upload required",
            )
        project.last_synced_at = datetime.now(timezone.utc)
        suites = await _rediscover(session, project, workspace)
        return SyncResult(
            project_id=project.id,
            suites_found=len(suites),
            suites=[_to_suite_out(s) for s in suites],
        )

    if project.intake_mode == "http":
        # HTTP projects don't have a workspace to walk — sync is a no-op that
        # just returns the current authored suites.
        rows = (
            await session.execute(
                select(models.Suite)
                .where(models.Suite.project_id == project_id)
                .order_by(models.Suite.name)
            )
        ).scalars().all()
        project.last_synced_at = datetime.now(timezone.utc)
        return SyncResult(
            project_id=project.id,
            suites_found=len(rows),
            suites=[_to_suite_out(s) for s in rows],
        )

    raise HTTPException(
        status_code=400,
        detail=f"sync not implemented for intake_mode={project.intake_mode!r} yet",
    )


# --------------------------------------------------------------- http suites


@router.post(
    "/{project_id}/suites", response_model=SuiteOut, status_code=status.HTTP_201_CREATED
)
async def create_http_suite(
    project_id: int,
    payload: HttpSuiteCreate,
    session: AsyncSession = Depends(get_session),
) -> SuiteOut:
    """Create a Studio-native suite for an HTTP-mode project.

    Git/zip projects discover suites from disk — they can't be authored here.
    """
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if project.intake_mode != "http":
        raise HTTPException(
            status_code=400,
            detail="suite authoring is only supported for http-mode projects",
        )

    # normalize_suite_definition raises HttpIntakeError on malformed input;
    # global handler turns that into a {detail, hint} response.
    definition = normalize_suite_definition(payload.model_dump())

    suite = models.Suite(
        project_id=project_id,
        name=definition["name"],
        file_path="http://",  # synthetic — there's no file
        case_count=len(definition["cases"]),
        definition_json=definition,
    )
    session.add(suite)
    await session.flush()
    return _to_suite_out(suite)


@router.put("/{project_id}/suites/{suite_id}", response_model=SuiteOut)
async def update_http_suite(
    project_id: int,
    suite_id: int,
    payload: HttpSuiteUpdate,
    session: AsyncSession = Depends(get_session),
) -> SuiteOut:
    project = await session.get(models.Project, project_id)
    if project is None or project.intake_mode != "http":
        raise HTTPException(status_code=404, detail="http project not found")
    suite = await session.get(models.Suite, suite_id)
    if suite is None or suite.project_id != project_id:
        raise HTTPException(status_code=404, detail="suite not found")

    # Merge: if a field was omitted in the PUT body, keep the existing value.
    raw = {
        "name": payload.name or suite.name,
        "cases": payload.cases if payload.cases is not None else (suite.definition_json or {}).get("cases", []),
    }
    definition = normalize_suite_definition(raw)

    suite.name = definition["name"]
    suite.case_count = len(definition["cases"])
    suite.definition_json = definition
    return _to_suite_out(suite)


@router.delete("/{project_id}/suites/{suite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_suite(
    project_id: int,
    suite_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a suite + all its runs / case_runs / events (FK cascade).

    For git/zip projects, also remove the file from disk so the next sync
    doesn't re-discover it. HTTP-mode suites have no on-disk presence;
    DB-row removal is enough.
    """
    suite = await session.get(models.Suite, suite_id)
    if suite is None or suite.project_id != project_id:
        raise HTTPException(status_code=404, detail="suite not found")
    project = await session.get(models.Project, project_id)

    # Remove the on-disk file for git/zip projects so re-sync doesn't bring
    # it back. Best-effort: a missing file is fine (someone may have already
    # deleted it via the file system); a permission error gets surfaced.
    if (
        project is not None
        and project.intake_mode in ("git", "zip")
        and project.workspace_path
        and suite.file_path
        and suite.file_path != "http://"
    ):
        try:
            target = Path(project.workspace_path) / suite.file_path
            if target.is_file():
                target.unlink()
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"removed DB row but couldn't delete {suite.file_path}: {exc}",
            ) from exc

        # Also clean up the companion *_cases.md dossier so the ProjectGuide
        # parser doesn't keep surfacing cases for a suite that's gone. The
        # generate flow saves dossiers next to the suite as
        # ``suites/<name>_cases.md`` per the AGENTS.md convention; check
        # both that canonical path and the same-directory-as-the-suite
        # fallback. Missing dossier is fine.
        workspace = Path(project.workspace_path)
        suite_path = workspace / suite.file_path
        dossier_candidates = [
            suite_path.with_name(f"{suite.name}_cases.md"),
            suite_path.parent / f"{suite_path.stem}_cases.md",
            workspace / "suites" / f"{suite.name}_cases.md",
        ]
        seen: set[Path] = set()
        for d in dossier_candidates:
            try:
                resolved = d.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                if resolved.is_file():
                    resolved.unlink()
            except OSError:
                # Non-fatal: the dossier file is informational. Surface the
                # primary delete success even if the cleanup couldn't reach
                # the markdown.
                pass

    await session.delete(suite)


# ---------------------------------------------------------------------- suites


# ----------------------------------------------------------------- diagnostics


@router.post("/{project_id}/requirements", status_code=status.HTTP_200_OK)
async def add_requirement(
    project_id: int,
    payload: dict,  # {"package": "openai", optional "version": ">=1.0"}
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Append a package to the project workspace's ``requirements.txt``.

    Idempotent: if the package is already declared we no-op. Creates the
    file when missing. After this call the next ``Sync`` (or implicit
    re-provision on the next run) rebuilds the venv with the new dep.

    Only valid for git/zip projects — HTTP projects have no workspace.
    """
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if project.intake_mode not in ("git", "zip") or not project.workspace_path:
        raise HTTPException(
            status_code=400,
            detail="add-requirement only applies to git/zip projects",
        )

    package = (payload.get("package") or "").strip()
    version = (payload.get("version") or "").strip()
    if not package:
        raise HTTPException(status_code=400, detail="missing 'package'")
    # Cheap guard: disallow shell-meaningful chars so a misuse can't smuggle
    # in things like "openai && rm -rf". Real PyPI packages match this set.
    if not re.match(r"^[A-Za-z0-9_.\-]+$", package):
        raise HTTPException(status_code=400, detail=f"invalid package name: {package!r}")
    line = f"{package}{version}".strip()

    workspace = Path(project.workspace_path).resolve()
    req_path = workspace / "requirements.txt"
    existing = req_path.read_text() if req_path.is_file() else ""
    lines = [ln.strip() for ln in existing.splitlines() if ln.strip()]
    # Already declared? Match by package name (strip extras / version).
    def _name(s: str) -> str:
        s = re.split(r"[<>=! \[]", s, maxsplit=1)[0]
        return s.strip().lower()
    if any(_name(ln) == package.lower() for ln in lines):
        return {
            "added": False,
            "already_present": True,
            "package": package,
            "path": "requirements.txt",
        }
    lines.append(line)
    req_path.write_text("\n".join(lines) + "\n")
    return {
        "added": True,
        "already_present": False,
        "package": package,
        "path": "requirements.txt",
        "wrote_bytes": req_path.stat().st_size,
    }


@router.delete("/{project_id}/workspace-files", status_code=status.HTTP_200_OK)
async def delete_workspace_file(
    project_id: int,
    path: str = Query(..., description="Path relative to the project workspace"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete a single file from the project workspace.

    Used by the Diagnose panel to clean up broken suite candidates that
    discovery rejected (so they don't have suite rows the regular delete
    can reach). Path is validated to live strictly inside the workspace.
    """
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if not project.workspace_path or project.intake_mode not in ("git", "zip"):
        raise HTTPException(
            status_code=400,
            detail="workspace file delete only applies to git/zip projects",
        )

    workspace = Path(project.workspace_path).resolve()
    # Reject absolute paths + parent-traversal up front.
    candidate = Path(path)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise HTTPException(status_code=400, detail="path must be relative and inside the workspace")

    target = (workspace / candidate).resolve()
    if workspace not in target.parents and target != workspace:
        raise HTTPException(status_code=400, detail="path escapes the workspace")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"no such file: {path}")

    try:
        target.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"couldn't delete: {exc}") from exc
    return {"deleted": str(target.relative_to(workspace))}


@router.get("/{project_id}/discovery-diagnostics")
async def discovery_diagnostics(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """Show *everything* discovery saw — including files that matched the
    heuristic but failed to load. Used by the suites empty state to explain
    'no suites found' vs 'found one but it crashed on import'.
    """
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if not project.workspace_path:
        return {"workspace_path": None, "loaded": [], "failed": []}

    workspace = Path(project.workspace_path)
    discovered = await discover_suites(workspace)
    loaded = []
    failed = []
    for d in discovered:
        rec = {
            "name": d.name,
            "relative_path": d.relative_path,
            "case_count": d.case_count,
            "load_error": d.load_error,
        }
        (failed if d.load_error else loaded).append(rec)
    return {
        "workspace_path": str(workspace),
        "loaded": loaded,
        "failed": failed,
    }


@router.get("/{project_id}/suites", response_model=list[SuiteOut])
async def list_suites(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> list[SuiteOut]:
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    rows = (
        await session.execute(
            select(models.Suite).where(models.Suite.project_id == project_id).order_by(models.Suite.name)
        )
    ).scalars().all()
    return [_to_suite_out(s) for s in rows]


# ------------------------------------------------------------------- runs list


@router.delete("/{project_id}/runs", status_code=status.HTTP_200_OK)
async def clear_project_runs(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """Delete every run for this project that isn't currently in flight.

    Returns counts of what was removed vs skipped. ``running`` / ``pending``
    runs are left alone so we don't orphan an executor subprocess.
    """
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    rows = (
        await session.execute(
            select(models.Run).where(models.Run.project_id == project_id)
        )
    ).scalars().all()
    deleted = skipped = 0
    for r in rows:
        if r.status in ("pending", "running"):
            skipped += 1
            continue
        await session.delete(r)
        deleted += 1
    return {"deleted": deleted, "skipped": skipped}


@router.get("/{project_id}/runs", response_model=list[RunOut])
async def list_recent_runs(
    project_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> list[RunOut]:
    """Most-recent-first runs for the project — for the detail-page panel."""
    project = await session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    rows = (
        await session.execute(
            select(models.Run)
            .where(models.Run.project_id == project_id)
            .order_by(models.Run.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        RunOut(
            id=r.id,
            project_id=r.project_id,
            suite_id=r.suite_id,
            command=r.command,
            status=r.status,
            case_filter=r.case_filter,
            started_at=r.started_at,
            finished_at=r.finished_at,
            exit_code=r.exit_code,
            cases_total=r.cases_total,
            cases_passed=r.cases_passed,
            cases_regressed=r.cases_regressed,
            stderr_tail=r.stderr_tail,
            created_at=r.created_at,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------- helpers


async def _resolve_git_auth(
    session: AsyncSession, *, project_id: int | None, source: str
):
    """Look up the right token from Studio Secrets for ``source``'s host.

    Returns a ``GitAuth`` (or ``None`` for SSH / public / unknown-host
    URLs). Project-scoped secrets override global; falls back to
    ``GIT_HTTPS_TOKEN`` when the host isn't one of the well-known ones.
    """
    from ..intake.git_auth import detect_scheme, host_of

    if detect_scheme(source) not in ("https", "http"):
        return None
    host = host_of(source)
    if not host:
        return None
    names = preferred_secret_names(host)
    secrets = await load_named_secrets(
        session, project_id=project_id, names=names
    )
    return resolve_auth(source, secrets)


async def _save_and_extract(
    upload: UploadFile, projects_dir: Path, project_id: int
) -> Path:
    """Stream the upload to a temp file, then extract via the zip intake.

    The temp file lives only as long as extraction takes — it's deleted in
    the ``finally`` block. We can't extract straight from ``upload.file``
    because ``zipfile`` needs a seekable stream and large uploads tend to be
    streamed.
    """
    suffix = Path(upload.filename or "uploaded.zip").suffix or ".zip"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = Path(tmp.name)
    try:
        # Chunked copy — UploadFile is an async-friendly wrapper around the
        # spooled temp file FastAPI builds. We stream into our own temp so
        # zipfile gets a seekable handle.
        while chunk := await upload.read(1024 * 1024):
            tmp.write(chunk)
        tmp.close()
        return await zip_extract(
            projects_dir=projects_dir,
            project_id=project_id,
            archive_path=tmp_path,
        )
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


async def _rediscover(
    session: AsyncSession, project: models.Project, workspace
) -> list[models.Suite]:
    """Wipe and re-create suite rows for the project from a fresh disk walk."""
    await session.execute(
        delete(models.Suite).where(models.Suite.project_id == project.id)
    )
    discovered = await discover_suites(workspace)
    out: list[models.Suite] = []
    for d in discovered:
        # Two surfaces:
        #   * load_error is None: a fully-loaded suite. Always persist.
        #   * load_error is set BUT we extracted a real suite name (via AST)
        #     because the file would load in the project venv. Persist those
        #     too — the executor handles the import at run time. We detect
        #     the AST-soft case by checking that ``name`` isn't the fallback
        #     (which we set to the file's relative path on hard failures).
        if d.load_error and d.name == d.relative_path:
            continue
        row = models.Suite(
            project_id=project.id,
            name=d.name,
            file_path=d.relative_path,
            case_count=d.case_count,
        )
        session.add(row)
        out.append(row)
    await session.flush()
    return out


def _to_project_out(p: models.Project) -> ProjectOut:
    return ProjectOut(
        id=p.id,
        name=p.name,
        intake_mode=p.intake_mode,
        source=p.source,
        git_ref=p.git_ref,
        workspace_path=p.workspace_path,
        http_config=p.http_config,
        last_synced_at=p.last_synced_at,
        created_at=p.created_at,
    )


def _to_suite_out(s: models.Suite) -> SuiteOut:
    return SuiteOut(
        id=s.id,
        project_id=s.project_id,
        name=s.name,
        file_path=s.file_path,
        case_count=s.case_count,
        discovered_at=s.discovered_at,
    )

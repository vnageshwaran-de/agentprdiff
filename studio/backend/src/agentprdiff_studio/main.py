"""FastAPI app entrypoint.

Run with::

    uvicorn agentprdiff_studio.main:app --reload --port 8080

The lifespan handler:

* ensures the data dir + projects dir exist;
* materializes a secret key (Fernet) — not yet used in M1;
* creates all tables (M1 uses ``create_all``; Alembic comes later).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import (
    agents_md_router,
    baseline_review_router,
    benchmark_router,
    case_runs_router,
    case_timeline_router,
    coverage_router,
    projects_router,
    replay_router,
    runs_router,
    secrets_router,
    suite_health_router,
    tour_router,
)
from .api.errors import install as install_error_handlers
from .db.session import create_all, init_engine
from .executor.bus import RunEventBus
from .executor.events import set_bus
from .executor.tasks import RunTaskRegistry
from .settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    # Ensure a key exists on first run, even though M1 doesn't use it.
    settings.resolve_secret_key()
    init_engine()
    await create_all()
    # Bus + registry are process-local — fine for the single-tenant Docker
    # image we ship. Multi-replica deployments would need an external broker
    # (Redis pub-sub) here; not in scope until M9+.
    app.state.event_bus = RunEventBus()
    app.state.task_registry = RunTaskRegistry()
    set_bus(app.state.event_bus)
    yield


app = FastAPI(
    title="agentprdiff Studio",
    version=__version__,
    description=(
        "Web server for agentprdiff. Trigger runs, view diffs, approve "
        "baselines from a browser. M1 ships the API; UI follows in M4."
    ),
    lifespan=lifespan,
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handlers(app)

app.include_router(projects_router)
app.include_router(runs_router)
app.include_router(secrets_router)
app.include_router(case_runs_router)
app.include_router(agents_md_router)
app.include_router(tour_router)
# Phase 2-4 routers: dashboards, history, coverage, benchmarks, replay.
app.include_router(suite_health_router)
app.include_router(baseline_review_router)
app.include_router(case_timeline_router)
app.include_router(coverage_router)
app.include_router(benchmark_router)
app.include_router(replay_router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


def _mount_frontend(app: FastAPI, frontend_dir: Path) -> None:
    """Serve the built SPA (Vite ``dist/``) alongside the API.

    Mount semantics:

    * ``GET /assets/...`` and the other top-level static files (``favicon``,
      ``vite.svg``, etc.) come straight from the directory.
    * Anything else that *isn't* an ``/api/`` route falls back to
      ``index.html`` so client-side routing works (the browser asks for
      ``/projects/new`` directly and we hand back the SPA shell).

    Local dev (``npm run dev``) doesn't go through here at all — the Vite
    dev server proxies ``/api`` calls to uvicorn. ``frontend_dir`` is only
    set in the Docker image (or by an operator who wants a single-port
    deployment).
    """
    assets_dir = frontend_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    index_html = frontend_dir / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(request: Request, full_path: str) -> FileResponse:
        # /api/* must never reach here; routers above are matched first.
        # Guard anyway in case the order ever changes.
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404, detail="not found")
        # Serve a real file at the requested path if one exists (favicon,
        # robots.txt, etc.) — otherwise fall back to index.html.
        candidate = frontend_dir / full_path if full_path else index_html
        if candidate.is_file():
            return FileResponse(candidate)
        if index_html.is_file():
            return FileResponse(index_html)
        raise HTTPException(status_code=404, detail="frontend not built")


# Mount the SPA at import time so uvicorn workers all get the routes.
_settings_for_static = get_settings()
if _settings_for_static.frontend_dir and Path(_settings_for_static.frontend_dir).exists():
    _mount_frontend(app, Path(_settings_for_static.frontend_dir))

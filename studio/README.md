# agentprdiff Studio

Web UI on top of the [agentprdiff](https://github.com/vnageshwaran-de/agentprdiff) engine.

The CLI (`pip install agentprdiff`, write `suite.py`, `agentprdiff check`) still works exactly the same. Studio is a parallel surface for non-dev users (PMs, QA, vibecoders) who want to trigger runs, watch progress live, diff cases, and approve baselines in a browser.

## Quick start (Docker)

```bash
# SQLite, single container, zero config:
docker compose up --build

# Open the UI:
open http://localhost:8080
```

That spins up one container, persists state to a `studio-data` volume, and serves both the API (under `/api/*`) and the SPA on port 8080.

For shared deployments swap to Postgres:

```bash
docker compose --profile postgres up --build
# also export STUDIO_DATABASE_URL=postgresql+asyncpg://studio:studio@db:5432/studio
# (or set it in studio.environment in compose.yml).
```

## Run modes

| Mode | Storage | Best for |
|---|---|---|
| Default (SQLite) | `studio-data` volume → `/data/studio.db` | Solo / small team, single host |
| `--profile postgres` | Postgres container + `studio-pg` volume | Multiple users, durable history |

Plus the orthogonal **intake modes** for projects you create inside Studio:

* **git** — clone a remote repo, walk it for suites, run via subprocess+venv.
* **zip** — upload an archive, same execution path as git.
* **http** — point Studio at a deployed endpoint, author suites as JSON, runs go in-process via httpx (no Python execution, baselines stored in the DB).

## Local development (without Docker)

```bash
# Backend (Python).
pip install -e .                # the engine, from the repo root
pip install -e studio/backend
uvicorn agentprdiff_studio.main:app --reload --port 8080

# Frontend (separate terminal).
cd studio/frontend
npm install
npm run dev                     # Vite on :5173, proxies /api → :8080
open http://localhost:5173
```

In dev the frontend lives on Vite. In Docker / prod the FastAPI app serves the built `dist/` on the same port as the API.

## Configuration

Everything is env-driven. The most useful knobs:

| Env var | Default | Notes |
|---|---|---|
| `STUDIO_DATA_DIR` | `./.studio-data` | DB + cloned repos + uploaded zips |
| `STUDIO_DATABASE_URL` | `sqlite+aiosqlite:///<data>/studio.db` | Set to `postgresql+asyncpg://…` for Postgres |
| `STUDIO_FRONTEND_DIR` | unset (dev) / `/opt/studio/frontend` (Docker) | If set + exists, FastAPI serves the SPA there |
| `STUDIO_SECRET_KEY` | generated and persisted to `<data>/.secret_key` | Fernet key for encrypting secrets at rest |
| `STUDIO_ENGINE_REQ` | `agentprdiff>=0.2.5` | What pip installs into per-project venvs |
| `STUDIO_RUN_WALLTIME_SECONDS` | `300` | Per-run hard wall-time |
| `STUDIO_RUN_MEMORY_MB` | `1024` | Per-run memory cap (POSIX rlimit) |
| `STUDIO_CORS_ORIGINS` | `["*"]` | Pass as JSON array via env if tightening |

## What's in the image

* Engine (`agentprdiff`) installed from PyPI (or a path, via `STUDIO_ENGINE_REQ`).
* Studio backend (FastAPI + SQLAlchemy + httpx + GitPython).
* Built SPA (Vite output) at `/opt/studio/frontend`.
* `git`, `build-essential`, `tini` for clean signal handling.

The image runs as `uvicorn agentprdiff_studio.main:app --host 0.0.0.0 --port 8080` under `tini`. A healthcheck hits `/api/health` every 30s.

## Persistence

All durable state lives under `/data` inside the container, mounted via the named volume `studio-data`:

```
/data
├── studio.db              # SQLite (default mode)
├── .secret_key            # Fernet key (created on first boot)
└── projects/<id>/
    ├── repo/              # git intake
    │   └── .studio-venv/  # per-project venv (provisioned on first run)
    └── upload/            # zip intake
```

For Postgres deployments the DB lives in `studio-pg`; the `studio-data` volume still holds the per-project workspaces and venvs.

## Where things land in the repo

```
studio/
├── Dockerfile                  multi-stage: node build → python runtime
├── docker-compose.yml          default + postgres profile
├── .dockerignore               keeps the build context small
├── backend/
│   └── src/agentprdiff_studio/ FastAPI app + executor + DB layer
└── frontend/
    └── src/                    Vite + React + TS SPA
```

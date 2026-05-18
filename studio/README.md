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

## Private git repos

Studio supports two non-interactive auth paths for private repos. The
container has no TTY, so git **never prompts** — you choose one of these
before creating the project.

### Option A — SSH (`git@github.com:owner/repo.git`)

The Studio image installs `openssh-client`, so the only thing you need
to provide is your SSH key. Mount your host's `~/.ssh` into the
container (read-only is fine):

```yaml
# studio/docker-compose.yml — add to the studio service
services:
  studio:
    # … existing config …
    volumes:
      - studio-data:/data
      - ${HOME}/.ssh:/root/.ssh:ro    # ← add this line
```

Two gotchas worth knowing:

* The container runs as root, so the SSH client looks at `/root/.ssh`.
  If you'd prefer a non-default location, set `GIT_SSH_COMMAND="ssh -i
  /path/to/key"` via `STUDIO_*` env (or directly on the service in
  compose).
* `known_hosts` lives in the same directory you mount. If you've never
  ssh'd to the remote from your host, run
  `ssh-keyscan github.com >> ~/.ssh/known_hosts` once before starting
  Studio, otherwise the first clone fails with *"Host key verification
  failed."* (Studio surfaces this error inline with the same hint.)

Then point Studio at an SSH URL when you create the project:

```
git@github.com:vnageshwaran-de/private-repo.git
```

### Option B — HTTPS + token (`https://github.com/owner/repo.git`)

Save a personal access token in **Studio's Secrets page** (top-right
nav), then create the project with a plain `https://` URL. Studio reads
the token at clone time, sends it via `Authorization: bearer …` in
git's transient config, and never embeds it in the URL or writes it to
disk.

Secret name → host mapping (project-scoped wins over global):

| Host | Secret name |
|---|---|
| `github.com` | `GITHUB_TOKEN` |
| `gitlab.com` | `GITLAB_TOKEN` |
| `bitbucket.org` | `BITBUCKET_TOKEN` |
| Anything else (self-hosted Enterprise / on-prem) | `GIT_HTTPS_TOKEN` |

The fallback `GIT_HTTPS_TOKEN` lets you point Studio at self-hosted
GitHub Enterprise, self-hosted GitLab, or Gitea / Forgejo without
baking the hostname into Studio's config.

**What the token needs:** for GitHub classic PATs, `repo` scope. For
GitHub fine-grained PATs, `Contents: read-only` on the repos you want
to clone is enough. For GitLab, `read_repository` scope.

**What Studio does with it:**

* Reads the encrypted secret at clone time (Fernet at rest, plaintext
  only in memory of the requesting worker).
* Injects `http.https://<host>/.extraheader: Authorization: bearer
  <token>` via `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_N` /
  `GIT_CONFIG_VALUE_N` env vars on the git subprocess.
* Sets `GIT_TERMINAL_PROMPT=0` so git can't block on stdin even when
  the token is wrong (you get a clean error in the UI instead of a
  hung sync).
* Redacts the token from any error string before it reaches the UI,
  the logs, or the project row.

**What Studio refuses:** URLs with embedded credentials
(`https://user:pat@host/...`). The error tells you to strip the
credential and save it in the Secrets page instead — embedded
credentials persist in the project row, in git's reflog, and in the
workspace's `.git/config`, which are all places they shouldn't be.

**Recovering from a wrong / expired token:** open the Secrets page,
update the value, click Sync on the project. The next clone picks up
the new token. The old one isn't kept anywhere.

### When to use which

| Situation | Pick |
|---|---|
| Personal use, ssh-agent already configured on host | SSH |
| Want to avoid mounting host paths into the container | HTTPS + token |
| Self-hosted Enterprise, no SSH access | HTTPS + token (`GIT_HTTPS_TOKEN`) |
| Multiple users sharing the Studio container | HTTPS + token (project-scoped secrets isolate per project) |
| CI / headless deployment | HTTPS + token (no key material to ship) |

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
* `git`, `openssh-client`, `build-essential`, `tini` for clean signal handling. `openssh-client` is what makes `git@github.com:…` URLs work — see the [Private git repos](#private-git-repos) section above.

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

# agentprdiff Studio — Session Handover

Last updated: end of long-running session covering M1 → M9 + post-M9 polish.

## What this is

A web UI (React + FastAPI, Docker-packaged) on top of the existing **agentprdiff** engine. The CLI workflow (`pip install agentprdiff`, write `suite.py`, `agentprdiff check`) is untouched. Studio is a parallel surface aimed at non-dev users (PMs, QA, "vibecoders") who want to trigger runs, watch them live, diff cases, and approve baselines in a browser.

The engine package (`src/agentprdiff/`) was **not modified** at any point. All Studio code lives under `studio/`.

## Run it

```bash
cd studio
docker compose down
docker compose up --build
```

Open <http://localhost:8080>. Default config is SQLite + single container; `--profile postgres` brings up a Postgres sidecar.

The first-run experience auto-redirects fresh projects into a guided **Tour** (`/projects/:id/tour`).

## LLM provider (for AI-generate)

Resolver order: `STUDIO_LLM_PROVIDER` env → `ANTHROPIC_API_KEY` secret → `OPENAI_API_KEY` secret → `GEMINI_API_KEY` secret → Ollama fallback at `STUDIO_OLLAMA_BASE_URL`. All four providers implemented as plain `httpx` calls (no SDK deps).

`docker-compose.yml` is currently pinned to Gemini by default (`STUDIO_LLM_PROVIDER=gemini`, `STUDIO_LLM_MODEL=gemini-flash-latest`). Save your Gemini key in **Secrets** UI as `GEMINI_API_KEY`. To use a different provider, change the env in compose or save a different key.

## Repo layout

```
agentprdiff/
├── src/agentprdiff/                # engine — DON'T touch
├── studio/
│   ├── Dockerfile                  # multi-stage: node build → python:3.12-slim
│   ├── docker-compose.yml          # SQLite default; --profile postgres
│   ├── README.md                   # public quickstart
│   ├── docs/
│   │   ├── quickstart-for-non-devs.md
│   │   └── screencast.md           # 75-second walkthrough script
│   ├── backend/
│   │   ├── pyproject.toml
│   │   └── src/agentprdiff_studio/
│   │       ├── main.py             # FastAPI app + lifespan + SPA fallback
│   │       ├── settings.py         # env-driven config
│   │       ├── api/                # routers
│   │       │   ├── projects.py     # CRUD + sync + discovery diagnostics
│   │       │   ├── runs.py         # POST runs + SSE stream + delete
│   │       │   ├── secrets.py      # Fernet-encrypted secrets store
│   │       │   ├── case_runs.py    # per-case detail + baseline approval
│   │       │   ├── agents_md.py    # parser endpoints + AI generate + save
│   │       │   ├── tour.py         # the 7-step guided flow
│   │       │   └── errors.py       # domain → {detail, hint} JSON
│   │       ├── agents_md/
│   │       │   ├── parser.py       # walks workspace for AGENTS.md + *_cases.md
│   │       │   ├── templates.py    # starter AGENTS.md + python suite skeleton
│   │       │   └── validate.py     # AST + heuristic + load check
│   │       ├── db/
│   │       │   ├── models.py       # Project, Suite, Run, CaseRun, Baseline,
│   │       │   │                   #   Secret, Event
│   │       │   └── session.py      # async SQLAlchemy + auto-add-column shim
│   │       ├── executor/
│   │       │   ├── dispatch.py     # routes by intake_mode
│   │       │   ├── run.py          # subprocess executor (git/zip)
│   │       │   ├── http_run.py     # in-process executor (http)
│   │       │   ├── runner_shim.py  # what the subprocess actually runs
│   │       │   ├── venv.py         # per-project venv + fingerprint cache
│   │       │   ├── bus.py          # in-memory pub-sub for SSE
│   │       │   ├── tasks.py        # asyncio.Task registry
│   │       │   └── events.py       # record_event() → DB + bus
│   │       ├── intake/
│   │       │   ├── git.py          # clone + stash-aware sync
│   │       │   ├── zip.py          # upload + zip-slip defense
│   │       │   ├── http.py         # http_config / suite definition validators
│   │       │   ├── discovery.py    # workspace walker + AST soft-discovery
│   │       │   └── ast_extract.py  # AST suite/case extractor
│   │       ├── llm/
│   │       │   ├── providers.py    # OpenAI/Anthropic/Gemini/Ollama clients
│   │       │   └── resolve.py      # provider picker
│   │       ├── tour/
│   │       │   ├── state.py        # computes per-step status from real data
│   │       │   ├── simulate.py     # mutate-then-revert helper for the demo
│   │       │   └── ci_yaml.py      # render + commit-and-push GHA workflow
│   │       ├── secrets/
│   │       │   ├── crypto.py       # Fernet wrapper
│   │       │   └── resolve.py      # load + decrypt for run env
│   │       └── resources/AGENTS.md # snapshot of the engine's playbook
│   └── frontend/
│       ├── package.json
│       ├── vite.config.ts          # /api proxy in dev, served by FastAPI in prod
│       └── src/
│           ├── App.tsx             # routes
│           ├── main.tsx            # providers (query, toaster)
│           ├── api/{client,types}.ts
│           ├── components/
│           │   ├── Layout.tsx
│           │   ├── Toaster.tsx
│           │   ├── ProjectGuide.tsx
│           │   └── ui/             # Button, Input, Card, Badge, Spinner, Select
│           ├── hooks/useRunStream.ts
│           └── pages/
│               ├── ProjectsList.tsx
│               ├── ProjectNew.tsx
│               ├── ProjectDetail.tsx
│               ├── RunDetail.tsx
│               ├── CaseDetail.tsx
│               ├── SecretsPage.tsx
│               └── TourPage.tsx
└── HANDOVER.md                     # this file
```

## Milestone status (all shipped)

| | | What it does |
|---|---|---|
| **M1** | done | Backend skeleton + git intake + subprocess executor |
| **M2** | done | Zip upload intake (with zip-slip defense) |
| **M2.5** | done | HTTP-endpoint intake (in-process executor, DB-backed baselines) |
| **M3** | done | Fernet secret store + per-project venv fingerprint cache |
| **M4** | done | React + Vite UI shell, projects list/new/detail |
| **M5** | done | Run trigger + SSE live progress + run detail page |
| **M6** | done | Per-case diff viewer + baseline approval (writes DB + disk) |
| **M7** | done | Docker image + compose + SQLite default |
| **M8** | done | Vibecoder polish (toasts, empty states, friendly errors, docs) |
| **M9** | done | Studio Tour — 7-step guided E2E flow with simulate-regression button |

## Post-M9 polish (also shipped)

- **AGENTS.md feature**: deterministic `*_cases.md` parser + Project Guide card with expandable case chips.
- **AI scaffold**: Generate-with-AI button uses bundled AGENTS.md as system context. Returns suite Python + companion dossier markdown in one call; tabbed preview before save.
- **Validation**: AST + heuristic + actual load check before saving, with project-venv softening so `import openai` doesn't false-positive.
- **Auto-detect agent module + callable**: scans workspace for the right `from agent import …` target; tells the LLM about it explicitly.
- **Discovery diagnostics**: a Diagnose button on the Suites panel showing loaded + failed candidates with hint copy ("add this dep to requirements.txt").
- **Soft discovery**: files that fail in the host but would load via project venv show up as real suites (AST extracts name + case count).
- **Non-destructive sync**: `git fetch` + stash + `merge --ff-only` + stash-pop. Studio-written files survive Sync.
- **Delete affordances**: trash icons on suites, runs, and failed-candidate files. "Clear all runs" button.
- **Auto-add-column DB shim**: nullable schema additions auto-apply on startup; saves the docker volume on every schema iteration.

## Database

SQLite by default at `/data/studio.db` (Docker volume `studio-data`).

Schema is created via `create_all` on startup + a small `_ensure_columns` shim that ALTERs in any new nullable columns. **Not a substitute for Alembic** — when you ship a non-additive change (rename, drop, NOT NULL with no default) this shim will refuse and you need a real migration.

Models in `studio/backend/src/agentprdiff_studio/db/models.py`. Read-friendly comments inline.

## Known gaps / future work

These are real but were deliberately out of scope:

1. **Alembic** — `create_all` + the auto-add-column shim handles dev. For prod / shared deployments, switch to Alembic before non-additive schema changes.
2. **Multi-user / auth** — single-tenant by design. Adding auth + invite links is a clean next layer; nothing in the app assumes single-user beyond the absence of session middleware.
3. **Run isolation** — subprocess + venv + rlimits is the current sandbox. Docs say "trust your operator." For SaaS-grade isolation use gVisor / Firecracker / ephemeral containers; the executor's `subprocess.create_subprocess_exec` is the single hook point.
4. **Pricing table for newer Gemini models** — engine warns "no pricing entry for model 'gemini-flash-latest'; cost_usd will be recorded as 0.0." Either pin a specific model name the engine knows, or register a price programmatically via `agentprdiff.adapters.register_prices({...})` at suite-import time.
5. **Real bundle of agentprdiff in the image** — Dockerfile pins `agentprdiff>=0.2.5`. Once you cut a release, the image picks it up. For dev right now, set `STUDIO_ENGINE_REQ` to `-e /path/to/agentprdiff` in compose.
6. **Connection profiles UX** — Studio Secrets are flat strings. A "profile" abstraction that bundles `{provider, key, model}` together would prevent the LLM_PROVIDER / LLM_API_KEY / LLM_MODEL coordination problem the user hit.

## Common debugging patterns we established

- **"No suites found yet"** → click **Diagnose** button on Suites panel. Three states:
  - Files matched + loaded → check the suite is bound to a module-level variable.
  - Files matched + failed → see the inline hint. Most common: `ModuleNotFoundError: No module named 'X'` where X is a project dep → add to `requirements.txt`. Or `cannot import name 'foo' from 'bar'` → wrong entrypoint name, edit or regenerate.
  - No files matched → heuristic needs both `from agentprdiff` and `suite(` in the same file.

- **"AuthenticationError 401"** → the project's agent code reads some env var. Check `config.py` / `agent/llm_provider.py` for `os.getenv("X")`. Save that exact name X as a Studio Secret. Common pattern: a project has multiple coordinated env vars (`LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`) — they all need to be saved and need to agree.

- **Stale schema after a model change** → either `docker compose down -v` (wipes the volume) or let the auto-add-column shim run on startup if it's an additive change.

- **Studio-written files vanish after Sync** → this was a real bug; now fixed by stash-aware `merge --ff-only`. If you ever see it again, the regression is in `intake/git.py`.

- **Gemini 400 on `:generateContent`** → 2.5+ models default to "thinking" mode which eats output tokens. The provider in `llm/providers.py` already sets `thinkingConfig.thinkingBudget: 0` to disable.

## Architecture notes worth keeping in head

- **Two executor paths.** Git/zip → subprocess + per-project venv + JSONL shim. HTTP → in-process httpx + DB-backed baselines. Dispatch in `executor/dispatch.py`.
- **Baselines live in two places.** For git/zip projects, on disk inside the workspace at `<workspace>/.agentprdiff/baselines/<suite>/<case>.json` (so the CLI workflow keeps working). For HTTP projects, in the `baselines` DB table.
- **SSE stream is best-effort.** Process-local bus; if Studio restarts mid-run, in-flight SSE consumers fall back to polling `/api/runs/{id}`. Replay-on-connect handles the late-subscriber case via the `events` DB table.
- **Tour state is computed, not stored.** Step completion comes from real data (runs exist, suites exist, regressions seen). Only user choices (skipped steps, ci_committed, completed flag) are persisted in `Project.tour_state`.

## Smoke tests we used (reproducible)

All in-process via `fastapi.testclient.TestClient`:

```python
from fastapi.testclient import TestClient
from agentprdiff_studio.settings import get_settings
get_settings.cache_clear()
import importlib, agentprdiff_studio.main as m
importlib.reload(m)

with TestClient(m.app) as c:
    # …
```

There's no formal test suite committed yet. Adding one with `pytest-asyncio` + `httpx.AsyncClient` is a reasonable next milestone before any real release.

## What to ask first in the next session

If a fresh session is going to continue this work:

1. **Where are you starting from?** Latest container working, or fresh clone?
2. **What's the next move?** Could be (a) wire Alembic, (b) build connection-profiles UX, (c) write the test suite, (d) deploy somewhere shared, (e) build the AI generate dossier rendering as a separate viewer, (f) add the LLM-judge approval shortcut for cases that are "obviously fine."
3. **Anything broken right now?** Paste the output of:
   ```
   docker compose ps
   docker compose logs --tail=50 studio
   curl -s http://localhost:8080/api/health
   ```

That's enough context for the next assistant (or future you) to pick up without re-tracing the whole journey.

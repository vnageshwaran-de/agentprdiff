# agentprdiff-studio (backend)

Web server for **agentprdiff Studio** — a browser UI on top of the [agentprdiff](https://github.com/vnageshwaran-de/agentprdiff) engine.

The CLI workflow (`pip install agentprdiff`, write `suite.py`, `agentprdiff check`) is unchanged. Studio is a parallel surface for non-dev users (PMs, QA, vibecoders) who want to trigger runs, review diffs, and approve baselines in a browser.

## Status

**M1** — backend skeleton + git intake + executor. No UI yet; everything is driven by `curl`.

## Run it locally

```bash
cd studio/backend
pip install -e ../..       # install the engine from the monorepo
pip install -e ".[dev]"    # install studio backend + dev tools
uvicorn agentprdiff_studio.main:app --reload --port 8080
```

Default config writes to `./.studio-data/` (SQLite + cloned project workspaces). Override with env vars — see `src/agentprdiff_studio/settings.py`.

## API (M1)

```
POST /api/projects                       create + git-clone
POST /api/projects/{id}/sync             re-pull + rediscover suites
GET  /api/projects/{id}                  detail
GET  /api/projects/{id}/suites           list discovered suites
POST /api/runs                           {project_id, suite_id, command}
GET  /api/runs/{id}                      status + case summary
GET  /api/runs/{id}/cases                full per-case results
```

Zip / HTTP intake, secrets, SSE, and the React UI come in M2–M5.

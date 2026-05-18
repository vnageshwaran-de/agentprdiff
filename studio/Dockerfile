# syntax=docker/dockerfile:1.7

# ─── Stage 1: build the frontend ───────────────────────────────────────────
FROM node:20-slim AS frontend

WORKDIR /work
COPY studio/frontend/package.json studio/frontend/package-lock.json* ./
# package-lock.json may not exist in fresh checkouts; tolerate either.
RUN npm install --no-audit --no-fund --prefer-offline --loglevel=error

COPY studio/frontend/ ./
# Vite reads this at build-time. Single-origin deploy → "/api" works as the
# default; override via build arg if the SPA ever lives on a different host.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build


# ─── Stage 2: runtime ──────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# git: required for ``intake_mode=git`` (GitPython shells out).
# openssh-client: required for cloning private repos over SSH
#   (``git@github.com:owner/repo.git``). Without this, GitPython's
#   subprocess fails with "cannot run ssh: No such file or directory".
#   Operators mount their ~/.ssh into the container at run time —
#   the package itself is just the ssh binary plus known_hosts machinery.
# build-essential: pip occasionally compiles small wheels in the venv
#   provisioner; keeping the toolchain available avoids surprise failures
#   when an adopter's project has a non-pure-python dep.
# tini: tiny init so SIGTERM reaches uvicorn cleanly on `docker stop`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git \
       openssh-client \
       build-essential \
       tini \
       ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/studio

# Install the engine + studio backend.
# Engine first so it's cached separately from studio's faster-moving deps.
COPY src/ /opt/agentprdiff/src/
COPY pyproject.toml README.md LICENSE /opt/agentprdiff/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir /opt/agentprdiff

COPY studio/backend/ /opt/studio-backend/
RUN pip install --no-cache-dir /opt/studio-backend

# Copy in the built SPA from stage 1.
COPY --from=frontend /work/dist /opt/studio/frontend

# Default runtime configuration. Override any of these via `-e` at run-time.
ENV STUDIO_DATA_DIR=/data \
    STUDIO_FRONTEND_DIR=/opt/studio/frontend \
    STUDIO_ENGINE_REQ="agentprdiff>=0.3.1" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Persistent state lives here. Mount a host directory or a named volume.
RUN mkdir -p /data
VOLUME /data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, sys; \
      sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3).status == 200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "agentprdiff_studio.main:app", "--host", "0.0.0.0", "--port", "8080"]

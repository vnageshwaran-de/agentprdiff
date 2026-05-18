"""Studio runtime configuration.

All knobs come from environment variables (or a ``.env`` file in the cwd).
Defaults are tuned for ``uvicorn agentprdiff_studio.main:app --reload`` working
zero-config in a fresh checkout.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Studio settings.

    Override any field via the equivalent env var, e.g. ``STUDIO_DATA_DIR=/data``,
    ``STUDIO_DATABASE_URL=postgresql+asyncpg://...``.
    """

    model_config = SettingsConfigDict(
        env_prefix="STUDIO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Where Studio keeps everything: SQLite db, cloned repos, uploads, per-project venvs.
    data_dir: Path = Field(default_factory=lambda: Path.cwd() / ".studio-data")

    # SQLAlchemy URL. Default is a SQLite file inside data_dir; set to a
    # postgresql+asyncpg URL for the compose deployment.
    database_url: str | None = None

    # Fernet key (base64 urlsafe, 32 raw bytes). Generated on first run if absent.
    # Used to encrypt API keys in the `secrets` table — not used in M1 but the
    # field exists so we don't churn the env contract later.
    secret_key: str | None = None

    # Subprocess resource caps for a single suite run.
    run_walltime_seconds: int = 300
    run_memory_mb: int = 1024
    run_cpu_seconds: int = 240

    # CORS for the eventual frontend. In dev we wide-open; tighten in prod.
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # Where the built SPA lives. In the Docker image this is set explicitly;
    # for local dev we don't mount it (the Vite dev server handles the UI).
    frontend_dir: Path | None = None

    # ----- derived paths ---------------------------------------------------

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "studio.db"

    def resolve_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.sqlite_path}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def resolve_secret_key(self) -> str:
        """Return the Fernet key, generating one on first run if unset.

        The generated key is persisted into ``data_dir/.secret_key`` so it
        survives restarts. Operators can pre-set ``STUDIO_SECRET_KEY`` to
        avoid the on-disk file.
        """
        if self.secret_key:
            return self.secret_key
        key_file = self.data_dir / ".secret_key"
        if key_file.exists():
            return key_file.read_text(encoding="utf-8").strip()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        new_key = _generate_fernet_key()
        key_file.write_text(new_key, encoding="utf-8")
        os.chmod(key_file, 0o600)
        return new_key


def _generate_fernet_key() -> str:
    # Imported lazily so settings is import-safe even if cryptography is
    # missing during a partial install.
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("utf-8")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

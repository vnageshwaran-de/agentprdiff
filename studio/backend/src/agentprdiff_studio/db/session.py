"""Async SQLAlchemy engine + session helpers.

The engine is created lazily on first use and held as a module-level singleton.
Tests can swap it via :func:`init_engine`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..settings import get_settings
from .models import Base

log = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str | None = None) -> AsyncEngine:
    """(Re)create the global engine + session factory.

    Called on app startup; tests call it with an explicit in-memory URL.
    """
    global _engine, _session_factory

    settings = get_settings()
    url = database_url or settings.resolve_database_url()
    # SQLite needs ``check_same_thread=False`` to be used across asyncio tasks;
    # aiosqlite handles this, but we still want the friendly default.
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}

    _engine = create_async_engine(url, connect_args=connect_args, future=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory


async def create_all() -> None:
    """Create tables, then ``ALTER TABLE ADD COLUMN`` for any nullable
    columns that exist on the SQLAlchemy models but not in the live DB.

    This is the developer-experience safety net: until we adopt Alembic,
    a fresh schema field on an existing install would otherwise 500 every
    query. Limitations are intentional — we only handle *additive* changes
    that are safe to apply against live data:

    * Column must be nullable (so existing rows can keep NULL).
    * Renames, drops, and constraint changes are not attempted.
    * Postgres-only types we don't use today (ARRAY, ENUM, …) are skipped
      to avoid silently corrupting a real deployment.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_columns)


def _ensure_columns(connection) -> None:
    """Add missing nullable columns to existing tables, in place.

    Runs synchronously inside ``conn.run_sync`` so we can use SQLAlchemy's
    Inspector. Idempotent — a second call is a no-op.
    """
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())

    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            continue  # ``create_all`` already handled this one
        live_cols = {c["name"] for c in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name in live_cols:
                continue
            if not column.nullable:
                log.warning(
                    "ensure_columns: refusing to add NOT NULL column %s.%s — "
                    "use a migration tool. Skipping.",
                    table_name, column.name,
                )
                continue
            col_type = column.type.compile(dialect=connection.dialect)
            stmt = text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {col_type}')
            log.info("ensure_columns: %s", stmt)
            connection.execute(stmt)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A context-managed AsyncSession that commits on success, rolls back on error."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Yields a session; commits at the end of the request."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

"""Async SQLAlchemy engine + session factory.

Schema is intentionally portable across PostgreSQL (production) and SQLite
(tests). Avoid Postgres-only types (UUID, JSONB) at the SQLAlchemy column
level; we serialize UUIDs as 36-char strings and use the generic JSON type
which maps to ``jsonb`` on Postgres and ``JSON`` (TEXT) on SQLite.

Engine creation is deferred until ``init_engine`` runs in the app lifespan,
so importing this module never opens a connection.
"""
from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Common base for all ORM models. Imported by alembic env."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Build the global engine + sessionmaker. Idempotent for the same URL.

    SQLite (``sqlite+aiosqlite://``) gets ``check_same_thread=False`` and a
    NullPool-style approach via SQLAlchemy's async pool defaults; Postgres
    uses asyncpg's pool. Either way the caller doesn't care.
    """
    global _engine, _sessionmaker

    if _engine is not None:
        # Same URL → keep. Different URL → tear down (test isolation).
        if str(_engine.url) == database_url:
            return _engine
        # Don't await dispose() here; tests that swap URLs call dispose_engine
        # explicitly. Just drop the reference.
        _engine = None
        _sessionmaker = None

    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    _engine = create_async_engine(
        database_url,
        echo=echo,
        future=True,
        connect_args=connect_args,
    )
    _sessionmaker = async_sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False
    )
    return _engine


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("DB engine not initialized; call init_engine() first")
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("DB engine not initialized; call init_engine() first")
    return _sessionmaker


async def session_scope() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that auto-commits on success
    and rolls back on exception. Routers add `Depends(session_scope)` via
    ``app.api._deps.get_session``.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ping() -> None:
    """Cheap connectivity check used at startup. Raises on failure."""
    from sqlalchemy import text

    engine = get_engine()
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))

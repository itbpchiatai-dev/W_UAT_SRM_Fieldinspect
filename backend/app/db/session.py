"""Async DB session helpers.

Two session forms with different commit contracts:

* ``get_db`` (async generator, FastAPI dependency):
    Auto-commits the transaction on a successful request and rolls back
    on any exception. Endpoints should call ``db.flush()`` to materialize
    identifiers but they do NOT need to ``await db.commit()`` themselves —
    the dependency boundary handles it.

* ``get_db_session`` (``@asynccontextmanager``, for CLI/scheduler/seed):
    Yields a session and only rolls back on exception. The caller is
    responsible for committing. Use this from ``app.seed``, APScheduler
    jobs, one-off scripts, etc.

Why the split: FastAPI's dependency lifecycle gives us a natural commit
point (the response is built before teardown), but CLI/job code typically
wants explicit control over multiple commits or partial rollbacks.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


async def init_db() -> None:
    global _engine, _sessionmaker
    settings = get_settings()
    _engine = create_async_engine(
        settings.database_runtime_url,   # srm_app (RLS-enforced) when configured
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_pre_ping=True,
    )
    _sessionmaker = async_sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False
    )


async def close_db() -> None:
    if _engine is not None:
        await _engine.dispose()


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: auto-commits on success, rolls back on any exception.

    Catches BaseException (not just Exception) so asyncio.CancelledError —
    raised by FastAPI/Starlette when a client disconnects mid-request — also
    triggers the rollback path. Since Python 3.8, CancelledError inherits
    from BaseException, not Exception, so a narrower clause would silently
    skip rollback for cancellation.
    """
    if _sessionmaker is None:
        raise RuntimeError("Database not initialized")
    async with _sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    """CLI/scheduler/seed helper: caller is responsible for committing."""
    if _sessionmaker is None:
        raise RuntimeError("Database not initialized")
    async with _sessionmaker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise



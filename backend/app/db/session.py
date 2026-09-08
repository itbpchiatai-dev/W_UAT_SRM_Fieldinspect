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
point, but CLI/job code typically wants explicit control over multiple
commits or partial rollbacks.

Round I — WHEN that commit happens is what DbDep below exists to fix. Every
endpoint must use it; a test enforces that none reach for `Depends(get_db)`
directly.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends
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


# Round I — THE dependency every endpoint must use:
#
#     async def endpoint(..., db: AsyncSession = DbDep) -> ...
#
# `scope="function"` is not a detail; it decides whether this app can lie to
# its users. FastAPI runs a `yield` dependency's teardown on one of two exit
# stacks (fastapi/routing.py):
#
#     async with AsyncExitStack() as request_stack:        # scope="request"
#         async with AsyncExitStack() as function_stack:   # scope="function"
#             response = await f(request)
#         #  ^ function_stack unwinds HERE — before anything is sent
#         await response(scope, receive, send)
#     #  ^ request_stack unwinds HERE — after the client already has the reply
#
# "request" is the DEFAULT, and it is where get_db's commit used to run: a
# commit that failed did so after 200 OK had already gone out. On 2026-09-01
# UAT imported 494 rows of master data, answered "success", rolled the whole
# transaction back and saved nothing. That day's trigger was a missing log
# partition (round H), but the shape is general — a unique violation, a
# deadlock, or a connection dropped at the wrong moment all produce the same
# "it succeeded, and nothing happened".
#
# With "function" the commit runs while the response can still be changed, so
# a failure becomes a 500 the caller can see and retry.
#
# Kept as a shared Depends INSTANCE rather than an Annotated alias so the
# parameter keeps its default. `db: DbSession` without one is a syntax error
# wherever `db` follows an argument that has a default — which is most
# endpoints here, and not a reason to reorder 124 signatures.
DbDep = Depends(get_db, scope="function")


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



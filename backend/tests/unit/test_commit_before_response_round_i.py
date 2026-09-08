"""The API must not answer 200 for a transaction that then rolls back (round I).

WHAT HAPPENED
On 2026-09-01 a user imported 494 rows of master data on UAT. The endpoint
did its work, returned a result, the browser showed "success" — and nothing
was saved. The commit failed (a log partition was missing, round H) and took
the whole transaction with it, but by then the 200 had already been sent.

WHY IT COULD
FastAPI runs a `yield` dependency's teardown on one of two exit stacks:

    async with AsyncExitStack() as request_stack:        # scope="request"
        async with AsyncExitStack() as function_stack:   # scope="function"
            response = await f(request)
        #  ^ function_stack unwinds here — before anything is sent
        await response(scope, receive, send)
    #  ^ request_stack unwinds here — after the client already has the reply

`scope="request"` is the default, so get_db's commit ran after the response
was on the wire. Nothing could change the status code any more.

The missing partition was one trigger. A unique violation, a deadlock, or a
connection dropped at the wrong moment all fail at exactly the same point, so
this is about the shape of the bug, not that one cause.

These tests dispatch real ASGI requests through the mounted app with a fake
session, and assert on the status code the client actually receives. No
database is touched.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import APIRouter
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as session_module
from app.db.session import DbDep
from app.main import app

# A route that exists only for these tests, mounted for the module's lifetime.
_probe = APIRouter()


@_probe.post("/__round_i_probe__")
async def _probe_endpoint(db: AsyncSession = DbDep) -> dict[str, str]:
    return {"status": "ok"}


@pytest.fixture(autouse=True)
def _mount_probe():
    app.include_router(_probe)
    yield
    app.router.routes = [
        r for r in app.router.routes
        if getattr(r, "path", None) != "/__round_i_probe__"
    ]


class _FakeSessionMaker:
    """Stands in for app.db.session._sessionmaker.

    Deliberately NOT a dependency_overrides entry for get_db: overriding it
    would replace the very commit/rollback logic under test. The real get_db
    runs; only the session it opens is fake.
    """

    def __init__(self, session):
        self._session = session

    def __call__(self):
        session = self._session

        class _Ctx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _use(session):
    return patch.object(session_module, "_sessionmaker", _FakeSessionMaker(session))


async def _post() -> httpx.Response:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/__round_i_probe__")


async def test_a_failing_commit_is_never_reported_as_success():
    """THE regression guard. Before round I this answered 200 — the response
    was already sent by the time the commit ran, so the status code was no
    longer the server's to decide."""
    session = AsyncMock(spec=AsyncSession)
    session.commit.side_effect = RuntimeError(
        'no partition of relation "activity_logs" found for row'
    )

    with _use(session):
        response = await _post()

    assert response.status_code == 500
    session.commit.assert_awaited_once()
    session.rollback.assert_awaited_once()


async def test_the_caller_is_told_their_work_was_discarded():
    """Round J — a bare "Internal Server Error" says nothing about whether to
    retry, or whether anything was saved."""
    session = AsyncMock(spec=AsyncSession)
    session.commit.side_effect = RuntimeError("deadlock detected")

    with _use(session):
        response = await _post()

    detail = response.json()["detail"]
    assert "ยกเลิกรายการนี้ทั้งหมด" in detail
    assert "กรุณาลองใหม่อีกครั้ง" in detail


async def test_the_database_error_never_reaches_the_client():
    """The cause belongs in the server's traceback, not in an API response."""
    session = AsyncMock(spec=AsyncSession)
    session.commit.side_effect = RuntimeError(
        'relation "activity_logs" does not exist at 10.0.0.5:5432'
    )

    with _use(session):
        response = await _post()

    body = response.text
    for leak in ("activity_logs", "10.0.0.5", "relation", "RuntimeError"):
        assert leak not in body


async def test_a_successful_commit_still_returns_the_result():
    session = AsyncMock(spec=AsyncSession)

    with _use(session):
        response = await _post()

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


async def test_the_commit_happens_before_the_client_gets_the_reply():
    """Pins the ordering the incident depended on: nothing reaches the client
    until the commit has run."""
    order: list[str] = []
    session = AsyncMock(spec=AsyncSession)

    async def _record():
        order.append("commit")

    session.commit.side_effect = _record

    with _use(session):
        response = await _post()
        order.append("client-received")

    assert order == ["commit", "client-received"]
    assert response.status_code == 200


def test_no_endpoint_reaches_for_the_unscoped_dependency():
    """A single `Depends(get_db)` left behind is an endpoint that still
    commits after its response — silently, exactly as before."""
    import pathlib

    app_dir = pathlib.Path(session_module.__file__).resolve().parents[1]
    offenders = [
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if path.name != "session.py" and "Depends(get_db)" in path.read_text("utf-8")
    ]

    assert offenders == []


def test_the_shared_dependency_is_function_scoped():
    assert DbDep.scope == "function"


async def test_an_endpoint_failure_is_not_dressed_up_as_a_commit_failure():
    """The two mean different things: the endpoint raising is its own error
    with its own status code, while a failed commit means work was accepted
    and then discarded. Only the second gets round J's message."""
    session = AsyncMock(spec=AsyncSession)

    @_probe.post("/__round_i_boom__")
    async def _boom(db: AsyncSession = DbDep) -> dict[str, str]:
        raise ValueError("endpoint's own problem")

    app.include_router(_probe)

    with _use(session), pytest.raises(ValueError, match="endpoint's own problem"):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await c.post("/__round_i_boom__")

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()

"""Round 56 — paging and the two exports, over real HTTP.

The unit tests read signatures and source; these dispatch requests through
the ASGI app, so they prove what FastAPI actually does with the query
string: that `offset` reaches the repository, that a bad page is refused
before any query runs, and that an export asks for every row and comes back
as a spreadsheet. Same dependency-override pattern as
test_executive_dashboard_http_round_42.py — nothing touches a database.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps.scope import get_rls_context
from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.main import app
from app.schemas.dashboard import (
    ConcernRow,
    ExecutiveConcerns,
    ExecutiveFollowUp,
    FollowUpRow,
)

KEY = "executive_dashboard.read"
BASE = "/api/v1/dashboard/executive"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_current_permissions: set[str] = set()


def _user() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        roles=[SimpleNamespace(name="internal:admin")],
        supplier_id=None,
        is_supplier_admin=False,
        _effective_permissions=set(_current_permissions),
        is_active=True,
    )


async def _fake_get_db() -> AsyncIterator[object]:
    db = MagicMock()
    db.add = MagicMock()          # the 403 path writes an activity-log row
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock()
    yield db


async def _noop_rls_context() -> None:
    return None


@pytest.fixture(autouse=True)
def _override_dependencies():
    global _current_permissions
    _current_permissions = {KEY}
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_rls_context] = _noop_rls_context
    yield
    app.dependency_overrides.clear()


def _concerns() -> ExecutiveConcerns:
    return ExecutiveConcerns(threshold_pct=70.0, total=23, rows=[
        ConcernRow(severity="high", kind="in_production_low", key="WM-141",
                   plots=4, value=59.3),
    ])


def _follow_up() -> ExecutiveFollowUp:
    return ExecutiveFollowUp(total=641, rows=[
        FollowUpRow(plot_id=uuid4(), plot_code="P-0001", plot_name="x",
                    supplier_code="SUP001", reason="never_inspected",
                    value=212.0, last_inspected=None),
    ])


async def _call(path: str, target: str, payload) -> tuple[httpx.Response, AsyncMock]:
    mock = AsyncMock(return_value=payload)
    with patch(f"app.repositories.dashboard_repository.{target}", mock):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            return await client.get(BASE + path), mock


class TestPaging:
    async def test_follow_up_forwards_the_offset(self) -> None:
        resp, mock = await _call("/follow-up?limit=10&offset=20",
                                 "get_executive_follow_up", _follow_up())
        assert resp.status_code == 200
        assert mock.await_args.kwargs["limit"] == 10
        assert mock.await_args.kwargs["offset"] == 20

    async def test_follow_up_starts_on_the_first_page(self) -> None:
        _resp, mock = await _call("/follow-up", "get_executive_follow_up",
                                  _follow_up())
        assert mock.await_args.kwargs["offset"] == 0
        assert mock.await_args.kwargs["limit"] == 10

    async def test_concerns_forward_limit_and_offset(self) -> None:
        resp, mock = await _call("/concerns?limit=10&offset=10",
                                 "get_concerns", _concerns())
        assert resp.status_code == 200
        assert mock.await_args.kwargs["limit"] == 10
        assert mock.await_args.kwargs["offset"] == 10

    async def test_concerns_without_a_limit_ask_for_everything(self) -> None:
        _resp, mock = await _call("/concerns", "get_concerns", _concerns())
        assert mock.await_args.kwargs["limit"] is None

    async def test_the_total_is_on_the_wire(self) -> None:
        resp, _ = await _call("/concerns?limit=10", "get_concerns", _concerns())
        assert resp.json()["total"] == 23
        resp, _ = await _call("/follow-up", "get_executive_follow_up", _follow_up())
        assert resp.json()["total"] == 641

    @pytest.mark.parametrize("path,target", [
        ("/follow-up?offset=-1", "get_executive_follow_up"),
        ("/concerns?offset=-1", "get_concerns"),
        ("/concerns?limit=0", "get_concerns"),
        ("/concerns?limit=201", "get_concerns"),
        ("/follow-up?limit=0", "get_executive_follow_up"),
    ])
    async def test_a_nonsense_page_is_refused_before_any_query(self, path, target) -> None:
        payload = _concerns() if target == "get_concerns" else _follow_up()
        resp, mock = await _call(path, target, payload)
        assert resp.status_code == 422
        mock.assert_not_awaited()


class TestExports:
    @pytest.mark.parametrize("path,target,payload,filename", [
        ("/concerns/export", "get_concerns", _concerns, "executive-concerns.xlsx"),
        ("/follow-up/export", "get_executive_follow_up", _follow_up,
         "executive-follow-up.xlsx"),
    ])
    async def test_it_downloads_every_row_as_a_spreadsheet(
        self, path, target, payload, filename,
    ) -> None:
        resp, mock = await _call(path, target, payload())
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(XLSX)
        assert filename in resp.headers["content-disposition"]
        assert resp.content[:2] == b"PK"
        assert mock.await_args.kwargs["limit"] is None

    async def test_the_concerns_file_uses_the_typed_threshold(self) -> None:
        _resp, mock = await _call("/concerns/export?threshold_pct=85",
                                  "get_concerns", _concerns())
        assert mock.await_args.kwargs["threshold_pct"] == 85

    async def test_the_concerns_file_uses_the_page_filters(self) -> None:
        sup = uuid4()
        _resp, mock = await _call(
            f"/concerns/export?range=6m&supplier_id={sup}&p_code=WM-141"
            "&cycle_status=active",
            "get_concerns", _concerns(),
        )
        kw = mock.await_args.kwargs
        assert kw["range_key"] == "6m"
        assert kw["supplier_id"] == sup
        assert kw["p_code"] == "WM-141"
        assert kw["cycle_status"] == "active"

    async def test_the_follow_up_file_uses_the_page_filters(self) -> None:
        sup = uuid4()
        _resp, mock = await _call(
            f"/follow-up/export?supplier_id={sup}&p_code=WM-141",
            "get_executive_follow_up", _follow_up(),
        )
        kw = mock.await_args.kwargs
        assert kw["supplier_id"] == sup
        assert kw["p_code"] == "WM-141"

    async def test_it_is_never_cached(self) -> None:
        resp, _ = await _call("/follow-up/export", "get_executive_follow_up",
                              _follow_up())
        assert "no-store" in resp.headers.get("cache-control", "")

    @pytest.mark.parametrize("path,target,payload", [
        ("/concerns/export", "get_concerns", _concerns),
        ("/follow-up/export", "get_executive_follow_up", _follow_up),
    ])
    @pytest.mark.parametrize("permissions", [set(), {"plots.read"}])
    async def test_without_the_key_there_is_no_file(
        self, path, target, payload, permissions,
    ) -> None:
        # plots.read is what supplier:owner holds — a file of every
        # supplier's plots must not reach them by another door.
        global _current_permissions
        _current_permissions = permissions
        resp, mock = await _call(path, target, payload())
        assert resp.status_code == 403
        assert resp.content[:2] != b"PK"
        mock.assert_not_awaited()

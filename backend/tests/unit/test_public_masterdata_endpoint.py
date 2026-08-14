"""GET /api/v1/public/masterdata — business logic (round 19.1).

No DB fixture exists in this repo — mocks the repository call and
exercises the real response-shaping logic directly.

Calls `.__wrapped__` to bypass the @limiter.limit slowapi decorator (which
expects a live Request bound to a real FastAPI app.state.limiter);
functools.wraps (used internally by slowapi) leaves the original
undecorated coroutine reachable there. Rate-limit wiring, the type
allowlist, and response-schema shape are checked separately at the source
level in tests/security/test_public_masterdata_wiring.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.api.v1.public_masterdata import list_public_master_data

_list = list_public_master_data.__wrapped__


def _item(**overrides):
    defaults = dict(value="พริก", parent=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_always_passes_active_only_true_regardless_of_caller() -> None:
    with patch(
        "app.api.v1.public_masterdata.repo.list_items", AsyncMock(return_value=[])
    ) as mocked:
        await _list(request=AsyncMock(), type="crop", parent=None, db=AsyncMock())

    assert mocked.await_args.kwargs["active_only"] is True


async def test_passes_the_parent_filter_through_to_the_repository() -> None:
    with patch(
        "app.api.v1.public_masterdata.repo.list_items", AsyncMock(return_value=[])
    ) as mocked:
        await _list(request=AsyncMock(), type="variety", parent="พริก", db=AsyncMock())

    assert mocked.await_args.kwargs["type"] == "variety"
    assert mocked.await_args.kwargs["parent"] == "พริก"


async def test_response_contains_only_value_and_parent() -> None:
    items = [_item(value="พริก", parent=None), _item(value="พริกขี้หนู", parent="พริก")]
    with patch("app.api.v1.public_masterdata.repo.list_items", AsyncMock(return_value=items)):
        result = await _list(request=AsyncMock(), type="crop", parent=None, db=AsyncMock())

    assert len(result) == 2
    for r in result:
        dumped = r.model_dump(by_alias=True)
        assert set(dumped.keys()) == {"value", "parent"}
    assert result[0].value == "พริก"
    assert result[1].value == "พริกขี้หนู"
    assert result[1].parent == "พริก"


async def test_preserves_repository_order() -> None:
    items = [_item(value="c"), _item(value="a"), _item(value="b")]
    with patch("app.api.v1.public_masterdata.repo.list_items", AsyncMock(return_value=items)):
        result = await _list(request=AsyncMock(), type="weather", parent=None, db=AsyncMock())

    assert [r.value for r in result] == ["c", "a", "b"]


async def test_empty_result_returns_empty_list_not_error() -> None:
    with patch("app.api.v1.public_masterdata.repo.list_items", AsyncMock(return_value=[])):
        result = await _list(request=AsyncMock(), type="growth_stage", parent=None, db=AsyncMock())

    assert result == []

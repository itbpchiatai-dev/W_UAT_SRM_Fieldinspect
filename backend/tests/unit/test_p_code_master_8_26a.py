"""P.Code Master Data — one ACTIVE P.Code per variety (round 8-26A).

Same DB-less style as test_masterdata_endpoint_duplicate.py: the route
functions and the service are called directly with a mocked repository.

What this locks in (the business rule confirmed with the user before the
round): P.Code is master_data type='p_code' with parent=<variety value>, and
a variety owns at most ONE ACTIVE one. Deactivated rows never block a new
one — that is the ONLY way to replace a variety's P.Code, and the old row
has to survive because cycles already carry its exact string inside their
Lot No.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.masterdata import create_master_data, update_master_data
from app.schemas.master_data import MasterDataCreate, MasterDataUpdate
from app.services import p_code_master

# NOTE: `app.api.v1.masterdata.repo` and `app.services.p_code_master.repo`
# are the SAME module object (both import master_data_repository), so
# patching "one of them" patches both — two nested patches of the same
# attribute silently collapse into whichever is innermost. Every test below
# therefore patches ONE path and, where the endpoint and the service both
# call get_by_type_value for different types, dispatches on the `type`
# argument via _lookup() instead of stacking mocks.
_API = "app.api.v1.masterdata.repo"
_SVC = "app.services.p_code_master.repo"


def _lookup(**by_type) -> AsyncMock:
    """get_by_type_value(db, type, value) mock that answers per `type` — the
    endpoint asks about 'p_code' (duplicate value check), the service asks
    about 'variety' (does the parent exist). Anything unlisted → None."""
    async def side_effect(_db, type_, _value):
        return by_type.get(type_)
    return AsyncMock(side_effect=side_effect)


def _item(**overrides) -> SimpleNamespace:
    base = dict(
        id=uuid4(), type="p_code", value="WM-111", parent="พริกขี้หนู", order_index=0,
        active=True, created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _variety(value: str = "พริกขี้หนู") -> SimpleNamespace:
    return _item(type="variety", value=value, parent="พริก")


# --- the rule itself ----------------------------------------------------


async def test_free_variety_has_no_errors() -> None:
    with patch(f"{_SVC}.get_by_type_value", AsyncMock(return_value=_variety())), \
         patch(f"{_SVC}.list_items", AsyncMock(return_value=[])):
        assert await p_code_master.p_code_assignment_errors(AsyncMock(), "พริกขี้หนู") == []


async def test_blank_parent_is_rejected_without_touching_the_db() -> None:
    """A P.Code with no variety has no meaning — and the check must short-
    circuit before any lookup, since there is nothing to look up."""
    for blank in (None, "", "   "):
        with patch(f"{_SVC}.get_by_type_value", AsyncMock()) as lookup, \
             patch(f"{_SVC}.list_items", AsyncMock()) as listed:
            errors = await p_code_master.p_code_assignment_errors(AsyncMock(), blank)
        assert errors == ["กรุณาระบุพันธุ์ที่ P.Code นี้สังกัด"]
        lookup.assert_not_awaited()
        listed.assert_not_awaited()


async def test_unknown_variety_is_rejected() -> None:
    with patch(f"{_SVC}.get_by_type_value", AsyncMock(return_value=None)), \
         patch(f"{_SVC}.list_items", AsyncMock(return_value=[])):
        errors = await p_code_master.p_code_assignment_errors(AsyncMock(), "ไม่มีพันธุ์นี้")
    assert len(errors) == 1
    assert "ไม่พบพันธุ์" in errors[0]
    assert "ไม่มีพันธุ์นี้" in errors[0]


async def test_variety_that_already_owns_an_active_p_code_is_rejected() -> None:
    with patch(f"{_SVC}.get_by_type_value", AsyncMock(return_value=_variety())), \
         patch(f"{_SVC}.list_items", AsyncMock(return_value=[_item(value="WM-111")])):
        errors = await p_code_master.p_code_assignment_errors(AsyncMock(), "พริกขี้หนู")
    assert len(errors) == 1
    assert "WM-111" in errors[0]
    assert "1 พันธุ์มีได้เพียง 1 P.Code" in errors[0]


async def test_only_active_rows_count_so_a_deactivated_p_code_can_be_replaced() -> None:
    """The whole replacement story: deactivate WM-111, then add WM-999 to the
    same variety. active_only=True is what makes that possible — the check
    must ASK the repository for active rows only, never filter afterwards."""
    list_items = AsyncMock(return_value=[])
    with patch(f"{_SVC}.get_by_type_value", AsyncMock(return_value=_variety())), \
         patch(f"{_SVC}.list_items", list_items):
        assert await p_code_master.p_code_assignment_errors(AsyncMock(), "พริกขี้หนู") == []
    assert list_items.await_args.kwargs["active_only"] is True
    assert list_items.await_args.kwargs["type"] == "p_code"
    assert list_items.await_args.kwargs["parent"] == "พริกขี้หนู"


async def test_exclude_id_lets_a_row_be_re_saved_against_itself() -> None:
    existing = _item(value="WM-111")
    with patch(f"{_SVC}.get_by_type_value", AsyncMock(return_value=_variety())), \
         patch(f"{_SVC}.list_items", AsyncMock(return_value=[existing])):
        errors = await p_code_master.p_code_assignment_errors(
            AsyncMock(), "พริกขี้หนู", exclude_id=existing.id,
        )
    assert errors == []


# --- create endpoint -----------------------------------------------------


async def test_create_p_code_for_a_free_variety_succeeds() -> None:
    created = _item(value="WM-999", parent="พริกจินดา")
    with patch(f"{_API}.get_by_type_value", _lookup(variety=_variety("พริกจินดา"))), \
         patch(f"{_SVC}.list_items", AsyncMock(return_value=[])), \
         patch(f"{_API}.create", AsyncMock(return_value=created)) as create_mock:
        result = await create_master_data(
            payload=MasterDataCreate(type="p_code", value="WM-999", parent="พริกจินดา"),
            db=AsyncMock(),
        )
    create_mock.assert_awaited_once()
    assert result.value == "WM-999"


async def test_create_second_p_code_for_the_same_variety_is_422_and_writes_nothing() -> None:
    with patch(f"{_API}.get_by_type_value", _lookup(variety=_variety())), \
         patch(f"{_SVC}.list_items", AsyncMock(return_value=[_item(value="WM-111")])), \
         patch(f"{_API}.create", AsyncMock()) as create_mock:
        with pytest.raises(HTTPException) as exc:
            await create_master_data(
                payload=MasterDataCreate(type="p_code", value="WM-999", parent="พริกขี้หนู"),
                db=AsyncMock(),
            )
    assert exc.value.status_code == 422
    assert "WM-111" in exc.value.detail
    create_mock.assert_not_awaited()


async def test_create_crop_or_variety_never_runs_the_p_code_rule() -> None:
    """The rule is scoped to type='p_code' alone — adding a second variety
    under one crop is completely normal and must stay untouched."""
    for type_ in ("crop", "variety", "province"):
        created = _item(type=type_, value="ใหม่")
        with patch(f"{_API}.get_by_type_value", AsyncMock(return_value=None)), \
             patch(f"{_SVC}.list_items", AsyncMock()) as listed, \
             patch(f"{_API}.create", AsyncMock(return_value=created)):
            await create_master_data(
                payload=MasterDataCreate(type=type_, value="ใหม่", parent="พริก"),
                db=AsyncMock(),
            )
        listed.assert_not_awaited()


# --- update endpoint -----------------------------------------------------


async def test_deactivating_a_p_code_is_always_allowed() -> None:
    """Turning one off is how a variety's P.Code gets replaced — it can never
    be blocked by the 'one active' rule, not even by itself."""
    item = _item(value="WM-111")
    with patch(f"{_API}.get", AsyncMock(return_value=item)), \
         patch(f"{_SVC}.list_items", AsyncMock()) as listed, \
         patch(f"{_API}.update", AsyncMock(return_value=_item(value="WM-111", active=False))):
        result = await update_master_data(
            item_id=item.id, payload=MasterDataUpdate(active=False), db=AsyncMock(),
        )
    listed.assert_not_awaited()
    assert result.active is False


async def test_reactivating_a_p_code_whose_variety_took_another_one_is_422() -> None:
    """WM-111 was deactivated and WM-999 took its place; switching WM-111 back
    on would leave the variety with two active P.Codes."""
    item = _item(value="WM-111", active=False)
    with patch(f"{_API}.get", AsyncMock(return_value=item)), \
         patch(f"{_API}.get_by_type_value", _lookup(variety=_variety())), \
         patch(f"{_SVC}.list_items", AsyncMock(return_value=[_item(value="WM-999")])), \
         patch(f"{_API}.update", AsyncMock()) as update_mock:
        with pytest.raises(HTTPException) as exc:
            await update_master_data(
                item_id=item.id, payload=MasterDataUpdate(active=True), db=AsyncMock(),
            )
    assert exc.value.status_code == 422
    assert "WM-999" in exc.value.detail
    update_mock.assert_not_awaited()


async def test_re_saving_an_active_p_code_unchanged_does_not_collide_with_itself() -> None:
    item = _item(value="WM-111")
    with patch(f"{_API}.get", AsyncMock(return_value=item)), \
         patch(f"{_API}.get_by_type_value", _lookup(variety=_variety())), \
         patch(f"{_SVC}.list_items", AsyncMock(return_value=[item])), \
         patch(f"{_API}.update", AsyncMock(return_value=_item(value="WM-111", order_index=3))):
        result = await update_master_data(
            item_id=item.id,
            payload=MasterDataUpdate(value="WM-111", parent="พริกขี้หนู", order_index=3),
            db=AsyncMock(),
        )
    assert result.order_index == 3


async def test_moving_an_active_p_code_to_an_occupied_variety_is_422() -> None:
    item = _item(value="WM-111", parent="พริกขี้หนู")
    with patch(f"{_API}.get", AsyncMock(return_value=item)), \
         patch(f"{_API}.get_by_type_value", _lookup(variety=_variety("พริกจินดา"))), \
         patch(f"{_SVC}.list_items", AsyncMock(return_value=[_item(value="WM-141", parent="พริกจินดา")])), \
         patch(f"{_API}.update", AsyncMock()) as update_mock:
        with pytest.raises(HTTPException) as exc:
            await update_master_data(
                item_id=item.id, payload=MasterDataUpdate(parent="พริกจินดา"), db=AsyncMock(),
            )
    assert exc.value.status_code == 422
    assert "WM-141" in exc.value.detail
    update_mock.assert_not_awaited()


async def test_omitted_parent_falls_back_to_the_stored_one_not_to_null() -> None:
    """An orderIndex-only PATCH must re-check against the row's OWN variety,
    never mistake the absent `parent` for a blank one and report
    'กรุณาระบุพันธุ์'."""
    item = _item(value="WM-111", parent="พริกขี้หนู")
    variety_lookup = _lookup(variety=_variety())
    with patch(f"{_API}.get", AsyncMock(return_value=item)), \
         patch(f"{_API}.get_by_type_value", variety_lookup), \
         patch(f"{_SVC}.list_items", AsyncMock(return_value=[item])), \
         patch(f"{_API}.update", AsyncMock(return_value=_item(order_index=9))):
        result = await update_master_data(
            item_id=item.id, payload=MasterDataUpdate(order_index=9), db=AsyncMock(),
        )
    assert variety_lookup.await_args.args[2] == "พริกขี้หนู"
    assert result.order_index == 9


async def test_explicitly_clearing_the_parent_of_an_active_p_code_is_422() -> None:
    """`parent: null` is a real value ("unassign"), distinct from an omitted
    key — an active P.Code that belongs to no variety can never be reached
    from the cycle form, so it is refused rather than silently orphaned."""
    item = _item(value="WM-111")
    with patch(f"{_API}.get", AsyncMock(return_value=item)), \
         patch(f"{_API}.get_by_type_value", AsyncMock(return_value=None)), \
         patch(f"{_API}.update", AsyncMock()) as update_mock:
        with pytest.raises(HTTPException) as exc:
            await update_master_data(
                item_id=item.id, payload=MasterDataUpdate(parent=None), db=AsyncMock(),
            )
    assert exc.value.status_code == 422
    assert "กรุณาระบุพันธุ์" in exc.value.detail
    update_mock.assert_not_awaited()


async def test_updating_a_crop_never_runs_the_p_code_rule() -> None:
    item = _item(type="crop", value="พริก", parent=None)
    with patch(f"{_API}.get", AsyncMock(return_value=item)), \
         patch(f"{_API}.get_by_type_value", AsyncMock(return_value=None)), \
         patch(f"{_SVC}.list_items", AsyncMock()) as listed, \
         patch(f"{_API}.update", AsyncMock(return_value=_item(type="crop", value="พริกหวาน"))):
        await update_master_data(
            item_id=item.id, payload=MasterDataUpdate(value="พริกหวาน"), db=AsyncMock(),
        )
    listed.assert_not_awaited()

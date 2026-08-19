"""Master Data create/update — duplicate (type, value) handling (round
8-22A). Calls the route functions directly with a mocked repository — same
DB-less style as test_master_data_crop_variety_import_endpoint.py and
test_supplier_import_endpoints.py. No live HTTP server, no real DB.

Root cause this locks in: master_data has UNIQUE(type, value) spanning both
active AND inactive rows (migration 0019, uq_master_data_type_value), but
create_master_data/update_master_data never caught the resulting
IntegrityError — a duplicate insert propagated as an unhandled 500 with no
explanation, which is what "เพิ่มชนิดพืชไม่สำเร็จ" looked like to a user
re-adding an existing (possibly deactivated) crop.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.v1.masterdata import create_master_data, update_master_data
from app.schemas.master_data import MasterDataCreate, MasterDataUpdate

_M = "app.api.v1.masterdata.repo"


def _item(**overrides) -> SimpleNamespace:
    base = dict(
        id=uuid4(), type="crop", value="ข้าวโพด", parent=None, order_index=0,
        active=True, created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --- create -----------------------------------------------------------

async def test_create_new_crop_succeeds_when_no_existing_row() -> None:
    created = _item(value="มะม่วง")
    with patch(f"{_M}.get_by_type_value", AsyncMock(return_value=None)), \
         patch(f"{_M}.create", AsyncMock(return_value=created)) as create_mock:
        result = await create_master_data(
            payload=MasterDataCreate(type="crop", value="มะม่วง"), db=AsyncMock(),
        )
    create_mock.assert_awaited_once()
    assert result.value == "มะม่วง"


async def test_create_with_order_index_zero_is_not_rejected() -> None:
    """orderIndex defaults to 0 and can repeat across rows (no uniqueness on
    it) — confirms the create path itself never treats it as a conflict."""
    for value in ("ข้าว", "ข้าวโพด"):
        created = _item(value=value, order_index=0)
        with patch(f"{_M}.get_by_type_value", AsyncMock(return_value=None)), \
             patch(f"{_M}.create", AsyncMock(return_value=created)):
            result = await create_master_data(
                payload=MasterDataCreate(type="crop", value=value, order_index=0), db=AsyncMock(),
            )
        assert result.order_index == 0


async def test_create_duplicate_active_value_returns_409_thai_message_no_insert_attempt() -> None:
    existing = _item(value="ข้าวโพด", active=True)
    with patch(f"{_M}.get_by_type_value", AsyncMock(return_value=existing)), \
         patch(f"{_M}.create", AsyncMock()) as create_mock:
        with pytest.raises(HTTPException) as exc:
            await create_master_data(
                payload=MasterDataCreate(type="crop", value="ข้าวโพด"), db=AsyncMock(),
            )
    assert exc.value.status_code == 409
    assert "ข้าวโพด" in exc.value.detail
    assert "อยู่แล้ว" in exc.value.detail
    create_mock.assert_not_awaited()


async def test_create_duplicate_inactive_value_returns_409_suggesting_reactivation() -> None:
    existing = _item(value="ข้าวโพด", active=False)
    with patch(f"{_M}.get_by_type_value", AsyncMock(return_value=existing)), \
         patch(f"{_M}.create", AsyncMock()) as create_mock:
        with pytest.raises(HTTPException) as exc:
            await create_master_data(
                payload=MasterDataCreate(type="crop", value="ข้าวโพด"), db=AsyncMock(),
            )
    assert exc.value.status_code == 409
    assert "ปิดใช้งาน" in exc.value.detail
    assert "เปิดใช้งาน" in exc.value.detail
    create_mock.assert_not_awaited()


async def test_create_integrity_error_race_maps_to_409_without_echoing_driver_message() -> None:
    """The pre-check missed a concurrent insert (TOCTOU) — the DB's unique
    index still catches it at flush time, and it must map to a clean 409,
    never a raw 500 with the driver's own message (which can carry the SQL
    and the offending value in a different form)."""
    err = IntegrityError(
        "INSERT INTO master_data ...", {},
        Exception('duplicate key value violates unique constraint "uq_master_data_type_value"'),
    )
    with patch(f"{_M}.get_by_type_value", AsyncMock(return_value=None)), \
         patch(f"{_M}.create", AsyncMock(side_effect=err)):
        with pytest.raises(HTTPException) as exc:
            await create_master_data(
                payload=MasterDataCreate(type="crop", value="ข้าวโพด"), db=AsyncMock(),
            )
    assert exc.value.status_code == 409
    assert "INSERT" not in str(exc.value.detail)
    assert "constraint" not in str(exc.value.detail)


# --- update -------------------------------------------------------------

async def test_update_unchanged_value_skips_the_duplicate_check() -> None:
    item = _item(value="ข้าวโพด")
    updated = _item(value="ข้าวโพด", order_index=5)
    with patch(f"{_M}.get", AsyncMock(return_value=item)), \
         patch(f"{_M}.get_by_type_value", AsyncMock()) as get_by_type_value_mock, \
         patch(f"{_M}.update", AsyncMock(return_value=updated)):
        result = await update_master_data(
            item_id=item.id, payload=MasterDataUpdate(order_index=5), db=AsyncMock(),
        )
    get_by_type_value_mock.assert_not_awaited()
    assert result.order_index == 5


async def test_update_duplicate_active_value_returns_409() -> None:
    item = _item(value="ข้าวโพด")
    other = _item(id=uuid4(), value="ข้าว", active=True)
    with patch(f"{_M}.get", AsyncMock(return_value=item)), \
         patch(f"{_M}.get_by_type_value", AsyncMock(return_value=other)), \
         patch(f"{_M}.update", AsyncMock()) as update_mock:
        with pytest.raises(HTTPException) as exc:
            await update_master_data(
                item_id=item.id, payload=MasterDataUpdate(value="ข้าว"), db=AsyncMock(),
            )
    assert exc.value.status_code == 409
    assert "ข้าว" in exc.value.detail
    update_mock.assert_not_awaited()


async def test_update_duplicate_inactive_value_returns_409_suggesting_reactivation() -> None:
    item = _item(value="ข้าวโพด")
    other = _item(id=uuid4(), value="ข้าว", active=False)
    with patch(f"{_M}.get", AsyncMock(return_value=item)), \
         patch(f"{_M}.get_by_type_value", AsyncMock(return_value=other)), \
         patch(f"{_M}.update", AsyncMock()) as update_mock:
        with pytest.raises(HTTPException) as exc:
            await update_master_data(
                item_id=item.id, payload=MasterDataUpdate(value="ข้าว"), db=AsyncMock(),
            )
    assert exc.value.status_code == 409
    assert "ปิดใช้งาน" in exc.value.detail
    assert "เปิดใช้งาน" in exc.value.detail
    update_mock.assert_not_awaited()


async def test_update_integrity_error_race_maps_to_409_without_echoing_driver_message() -> None:
    item = _item(value="ข้าวโพด")
    err = IntegrityError("UPDATE master_data ...", {}, Exception("duplicate key value"))
    with patch(f"{_M}.get", AsyncMock(return_value=item)), \
         patch(f"{_M}.get_by_type_value", AsyncMock(return_value=None)), \
         patch(f"{_M}.update", AsyncMock(side_effect=err)):
        with pytest.raises(HTTPException) as exc:
            await update_master_data(
                item_id=item.id, payload=MasterDataUpdate(value="ข้าว"), db=AsyncMock(),
            )
    assert exc.value.status_code == 409
    assert "UPDATE master_data" not in str(exc.value.detail)


async def test_update_missing_item_still_404s_before_any_duplicate_check() -> None:
    with patch(f"{_M}.get", AsyncMock(return_value=None)), \
         patch(f"{_M}.get_by_type_value", AsyncMock()) as get_by_type_value_mock:
        with pytest.raises(HTTPException) as exc:
            await update_master_data(
                item_id=uuid4(), payload=MasterDataUpdate(value="ข้าว"), db=AsyncMock(),
            )
    assert exc.value.status_code == 404
    get_by_type_value_mock.assert_not_awaited()

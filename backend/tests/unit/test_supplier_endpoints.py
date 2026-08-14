"""GET /api/v1/suppliers (list) and GET /api/v1/suppliers/{id} — scope
wiring at the endpoint layer. Mocks the repository so no DB is needed;
confirms the route passes the resolved scope conditions through to the
repo call, and that an out-of-scope supplier resolves to the same generic
404 as a truly-nonexistent one (doesn't leak that the row exists)."""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.suppliers import get_supplier, list_suppliers


def _fake_supplier(**overrides):
    now = datetime.datetime.now(datetime.timezone.utc)
    defaults = dict(
        id=uuid4(), code="SUP001", name="Supplier One", tax_id=None,
        contact_name=None, contact_email=None, contact_phone=None,
        address=None, is_active=True, inspection_code="1111",
        created_at=now, updated_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_list_suppliers_passes_scope_conditions_to_repo() -> None:
    sentinel_scope = ["SENTINEL_CONDITION"]
    with patch("app.api.v1.suppliers.repo.list_suppliers", AsyncMock(return_value=[])) as mocked:
        await list_suppliers(scope=sentinel_scope, db=AsyncMock())

    mocked.assert_awaited_once()
    _, kwargs = mocked.call_args
    assert kwargs["scope_conditions"] is sentinel_scope


async def test_get_supplier_uses_scoped_lookup() -> None:
    with patch("app.api.v1.suppliers.repo.get_supplier_scoped", AsyncMock(return_value=None)) as mocked:
        with pytest.raises(HTTPException):
            await get_supplier(supplier_id=uuid4(), scope=["SENTINEL"], db=AsyncMock())

    mocked.assert_awaited_once()
    args, _ = mocked.call_args
    assert args[-1] == ["SENTINEL"]


async def test_get_supplier_out_of_scope_returns_generic_404() -> None:
    """Same message whether the id truly doesn't exist or just isn't in the
    caller's scope — doesn't confirm/deny existence to an unauthorized caller."""
    with patch("app.api.v1.suppliers.repo.get_supplier_scoped", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await get_supplier(supplier_id=uuid4(), scope=[], db=AsyncMock())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Supplier not found"


async def test_get_supplier_in_scope_returns_it() -> None:
    fake = _fake_supplier()
    with patch("app.api.v1.suppliers.repo.get_supplier_scoped", AsyncMock(return_value=fake)):
        result = await get_supplier(supplier_id=fake.id, scope=[], db=AsyncMock())

    assert result.id == fake.id
    assert result.code == "SUP001"

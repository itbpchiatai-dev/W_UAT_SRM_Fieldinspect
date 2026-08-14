"""supplier_repository.create_supplier — inspection_code retirement
(round 8-3G).

Supersedes this file's former content, which tested default/custom/trim/
plaintext-storage behavior of Supplier.inspection_code — that column and
schema field are both dropped (migration 0040 / SupplierCreate). These
tests instead pin the retirement: the field can't be set even if a caller
tries, and create_supplier's own source never references it.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

from app.db.models.supplier import Supplier
from app.repositories.supplier_repository import create_supplier
from app.schemas.supplier import SupplierCreate, SupplierRead, SupplierSummary, SupplierUpdate


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


def test_supplier_create_schema_has_no_inspection_code_field() -> None:
    assert "inspection_code" not in SupplierCreate.model_fields


def test_supplier_update_schema_has_no_inspection_code_field() -> None:
    assert "inspection_code" not in SupplierUpdate.model_fields


def test_a_stray_inspection_code_in_create_payload_is_never_set() -> None:
    """SupplierCreate has no extra="forbid" (unchanged, pre-existing lenient
    behavior — see the round 8-3G report's Risks section) so an unknown key
    is silently ignored rather than 422ing, but it must never actually reach
    the Supplier model either way."""
    payload = SupplierCreate.model_validate(
        {"code": "SUP001", "name": "Supplier One", "inspectionCode": "9999"}
    )
    assert not hasattr(payload, "inspection_code")


async def test_create_supplier_never_references_inspection_code_in_source() -> None:
    src = inspect.getsource(create_supplier)
    assert "inspection_code" not in src


async def test_create_supplier_does_not_set_an_inspection_code_attribute() -> None:
    supplier = await create_supplier(
        _mock_db(), SupplierCreate(code="SUP001", name="Supplier One")
    )
    assert not hasattr(supplier, "inspection_code")


def test_supplier_model_has_no_inspection_code_column() -> None:
    assert "inspection_code" not in Supplier.__table__.c


def test_supplier_read_response_schema_has_no_inspection_code_field() -> None:
    assert "inspection_code" not in SupplierRead.model_fields


def test_supplier_summary_response_schema_has_no_inspection_code_field() -> None:
    assert "inspection_code" not in SupplierSummary.model_fields

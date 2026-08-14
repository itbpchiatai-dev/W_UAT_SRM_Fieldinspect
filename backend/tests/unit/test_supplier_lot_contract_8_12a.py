"""Round 8-12A — the supplierLotNo API/Excel contract, and the Auto Lot V2
Excel-side changes (template column, Thai description, preview formula, payload
echo, old-workbook compatibility).

Complements:
  - test_lot_number.py                 (the formatter itself)
  - test_plot_cycle_lot_resolution.py  (repository create/update behaviour)
  - test_supplier_lot_auto_lot_v2_migration.py (migration 0048)
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.v1.plots import (
    _EDITABLE_COLUMNS,
    _PLOT_TEMPLATE_HEADERS,
    _REFERENCE_COLUMNS,
    _template_example_rows,
)
from app.schemas.plot import PlotCycleCreate, PlotCycleRead, PlotCycleUpdate
from app.schemas.plot_import import PlotImportRowPayload
from app.services import plot_import
from app.services.excel_workbook import build_xlsx
from app.services.plot_import import (
    IMPORT_COLUMNS,
    ImportContext,
    build_preview,
)

_M = "app.services.plot_import"


# --- API schemas ------------------------------------------------------------

def test_create_accepts_and_trims_supplier_lot_no() -> None:
    payload = PlotCycleCreate(
        poNumber="po1", pCode="WM-141", cycleLabel="jun2026", supplierLotNo="  SUP-OWN-1  ",
    )
    assert payload.supplier_lot_no == "SUP-OWN-1"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_create_blank_supplier_lot_no_becomes_none(blank) -> None:
    payload = PlotCycleCreate(poNumber="po1", pCode="WM-141", cycleLabel="jun2026", supplierLotNo=blank)
    assert payload.supplier_lot_no is None


def test_create_supplier_lot_no_is_optional() -> None:
    """Unlike poNumber/pCode it never builds the lot, so requiring it would
    block a legitimate cycle for data the system does not need."""
    payload = PlotCycleCreate(poNumber="po1", pCode="WM-141", cycleLabel="jun2026")
    assert payload.supplier_lot_no is None


def test_supplier_lot_no_max_length_is_enforced() -> None:
    with pytest.raises(ValidationError):
        PlotCycleCreate(poNumber="po1", pCode="P", cycleLabel="jun2026", supplierLotNo="X" * 101)


def test_update_supplier_lot_no_round_trips_camel_case() -> None:
    payload = PlotCycleUpdate(supplierLotNo=" ABC-9 ")
    assert payload.supplier_lot_no == "ABC-9"
    dumped = payload.model_dump(by_alias=True, exclude_unset=True)
    assert dumped == {"supplierLotNo": "ABC-9"}


def test_update_omitting_supplier_lot_no_keeps_it_out_of_exclude_unset() -> None:
    """ABSENT must mean "leave it alone" — the repository only writes the field
    when the key is present."""
    payload = PlotCycleUpdate(crop="พริก")
    assert "supplier_lot_no" not in payload.model_dump(exclude_unset=True)


def test_update_explicit_null_is_present_in_exclude_unset() -> None:
    """Explicit null must reach the repository so it can CLEAR the value."""
    payload = PlotCycleUpdate(supplierLotNo=None)
    assert "supplier_lot_no" in payload.model_dump(exclude_unset=True)


def test_read_model_exposes_supplier_lot_no_camel_case() -> None:
    read = PlotCycleRead(
        id=uuid4(), plotId=uuid4(), cycleNo=1, status="active",
        crop=None, variety=None, cycleLabel="2605", lotNo="2605-SUP010-WM-141-001",
        supplierLotNo="SUP-OWN-1",
        plantingDate=None, plantCount=None,
        expectedYieldFull=None, expectedYieldUnit=None,
        startedAt=datetime.datetime.now(datetime.timezone.utc),
        closedAt=None, closedById=None, closeReason=None,
        createdAt=datetime.datetime.now(datetime.timezone.utc),
        updatedAt=datetime.datetime.now(datetime.timezone.utc),
    )
    assert read.model_dump(by_alias=True)["supplierLotNo"] == "SUP-OWN-1"


def test_internal_series_key_is_never_client_writable_or_readable() -> None:
    """auto_lot_series_key is server bookkeeping: it must not appear in any
    request OR response schema."""
    for model in (PlotCycleCreate, PlotCycleUpdate, PlotCycleRead):
        assert "auto_lot_series_key" not in model.model_fields
        assert "autoLotSeriesKey" not in {
            f.alias for f in model.model_fields.values() if f.alias
        }


def test_sending_the_internal_field_is_ignored_not_stored() -> None:
    payload = PlotCycleUpdate.model_validate({"autoLotSeriesKey": "hack"})
    assert "auto_lot_series_key" not in payload.model_dump(exclude_unset=True)


def test_po_and_lot_fields_are_all_still_present() -> None:
    """V2 changes the formula, not the schema surface — nothing is removed."""
    for field in ("po_number", "p_code", "lot_no", "lot_no_source", "lot_running_no"):
        assert field in PlotCycleRead.model_fields


# --- Excel template contract ------------------------------------------------

def test_supplier_lot_no_column_sits_between_lot_no_and_planting_date() -> None:
    cols = IMPORT_COLUMNS
    assert cols.index("lotNo") + 1 == cols.index("supplierLotNo")
    # Round 8-21A — oracleSupplierCode/oracleInvoice/refAccount now sit
    # between supplierLotNo and plantingDate; supplierLotNo itself still
    # immediately follows lotNo (unchanged by this round).
    assert cols.index("supplierLotNo") + 1 == cols.index("oracleSupplierCode")
    assert cols.index("oracleSupplierCode") + 1 == cols.index("oracleInvoice")
    assert cols.index("oracleInvoice") + 1 == cols.index("refAccount")
    assert cols.index("refAccount") + 1 == cols.index("plantingDate")


def test_template_headers_match_the_import_columns() -> None:
    assert _PLOT_TEMPLATE_HEADERS == IMPORT_COLUMNS
    assert "supplierLotNo" in _PLOT_TEMPLATE_HEADERS


def test_supplier_lot_no_is_editable_not_reference_only() -> None:
    assert "supplierLotNo" in _EDITABLE_COLUMNS
    assert "supplierLotNo" not in _REFERENCE_COLUMNS


def test_row_two_description_explains_it_is_not_the_system_lot() -> None:
    desc = plot_import.TEMPLATE_COLUMN_DESCRIPTIONS["supplierLotNo"]
    assert "Supplier" in desc
    assert "Auto Lot" in desc          # says it is unrelated to the system lot
    assert "ไม่เกี่ยวกับ" in desc


def test_lot_no_description_now_describes_the_v2_formula() -> None:
    desc = plot_import.TEMPLATE_COLUMN_DESCRIPTIONS["lotNo"]
    assert "ชื่อรอบปลูก" in desc and "P.Code" in desc
    assert "plotCode" not in desc      # V1 wording is gone


def test_example_rows_show_a_supplier_lot_no() -> None:
    rows = _template_example_rows("SUP001")
    values = [r.get("supplierLotNo") for r in rows]
    assert any(v for v in values), "at least one example must show a value"
    assert any(v is None for v in values), "and one must show the blank case"


# --- Excel parse / preview --------------------------------------------------

def _xlsx(rows, columns=None):
    cols = list(columns or IMPORT_COLUMNS)
    data = [cols] + [[r.get(c) for c in cols] for r in rows]
    return build_xlsx([("plots", data)])


def _ctx() -> ImportContext:
    return ImportContext(allowed_supplier_id=None, can_create=True, can_update=True)


def _supplier(code="SUP010"):
    return SimpleNamespace(id=uuid4(), code=code, is_active=True)


def _row(**over):
    base = {
        "action": "create_plot_with_cycle", "supplierCode": "SUP010",
        "plotCode": "P101", "plotName": "แปลงใหม่", "province": "เชียงใหม่",
        "cycleLabel": "2605", "poNumber": "PO25001", "pCode": "WM-141",
        "plantingDate": "2026-06-01",
    }
    base.update(over)
    return base


async def _preview(rows, columns=None):
    with patch(f"{_M}.supplier_repo.get_supplier_by_code",
               AsyncMock(return_value=_supplier())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot",
               AsyncMock(return_value=None)):
        return await build_preview(AsyncMock(), _xlsx(rows, columns), ctx=_ctx())


async def test_preview_echoes_supplier_lot_no_trimmed() -> None:
    pv = await _preview([_row(supplierLotNo="  SUP-OWN-5  ")])
    assert pv.error_rows == 0
    assert pv.rows[0].payload.supplier_lot_no == "SUP-OWN-5"


async def test_preview_blank_supplier_lot_no_is_null() -> None:
    pv = await _preview([_row(supplierLotNo="   ")])
    assert pv.rows[0].payload.supplier_lot_no is None


async def test_preview_shows_the_v2_auto_lot_formula() -> None:
    pv = await _preview([_row(lotNo=None)])
    row = pv.rows[0]
    assert row.lot_mode == "auto"
    assert row.proposed_lot_no == "2605-SUP010-WM-141-###"


async def test_preview_auto_lot_uses_the_authoritative_supplier_code() -> None:
    """The supplierCode CELL is only a lookup key. Even if a file names one
    supplier in the cell, the lot must use the RESOLVED supplier's code."""
    pv = await _preview([_row(supplierCode="SUP010", lotNo=None)])
    # _supplier() resolves to SUP010 regardless of what the cell said
    assert pv.rows[0].proposed_lot_no.startswith("2605-SUP010-")


async def test_preview_never_shows_the_v1_formula() -> None:
    pv = await _preview([_row(lotNo=None)])
    proposed = pv.rows[0].proposed_lot_no
    assert "-XX" not in proposed
    assert "PO25001" not in proposed


async def test_manual_lot_still_wins_over_auto_in_preview() -> None:
    pv = await _preview([_row(lotNo="HAND-7", supplierLotNo="SUP-OWN-9")])
    row = pv.rows[0]
    assert row.lot_mode == "manual"
    assert row.proposed_lot_no == "HAND-7"
    # a supplier lot number never flips the Manual/Auto decision
    assert row.payload.supplier_lot_no == "SUP-OWN-9"


async def test_supplier_lot_no_does_not_change_the_auto_decision() -> None:
    with_slot = await _preview([_row(lotNo=None, supplierLotNo="SUP-OWN-9")])
    without = await _preview([_row(lotNo=None)])
    assert with_slot.rows[0].lot_mode == without.rows[0].lot_mode == "auto"
    assert with_slot.rows[0].proposed_lot_no == without.rows[0].proposed_lot_no


# --- old workbook compatibility --------------------------------------------

_OLD_COLUMNS = [c for c in IMPORT_COLUMNS if c != "supplierLotNo"]


async def test_a_workbook_without_the_column_still_imports() -> None:
    """The reader maps by header NAME, so a pre-8-12A file simply has no such
    key — it must parse exactly as before, with supplierLotNo null."""
    pv = await _preview([_row()], columns=_OLD_COLUMNS)
    assert pv.error_rows == 0
    assert pv.rows[0].payload.supplier_lot_no is None


async def test_an_old_workbook_still_gets_a_v2_auto_lot() -> None:
    pv = await _preview([_row(lotNo=None)], columns=_OLD_COLUMNS)
    assert pv.rows[0].proposed_lot_no == "2605-SUP010-WM-141-###"


def test_payload_model_defaults_supplier_lot_no_for_older_clients() -> None:
    payload = PlotImportRowPayload()
    assert payload.supplier_lot_no is None


# --- password columns are untouched by this round --------------------------

async def test_inspection_password_columns_are_unaffected() -> None:
    pv = await _preview([_row(newInspectionPassword="1357")])
    row = pv.rows[0]
    assert row.inspection_password_change == "set"
    # the password itself never travels back out
    assert "1357" not in str(row.model_dump())

"""Round 8-10B — the final_plot Excel contract after finalYieldUnit and
finalInspectionRecordId were removed.

Round 8-7A pre-filled an ACTIVE plot's Sheet 1 row with its active cycle's
latest record id so the same row could be repurposed for final_plot. Round
8-10B removed that column (and the unit column beside it): the figures are
always kilograms and the importer resolves the record itself, so neither was
ever a decision a user should have had to make. This file is what stops either
column reappearing in any template variant.

plot_cycle_repository.get_latest_active_records_for_cycles keeps its own tests
at the bottom: it is a general read helper and still behaves exactly as it did,
it simply has no template caller any more.

No real DB: workbook builders take plain Plot/PlotCycle-shaped SimpleNamespace
fixtures (same style as test_plot_import_template_status_aware.py).
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.api.v1.plots import (
    _EDITABLE_COLUMNS,
    _PLOT_TEMPLATE_HEADERS,
    _REFERENCE_COLUMNS,
    _contextual_plot_template_workbook,
    _new_cycle_row_values,
    _new_cycle_sheet,
    _plot_template_workbook,
    _template_example_rows,
)
from app.repositories.plot_cycle_repository import get_latest_active_records_for_cycles
from app.services import plot_import
from app.services.excel_reader import read_first_sheet

REMOVED_COLUMNS = ("finalYieldUnit", "finalInspectionRecordId")


def _record(**over) -> SimpleNamespace:
    base = dict(id=uuid4(), plot_id=uuid4(), plot_cycle_id=uuid4(), is_active=True)
    base.update(over)
    return SimpleNamespace(**base)


def _supplier(code: str = "SUP001") -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), code=code, name="Supplier One", is_active=True)


def _cycle(**over) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=1, status="active", cycle_label="jun2026",
        crop="พริก", variety="พริกขี้หนู", po_number="PO25001", p_code="Melon-A",
        supplier_lot_no=None,
        oracle_supplier_code=None, oracle_invoice=None, ref_account=None,
        lot_no="LOT-01", planting_date=None, plant_count=1000,
        expected_yield_full=Decimal("800"), expected_yield_unit="kg",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _plot(**over) -> SimpleNamespace:
    base = dict(
        id=uuid4(), plot_code="P001", name="แปลงหนึ่ง", is_active=True,
        province="เชียงใหม่", village=None, district=None,
        latitude=None, longitude=None, rai=None,
        supplier=_supplier(), active_cycle=_cycle(), cycles=[_cycle()],
        access_phones=[],
    )
    base.update(over)
    return SimpleNamespace(**base)


# --- items 1/2/3: the contract itself ---------------------------------------

def test_the_import_contract_no_longer_has_either_column():
    for column in REMOVED_COLUMNS:
        assert column not in plot_import.IMPORT_COLUMNS
        assert column not in plot_import.TEMPLATE_COLUMN_DESCRIPTIONS
        assert column not in _PLOT_TEMPLATE_HEADERS
        assert column not in _EDITABLE_COLUMNS
        assert column not in _REFERENCE_COLUMNS


def test_the_remaining_columns_kept_their_order():
    """Only two entries were dropped — nothing else moved, so a user's existing
    mental model of the sheet still holds."""
    assert _PLOT_TEMPLATE_HEADERS == plot_import.IMPORT_COLUMNS
    tail = plot_import.IMPORT_COLUMNS[
        plot_import.IMPORT_COLUMNS.index("currentPlotStatus"):
    ]
    assert tail == [
        "currentPlotStatus", "harvestYield", "finalYieldAfterClean",
        "harvestDate", "finalNote",
        "inspectionPasswordStatus", "newInspectionPassword",
    ]


def test_the_two_kg_columns_say_so_in_their_description():
    """The unit moved out of a column and into the guidance row — a user
    reading the sheet still learns what the number means."""
    for column in ("harvestYield", "finalYieldAfterClean"):
        assert "กิโลกรัม (kg)" in plot_import.TEMPLATE_COLUMN_DESCRIPTIONS[column]
        assert "final_plot" in plot_import.TEMPLATE_COLUMN_DESCRIPTIONS[column]


# --- items 4/6/7: every template variant ------------------------------------

def test_the_generic_template_has_neither_column():
    headers, rows = read_first_sheet(_plot_template_workbook([_supplier()]))
    for column in REMOVED_COLUMNS:
        assert column not in headers
    # the Thai guidance row describes exactly the columns that exist
    description_row = rows[0][1]
    assert set(description_row) == set(headers)


def test_the_filtered_template_has_neither_column():
    headers, _rows = read_first_sheet(_contextual_plot_template_workbook([_plot()]))
    for column in REMOVED_COLUMNS:
        assert column not in headers


def test_the_all_suppliers_template_has_neither_column():
    plots = [_plot(plot_code="P001"), _plot(plot_code="P002", supplier=_supplier("SUP002"))]
    headers, _rows = read_first_sheet(_contextual_plot_template_workbook(plots))
    for column in REMOVED_COLUMNS:
        assert column not in headers


def test_the_final_plot_example_row_has_neither_column():
    example = next(
        r for r in _template_example_rows("SUP001") if r["action"] == "final_plot"
    )
    for column in REMOVED_COLUMNS:
        assert column not in example
    # ...and still shows the figures it does need
    assert example["harvestYield"] == "1250"
    assert example["finalYieldAfterClean"] == "1180"


def test_a_downloaded_row_no_longer_carries_a_record_id():
    """The row a user downloads and edits must not contain a record id at all —
    there is nowhere left to put one."""
    values = _new_cycle_row_values(_plot())
    for column in REMOVED_COLUMNS:
        assert column not in values


def test_building_sheet_one_needs_no_record_lookup():
    """_new_cycle_sheet used to take a latest_active_records map purely to fill
    the retired column; the template download no longer queries for it."""
    rows = _new_cycle_sheet([_plot()])
    assert len(rows) >= 3   # header + description + one plot
    import inspect as _inspect
    assert "latest_active_records" not in _inspect.signature(_new_cycle_sheet).parameters


# --- item 11: header/row alignment stays intact -----------------------------

def test_every_data_row_still_lines_up_with_the_header():
    content = _contextual_plot_template_workbook([_plot(plot_code="P001")])
    headers, rows = read_first_sheet(content)
    assert len(headers) == len(plot_import.IMPORT_COLUMNS)
    for _row_no, values in rows:
        # read_first_sheet keys by header name — an off-by-one would surface as
        # an unexpected key here.
        assert set(values).issubset(set(headers))


# --- plot_cycle_repository.get_latest_active_records_for_cycles -------------
# Unchanged behaviour, kept: it is a general read helper. Round 8-10B simply
# removed its template caller.

async def test_get_latest_active_records_for_cycles_issues_exactly_one_query():
    db = AsyncMock()
    result = AsyncMock()
    result.scalars = lambda: SimpleNamespace(all=lambda: [])
    db.execute.return_value = result
    await get_latest_active_records_for_cycles(db, [uuid4(), uuid4(), uuid4()])
    db.execute.assert_awaited_once()


async def test_get_latest_active_records_for_cycles_empty_list_never_queries():
    db = AsyncMock()
    out = await get_latest_active_records_for_cycles(db, [])
    assert out == {}
    db.execute.assert_not_awaited()


async def test_get_latest_active_records_for_cycles_keyed_by_cycle_id():
    cycle_a, cycle_b = uuid4(), uuid4()
    record_a = _record(plot_cycle_id=cycle_a)
    record_b = _record(plot_cycle_id=cycle_b)
    db = AsyncMock()
    result = AsyncMock()
    result.scalars = lambda: SimpleNamespace(all=lambda: [record_a, record_b])
    db.execute.return_value = result
    out = await get_latest_active_records_for_cycles(db, [cycle_a, cycle_b])
    assert out == {cycle_a: record_a, cycle_b: record_b}


async def test_get_latest_active_records_for_cycles_cycle_with_no_record_is_absent():
    cycle_a = uuid4()
    db = AsyncMock()
    result = AsyncMock()
    result.scalars = lambda: SimpleNamespace(all=lambda: [])
    db.execute.return_value = result
    out = await get_latest_active_records_for_cycles(db, [cycle_a])
    assert out == {}
    assert out.get(cycle_a) is None

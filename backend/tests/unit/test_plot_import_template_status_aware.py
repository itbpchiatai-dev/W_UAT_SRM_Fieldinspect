"""Round 8-6J — status-aware Excel template: an INACTIVE plot gets a Sheet 1
row too (action=reactivate_plot_with_cycle, seeded from its latest historical
cycle) instead of always being excluded; a new informational
`currentPlotStatus` column; the excluded sheet's reason becomes plotStatus-
aware. No real DB: workbook builders take plain Plot/PlotCycle-shaped
SimpleNamespace fixtures (same style as test_plot_import_template_
contextual.py).
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.api.v1.plots import (
    _CURRENT_PLOT_STATUS_ACTIVE_LABEL,
    _CURRENT_PLOT_STATUS_INACTIVE_LABEL,
    _EDITABLE_COLUMNS,
    _EXCLUSION_REASON_STATUS_FILTER_ACTIVE,
    _EXCLUSION_REASON_STATUS_FILTER_INACTIVE,
    _PLOT_TEMPLATE_HEADERS,
    _STYLE_EDITABLE,
    _STYLE_REFERENCE,
    _contextual_plot_template_workbook,
    _excluded_row_values,
    _new_cycle_sheet,
    _reactivate_row_values,
)
from app.repositories.plot_cycle_repository import get_latest_cycles_for_plots
from app.services import plot_import
from app.services.excel_reader import read_first_sheet
from app.services.excel_workbook import StyledCell


def _supplier(code: str = "SUP001", name: str = "Supplier One", is_active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(code=code, name=name, is_active=is_active)


def _cycle(**over) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=2, status="harvested", cycle_label="closed2025",
        crop="ข้าวโพด", variety="สายพันธุ์เก่า",
        po_number="PO24009", p_code="Corn-Z",
        supplier_lot_no=None,
        oracle_supplier_code=None, oracle_invoice=None, ref_account=None,
        lot_no="PO24009-P002-01", planting_date=datetime.date(2025, 6, 1), plant_count=400,
        expected_yield_full=Decimal("900"), expected_yield_unit="kg",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _plot(**over) -> SimpleNamespace:
    base = dict(
        id=uuid4(), plot_code="P002", name="แปลงสอง", is_active=False,
        village="ต.สอง", district="อ.สอง", province="เชียงใหม่",
        latitude=Decimal("18.1"), longitude=Decimal("98.1"), rai=Decimal("3"),
        supplier=_supplier(), active_cycle=None,
        access_phones=[], cycles=[],
    )
    base.update(over)
    return SimpleNamespace(**base)


def _unzip_rows(plots, latest_cycles=None):
    content = _contextual_plot_template_workbook(plots, latest_cycles=latest_cycles)
    headers, rows = read_first_sheet(content)
    return headers, {n: v for n, v in rows if n > 2}


# --- item 1/2/3: active -> start_next_cycle, inactive -> reactivate_plot_with_cycle, mixed -----

def test_active_plot_row_uses_start_next_cycle_action():
    _headers, by_no = _unzip_rows([_plot(plot_code="P001", is_active=True, active_cycle=_cycle(status="active"))])
    row = next(iter(by_no.values()))
    assert row["action"] == plot_import.ACTION_START_NEXT
    assert row["currentPlotStatus"] == _CURRENT_PLOT_STATUS_ACTIVE_LABEL


def test_inactive_plot_row_uses_reactivate_plot_with_cycle_action():
    _headers, by_no = _unzip_rows([_plot(plot_code="P002", is_active=False)])
    row = next(iter(by_no.values()))
    assert row["action"] == plot_import.ACTION_REACTIVATE_WITH_CYCLE
    assert row["currentPlotStatus"] == _CURRENT_PLOT_STATUS_INACTIVE_LABEL


def test_mixed_active_and_inactive_plots_each_get_the_right_action():
    plots = [
        _plot(plot_code="P001", is_active=True, active_cycle=_cycle(status="active")),
        _plot(plot_code="P002", is_active=False),
    ]
    _headers, by_no = _unzip_rows(plots)
    by_code = {v["plotCode"]: v for v in by_no.values()}
    assert by_code["P001"]["action"] == plot_import.ACTION_START_NEXT
    assert by_code["P002"]["action"] == plot_import.ACTION_REACTIVATE_WITH_CYCLE


# --- item 7: inactive plot's row is seeded from its latest historical cycle -

def test_reactivate_row_copies_latest_historical_cycle_fields():
    plot = _plot(is_active=False)
    latest = _cycle(
        crop="ทุเรียน", variety="หมอนทอง", cycle_label="closed2025",
        po_number="PO24009", p_code="Durian-Z", lot_no="OLD-LOT-09",
        planting_date=datetime.date(2025, 3, 1), plant_count=300,
        expected_yield_full=Decimal("777"), expected_yield_unit="ตัน",
    )
    values = _reactivate_row_values(plot, latest)
    assert values["action"] == plot_import.ACTION_REACTIVATE_WITH_CYCLE
    assert values["crop"] == "ทุเรียน"
    assert values["variety"] == "หมอนทอง"
    assert values["cycleLabel"] == "closed2025"
    assert values["poNumber"] == "PO24009"
    assert values["pCode"] == "Durian-Z"
    assert values["lotNo"] == "OLD-LOT-09"
    assert values["plantingDate"] == "2025-03-01"
    assert values["plantCount"] == "300"
    assert values["expectedYieldFull"] == "777"
    assert values["expectedYieldUnit"] == "ตัน"


# --- item 9: inactive plot with NO cycle history at all -> still a row, blank fields ----

def test_reactivate_row_with_no_cycle_history_is_blank_never_invented():
    plot = _plot(is_active=False)
    values = _reactivate_row_values(plot, None)
    assert values["action"] == plot_import.ACTION_REACTIVATE_WITH_CYCLE
    for field in ("crop", "variety", "cycleLabel", "poNumber", "pCode", "lotNo",
                  "plantingDate", "plantCount", "expectedYieldFull", "expectedYieldUnit"):
        assert values[field] is None, field


def test_workbook_still_includes_a_row_for_inactive_plot_with_no_history():
    _headers, by_no = _unzip_rows([_plot(plot_code="P999", is_active=False, cycles=[])])
    assert len(by_no) == 1
    row = next(iter(by_no.values()))
    assert row["action"] == plot_import.ACTION_REACTIVATE_WITH_CYCLE
    assert row.get("crop") is None


# --- item 8: batch latest-cycle loader is ONE query, not N+1 ----------------

async def test_get_latest_cycles_for_plots_issues_exactly_one_query():
    db = AsyncMock()
    result = AsyncMock()
    result.scalars = lambda: SimpleNamespace(all=lambda: [])
    db.execute.return_value = result
    await get_latest_cycles_for_plots(db, [uuid4(), uuid4(), uuid4()])
    db.execute.assert_awaited_once()


async def test_get_latest_cycles_for_plots_empty_list_never_queries():
    db = AsyncMock()
    result = await get_latest_cycles_for_plots(db, [])
    assert result == {}
    db.execute.assert_not_awaited()


async def test_get_latest_cycles_for_plots_keyed_by_plot_id():
    plot_a, plot_b = uuid4(), uuid4()
    cycle_a = SimpleNamespace(plot_id=plot_a, cycle_no=3)
    cycle_b = SimpleNamespace(plot_id=plot_b, cycle_no=1)
    db = AsyncMock()
    result = AsyncMock()
    result.scalars = lambda: SimpleNamespace(all=lambda: [cycle_a, cycle_b])
    db.execute.return_value = result
    out = await get_latest_cycles_for_plots(db, [plot_a, plot_b])
    assert out == {plot_a: cycle_a, plot_b: cycle_b}


# --- item 10: currentPlotStatus is a real column, has a description --------

def test_current_plot_status_is_in_import_columns_and_has_a_description():
    assert "currentPlotStatus" in plot_import.IMPORT_COLUMNS
    desc = plot_import.TEMPLATE_COLUMN_DESCRIPTIONS["currentPlotStatus"]
    assert "อ้างอิงเท่านั้น" in desc
    assert "ไม่ทำให้สถานะแปลงเปลี่ยน" in desc


def test_current_plot_status_is_a_reference_not_editable_column():
    assert "currentPlotStatus" in _PLOT_TEMPLATE_HEADERS
    assert "currentPlotStatus" not in _EDITABLE_COLUMNS


def test_workbook_sheet_one_reactivate_row_current_plot_status_cell_is_reference_style():
    rows = _new_cycle_sheet([_plot(is_active=False)])
    data_row = rows[2]
    idx = _PLOT_TEMPLATE_HEADERS.index("currentPlotStatus")
    cell = data_row[idx]
    assert isinstance(cell, StyledCell)
    assert cell.style == _STYLE_REFERENCE
    assert cell.value == _CURRENT_PLOT_STATUS_INACTIVE_LABEL


# --- item 11: editing currentPlotStatus in an uploaded file has zero effect -

async def test_editing_current_plot_status_cell_never_changes_the_row_action():
    """The importer never reads currentPlotStatus at all — a row whose
    currentPlotStatus cell CONTRADICTS the plot's real DB state (simulating a
    user editing that cell) still executes purely based on `action` +
    the plot's real is_active, never the cell's text."""
    from unittest.mock import patch

    from app.services.plot_import import ImportContext, build_preview
    from app.services.excel_workbook import build_xlsx

    row = {col: None for col in plot_import.IMPORT_COLUMNS}
    row.update({
        "action": plot_import.ACTION_REACTIVATE_WITH_CYCLE,
        "supplierCode": "SUP001", "plotCode": "P002",
        "cycleLabel": "aug2026", "poNumber": "PO1", "pCode": "PC1",
        # Lies about the plot being active — must be completely ignored.
        "currentPlotStatus": _CURRENT_PLOT_STATUS_ACTIVE_LABEL,
    })
    content = build_xlsx([("plots", [list(plot_import.IMPORT_COLUMNS), list(row.get(c) for c in plot_import.IMPORT_COLUMNS)])])
    ctx = ImportContext(allowed_supplier_id=None, can_create=True, can_update=True, can_reactivate=True)

    fake_supplier = SimpleNamespace(id=uuid4(), code="SUP001", is_active=True)
    fake_plot = SimpleNamespace(id=uuid4(), is_active=False)  # real DB truth: inactive
    _M = "app.services.plot_import"
    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=fake_supplier)), \
         patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=fake_plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_cycle_repo.get_cycle_labels_for_plots", AsyncMock(return_value={})):
        preview = await build_preview(AsyncMock(), content, ctx=ctx)
    assert preview.error_rows == 0
    assert preview.rows[0].action == plot_import.ACTION_REACTIVATE_WITH_CYCLE


# --- item 17: blank cells in a reactivate row still carry their style ------

def test_reactivate_row_blank_editable_columns_still_carry_yellow_style():
    rows = _new_cycle_sheet([_plot(is_active=False)])  # no history at all -> every editable col blank
    data_row = rows[2]
    for col, cell in zip(_PLOT_TEMPLATE_HEADERS, data_row, strict=True):
        assert isinstance(cell, StyledCell)
        if col in _EDITABLE_COLUMNS:
            assert cell.style == _STYLE_EDITABLE, col
        else:
            assert cell.style == _STYLE_REFERENCE, col


# --- item 18: excluded sheet reports the RIGHT status-filter reason --------

def test_excluded_row_reason_when_plot_status_active_excludes_inactive_plot():
    plot = _plot(is_active=False)
    values = _excluded_row_values(plot, "active")
    assert values["exclusionReason"] == _EXCLUSION_REASON_STATUS_FILTER_INACTIVE


def test_excluded_row_reason_when_plot_status_inactive_excludes_active_plot():
    plot = _plot(is_active=True)
    values = _excluded_row_values(plot, "inactive")
    assert values["exclusionReason"] == _EXCLUSION_REASON_STATUS_FILTER_ACTIVE


def test_excluded_row_supplier_inactive_reason_wins_over_status_filter():
    plot = _plot(is_active=False, supplier=_supplier(is_active=False))
    values = _excluded_row_values(plot, "active")
    assert values["exclusionReason"] == "Supplier ปิดใช้งาน"

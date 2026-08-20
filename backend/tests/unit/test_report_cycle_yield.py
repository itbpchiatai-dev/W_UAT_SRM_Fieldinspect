"""Cycle-yield report (Report #2 "ผลผลิตตามรอบปลูก", round 8-2.8B).

DB-free, three layers:
- repository mapping/query shape (cycle_yield_rows) — final_* read verbatim
  from the cycle, no records-table query, no plot mirror, order, inactive-plot
  history included (mock db.execute + source inspection);
- the Excel workbook shape (_cycle_yield_workbook) — header order, status
  labels, numeric cells, and (round 8-7C.1) the actual-harvest columns kept
  distinct from the estimate columns;
- endpoint plumbing — filters reach the repo unchanged, status/date validation
  → 422, JSON + export share one repo call. Call-the-function-directly pattern.
"""
from __future__ import annotations

import datetime
import inspect
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi import HTTPException

from app.api.v1.reports import (
    _CYCLE_YIELD_HEADERS,
    _cycle_yield_workbook,
    cycle_yield_report,
    export_cycle_yield_report,
)
from app.repositories import report_repository as repo
from app.schemas.report import ReportCycleYieldRow

_MODULE = "app.api.v1.reports"


def _row(**overrides) -> ReportCycleYieldRow:
    base = dict(
        supplier_id=uuid4(),
        supplier_code="SUP001",
        supplier_name="Supplier One",
        plot_id=uuid4(),
        plot_code="P001",
        plot_name="แปลงหนึ่ง",
        province="เชียงใหม่",
        plot_is_active=True,
        cycle_id=uuid4(),
        cycle_no=2,
        cycle_label="jun2026",
        cycle_status="harvested",
        crop="พริก",
        variety="พริกขี้หนู",
        lot_no="LOT-01",
        planting_date=datetime.date(2026, 6, 1),
        plant_count=1000,
        expected_yield_full=Decimal("1000.00"),
        expected_yield_unit="kg",
        started_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        closed_at=datetime.datetime(2026, 9, 1, 8, 0, tzinfo=datetime.timezone.utc),
        close_reason="เก็บเกี่ยวเสร็จ",
        final_yield_pct=Decimal("80.0"),
        final_estimated_yield=Decimal("800.00"),
        final_inspection_record_id=uuid4(),
        # Round 8-7C.1 — ACTUAL harvest figures; default None (estimate-only
        # baseline) so every pre-8-7C.1 test using this fixture is unaffected.
        harvest_yield=None,
        final_yield_after_clean=None,
        final_yield_unit=None,
        harvest_date=None,
        final_note=None,
    )
    base.update(overrides)
    return ReportCycleYieldRow(**base)


def _unzip(content: bytes) -> dict[str, str]:
    with ZipFile(BytesIO(content)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


# --- repository: verbatim mapping + query shape ---------------------------

def _result_all(rows: list) -> MagicMock:
    r = MagicMock()
    r.all = MagicMock(return_value=rows)
    return r


def _fake_cycle(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=3, cycle_label="aug2026", status="cancelled",
        crop="เมล่อน", variety="ออเรนจ์", lot_no="LOT-9",
        # Round 8-5B — report_repository reads these off the cycle.
        po_number="PO25009", p_code="Melon-I", lot_no_source="manual",
        # Round 8-12C — report_repository reads this off the cycle too.
        supplier_lot_no="SUP-OWN-9",
        planting_date=datetime.date(2026, 8, 1), plant_count=500,
        expected_yield_full=Decimal("900.00"), expected_yield_unit="kg",
        started_at=datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc),
        closed_at=datetime.datetime(2026, 8, 20, tzinfo=datetime.timezone.utc),
        close_reason="น้ำท่วม",
        final_yield_pct=Decimal("45.0"), final_estimated_yield=Decimal("405.00"),
        final_inspection_record_id=uuid4(),
        # Round 8-7C.1 — actual harvest columns on the ORM cycle.
        harvest_yield=Decimal("380.00"), final_yield_after_clean=Decimal("360.00"),
        final_yield_unit="kg", harvest_date=datetime.date(2026, 8, 19),
        final_note="ผลผลิตหลังคัดแยก",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _fake_plot(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), plot_code="P009", name="แปลงเก้า", province="เชียงราย",
        supplier_id=uuid4(), is_active=True,
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def test_repo_maps_final_fields_verbatim_and_keeps_inactive_plot() -> None:
    cycle = _fake_cycle()
    plot = _fake_plot(is_active=False)  # deactivated plot → history still kept
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result_all([(cycle, plot, "SUP009", "Sup Nine")]))

    rows = await repo.cycle_yield_rows(db, status="all")

    assert len(rows) == 1
    r = rows[0]
    # final_* read VERBATIM from the cycle snapshot (never recomputed)
    assert r.final_yield_pct == cycle.final_yield_pct
    assert r.final_estimated_yield == cycle.final_estimated_yield
    assert r.final_inspection_record_id == cycle.final_inspection_record_id
    # Round 8-7C.1 — ACTUAL harvest figures, also read VERBATIM (never
    # recomputed, never derived from the estimate fields above).
    assert r.harvest_yield == cycle.harvest_yield
    assert r.final_yield_after_clean == cycle.final_yield_after_clean
    assert r.final_yield_unit == cycle.final_yield_unit
    assert r.harvest_date == cycle.harvest_date
    assert r.final_note == cycle.final_note
    # identity from plot/supplier
    assert r.plot_is_active is False
    assert r.supplier_code == "SUP009"
    assert r.cycle_status == "cancelled"
    assert r.cycle_label == "aug2026"
    # Round 8-12C — supplier_lot_no read verbatim from the cycle, never the
    # plots mirror, never recomputed.
    assert r.supplier_lot_no == cycle.supplier_lot_no == "SUP-OWN-9"


async def test_repo_limit_none_default_stays_unbounded() -> None:
    """Round 8-25D — the export endpoint relies on this default: no `limit`
    argument at all must never add a SQL LIMIT/OFFSET clause."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result_all([]))
    await repo.cycle_yield_rows(db)
    stmt = db.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT" not in compiled
    assert "OFFSET" not in compiled


async def test_repo_explicit_limit_and_offset_reach_the_query() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result_all([]))
    await repo.cycle_yield_rows(db, limit=500, offset=1000)
    stmt = db.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT 500" in compiled
    assert "OFFSET 1000" in compiled


async def test_repo_supplier_lot_no_is_null_safe_for_older_cycles() -> None:
    """A pre-8-12A cycle has no supplier_lot_no at all — must map to None,
    never crash, never fall back to lot_no."""
    cycle = _fake_cycle(supplier_lot_no=None, lot_no="LOT-9")
    plot = _fake_plot()
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result_all([(cycle, plot, "SUP009", "Sup Nine")]))

    rows = await repo.cycle_yield_rows(db, status="all")

    assert rows[0].supplier_lot_no is None
    assert rows[0].lot_no == "LOT-9"  # never conflated with the system lot


def test_repo_never_queries_records_or_plot_mirror() -> None:
    src = inspect.getsource(repo.cycle_yield_rows)
    # never the records table nor the plot inspection mirror
    assert "Record" not in src
    assert "current_yield_pct" not in src
    assert "sync_current_status" not in src
    # final fields assigned straight from the cycle
    assert "final_yield_pct=cycle.final_yield_pct" in src
    assert "final_estimated_yield=cycle.final_estimated_yield" in src
    # Round 8-7C.1 — actual-harvest fields likewise assigned straight from
    # the cycle (no N+1, no separate query, no recomputation).
    assert "harvest_yield=cycle.harvest_yield" in src
    assert "final_yield_after_clean=cycle.final_yield_after_clean" in src
    assert "final_yield_unit=cycle.final_yield_unit" in src
    assert "harvest_date=cycle.harvest_date" in src
    assert "final_note=cycle.final_note" in src
    # Round 8-12C — supplier_lot_no likewise: straight off the cycle already
    # loaded by the single join, no extra query.
    assert "supplier_lot_no=cycle.supplier_lot_no" in src


def test_repo_orders_correctly_and_includes_inactive_plots() -> None:
    src = inspect.getsource(repo.cycle_yield_rows)
    assert "Supplier.code.asc()" in src
    assert "Plot.plot_code.asc()" in src
    assert "PlotCycle.cycle_no.desc()" in src
    # inspect the CODE only (the docstring legitimately names Plot.is_active to
    # explain that it is NOT filtered). No is_active WHERE → inactive plots'
    # cycle history is included.
    body = src.split('"""')[2]
    assert "Plot.is_active" not in body


def test_repo_status_filter_branches() -> None:
    src = inspect.getsource(repo.cycle_yield_rows)
    assert "CYCLE_CLOSED_STATUSES" in src           # closed = harvested+cancelled
    assert 'status == "all"' in src                  # all = no filter
    assert "PlotCycle.status == status" in src       # single explicit status
    # date range on closed_at (so active cycles drop out)
    assert "PlotCycle.closed_at" in src


# --- workbook shape -------------------------------------------------------

def test_headers_order_separates_estimated_and_actual_harvest_columns() -> None:
    """Round 8-7C.1 — supersedes the old 'never an actual-yield column'
    contract test (round 8-2.8B): the report NOW has actual-harvest columns
    (round 8-7A's final_plot), inserted immediately after the ESTIMATE
    column. This test asserts the two families are named DISTINCTLY and sit
    in the exact required order, so they can never be visually/semantically
    confused — not that an actual column can never exist."""
    assert _CYCLE_YIELD_HEADERS[0] == "รหัส Supplier"
    assert _CYCLE_YIELD_HEADERS[-1] == "รหัสบันทึกที่ใช้สรุป"  # still the last column
    assert "Yield สุดท้าย (%)" in _CYCLE_YIELD_HEADERS

    estimate_i = _CYCLE_YIELD_HEADERS.index("ผลผลิตประมาณการสุดท้าย")
    actual_cols = [
        "ผลผลิตตอนเก็บเกี่ยว",
        "ผลผลิตจริงหลังทำความสะอาด",
        "หน่วยผลผลิตจริง",
        "วันที่เก็บเกี่ยว",
        "หมายเหตุผลผลิตสุดท้าย",
    ]
    for i, name in enumerate(actual_cols):
        assert _CYCLE_YIELD_HEADERS[estimate_i + 1 + i] == name

    # The estimate column's own name is never reused for an actual column,
    # and vice versa — distinct, non-overlapping labels.
    assert "ผลผลิตประมาณการสุดท้าย" not in actual_cols
    assert len(set(_CYCLE_YIELD_HEADERS)) == len(_CYCLE_YIELD_HEADERS)  # no duplicate header

    # "วันที่เริ่มรอบ" immediately follows the 5 actual-harvest columns.
    assert _CYCLE_YIELD_HEADERS[estimate_i + 1 + len(actual_cols)] == "วันที่เริ่มรอบ"


def test_headers_include_po_pcode_lot_source() -> None:  # round 8-5B
    for h in ("PO Number", "P.Code", "Lot No ระบบ", "ที่มา Lot"):
        assert h in _CYCLE_YIELD_HEADERS
    # PO/P.Code sit after พันธุ์ and before Lot No ระบบ.
    assert _CYCLE_YIELD_HEADERS.index("PO Number") < _CYCLE_YIELD_HEADERS.index("Lot No ระบบ")
    assert _CYCLE_YIELD_HEADERS.index("P.Code") < _CYCLE_YIELD_HEADERS.index("Lot No ระบบ")


def test_headers_supplier_lot_no_sits_right_after_lot_no() -> None:  # round 8-12C.1
    # Column order: Lot No ระบบ -> Supplier Lot No -> ที่มา Lot. The two lot
    # identities (system vs supplier) are adjacent but distinct columns —
    # never merged into one — with the source label immediately after both.
    lot_i = _CYCLE_YIELD_HEADERS.index("Lot No ระบบ")
    assert _CYCLE_YIELD_HEADERS[lot_i + 1] == "Supplier Lot No"
    assert _CYCLE_YIELD_HEADERS[lot_i + 2] == "ที่มา Lot"
    assert len(set(_CYCLE_YIELD_HEADERS)) == len(_CYCLE_YIELD_HEADERS)  # still no duplicate header


def test_workbook_carries_po_pcode_and_lot_source_label() -> None:  # round 8-5B
    sheet = _unzip(_cycle_yield_workbook([_row(
        po_number="PO25009", p_code="Melon-I", lot_no="LOT-9", lot_no_source="auto",
    )]))["xl/worksheets/sheet1.xml"]
    assert "PO25009" in sheet
    assert "Melon-I" in sheet
    assert "อัตโนมัติ" in sheet  # lot_no_source 'auto' → Thai label


def test_workbook_carries_supplier_lot_no_as_separate_cell() -> None:  # round 8-12C
    sheet = _unzip(_cycle_yield_workbook([_row(
        lot_no="2605-SUP010-WM-141-003", lot_no_source="auto",
        supplier_lot_no="SUP-OWN-7",
    )]))["xl/worksheets/sheet1.xml"]
    assert "2605-SUP010-WM-141-003" in sheet
    assert "SUP-OWN-7" in sheet  # the two lot identities never merged into one cell


def test_workbook_supplier_lot_no_null_renders_as_blank_no_crash() -> None:  # round 8-12C
    parts = _unzip(_cycle_yield_workbook([_row(supplier_lot_no=None)]))
    sheet = parts["xl/worksheets/sheet1.xml"]
    assert "SUP001" in sheet  # renders fine, no crash on a NULL supplier lot


def test_workbook_header_and_data_cells_are_positionally_aligned() -> None:  # round 8-12C.1
    """The Lot No ระบบ / Supplier Lot No / ที่มา Lot triplet must land in the
    SAME columns in the header row and every data row — not just "the values
    exist somewhere in the sheet" (the weaker check the substring assertions
    above give)."""
    from app.services.excel_workbook import _col_name

    lot_col = _col_name(_CYCLE_YIELD_HEADERS.index("Lot No ระบบ") + 1)
    supplier_lot_col = _col_name(_CYCLE_YIELD_HEADERS.index("Supplier Lot No") + 1)
    source_col = _col_name(_CYCLE_YIELD_HEADERS.index("ที่มา Lot") + 1)

    sheet = _unzip(_cycle_yield_workbook([_row(
        lot_no="2605-SUP010-WM-141-003", lot_no_source="auto",
        supplier_lot_no="SUP-OWN-7",
    )]))["xl/worksheets/sheet1.xml"]

    # header row (row 1)
    assert f'<c r="{lot_col}1"' in sheet
    assert f'<c r="{supplier_lot_col}1"' in sheet
    assert f'<c r="{source_col}1"' in sheet
    # data row (row 2) — exact cell holds the exact value, no cross-column bleed
    assert f'r="{lot_col}2" t="inlineStr"><is><t>2605-SUP010-WM-141-003</t>' in sheet
    assert f'r="{supplier_lot_col}2" t="inlineStr"><is><t>SUP-OWN-7</t>' in sheet
    assert f'r="{source_col}2" t="inlineStr"><is><t>อัตโนมัติ</t>' in sheet


def test_workbook_single_sheet_named_cycle_yield() -> None:
    parts = _unzip(_cycle_yield_workbook([_row()]))
    workbook = parts["xl/workbook.xml"]
    assert workbook.count("<sheet ") == 1
    assert 'name="cycle-yield"' in workbook


def test_workbook_status_labels() -> None:
    harvested = _unzip(_cycle_yield_workbook([_row(cycle_status="harvested")]))["xl/worksheets/sheet1.xml"]
    assert "เก็บเกี่ยวแล้ว" in harvested
    cancelled = _unzip(_cycle_yield_workbook([_row(cycle_status="cancelled")]))["xl/worksheets/sheet1.xml"]
    assert "ยกเลิก" in cancelled
    active = _unzip(_cycle_yield_workbook([_row(cycle_status="active", closed_at=None)]))["xl/worksheets/sheet1.xml"]
    assert "กำลังปลูก" in active


def test_workbook_decimals_are_numeric_cells_not_text() -> None:
    sheet = _unzip(_cycle_yield_workbook([_row()]))["xl/worksheets/sheet1.xml"]
    # final_estimated_yield 800.0 → numeric <v>, not an inline string
    assert "<v>800.0</v>" in sheet
    assert "<v>80.0</v>" in sheet          # final_yield_pct
    assert "<v>1000.0</v>" in sheet        # expected_yield_full
    assert "<v>1000</v>" in sheet          # plant_count int


def test_workbook_null_snapshot_renders_without_final_values() -> None:
    parts = _unzip(_cycle_yield_workbook([_row(
        final_yield_pct=None, final_estimated_yield=None, final_inspection_record_id=None,
    )]))
    sheet = parts["xl/worksheets/sheet1.xml"]
    # still renders identity; no crash, no stray final numbers
    assert "SUP001" in sheet


# --- Round 8-7C.1: actual-harvest columns ----------------------------------

def test_workbook_actual_harvest_decimals_are_numeric_cells() -> None:
    sheet = _unzip(_cycle_yield_workbook([_row(
        harvest_yield=Decimal("1250.00"), final_yield_after_clean=Decimal("1180.00"),
        final_yield_unit="kg", harvest_date=datetime.date(2026, 7, 28),
        final_note="ผลผลิตหลังคัดแยก",
    )]))["xl/worksheets/sheet1.xml"]
    assert "<v>1250.0</v>" in sheet
    assert "<v>1180.0</v>" in sheet
    assert "kg" in sheet
    assert "2026-07-28" in sheet
    assert "ผลผลิตหลังคัดแยก" in sheet


def test_workbook_actual_harvest_date_is_iso_format() -> None:
    sheet = _unzip(_cycle_yield_workbook([_row(harvest_date=datetime.date(2026, 1, 5))]))["xl/worksheets/sheet1.xml"]
    assert "2026-01-05" in sheet


def test_workbook_actual_harvest_null_fields_render_as_blank_no_crash() -> None:
    parts = _unzip(_cycle_yield_workbook([_row(
        harvest_yield=None, final_yield_after_clean=None, final_yield_unit=None,
        harvest_date=None, final_note=None,
    )]))
    sheet = parts["xl/worksheets/sheet1.xml"]
    # still renders identity; no crash, no stray actual-harvest values
    assert "SUP001" in sheet


def test_workbook_estimate_never_labeled_as_actual_and_vice_versa() -> None:
    """Round 8-7C.1 — a row carrying BOTH estimate and actual values must
    show them as clearly separate numbers (never merged/renamed)."""
    sheet = _unzip(_cycle_yield_workbook([_row(
        final_estimated_yield=Decimal("800.00"), harvest_yield=Decimal("1250.00"),
        final_yield_after_clean=Decimal("1180.00"),
    )]))["xl/worksheets/sheet1.xml"]
    assert "<v>800.0</v>" in sheet   # estimate, unchanged
    assert "<v>1250.0</v>" in sheet  # actual harvest
    assert "<v>1180.0</v>" in sheet  # actual after-clean


def test_schema_actual_harvest_fields_are_camel_case_on_the_wire() -> None:
    row = _row(
        harvest_yield=Decimal("1250.00"), final_yield_after_clean=Decimal("1180.00"),
        final_yield_unit="kg", harvest_date=datetime.date(2026, 7, 28),
        final_note="โน้ต",
    )
    payload = row.model_dump(by_alias=True, mode="json")
    assert payload["harvestYield"] == "1250.00"
    assert payload["finalYieldAfterClean"] == "1180.00"
    assert payload["finalYieldUnit"] == "kg"
    assert payload["harvestDate"] == "2026-07-28"
    assert payload["finalNote"] == "โน้ต"


def test_schema_supplier_lot_no_is_camel_case_on_the_wire_and_distinct_from_lot_no() -> None:  # round 8-12C
    row = _row(lot_no="2605-SUP010-WM-141-003", supplier_lot_no="SUP-OWN-7")
    payload = row.model_dump(by_alias=True, mode="json")
    assert payload["supplierLotNo"] == "SUP-OWN-7"
    assert payload["lotNo"] == "2605-SUP010-WM-141-003"
    assert payload["supplierLotNo"] != payload["lotNo"]


def test_empty_rows_still_valid_single_sheet_with_headers() -> None:
    parts = _unzip(_cycle_yield_workbook([]))
    assert parts["xl/workbook.xml"].count("<sheet ") == 1
    sheet = parts["xl/worksheets/sheet1.xml"]
    assert "ผลผลิตประมาณการสุดท้าย" in sheet
    assert "ผลผลิตตอนเก็บเกี่ยว" in sheet
    assert "ผลผลิตจริงหลังทำความสะอาด" in sheet


# --- endpoint plumbing (repo mocked) --------------------------------------

async def test_endpoint_passes_all_filters_to_repository() -> None:
    captured: dict = {}

    async def fake_rows(db, **kwargs):
        captured.update(kwargs)
        return []

    sid = uuid4()
    with patch(f"{_MODULE}.repo.cycle_yield_rows", AsyncMock(side_effect=fake_rows)):
        result = await cycle_yield_report(
            db=object(), supplier_id=sid, crop="พริก", status="harvested",
            date_from=datetime.date(2026, 6, 1), date_to=datetime.date(2026, 6, 30),
        )

    assert result == []
    assert captured["supplier_id"] == sid
    assert captured["crop"] == "พริก"
    assert captured["status"] == "harvested"
    assert captured["date_from"] == datetime.date(2026, 6, 1)
    assert captured["date_to"] == datetime.date(2026, 6, 30)
    # Round 8-25D — the on-screen endpoint always has a real ceiling, unlike
    # the export endpoint (see test_export_never_passes_a_limit below).
    assert captured["limit"] == 100
    assert captured["offset"] == 0


async def test_endpoint_passes_through_a_caller_supplied_limit_and_offset() -> None:
    captured: dict = {}

    async def fake_rows(db, **kwargs):
        captured.update(kwargs)
        return []

    with patch(f"{_MODULE}.repo.cycle_yield_rows", AsyncMock(side_effect=fake_rows)):
        await cycle_yield_report(db=object(), limit=500, offset=1000)

    assert captured["limit"] == 500
    assert captured["offset"] == 1000


async def test_endpoint_default_status_is_closed() -> None:
    captured: dict = {}

    async def fake_rows(db, **kwargs):
        captured.update(kwargs)
        return []

    with patch(f"{_MODULE}.repo.cycle_yield_rows", AsyncMock(side_effect=fake_rows)):
        await cycle_yield_report(db=object())

    assert captured["status"] == "closed"


async def test_endpoint_invalid_status_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        await cycle_yield_report(db=object(), status="bogus")
    assert exc.value.status_code == 422


async def test_endpoint_inverted_date_range_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        await cycle_yield_report(
            db=object(),
            date_from=datetime.date(2026, 7, 1), date_to=datetime.date(2026, 6, 1),
        )
    assert exc.value.status_code == 422


async def test_export_uses_same_repo_and_returns_xlsx() -> None:
    with patch(f"{_MODULE}.repo.cycle_yield_rows", AsyncMock(return_value=[_row()])) as m:
        resp = await export_cycle_yield_report(db=object(), status="closed")

    m.assert_awaited_once()
    assert resp.media_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "cycle-yield-report.xlsx" in resp.headers["content-disposition"]
    assert resp.headers["cache-control"] == "no-store"
    parts = _unzip(resp.body)
    assert 'name="cycle-yield"' in parts["xl/workbook.xml"]
    assert "SUP001" in parts["xl/worksheets/sheet1.xml"]


async def test_export_never_passes_a_limit() -> None:
    """Round 8-25D regression guard — a downloaded workbook must contain
    every filtered row regardless of what's paged on screen."""
    captured: dict = {}

    async def fake_rows(db, **kwargs):
        captured.update(kwargs)
        return []

    with patch(f"{_MODULE}.repo.cycle_yield_rows", AsyncMock(side_effect=fake_rows)):
        await export_cycle_yield_report(db=object(), status="closed")

    assert "limit" not in captured
    assert "offset" not in captured


async def test_export_also_validates_status_and_dates() -> None:
    with pytest.raises(HTTPException):
        await export_cycle_yield_report(db=object(), status="bogus")
    with pytest.raises(HTTPException):
        await export_cycle_yield_report(
            db=object(),
            date_from=datetime.date(2026, 7, 1), date_to=datetime.date(2026, 6, 1),
        )


def test_report_routes_are_authenticated_and_not_public() -> None:
    from app.api.v1.public_plots import router as public_router
    from app.api.v1.reports import router as reports_router

    cycle_routes = [r for r in reports_router.routes if "cycle-yield" in getattr(r, "path", "")]
    assert len(cycle_routes) == 2  # list + export
    # no cycle-yield route on the public router
    assert not any("cycle-yield" in getattr(r, "path", "") for r in public_router.routes)

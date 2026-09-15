"""Round X — the รายงาน menu: find rows by invoice, and export what the two
reports are actually used for.

  * Both reports take an `invoice` filter (all four endpoints), matched the way
    the Plots list matches it: trimmed, case-insensitive substring — with
    `%` and `_` in the user's text taken literally.
  * "ผลผลิตตามรอบปลูก" exports the three Oracle references and the actual
    yield as a percentage of the target — only when the two units are the
    same, the rule the Plots list uses (round R); never a cross-unit number.
  * "สถานะแปลง" exports the open cycle's identity and references, plus the
    harvest the field team has REPORTED — labelled as such, because it is not
    confirmed until the cycle is closed — resolved for the whole report in one
    batched query.

Seams: the endpoint functions (called directly, repository mocked) and the
exported workbook read back through the real reader; plus the two pure
helpers the rules live in, and the row mapping's single batched lookup.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import column
from sqlalchemy.dialects import postgresql

from app.api.v1 import reports
from app.repositories import report_repository
from app.repositories.text_filters import contains_text
from app.schemas.report import ReportCycleYieldRow, ReportPlotStatusRow
from app.services.excel_reader import read_first_sheet
from app.services.yield_calculation import actual_vs_target_pct

BACKSLASH = chr(92)

# --- the invoice matching rule ------------------------------------------------


def _pattern(clause) -> tuple[str, str]:
    """(the bound LIKE pattern, the escape character) of an ILIKE clause."""
    return clause.right.value, clause.modifiers.get("escape")


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_blank_invoice_is_no_filter(blank) -> None:
    assert contains_text(column("oracle_invoice"), blank) is None


def test_the_invoice_is_trimmed_and_matched_as_a_case_insensitive_substring() -> None:
    clause = contains_text(column("oracle_invoice"), "  inv-2026  ")
    assert "ILIKE" in str(clause.compile(dialect=postgresql.dialect())).upper()
    assert _pattern(clause)[0] == "%inv-2026%"


def test_percent_and_underscore_in_the_invoice_are_literal() -> None:
    """Unescaped, "INV_1" would also match "INVX1" and "%" would match
    anything — an invoice search that quietly widens itself."""
    pattern, escape = _pattern(contains_text(column("oracle_invoice"), "INV_1%"))
    assert pattern == "%INV" + BACKSLASH + "_1" + BACKSLASH + "%%"
    assert escape == BACKSLASH


# --- actual vs target, the round-R rule ----------------------------------------

def test_actual_vs_target_is_a_percentage_when_the_units_match() -> None:
    assert actual_vs_target_pct(Decimal("1180"), "kg", Decimal("1000"), "kg") == Decimal("118.0")


def test_actual_vs_target_rounds_to_one_decimal_half_up() -> None:
    assert actual_vs_target_pct(Decimal("1"), "kg", Decimal("3"), "kg") == Decimal("33.3")
    assert actual_vs_target_pct(Decimal("1"), "kg", Decimal("8"), "kg") == Decimal("12.5")


@pytest.mark.parametrize("args", [
    (Decimal("1180"), "kg", Decimal("5"), "ตัน"),     # 23.6% really — never 23600%
    (Decimal("1180"), "kg", Decimal("1000"), "ผล"),
    (None, "kg", Decimal("1000"), "kg"),
    (Decimal("1180"), "kg", None, "kg"),
    (Decimal("1180"), None, Decimal("1000"), "kg"),
    (Decimal("1180"), "kg", Decimal("0"), "kg"),
])
def test_actual_vs_target_is_blank_whenever_it_cannot_be_said_honestly(args) -> None:
    assert actual_vs_target_pct(*args) is None


# --- the four endpoints pass the invoice through --------------------------------

async def test_plot_status_json_and_export_filter_by_invoice() -> None:
    rows = AsyncMock(return_value=[])
    with patch.object(reports.repo, "plot_status_rows", rows):
        await reports.plot_status_report(db=MagicMock(), invoice="INV-1")
        await reports.export_plot_status_report(db=MagicMock(), invoice="INV-1")
    assert [c.kwargs["invoice"] for c in rows.await_args_list] == ["INV-1", "INV-1"]


async def test_cycle_yield_json_and_export_filter_by_invoice() -> None:
    rows = AsyncMock(return_value=[])
    with patch.object(reports.repo, "cycle_yield_rows", rows):
        await reports.cycle_yield_report(db=MagicMock(), invoice="INV-1")
        await reports.export_cycle_yield_report(db=MagicMock(), invoice="INV-1")
    assert [c.kwargs["invoice"] for c in rows.await_args_list] == ["INV-1", "INV-1"]


async def test_no_invoice_means_no_invoice_filter() -> None:
    rows = AsyncMock(return_value=[])
    with patch.object(reports.repo, "cycle_yield_rows", rows):
        await reports.cycle_yield_report(db=MagicMock())
    assert rows.await_args.kwargs["invoice"] is None


# --- the exported workbooks ------------------------------------------------------

def _read(content: bytes) -> tuple[list[str], dict[str, object]]:
    headers, rows = read_first_sheet(content)
    return headers, (rows[0][1] if rows else {})


def _cycle_row(**over) -> ReportCycleYieldRow:
    base = dict(
        supplier_id=uuid4(), supplier_code="SUP001", supplier_name="Supplier One",
        plot_id=uuid4(), plot_code="SUP001-2606-001", plot_name="แปลงหนึ่ง",
        province="เชียงใหม่", plot_is_active=False, cycle_id=uuid4(), cycle_no=1,
        cycle_label="jun2026", cycle_status="harvested", crop="พริก", variety="พริกขี้หนู",
        lot_no="jun2026-SUP001-WM-141-001", planting_date=datetime.date(2026, 6, 1),
        plant_count=1000, expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
        started_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        closed_at=datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc),
        close_reason=None, final_yield_pct=None, final_estimated_yield=None,
        final_inspection_record_id=None, harvest_yield=Decimal("1250"),
        final_yield_after_clean=Decimal("1180"), final_yield_unit="kg",
        harvest_date=datetime.date(2026, 8, 30), final_note=None,
        oracle_supplier_code="ORC-SUP-1", oracle_invoice="INV-2026-0001", ref_account="ACC-9",
        actual_yield_pct=Decimal("118.0"),
    )
    base.update(over)
    return ReportCycleYieldRow(**base)


# The headers every existing cycle-yield export already had, in order. New
# columns may be inserted between them; none of these may change or move
# relative to one another — spreadsheets are built on them.
_CYCLE_YIELD_HEADERS_BEFORE_X = [
    "รหัส Supplier", "ชื่อ Supplier", "รหัสแปลง", "ชื่อแปลง", "จังหวัด", "ชื่อรอบปลูก",
    "รอบที่", "สถานะรอบ", "ชนิดพืช", "พันธุ์", "PO Number", "P.Code", "Lot No ระบบ",
    "Supplier Lot No", "ที่มา Lot", "วันที่ปลูก", "จำนวนต้น/จำนวนปลูก", "เป้าผลิต", "หน่วย",
    "Yield สุดท้าย (%)", "ผลผลิตประมาณการสุดท้าย", "ผลผลิตตอนเก็บเกี่ยว",
    "ผลผลิตจริงหลังทำความสะอาด", "หน่วยผลผลิตจริง", "วันที่เก็บเกี่ยว",
    "หมายเหตุผลผลิตสุดท้าย", "วันที่เริ่มรอบ", "วันที่ปิดรอบ", "เหตุผลการปิด",
    "รหัสบันทึกที่ใช้สรุป",
]


def _keeps_the_old_headers_in_order(headers: list[str], old: list[str]) -> bool:
    positions = [headers.index(h) for h in old]
    return positions == sorted(positions)


def test_cycle_yield_export_keeps_every_existing_header_in_order() -> None:
    headers, _ = _read(reports._cycle_yield_workbook([_cycle_row()]))
    assert _keeps_the_old_headers_in_order(headers, _CYCLE_YIELD_HEADERS_BEFORE_X)


def test_cycle_yield_export_places_the_oracle_references_after_the_lot_columns() -> None:
    # After "ที่มา Lot", so system lot / supplier lot / lot source stay together.
    headers, cells = _read(reports._cycle_yield_workbook([_cycle_row()]))
    at = headers.index("ที่มา Lot")
    assert headers[at + 1:at + 4] == ["Oracle Supplier Code", "Oracle Invoice", "Ref Account"]
    assert (cells["Oracle Supplier Code"], cells["Oracle Invoice"], cells["Ref Account"]) == (
        "ORC-SUP-1", "INV-2026-0001", "ACC-9")


def test_cycle_yield_export_shows_actual_vs_target_after_the_actual_unit() -> None:
    headers, cells = _read(reports._cycle_yield_workbook([_cycle_row()]))
    assert headers[headers.index("หน่วยผลผลิตจริง") + 1] == "ผลผลิตจริงเทียบเป้า (%)"
    assert cells["ผลผลิตจริงเทียบเป้า (%)"] == "118.0"   # a numeric cell


def test_cycle_yield_export_says_why_across_units() -> None:
    """Story 14: blank WITH its reason. Both figures exist but the target is in
    ตัน — the cell says so, like the Plots list does, never a number."""
    _, cells = _read(reports._cycle_yield_workbook([_cycle_row(
        actual_yield_pct=None, expected_yield_full=Decimal("5"), expected_yield_unit="ตัน",
    )]))
    assert cells["ผลผลิตจริงเทียบเป้า (%)"] == "คนละหน่วย"


def test_cycle_yield_export_is_blank_when_there_is_nothing_to_compare() -> None:
    _, cells = _read(reports._cycle_yield_workbook([_cycle_row(
        actual_yield_pct=None, final_yield_after_clean=None,
    )]))
    assert "ผลผลิตจริงเทียบเป้า (%)" not in cells     # a blank cell, never 0


def _plot_row(**over) -> ReportPlotStatusRow:
    base = dict(
        plot_id=uuid4(), supplier_code="SUP001", supplier_name="Supplier One",
        plot_code="SUP001-2606-001", plot_name="แปลงหนึ่ง", province="เชียงใหม่",
        active_cycle_id=uuid4(), active_cycle_no=1, active_cycle_status="active",
        current_crop="พริก", current_variety="พริกขี้หนู", current_stage="เก็บเกี่ยว",
        current_yield_pct=Decimal("90"), current_field_prep_score=8, current_weather_score=7,
        current_care_score=6, current_variety_resistance_score=5,
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg", plant_count=500,
        last_inspected_at=datetime.datetime(2026, 8, 30, tzinfo=datetime.timezone.utc),
        last_inspected_by_code="W01", is_inspected=True,
        cycle_label="jun2026", po_number="PO25001", p_code="WM-141",
        lot_no="jun2026-SUP001-WM-141-001", supplier_lot_no="SUP-LOT-1",
        oracle_supplier_code="ORC-SUP-1", oracle_invoice="INV-2026-0001", ref_account="ACC-9",
        planting_date=datetime.date(2026, 6, 1),
        reported_harvest_yield=Decimal("1250"), reported_final_yield_after_clean=Decimal("1180"),
        reported_harvest_date=datetime.date(2026, 8, 30),
    )
    base.update(over)
    return ReportPlotStatusRow(**base)


_PLOT_STATUS_HEADERS_BEFORE_X = [
    "Supplier", "ชื่อ Supplier", "รหัสแปลง", "ชื่อแปลง", "จังหวัด", "สถานะรอบปลูก",
    "ชนิดพืช", "พันธุ์", "ระยะ", "เปอร์เซ็นต์เทียบเป้าผลิต", "เป้าผลิต", "หน่วย",
    "ผลผลิตที่คาดว่าจะได้", "เตรียมแปลง", "สภาพอากาศ", "ดูแลรักษา", "ต้านทานโรค",
    "ตรวจล่าสุด", "โดย", "สถานะตรวจ",
]
_REPORTED = ("ผลผลิตตอนเก็บเกี่ยว (kg) — ภาคสนามรายงาน",
             "ผลผลิตหลังทำความสะอาด (kg) — ภาคสนามรายงาน",
             "วันที่รายงานผลผลิต")


def test_plot_status_export_keeps_every_existing_header_in_order() -> None:
    headers, _ = _read(reports._plot_status_workbook([_plot_row()]))
    assert _keeps_the_old_headers_in_order(headers, _PLOT_STATUS_HEADERS_BEFORE_X)


def test_plot_status_export_carries_the_open_cycle_identity_and_references() -> None:
    headers, cells = _read(reports._plot_status_workbook([_plot_row()]))
    at = headers.index("สถานะรอบปลูก")
    assert headers[at + 1:at + 10] == [
        "ชื่อรอบปลูก", "PO Number", "P.Code", "Lot No ระบบ", "Supplier Lot No",
        "Oracle Supplier Code", "Oracle Invoice", "Ref Account", "วันที่ปลูก",
    ]
    # before the target, so "เป้าผลิต" keeps its "หน่วย" right beside it
    assert headers[headers.index("จำนวนต้น") + 1: headers.index("จำนวนต้น") + 3] == ["เป้าผลิต", "หน่วย"]
    assert (cells["Lot No ระบบ"], cells["Oracle Invoice"], cells["วันที่ปลูก"], cells["จำนวนต้น"]) == (
        "jun2026-SUP001-WM-141-001", "INV-2026-0001", "2026-06-01", "500")


def test_plot_status_export_labels_the_field_reported_harvest_as_such() -> None:
    headers, cells = _read(reports._plot_status_workbook([_plot_row()]))
    at = headers.index("ผลผลิตที่คาดว่าจะได้")
    assert tuple(headers[at + 1:at + 4]) == _REPORTED
    assert (cells[_REPORTED[0]], cells[_REPORTED[1]], cells[_REPORTED[2]]) == ("1250.0", "1180.0", "2026-08-30")


def test_no_harvest_report_yet_is_blank_never_zero() -> None:
    _, cells = _read(reports._plot_status_workbook([_plot_row(
        reported_harvest_yield=None, reported_final_yield_after_clean=None, reported_harvest_date=None,
    )]))
    assert not any(h in cells for h in _REPORTED)


# --- the harvest figures come from one batched lookup ---------------------------

def _cycle(**over) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=1, status="active", crop="พริก", variety="พริกขี้หนู",
        cycle_label="jun2026", po_number=None, p_code="WM-141", lot_no="L-1",
        supplier_lot_no=None, oracle_supplier_code=None, oracle_invoice="INV-1", ref_account=None,
        planting_date=datetime.date(2026, 6, 1), plant_count=500,
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _plot(active_cycle) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(), plot_code="P", name="แปลง", province=None, active_cycle=active_cycle,
        current_stage=None, current_yield_pct=None, current_field_prep_score=None,
        current_weather_score=None, current_care_score=None,
        current_variety_resistance_score=None, last_inspected_at=None,
        last_inspected_by_code=None, last_inspection_record_id=None,
    )


async def test_the_reported_harvest_is_looked_up_once_for_the_whole_report() -> None:
    cycles = [_cycle(), _cycle(), None]            # the third plot has no open cycle
    plots = [_plot(c) for c in cycles]
    result = MagicMock()
    result.all.return_value = [(p, "SUP001", "Supplier One") for p in plots]
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    record = SimpleNamespace(yield_quantity_kg=Decimal("1250"), final_yield_after_clean=Decimal("1180"),
                             record_date=datetime.date(2026, 8, 30))
    lookup = AsyncMock(return_value={cycles[0].id: record})
    with patch.object(report_repository.plot_cycle_repo,
                      "get_actual_harvest_source_records_for_cycles", lookup):
        rows = await report_repository.plot_status_rows(db)

    lookup.assert_awaited_once()
    assert set(lookup.await_args.args[1]) == {cycles[0].id, cycles[1].id}   # open cycles only
    assert (rows[0].reported_harvest_yield, rows[0].reported_final_yield_after_clean,
            rows[0].reported_harvest_date) == (Decimal("1250"), Decimal("1180"), datetime.date(2026, 8, 30))
    assert rows[1].reported_harvest_yield is None          # open cycle, nothing reported yet
    assert rows[2].reported_harvest_yield is None and rows[2].oracle_invoice is None
    assert rows[0].oracle_invoice == "INV-1" and rows[0].cycle_label == "jun2026"


# --- the shape of the two queries (DB-free; the live check ran them for real) --

def _captured_sql(db: MagicMock) -> tuple[str, dict]:
    compiled = db.execute.await_args_list[0].args[0].compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


def _empty_db() -> MagicMock:
    result = MagicMock()
    result.all.return_value = []
    result.scalars.return_value.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


async def test_plot_status_invoice_matches_the_open_cycle_only_and_combines() -> None:
    db = _empty_db()
    supplier = uuid4()
    await report_repository.plot_status_rows(db, supplier_id=supplier, invoice="INV-1")
    sql, params = _captured_sql(db)
    exists = sql[sql.index("EXISTS"):]
    assert "plot_cycles.status" in exists and "ILIKE" in exists.upper()
    assert "active" in params.values() and "%INV-1%" in params.values()
    assert supplier in params.values()                  # combined with the other filters


async def test_the_batched_harvest_lookup_prefers_a_final_yield_report_then_newest() -> None:
    db = _empty_db()
    from app.repositories import plot_cycle_repository as cycle_repo
    await cycle_repo.get_actual_harvest_source_records_for_cycles(db, [uuid4(), uuid4()])
    sql, _ = _captured_sql(db)
    assert "DISTINCT ON (records.plot_cycle_id)" in sql
    order = sql[sql.index("ORDER BY"):]
    assert order.index("final_yield_after_clean IS NULL") < order.index("created_at DESC")


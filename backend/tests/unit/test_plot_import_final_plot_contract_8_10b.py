"""Round 8-10B — the result workbook, the preview note, and the record-drift
message, after finalYieldUnit / finalInspectionRecordId left the Excel contract.

The template side is covered in test_plot_import_template_final_plot_prefill.py
and the row-level validation/commit behaviour in
test_plot_import_final_plot_action.py; this file covers what those two do not:
the workbooks the importer WRITES BACK, and the two user-facing strings the
round introduced.

DB-less throughout: workbook builders take plain result objects, and the drift
check is driven through the same patched repositories the other import tests
use.
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services import plot_import
from app.services import plot_import_report as report
from app.services.excel_reader import read_first_sheet
from app.services.excel_workbook import build_xlsx
from app.services.plot_import import ACTION_FINAL, build_preview, commit_import
from app.services.plot_import import ImportContext

_M = "app.services.plot_import"
REMOVED_COLUMNS = ("finalYieldUnit", "finalInspectionRecordId")


# --- fixtures ---------------------------------------------------------------

def _xlsx(rows: list[dict[str, str]]) -> bytes:
    data: list[list] = [list(plot_import.IMPORT_COLUMNS)]
    for r in rows:
        data.append([r.get(c) for c in plot_import.IMPORT_COLUMNS])
    return build_xlsx([("plots", data)])


def _row(**over) -> dict[str, str]:
    base = {
        "action": ACTION_FINAL, "supplierCode": "SUP001", "plotCode": "P001",
        "cycleLabel": "jul2026",
        "harvestYield": "1250", "finalYieldAfterClean": "1180",
        "harvestDate": "2026-07-28",
    }
    base.update(over)
    return base


def _ctx() -> ImportContext:
    return ImportContext(allowed_supplier_id=None, can_create=True, can_update=True)


def _supplier():
    # code is read by the Auto Lot V2 formula (round 8-12A).
    return SimpleNamespace(id=uuid4(), code="SUP001", is_active=True)


# Same fixture shapes as test_plot_import_final_plot_action.py — the importer
# reads a lot of PlotCycle attributes, so a thin stub trips over unrelated code.
def _plot(**kw):
    base = dict(
        id=uuid4(), is_active=True,
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _cycle(**kw):
    base = dict(
        id=uuid4(), cycle_no=3, status="active", cycle_label="jul2026",
        crop=None, variety=None, lot_no=None, planting_date=None, plant_count=None,
        expected_yield_full=None, expected_yield_unit=None,
        po_number=None, p_code=None, lot_no_source=None, lot_running_no=None,
        supplier_lot_no=None,
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        harvest_yield=None, final_yield_after_clean=None, final_yield_unit=None,
        harvest_date=None, final_note=None,
        final_yield_pct=None, final_estimated_yield=None, final_inspection_record_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _record(**kw):
    base = dict(id=uuid4(), plot_id=None, plot_cycle_id=None, is_active=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _patch_lookups(*, plot=None, active=None, latest_record=None):
    return (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active)),
        patch(f"{_M}.plot_cycle_repo.get_latest_active_record_for_cycle",
              AsyncMock(return_value=latest_record)),
    )


async def _preview(rows, *, plot, active, latest_record=None):
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=active, latest_record=latest_record)
    with p1, p2, p3, p4:
        return await build_preview(AsyncMock(), _xlsx(rows), ctx=_ctx())


# --- items 8/9/10/11: the result workbooks ---------------------------------

def _result_headers(content: bytes) -> list[str]:
    headers, _rows = read_first_sheet(content)
    return headers


def _final_row_view(**over) -> dict:
    """A neutral row_view dict of the shape plot_import.report_row_view emits."""
    base = {
        "rowNumber": 3, "action": ACTION_FINAL, "supplierCode": "SUP001",
        "plotCode": "P001", "status": "valid", "message": "",
        "raw": {
            "action": ACTION_FINAL, "supplierCode": "SUP001", "plotCode": "P001",
            "harvestYield": "1250", "finalYieldAfterClean": "1180",
            "harvestDate": "2026-07-28",
        },
    }
    base.update(over)
    return base


@pytest.mark.parametrize("completed", [False, True])
def test_the_result_workbook_has_neither_column(completed):
    """Both the validation (Preview) workbook and the completed (Commit) one —
    they share a builder, so one parametrized test proves both."""
    content = report.build_plot_import_result_workbook(
        [_final_row_view()],
        phase="COMMIT" if completed else "PREVIEW",
        completed=completed,
    )
    headers = _result_headers(content)
    for column in REMOVED_COLUMNS:
        assert column not in headers
    # the server's own result columns survive untouched
    for column in report.RESULT_COLUMNS:
        assert column in headers


@pytest.mark.parametrize("completed", [False, True])
def test_the_result_workbook_rows_still_line_up_with_its_headers(completed):
    content = report.build_plot_import_result_workbook(
        [_final_row_view()],
        phase="COMMIT" if completed else "PREVIEW",
        completed=completed,
    )
    headers, rows = read_first_sheet(content)
    assert headers == report.ALL_COLUMNS
    for _row_no, values in rows:
        assert set(values).issubset(set(headers))


def test_the_result_column_layout_drops_exactly_two_columns():
    """ALL_COLUMNS is IMPORT_COLUMNS + the server's own result columns, so the
    result sheet shrinks by exactly the two removed inputs and keeps every
    status/error column."""
    assert report.ALL_COLUMNS == [*plot_import.IMPORT_COLUMNS, *report.RESULT_COLUMNS]
    for column in REMOVED_COLUMNS:
        assert column not in report.ALL_COLUMNS
    # the server-owned columns are untouched
    for column in report.RESULT_COLUMNS:
        assert column in report.ALL_COLUMNS
    assert len(report.ALL_COLUMNS) == len(plot_import.IMPORT_COLUMNS) + len(report.RESULT_COLUMNS)


def test_every_result_column_still_has_a_description():
    """Row 2 of the result workbook describes every column — an off-by-one
    between the header row and the description row is exactly the kind of
    breakage removing columns can cause."""
    described = {**plot_import.TEMPLATE_COLUMN_DESCRIPTIONS, **report.RESULT_COLUMN_DESCRIPTIONS}
    for column in report.ALL_COLUMNS:
        assert column in described, f"{column} has no description"
    for column in REMOVED_COLUMNS:
        assert column not in described


# --- Part G: the informational preview note --------------------------------

async def test_preview_says_a_record_was_found():
    plot = _plot()
    cycle = _cycle()
    latest = _record(plot_id=plot.id, plot_cycle_id=cycle.id)
    preview = await _preview([_row()], plot=plot, active=cycle, latest_record=latest)
    assert preview.rows[0].final_record_note == "พบบันทึกการตรวจที่ใช้สรุป"
    assert preview.rows[0].status == "valid"


async def test_preview_says_no_record_and_the_row_is_still_valid():
    """A cycle with no inspection still finalizes — the note is information,
    not an obstacle."""
    plot = _plot()
    cycle = _cycle()
    preview = await _preview([_row()], plot=plot, active=cycle, latest_record=None)
    assert preview.rows[0].final_record_note == "ไม่มีบันทึกการตรวจที่ใช้สรุป"
    assert preview.rows[0].status == "valid"
    assert preview.error_rows == 0


async def test_the_note_is_not_a_warning_and_not_an_error():
    """`warning` means "this may be wrong"; finding a record is the healthy
    case, so the two must not share a field."""
    plot = _plot()
    cycle = _cycle()
    latest = _record()
    preview = await _preview([_row()], plot=plot, active=cycle, latest_record=latest)
    assert preview.rows[0].warning is None
    assert preview.rows[0].message == ""


async def test_the_note_never_contains_the_record_id():
    plot = _plot()
    cycle = _cycle()
    latest = _record(plot_id=plot.id, plot_cycle_id=cycle.id)
    preview = await _preview([_row()], plot=plot, active=cycle, latest_record=latest)
    assert str(latest.id) not in (preview.rows[0].final_record_note or "")
    assert str(latest.id) not in preview.rows[0].model_dump_json()


async def test_non_final_actions_get_no_note_at_all():
    p1, p2, p3, p4 = _patch_lookups(plot=None, active=None)
    row = {
        "action": "create_plot_with_cycle", "supplierCode": "SUP001", "plotCode": "P900",
        "plotName": "แปลงใหม่", "province": "เชียงใหม่",
        "poNumber": "PO25001", "pCode": "Melon-A",
    }
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([row]), ctx=_ctx())
    assert preview.rows[0].final_record_note is None


# --- Part E: the record-drift message --------------------------------------

def _commit_patches(*, plot, cycle, latest_record=None):
    return (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)),
        patch(f"{_M}.plot_cycle_repo.get_latest_active_record_for_cycle",
              AsyncMock(return_value=latest_record)),
        patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
              AsyncMock(return_value=cycle)),
        patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock(return_value=cycle)),
    )


async def test_a_record_appearing_after_preview_gets_its_own_message():
    """The user never chose the record, so "the cycle state changed" would send
    them looking for a mistake that is not in their file. Name the actual
    cause: someone submitted an inspection in between."""
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    content = _xlsx([_row()])
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), content, ctx=_ctx())
    state = preview.preview_state

    appeared = _record(plot_id=plot.id, plot_cycle_id=cycle.id)
    with_patches = _commit_patches(plot=plot, cycle=cycle, latest_record=appeared)
    with with_patches[0], with_patches[1], with_patches[2], with_patches[3], \
         with_patches[4], with_patches[5], with_patches[6]:
        with pytest.raises(plot_import.ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=state)

    assert exc.value.args[0] == (
        "บันทึกการตรวจล่าสุดของรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า"
    )
    assert exc.value.reason == "resolution_changed"
    # row 2 in the sheet — this fixture has no Thai description row
    assert exc.value.changed_rows == [2]


async def test_the_generic_state_message_is_still_used_for_cycle_drift():
    """Only RECORD drift gets the new wording — a cycle that was closed or
    swapped underneath the user is still the generic state-changed case."""
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    content = _xlsx([_row()])
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), content, ctx=_ctx())
    state = preview.preview_state

    other_cycle = _cycle(cycle_label="jul2026")   # same label, DIFFERENT id
    with_patches = _commit_patches(plot=plot, cycle=other_cycle, latest_record=None)
    with with_patches[0], with_patches[1], with_patches[2], with_patches[3], \
         with_patches[4], with_patches[5], with_patches[6]:
        with pytest.raises(plot_import.ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=state)

    assert exc.value.args[0] == "สถานะรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า"


async def test_neither_conflict_message_ever_quotes_a_record_id():
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    content = _xlsx([_row()])
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), content, ctx=_ctx())
    state = preview.preview_state

    appeared = _record(plot_id=plot.id, plot_cycle_id=cycle.id)
    with_patches = _commit_patches(plot=plot, cycle=cycle, latest_record=appeared)
    with with_patches[0], with_patches[1], with_patches[2], with_patches[3], \
         with_patches[4], with_patches[5], with_patches[6]:
        with pytest.raises(plot_import.ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=state)
    assert str(appeared.id) not in str(exc.value.args[0])


# --- security ---------------------------------------------------------------

def test_the_importer_can_no_longer_be_handed_a_record_id_at_all():
    """The parser sets the field to a literal None — there is no expression
    that could ever read a cell into it."""
    import inspect

    src = inspect.getsource(plot_import._parse_row)
    assert "final_inspection_record_id=None" in src
    assert 'final_yield_unit=FINAL_PLOT_FIXED_YIELD_UNIT' in src


def test_no_import_error_message_mentions_a_uuid_shaped_value():
    """Every message this round added names a COLUMN, never a value."""
    for message in (
        "ไม่ต้องระบุ finalYieldUnit ระบบใช้หน่วย kg อัตโนมัติ กรุณาลบค่าจากคอลัมน์นี้",
        "ไม่ต้องระบุ finalInspectionRecordId ระบบเลือกบันทึกการตรวจล่าสุดให้อัตโนมัติ "
        "กรุณาลบค่าจากคอลัมน์นี้",
        plot_import._MSG_FINAL_RECORD_CHANGED,
        plot_import._MSG_FINAL_RECORD_FOUND,
        plot_import._MSG_FINAL_RECORD_NONE,
    ):
        assert "{" not in message and "%s" not in message
        assert "-" not in message.replace("—", "")

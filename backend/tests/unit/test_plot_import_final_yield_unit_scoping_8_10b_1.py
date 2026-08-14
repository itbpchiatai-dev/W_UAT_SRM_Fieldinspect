"""Round 8-10B.1 — 8-10B removed finalYieldUnit/finalInspectionRecordId from
the Excel INPUT contract, but _parse_row still stamped
FINAL_PLOT_FIXED_YIELD_UNIT ("kg") onto every row's payload regardless of
action: a create_plot_with_cycle or update_current_cycle row — which has no
harvest yield at all — echoed finalYieldUnit="kg" in its Preview/API payload.
This file proves the narrower contract: the "kg" stamp, and the informational
finalRecordNote, exist ONLY on a final_plot row; every other action's payload
carries null for both, exactly as before 8-10B ever touched this file.

DB-less: repo lookups are patched with AsyncMocks, same pattern as
test_plot_import_final_plot_action.py / test_plot_import_service.py /
test_plot_import_reactivate_action.py. Real parser/validator/preview/commit
code runs; only the DB-facing repo functions are mocked.
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services import plot_import
from app.services.excel_workbook import build_xlsx
from app.services.plot_import import (
    ACTION_CREATE,
    ACTION_FINAL,
    ACTION_REACTIVATE_WITH_CYCLE,
    ACTION_ROLLOVER,
    ACTION_START,
    ACTION_START_NEXT,
    ACTION_UPDATE,
    IMPORT_COLUMNS,
    ImportContext,
    build_preview,
    commit_import,
)

_M = "app.services.plot_import"

# Every action except final_plot — the set this round scopes finalYieldUnit
# AWAY from.
NON_FINAL_ACTIONS = tuple(a for a in plot_import.SUPPORTED_ACTIONS if a != ACTION_FINAL)


def _xlsx(rows: list[dict[str, str]]) -> bytes:
    data: list[list] = [list(IMPORT_COLUMNS)]
    for r in rows:
        data.append([r.get(c) for c in IMPORT_COLUMNS])
    return build_xlsx([("plots", data)])


def _ctx(**over) -> ImportContext:
    base = dict(allowed_supplier_id=None, can_create=True, can_update=True, can_reactivate=True)
    base.update(over)
    return ImportContext(**base)


def _supplier(**kw) -> SimpleNamespace:
    return SimpleNamespace(id=kw.get("id", uuid4()), code=kw.get("code", "SUP001"), is_active=kw.get("is_active", True))


def _plot(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), is_active=True,
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _cycle(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=1, status="active", cycle_label="jul2026",
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


def _record(**kw) -> SimpleNamespace:
    base = dict(id=uuid4(), plot_id=None, plot_cycle_id=None, is_active=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _patch_lookups(*, supplier=..., plot=None, active=None, labels=None, latest_record=None):
    """Patches every repo lookup any SUPPORTED_ACTIONS row might reach.
    get_cycle_labels_for_plots is only awaited when a reactivate row needs the
    history check; get_latest_active_record_for_cycle only for final_plot —
    patching both unconditionally is harmless for the other actions (never
    called) and keeps one helper usable for every action in this file."""
    sup = _supplier() if supplier is ... else supplier
    return (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=sup)),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active)),
        patch(f"{_M}.plot_cycle_repo.get_cycle_labels_for_plots", AsyncMock(return_value=labels or {})),
        patch(f"{_M}.plot_cycle_repo.get_latest_active_record_for_cycle", AsyncMock(return_value=latest_record)),
    )


async def _preview_one(row: dict[str, str], **lookups):
    p1, p2, p3, p4, p5 = _patch_lookups(**lookups)
    with p1, p2, p3, p4, p5:
        return await build_preview(AsyncMock(), _xlsx([row]), ctx=_ctx())


# --- one valid row + matching lookup state, per action -----------------------

def _row_create(**over) -> dict[str, str]:
    base = {
        "action": ACTION_CREATE, "supplierCode": "SUP001", "plotCode": "P101",
        "plotName": "แปลงใหม่", "province": "เชียงใหม่",
        "poNumber": "PO25001", "pCode": "Melon-A",
        # Round 8-17A.1 — required on every new-cycle action; matches
        # _cycle()'s own default label so update_current_cycle/rollover rows
        # here don't accidentally try to CLEAR the active cycle's label.
        "cycleLabel": "jul2026",
        "crop": "พริก", "variety": "พริกขี้หนู", "lotNo": "LOT-01",
        "plantingDate": "2026-06-01", "plantCount": "1000",
        "expectedYieldFull": "800", "expectedYieldUnit": "kg",
    }
    base.update(over)
    return base


def _row_final(**over) -> dict[str, str]:
    base = {
        "action": ACTION_FINAL, "supplierCode": "SUP001", "plotCode": "P001",
        "cycleLabel": "jul2026",
        "harvestYield": "1250", "finalYieldAfterClean": "1180",
        "harvestDate": "2026-07-28", "finalNote": "ผลผลิตหลังคัดแยก",
    }
    base.update(over)
    return base


# {action: (row, lookup kwargs)} — one proven-valid fixture per non-final action.
_NON_FINAL_FIXTURES = {
    ACTION_CREATE: (
        _row_create(),
        dict(plot=None, active=None),
    ),
    ACTION_START: (
        _row_create(action=ACTION_START, plotCode="P001"),
        dict(plot=_plot(), active=None),
    ),
    ACTION_UPDATE: (
        _row_create(action=ACTION_UPDATE, plotCode="P002"),
        dict(plot=_plot(), active=_cycle()),
    ),
    ACTION_ROLLOVER: (
        _row_create(action=ACTION_ROLLOVER, plotCode="P003"),
        dict(plot=_plot(), active=_cycle()),
    ),
    ACTION_START_NEXT: (
        _row_create(action=ACTION_START_NEXT, plotCode="P003", cycleLabel="sep2026"),
        dict(plot=_plot(), active=None),
    ),
    ACTION_REACTIVATE_WITH_CYCLE: (
        _row_create(action=ACTION_REACTIVATE_WITH_CYCLE, plotCode="P002", cycleLabel="aug2026"),
        dict(plot=_plot(is_active=False), active=None),
    ),
}


def test_every_supported_action_has_a_fixture():
    """Guard against silently skipping an action this round should also cover
    (e.g. a future action added to SUPPORTED_ACTIONS)."""
    assert set(_NON_FINAL_FIXTURES) == set(NON_FINAL_ACTIONS)


# --- item 1: final_plot Preview carries "kg" ---------------------------------

async def test_final_plot_preview_payload_carries_kg():
    plot, cycle = _plot(), _cycle()
    preview = await _preview_one(_row_final(), plot=plot, active=cycle, latest_record=None)
    assert preview.error_rows == 0
    assert preview.rows[0].payload.final_yield_unit == "kg"


# --- items 2-6: every OTHER action's payload carries null --------------------

@pytest.mark.parametrize("action", NON_FINAL_ACTIONS)
async def test_non_final_action_payload_final_yield_unit_is_null(action):
    row, lookups = _NON_FINAL_FIXTURES[action]
    preview = await _preview_one(row, **lookups)
    assert preview.error_rows == 0, preview.rows[0].message
    assert preview.rows[0].payload.final_yield_unit is None


# --- item 7: finalInspectionRecordId stays exactly as narrow as before ------

async def test_final_plot_payload_never_carries_a_record_id_even_when_resolved():
    plot, cycle = _plot(), _cycle()
    latest = _record(plot_id=plot.id, plot_cycle_id=cycle.id)
    preview = await _preview_one(_row_final(), plot=plot, active=cycle, latest_record=latest)
    assert preview.error_rows == 0
    assert preview.rows[0].payload.final_inspection_record_id is None
    assert preview.preview_state.final_plot_rows[0].resolved_final_inspection_record_id == latest.id


@pytest.mark.parametrize("action", NON_FINAL_ACTIONS)
async def test_non_final_action_payload_final_inspection_record_id_is_null(action):
    row, lookups = _NON_FINAL_FIXTURES[action]
    preview = await _preview_one(row, **lookups)
    assert preview.error_rows == 0, preview.rows[0].message
    assert preview.rows[0].payload.final_inspection_record_id is None


# --- item 8: finalRecordNote is final_plot-only ------------------------------

async def test_final_plot_row_gets_a_found_or_none_note():
    plot, cycle = _plot(), _cycle()
    found = await _preview_one(_row_final(), plot=plot, active=cycle, latest_record=_record())
    assert found.rows[0].final_record_note == "พบบันทึกการตรวจที่ใช้สรุป"

    none_found = await _preview_one(_row_final(), plot=plot, active=cycle, latest_record=None)
    assert none_found.rows[0].final_record_note == "ไม่มีบันทึกการตรวจที่ใช้สรุป"


@pytest.mark.parametrize("action", NON_FINAL_ACTIONS)
async def test_non_final_action_final_record_note_is_null(action):
    row, lookups = _NON_FINAL_FIXTURES[action]
    preview = await _preview_one(row, **lookups)
    assert preview.error_rows == 0, preview.rows[0].message
    assert preview.rows[0].final_record_note is None


# --- item 9: template/result workbook still has neither retired column ------

def test_template_still_has_neither_retired_column():
    for column in ("finalYieldUnit", "finalInspectionRecordId"):
        assert column not in plot_import.IMPORT_COLUMNS
        assert column not in plot_import.TEMPLATE_COLUMN_DESCRIPTIONS


def test_result_workbook_still_has_neither_retired_column():
    from app.services import plot_import_report as report
    from app.services.excel_reader import read_first_sheet

    row_view = {
        "rowNumber": 2, "action": ACTION_CREATE, "supplierCode": "SUP001",
        "plotCode": "P101", "status": "valid", "message": "",
        "raw": {"action": ACTION_CREATE, "supplierCode": "SUP001", "plotCode": "P101"},
    }
    content = report.build_plot_import_result_workbook(
        [row_view], phase="PREVIEW", completed=False,
    )
    headers, _rows = read_first_sheet(content)
    assert "finalYieldUnit" not in headers
    assert "finalInspectionRecordId" not in headers


# --- item 10: final_plot commit is unaffected --------------------------------

async def test_final_plot_commit_still_writes_the_fixed_kg_unit():
    """Confirms 8-10B.1's Preview-only fix left the commit path — already
    gated to ACTION_FINAL — untouched."""
    plot, cycle = _plot(), _cycle()
    row = _row_final()
    content = _xlsx([row])
    p1, p2, p3, p4, p5 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4, p5:
        preview = await build_preview(AsyncMock(), content, ctx=_ctx())
    q1, q2, q3, q4, q5 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with q1, q2, q3, q4, q5, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.set_actual_harvest") as mk_set, \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock(return_value=cycle)):
        await commit_import(
            AsyncMock(), content, ctx=_ctx(), preview_state=preview.preview_state,
        )
    mk_set.assert_called_once()
    assert mk_set.call_args.kwargs["final_yield_unit"] == "kg"


# --- item 11: legacy nonblank finalYieldUnit is still a clear, non-echoing error

async def test_legacy_nonblank_finalYieldUnit_still_errors_without_echoing():
    headers = [*IMPORT_COLUMNS, "finalYieldUnit", "finalInspectionRecordId"]
    data = [headers, [_row_final(finalYieldUnit="g").get(c) for c in headers]]
    content = build_xlsx([("plots", data)])
    plot, cycle = _plot(), _cycle()
    p1, p2, p3, p4, p5 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4, p5:
        preview = await build_preview(AsyncMock(), content, ctx=_ctx())
    assert preview.error_rows == 1
    # the exact-equality assertion above IS the proof of non-echo — a static
    # Thai string, never templated with the user's "g".
    assert preview.rows[0].message == (
        "ไม่ต้องระบุ finalYieldUnit ระบบใช้หน่วย kg อัตโนมัติ กรุณาลบค่าจากคอลัมน์นี้"
    )

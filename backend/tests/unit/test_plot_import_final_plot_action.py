"""Excel final_plot action (round 8-7A) — records the REAL harvested yield
and closes the plot's active cycle as harvested, while Plot.is_active stays
true so a new cycle can start later. DB-less: mocks the repo lookups/writers,
same pattern as test_plot_import_reactivate_action.py.
"""
from __future__ import annotations

import datetime
import inspect
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services import plot_import
from app.services.excel_workbook import build_xlsx
from app.services.plot_import import (
    ACTION_FINAL,
    CYCLE_STATUS_HARVESTED,
    IMPORT_COLUMNS,
    ImportContext,
    ImportFileError,
    ImportHasErrors,
    build_preview,
    commit_import,
)

_M = "app.services.plot_import"


def _xlsx(rows: list[dict[str, str]]) -> bytes:
    data: list[list] = [list(IMPORT_COLUMNS)]
    for r in rows:
        data.append([r.get(c) for c in IMPORT_COLUMNS])
    return build_xlsx([("plots", data)])


# Round 8-10B — the two retired columns, in the position a pre-8-10B workbook
# had them. The reader maps by header NAME, so appending them is enough to
# reproduce a legacy file exactly.
LEGACY_COLUMNS = ["finalYieldUnit", "finalInspectionRecordId"]


def _legacy_xlsx(rows: list[dict[str, str]]) -> bytes:
    """A workbook that still carries the retired columns — what a user who
    downloaded a template before this round will actually upload."""
    headers = [*IMPORT_COLUMNS, *LEGACY_COLUMNS]
    data: list[list] = [headers]
    for r in rows:
        data.append([r.get(c) for c in headers])
    return build_xlsx([("plots", data)])


def _ctx(*, allowed=None, can_update=True) -> ImportContext:
    return ImportContext(allowed_supplier_id=allowed, can_create=True, can_update=can_update)


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
        id=uuid4(), cycle_no=3, status="active", cycle_label="jul2026",
        crop=None, variety=None, lot_no=None, planting_date=None, plant_count=None,
        expected_yield_full=None, expected_yield_unit=None,
        po_number=None, p_code=None, lot_no_source=None, lot_running_no=None,
        supplier_lot_no=None,
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        # actual-harvest fields (round 8-7A) — start unset, set_actual_harvest
        # (a real, unmocked function in these tests) fills them at commit.
        harvest_yield=None, final_yield_after_clean=None, final_yield_unit=None,
        harvest_date=None, final_note=None,
        # estimate snapshot fields (round 8-2.8A) — untouched by final_plot's
        # OWN code; close_cycle (mocked in these tests) would set them for real.
        final_yield_pct=None, final_estimated_yield=None, final_inspection_record_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _record(**kw) -> SimpleNamespace:
    base = dict(id=uuid4(), plot_id=None, plot_cycle_id=None, is_active=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _row(**over) -> dict[str, str]:
    base = {
        "action": ACTION_FINAL, "supplierCode": "SUP001", "plotCode": "P001",
        "cycleLabel": "jul2026",
        "harvestYield": "1250", "finalYieldAfterClean": "1180",
        "harvestDate": "2026-07-28",
        "finalNote": "ผลผลิตหลังคัดแยก",
    }
    base.update(over)
    return base


def _patch_lookups(*, supplier=..., plot=None, active=None, latest_record=None):
    """Round 8-10B — record_repo.get_record_full is gone from the importer:
    the snapshot record is resolved ONLY through
    plot_cycle_repo.get_latest_active_record_for_cycle, so that is the single
    lookup a test has to control."""
    sup = _supplier() if supplier is ... else supplier
    return (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=sup)),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active)),
        patch(f"{_M}.plot_cycle_repo.get_latest_active_record_for_cycle", AsyncMock(return_value=latest_record)),
    )


async def _final_preview_state(rows, *, plot, active, latest_record=None, ctx=None):
    """Build a REAL preview_state for final_plot rows (round 8-7A.1 Part B/C
    now requires the caller to echo one back on commit) using the exact same
    patched lookups the commit itself will re-validate against, so a
    happy-path test's preview and commit can never accidentally disagree.
    Returns (content, preview_state) — pass content into commit_import so the
    file-digest check matches too."""
    ctx = ctx or _ctx()
    content = _xlsx(rows)
    p1, p2, p3, p4 = _patch_lookups(
        plot=plot, active=active, latest_record=latest_record,
    )
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), content, ctx=ctx)
    return content, preview.preview_state


# --- item: action recognized -------------------------------------------------

def test_final_plot_action_is_recognized_supported_action():
    assert ACTION_FINAL in plot_import.SUPPORTED_ACTIONS
    assert ACTION_FINAL == "final_plot"


def test_final_plot_not_in_new_cycle_actions_never_requires_po_p_code():
    assert ACTION_FINAL not in plot_import._NEW_CYCLE_ACTIONS


# --- item: parser + all-fields-required validation ---------------------------

async def test_valid_row_with_all_required_fields_is_valid():
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    assert preview.error_rows == 0
    assert preview.rows[0].status == "valid"
    assert preview.rows[0].action == ACTION_FINAL


async def test_final_plot_row_never_gets_a_fabricated_lot_preview():
    """Regression: final_plot never touches lot_no/po_number at all — a
    naive lot-preview computation would otherwise invent a nonsense Auto Lot
    string ("<PO>-{plotCode}-XX") from this action's always-blank po_number."""
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    assert preview.rows[0].lot_mode is None
    assert preview.rows[0].proposed_lot_no is None


@pytest.mark.parametrize("missing_field", ["harvestYield", "finalYieldAfterClean", "harvestDate"])
async def test_missing_required_field_is_invalid(missing_field):
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row(**{missing_field: ""})]), ctx=_ctx())
    assert preview.error_rows == 1
    assert missing_field in preview.rows[0].message


async def test_final_note_is_optional():
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row(finalNote="")]), ctx=_ctx())
    assert preview.error_rows == 0


# --- item: zero yield valid / negative reject --------------------------------

async def test_zero_harvest_yield_is_valid():
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(
            AsyncMock(), _xlsx([_row(harvestYield="0", finalYieldAfterClean="0")]), ctx=_ctx(),
        )
    assert preview.error_rows == 0


async def test_negative_harvest_yield_is_rejected():
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row(harvestYield="-1")]), ctx=_ctx())
    assert preview.error_rows == 1
    assert "ติดลบ" in preview.rows[0].message


async def test_negative_final_yield_after_clean_is_rejected():
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row(finalYieldAfterClean="-5")]), ctx=_ctx())
    assert preview.error_rows == 1
    assert "ติดลบ" in preview.rows[0].message


# --- item: the unit is fixed at kg (round 8-10B) -----------------------------

async def test_the_unit_is_stamped_kg_without_the_file_saying_anything():
    """The column is gone; the value is not. Every final_plot row carries
    "kg" into the payload because the server puts it there."""
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    assert preview.error_rows == 0
    assert preview.rows[0].payload.final_yield_unit == "kg"
    assert plot_import.FINAL_PLOT_FIXED_YIELD_UNIT == "kg"


async def test_a_legacy_file_with_a_blank_finalYieldUnit_column_is_accepted():
    """A workbook downloaded before this round still HAS the column. An empty
    cell is simply an older file, not a mistake."""
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _legacy_xlsx([_row(finalYieldUnit="")]), ctx=_ctx())
    assert preview.error_rows == 0
    assert preview.rows[0].payload.final_yield_unit == "kg"


@pytest.mark.parametrize("unit", ["kg", "g", "ตัน", "KG", "หน่วยลึกลับ"])
async def test_a_legacy_file_that_still_FILLS_finalYieldUnit_is_rejected(unit):
    """Never silently ignored — including "kg", which happens to be what the
    server would have used anyway. The user typed something into a column that
    no longer has any effect, and the only honest answer is to say so."""
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _legacy_xlsx([_row(finalYieldUnit=unit)]), ctx=_ctx())
    assert preview.error_rows == 1
    assert preview.rows[0].message == (
        "ไม่ต้องระบุ finalYieldUnit ระบบใช้หน่วย kg อัตโนมัติ กรุณาลบค่าจากคอลัมน์นี้"
    )
    # the exact-equality assertion above is itself the proof that the value
    # the user typed is never quoted back at them


async def test_finalYieldUnit_is_gone_from_the_import_contract():
    assert "finalYieldUnit" not in plot_import.IMPORT_COLUMNS
    assert "finalYieldUnit" not in plot_import.TEMPLATE_COLUMN_DESCRIPTIONS


async def test_expected_yield_unit_validation_is_unaffected_by_final_yield_unit_allowlist():
    """Part E is scoped to final_plot's finalYieldUnit only — expectedYieldUnit
    (used by every OTHER action) must still accept any nonblank value up to
    its length limit, no allowlist added to it."""
    p1, p2, p3 = (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=None)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=None)),
    )
    row = {
        "action": "create_plot_with_cycle", "supplierCode": "SUP001", "plotCode": "P101",
        "plotName": "แปลงใหม่", "province": "เชียงใหม่", "poNumber": "PO25001", "pCode": "Melon-A",
        # cycleLabel required with a blank lotNo (round 8-12A.1).
        "cycleLabel": "2605",
        "crop": "พริก", "variety": "พริกขี้หนู", "plantingDate": "2026-06-01",
        "expectedYieldFull": "800", "expectedYieldUnit": "กก. (ไม่มาตรฐาน)",
    }
    with p1, p2, p3:
        preview = await build_preview(AsyncMock(), _xlsx([row]), ctx=_ctx())
    assert preview.error_rows == 0


# --- item: no active cycle ----------------------------------------------------

async def test_no_active_cycle_is_invalid_with_exact_message():
    plot = _plot()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    assert preview.error_rows == 1
    assert preview.rows[0].message == "แปลงนี้ไม่มีรอบปลูกที่เปิดอยู่ จึงไม่สามารถลงผลผลิตสุดท้ายได้"


# --- item: inactive plot -------------------------------------------------------

async def test_inactive_plot_is_invalid():
    plot = _plot(is_active=False)
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    assert preview.error_rows == 1
    assert "ปิดถาวร" in preview.rows[0].message or "inactive" in preview.rows[0].message.lower()


# --- item: cycleLabel mismatch --------------------------------------------------

async def test_cycle_label_mismatch_is_invalid_with_exact_message():
    plot = _plot()
    cycle = _cycle(cycle_label="jun2026")
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row(cycleLabel="jul2026")]), ctx=_ctx())
    assert preview.error_rows == 1
    assert preview.rows[0].message == (
        "ชื่อรอบปลูกในไฟล์ไม่ตรงกับรอบที่เปิดอยู่ กรุณาดาวน์โหลดข้อมูลล่าสุดและตรวจสอบอีกครั้ง"
    )


async def test_cycle_label_match_is_case_and_whitespace_insensitive():
    plot = _plot()
    cycle = _cycle(cycle_label=" Jul2026 ")
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row(cycleLabel="jul2026")]), ctx=_ctx())
    assert preview.error_rows == 0


async def test_both_cycle_label_blank_counts_as_a_match():
    plot = _plot()
    cycle = _cycle(cycle_label=None)
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row(cycleLabel="")]), ctx=_ctx())
    assert preview.error_rows == 0


# --- item: the record is resolved server-side (round 8-10B) -----------------

async def test_the_latest_active_record_of_the_cycle_is_resolved_automatically():
    """No column, no id, no choice to get wrong: whatever the cycle's latest
    active record is at Preview time is what the row binds to."""
    plot = _plot()
    cycle = _cycle()
    latest = _record(plot_id=plot.id, cycle_id=cycle.id)
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=latest)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    assert preview.error_rows == 0
    assert preview.preview_state.final_plot_rows[0].resolved_final_inspection_record_id == latest.id


async def test_the_resolver_is_scoped_to_this_cycle_by_construction():
    """The only query the importer can make is "latest active record OF THIS
    CYCLE" — there is no code path left that could reach another cycle's or
    another plot's record, because there is no id to reach it with."""
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4 as mk_latest:
        await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    # called with the ACTIVE cycle's id and nothing else
    assert mk_latest.await_args.args[1] == cycle.id


async def test_the_importer_no_longer_looks_records_up_by_id_at_all():
    src = inspect.getsource(plot_import)
    assert "record_repo" not in src
    assert "get_record_full" not in src


async def test_a_legacy_file_with_a_blank_finalInspectionRecordId_column_is_accepted():
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(
            AsyncMock(), _legacy_xlsx([_row(finalInspectionRecordId="")]), ctx=_ctx(),
        )
    assert preview.error_rows == 0


async def test_a_legacy_file_that_still_FILLS_finalInspectionRecordId_is_rejected():
    plot = _plot()
    cycle = _cycle()
    given = str(uuid4())
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(
            AsyncMock(), _legacy_xlsx([_row(finalInspectionRecordId=given)]), ctx=_ctx(),
        )
    assert preview.error_rows == 1
    assert preview.rows[0].message == (
        "ไม่ต้องระบุ finalInspectionRecordId ระบบเลือกบันทึกการตรวจล่าสุดให้อัตโนมัติ "
        "กรุณาลบค่าจากคอลัมน์นี้"
    )
    # The id the user supplied is NEVER echoed — not in the message, not
    # anywhere else in the preview row.
    assert given not in preview.rows[0].message
    assert given not in str(preview.rows[0].model_dump())


async def test_even_a_malformed_finalInspectionRecordId_is_the_same_message():
    """It is not parsed at all any more, so "not a UUID" is not a distinct
    case — and the junk the user typed still never comes back."""
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(
            AsyncMock(), _legacy_xlsx([_row(finalInspectionRecordId="not-a-uuid")]), ctx=_ctx(),
        )
    assert preview.error_rows == 1
    assert "ไม่ต้องระบุ finalInspectionRecordId" in preview.rows[0].message
    assert "not-a-uuid" not in preview.rows[0].message


async def test_the_row_payload_never_carries_a_user_supplied_record_id():
    plot = _plot()
    cycle = _cycle()
    latest = _record(plot_id=plot.id, cycle_id=cycle.id)
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=latest)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    # raw input is always None; previewState holds the authoritative resolution
    assert preview.rows[0].payload.final_inspection_record_id is None
    assert preview.preview_state.final_plot_rows[0].resolved_final_inspection_record_id == latest.id


async def test_finalInspectionRecordId_is_gone_from_the_import_contract():
    assert "finalInspectionRecordId" not in plot_import.IMPORT_COLUMNS
    assert "finalInspectionRecordId" not in plot_import.TEMPLATE_COLUMN_DESCRIPTIONS


# --- item: no Record at all -> still finalize, estimate fields NULL ----------

async def test_no_record_at_all_still_valid_and_finalizes():
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    assert preview.error_rows == 0


# --- item: after-clean > harvest -> warning, never blocks ---------------------

async def test_final_yield_after_clean_greater_than_harvest_is_a_non_blocking_warning():
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(
            AsyncMock(), _xlsx([_row(harvestYield="1000", finalYieldAfterClean="1100")]), ctx=_ctx(),
        )
    assert preview.error_rows == 0
    assert preview.rows[0].status == "valid"
    assert preview.rows[0].warning is not None
    assert "finalYieldAfterClean" in preview.rows[0].warning


async def test_final_yield_after_clean_less_than_or_equal_harvest_has_no_warning():
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(
            AsyncMock(), _xlsx([_row(harvestYield="1000", finalYieldAfterClean="1000")]), ctx=_ctx(),
        )
    assert preview.rows[0].warning is None


# --- item: preview never mutates ----------------------------------------------

async def test_preview_never_calls_close_cycle_or_set_actual_harvest():
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4, \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()) as mk_close, \
         patch(f"{_M}.plot_cycle_repo.set_actual_harvest", AsyncMock()) as mk_set, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock()) as mk_lock, \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock()) as mk_cycle_lock:
        await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    mk_close.assert_not_awaited()
    mk_set.assert_not_called()
    mk_lock.assert_not_awaited()
    mk_cycle_lock.assert_not_awaited()


# --- item: permission ----------------------------------------------------------

async def test_final_plot_requires_plots_update_permission():
    plot = _plot()
    cycle = _cycle()
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx(can_update=False))
    assert preview.error_rows == 1
    assert "plots.update" in preview.rows[0].message


# --- item: commit writes actual fields, keeps estimate snapshot separate -----

async def test_commit_writes_actual_harvest_fields_and_closes_harvested():
    plot = _plot()
    cycle = _cycle(status="active")
    rows = [_row(
        harvestYield="1250", finalYieldAfterClean="1180", finalYieldUnit="kg",
        harvestDate="2026-07-28", finalNote="โน้ต",
    )]
    content, preview_state = await _final_preview_state(rows, plot=plot, active=cycle)

    async def _closer(db, cyc, *, status, closed_by_id, reason, final_estimate_record=None):
        assert status == CYCLE_STATUS_HARVESTED
        assert reason == plot_import.FINAL_PLOT_CLOSE_REASON
        cyc.status = status
        cyc.closed_by_id = closed_by_id
        cyc.close_reason = reason
        # Simulate the REAL close_cycle's estimate snapshot (kept separate
        # from the actual-harvest fields set_actual_harvest already wrote).
        cyc.final_yield_pct = Decimal("80.0")
        cyc.final_estimated_yield = Decimal("640.00")
        cyc.final_inspection_record_id = uuid4()
        return cyc

    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock(side_effect=_closer)) as mk_close:
        result = await commit_import(
            AsyncMock(), content, ctx=_ctx(), preview_state=preview_state,
        )
    mk_close.assert_awaited_once()
    # Round 8-7A.1 — no Record at all (latest_record=None) → close_cycle is
    # handed final_estimate_record=None explicitly (never omitted, so it
    # never falls back to close_cycle's own internal "resolve latest" path).
    assert mk_close.call_args.kwargs["final_estimate_record"] is None
    # set_actual_harvest is real (unmocked) — verify it actually wrote onto the cycle.
    assert cycle.harvest_yield == Decimal("1250")
    assert cycle.final_yield_after_clean == Decimal("1180")
    assert cycle.final_yield_unit == "kg"
    assert cycle.harvest_date == datetime.date(2026, 7, 28)
    assert cycle.final_note == "โน้ต"
    # close_cycle's OWN estimate snapshot is separate data, untouched by
    # set_actual_harvest, and still present.
    assert cycle.final_yield_pct == Decimal("80.0")
    assert cycle.final_estimated_yield == Decimal("640.00")
    assert cycle.status == CYCLE_STATUS_HARVESTED
    assert result.finalized_plots == 1
    assert result.row_results[0].action == ACTION_FINAL


# --- item: the snapshot source is the server-resolved record (round 8-10B) --
# Round 8-7A.1 made an explicit finalInspectionRecordId authoritative; round
# 8-10B removed the column, so "the record the server resolved" is now the only
# possible source and close_cycle must be handed exactly that object.

async def test_commit_hands_close_cycle_the_server_resolved_record():
    plot = _plot()
    cycle = _cycle()
    latest = _record(plot_id=plot.id, plot_cycle_id=cycle.id)
    content, preview_state = await _final_preview_state(
        [_row()], plot=plot, active=cycle, latest_record=latest,
    )
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=latest)
    with p1, p2, p3, p4, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock(return_value=cycle)) as mk_close:
        await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=preview_state)
    mk_close.assert_awaited_once()
    # the SAME object the resolver returned — close_cycle never re-queries
    assert mk_close.call_args.kwargs["final_estimate_record"] is latest


async def test_commit_writes_the_fixed_kg_unit_onto_the_cycle():
    plot = _plot()
    cycle = _cycle()
    content, preview_state = await _final_preview_state([_row()], plot=plot, active=cycle)
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.set_actual_harvest") as mk_set, \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock(return_value=cycle)):
        await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=preview_state)
    mk_set.assert_called_once()
    assert mk_set.call_args.kwargs["final_yield_unit"] == "kg"


async def test_commit_blank_record_id_uses_latest_as_the_snapshot_source():
    plot = _plot()
    cycle = _cycle()
    latest = _record(plot_id=plot.id, plot_cycle_id=cycle.id)
    content, preview_state = await _final_preview_state(
        [_row()], plot=plot, active=cycle, latest_record=latest,
    )
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=latest)
    with p1, p2, p3, p4, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock(return_value=cycle)) as mk_close:
        await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=preview_state)
    assert mk_close.call_args.kwargs["final_estimate_record"] is latest


async def test_commit_plot_stays_active_never_reactivated_never_deactivated():
    plot = _plot(is_active=True)
    cycle = _cycle()
    content, preview_state = await _final_preview_state([_row()], plot=plot, active=cycle)
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock(return_value=cycle)):
        await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=preview_state)
    assert plot.is_active is True


async def test_commit_never_calls_create_cycle_or_reactivate_helper():
    plot = _plot()
    cycle = _cycle()
    content, preview_state = await _final_preview_state([_row()], plot=plot, active=cycle)
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock()) as mk_create, \
         patch(f"{_M}.plot_repo.reactivate_plot_with_cycle", AsyncMock()) as mk_reactivate:
        await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=preview_state)
    mk_create.assert_not_awaited()
    mk_reactivate.assert_not_awaited()


def test_execute_row_final_branch_never_touches_records_or_qr_in_source():
    src = inspect.getsource(plot_import._execute_row)
    idx = src.index("if p.action == ACTION_FINAL:")
    end = src.index("# ACTION_UPDATE")
    branch = src[idx:end]
    assert "await plot_cycle_repo.close_cycle(" in branch
    assert "set_actual_harvest(" in branch
    assert "create_cycle" not in branch
    assert "qr_key" not in branch
    assert ".is_active = True" not in branch
    assert ".is_active=True" not in branch


# --- item: duplicate/replay + stale -> reject, no double-write --------------

async def test_replay_after_cycle_already_closed_is_rejected_and_does_not_write():
    """The cycle this row targeted was already closed (e.g. this exact file
    was committed once already) — round 8-7A.1 Part C's under-lock preview-
    state verification now catches this BEFORE any row executes (the active
    cycle vanishing is one of the state-drift checks _verify_final_plot_
    snapshot performs), so it surfaces as the generic ImportPreviewStateConflict
    (never silently re-closing/re-writing anything) rather than reaching
    _execute_row's own defense-in-depth replay check at all."""
    plot = _plot()
    cycle = _cycle()  # preview sees an active cycle...
    content, preview_state = await _final_preview_state([_row()], plot=plot, active=cycle)
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=cycle, latest_record=None)
    with p1, p2, p3, p4, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()) as mk_close:
        with pytest.raises(plot_import.ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.message == plot_import._MSG_STATE_CHANGED
    mk_close.assert_not_awaited()


def test_execute_row_final_branch_still_rejects_replay_if_ever_reached_in_source():
    """Defense-in-depth: even though _verify_final_plot_snapshot now catches a
    replay before execute in every real commit, _execute_row's OWN 'already
    closed' check must stay in place (same pattern rollover/start_next use)."""
    src = inspect.getsource(plot_import._execute_row)
    idx = src.index("if p.action == ACTION_FINAL:")
    end = src.index("# ACTION_UPDATE")
    branch = src[idx:end]
    assert "ถูกปิดแล้ว" in branch


async def test_stale_cycle_label_at_commit_time_is_rejected():
    """The active cycle's label changed between preview and commit (e.g. a
    concurrent update_current_cycle row) — round 8-7A.1 Part C's under-lock
    verification catches this before any row executes, as a generic
    ImportPreviewStateConflict (same architecture as the replay case above)."""
    plot = _plot()
    preview_cycle = _cycle(cycle_label="jul2026")
    content, preview_state = await _final_preview_state(
        [_row(cycleLabel="jul2026")], plot=plot, active=preview_cycle,
    )
    # Same cycle identity/cycle_no/updated_at as Preview saw — ONLY the label
    # drifted, isolating this test to the label-mismatch branch specifically.
    locked_cycle = _cycle(
        id=preview_cycle.id, cycle_no=preview_cycle.cycle_no,
        updated_at=preview_cycle.updated_at, cycle_label="aug2026",
    )
    p1, p2, p3, p4 = _patch_lookups(plot=plot, active=preview_cycle, latest_record=None)
    with p1, p2, p3, p4, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=locked_cycle)), \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()) as mk_close:
        with pytest.raises(plot_import.ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.message == plot_import._MSG_STATE_CHANGED
    mk_close.assert_not_awaited()


def test_execute_row_final_branch_uses_shared_record_resolver_in_source():
    """Round 8-7A.1 — _execute_row delegates to the shared
    _resolve_final_inspection_record helper (also used by _validate_row and
    _verify_final_plot_snapshot) so all three can never disagree on which
    Record is the snapshot source.

    Round 8-10B — that helper now has exactly one query in it: the cycle's
    latest active record. Nothing resolves a record by id any more."""
    src = inspect.getsource(plot_import._execute_row)
    idx = src.index("if p.action == ACTION_FINAL:")
    end = src.index("# ACTION_UPDATE")
    branch = src[idx:end]
    assert "_resolve_final_inspection_record(" in branch
    assert "final_estimate_record=resolved_record" in branch
    helper_src = inspect.getsource(plot_import._resolve_final_inspection_record)
    assert "get_latest_active_record_for_cycle(db, cycle_id)" in helper_src
    assert "get_record_full" not in helper_src


# --- item: transaction rollback when another row fails (all-or-nothing) ----

async def test_commit_all_or_nothing_when_file_has_other_invalid_row():
    plot = _plot()
    cycle = _cycle()
    good_row = _row()
    bad_row = _row(action="update_current_cycle", plotCode="P999")

    async def _get_plot_by_code(db, supplier_id, code):
        return plot if code == "P001" else None

    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(side_effect=_get_plot_by_code)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_for_cycle", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()) as mk_close:
        with pytest.raises(ImportHasErrors):
            await commit_import(AsyncMock(), _xlsx([good_row, bad_row]), ctx=_ctx())
    mk_close.assert_not_awaited()


# --- item: lock order Plot -> Cycle (source-level) ---------------------------

def test_execute_row_final_branch_locks_cycle_after_plot_via_for_update():
    src = inspect.getsource(plot_import._execute_row)
    idx = src.index("if p.action == ACTION_FINAL:")
    end = src.index("# ACTION_UPDATE")
    branch = src[idx:end]
    assert "get_active_cycle_for_plot_for_update(db, plot.id)" in branch


async def test_lock_existing_plots_still_requires_plot_active_for_final_plot_no_regression():
    """final_plot is not reactivate_plot_with_cycle — an inactive plot at
    lock time must still be rejected the ordinary way (no inverted
    precondition for this action)."""
    from app.services.plot_import import _Parsed, _RowState, _lock_existing_plots

    plot_id = uuid4()
    row = _RowState(row_number=1, parsed=_Parsed(action=ACTION_FINAL, plot_code="P001"), existing_plot_id=plot_id)
    inactive_plot = _plot(id=plot_id, is_active=False)
    with patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=inactive_plot)):
        with pytest.raises(ImportFileError):
            await _lock_existing_plots(AsyncMock(), [row])


# --- item: error messages never leak secrets ---------------------------------

def test_final_plot_error_messages_never_mention_qr_or_secrets():
    forbidden = ("qr_key", "qrKey", "token", "secret")
    messages = [
        "แปลงนี้ไม่มีรอบปลูกที่เปิดอยู่ จึงไม่สามารถลงผลผลิตสุดท้ายได้",
        "ชื่อรอบปลูกในไฟล์ไม่ตรงกับรอบที่เปิดอยู่ กรุณาดาวน์โหลดข้อมูลล่าสุดและตรวจสอบอีกครั้ง",
        "รอบปลูกนี้ถูกปิดแล้ว ไม่สามารถลงผลผลิตสุดท้ายซ้ำได้",
        "รหัสบันทึกการตรวจไม่อยู่ในแปลงหรือรอบปลูกที่ระบุ",
    ]
    for msg in messages:
        for bad in forbidden:
            assert bad not in msg

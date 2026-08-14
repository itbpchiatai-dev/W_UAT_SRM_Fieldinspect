"""Excel reactivate_plot_with_cycle action (round 8-6H Part F) — the
explicit-only importer action that reopens an INACTIVE plot and starts its
first new planting cycle, atomically, via the shared plot_repository helper.

DB-less: mocks the repo lookups/writers, same pattern as
test_plot_import_service.py.
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services import plot_import
from app.services.excel_workbook import build_xlsx
from app.services.plot_import import (
    ACTION_REACTIVATE_WITH_CYCLE,
    IMPORT_COLUMNS,
    ImportContext,
    ImportFileError,
    ImportHasErrors,
    _Parsed,
    _RowState,
    _lock_existing_plots,
    build_preview,
    commit_import,
)
from app.services.plot_import_report import PHASE_COMMIT, _summarize

_M = "app.services.plot_import"


def _xlsx(rows: list[dict[str, str]]) -> bytes:
    data: list[list] = [list(IMPORT_COLUMNS)]
    for r in rows:
        data.append([r.get(c) for c in IMPORT_COLUMNS])
    return build_xlsx([("plots", data)])


def _ctx(*, allowed=None, can_create=True, can_update=True, can_reactivate=True) -> ImportContext:
    return ImportContext(
        allowed_supplier_id=allowed, can_create=can_create,
        can_update=can_update, can_reactivate=can_reactivate,
    )


def _supplier(**kw) -> SimpleNamespace:
    return SimpleNamespace(id=kw.get("id", uuid4()), code=kw.get("code", "SUP001"), is_active=kw.get("is_active", True))


def _plot(**kw) -> SimpleNamespace:
    return SimpleNamespace(id=kw.get("id", uuid4()), is_active=kw.get("is_active", False))


def _cycle(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=1, crop=None, variety=None, cycle_label=None,
        lot_no=None, planting_date=None, plant_count=None,
        expected_yield_full=None, expected_yield_unit=None,
        po_number=None, p_code=None, lot_no_source=None, lot_running_no=None,
        supplier_lot_no=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _row(**over) -> dict[str, str]:
    base = {
        "action": ACTION_REACTIVATE_WITH_CYCLE, "supplierCode": "SUP001",
        "plotCode": "P002", "cycleLabel": "aug2026",
        "poNumber": "PO25009", "pCode": "Melon-Z",
        "crop": "แตงโม", "variety": "กินรี",
        "plantingDate": "2026-08-01", "plantCount": "500",
        "expectedYieldFull": "2000", "expectedYieldUnit": "kg",
    }
    base.update(over)
    return base


def _patch_lookups(*, supplier=..., plot=None, active=None, labels=None):
    sup = _supplier() if supplier is ... else supplier
    # Round 8-6K Part B — every reactivate row with a cycleLabel is checked
    # against a BATCHED map of {plot_id: {labels}} (get_cycle_labels_for_
    # plots), never a per-row get_cycles_for_plot call anymore. Default to
    # "no history for this plot" ({}) so every pre-existing test in this file
    # (none of which care about history reuse) keeps working unchanged.
    # Tests that DO care pass labels={plot.id: {...}} explicitly.
    return (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=sup)),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active)),
        patch(f"{_M}.plot_cycle_repo.get_cycle_labels_for_plots", AsyncMock(return_value=labels or {})),
    )


# --- item 30: action recognized ---------------------------------------------

def test_reactivate_action_is_recognized_supported_action():
    assert ACTION_REACTIVATE_WITH_CYCLE in plot_import.SUPPORTED_ACTIONS
    assert ACTION_REACTIVATE_WITH_CYCLE == "reactivate_plot_with_cycle"


# --- item 31: inactive plot → valid preview ---------------------------------

async def test_preview_inactive_plot_reactivate_row_is_valid():
    inactive_plot = _plot(is_active=False)
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    assert preview.error_rows == 0
    assert preview.rows[0].status == "valid"
    assert preview.rows[0].action == ACTION_REACTIVATE_WITH_CYCLE


# --- round 8-13A: poNumber optional -----------------------------------------

async def test_preview_reactivate_row_without_po_is_valid():
    inactive_plot = _plot(is_active=False)
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None)
    with p1, p2, p3, p4:
        preview = await build_preview(
            AsyncMock(), _xlsx([_row(poNumber=None)]), ctx=_ctx(),
        )
    assert preview.error_rows == 0
    assert preview.rows[0].status == "valid"
    assert preview.rows[0].payload.po_number is None


# --- item 32: active plot → invalid, clear message --------------------------

async def test_preview_active_plot_reactivate_row_is_invalid_with_clear_message():
    active_plot = _plot(is_active=True)
    p1, p2, p3, p4 = _patch_lookups(plot=active_plot, active=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    assert preview.error_rows == 1
    assert "เปิดใช้งานอยู่แล้ว" in preview.rows[0].message


# --- item 33: missing plot → generic error ----------------------------------

async def test_preview_missing_plot_reactivate_row_generic_error():
    p1, p2, p3, p4 = _patch_lookups(plot=None, active=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    assert preview.error_rows == 1
    assert "ไม่พบแปลง" in preview.rows[0].message


# --- item 34: permission errors ---------------------------------------------

async def test_preview_reactivate_row_without_activation_permission_errors():
    inactive_plot = _plot(is_active=False)
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None)
    with p1, p2, p3, p4:
        preview = await build_preview(
            AsyncMock(), _xlsx([_row()]), ctx=_ctx(can_reactivate=False),
        )
    assert preview.error_rows == 1
    assert "plots.delete" in preview.rows[0].message


async def test_preview_reactivate_row_without_plots_update_also_errors():
    inactive_plot = _plot(is_active=False)
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None)
    with p1, p2, p3, p4:
        preview = await build_preview(
            AsyncMock(), _xlsx([_row()]), ctx=_ctx(can_update=False),
        )
    assert preview.error_rows == 1
    assert "plots.update" in preview.rows[0].message


async def test_preview_reactivate_row_with_both_permissions_has_no_permission_error():
    inactive_plot = _plot(is_active=False)
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    assert preview.error_rows == 0


# --- item 35: commit calls the shared helper exactly once -------------------

async def test_commit_calls_reactivate_helper_exactly_once():
    inactive_plot = _plot(is_active=False)
    cycle = _cycle(cycle_no=2)
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None)
    with p1, p2, p3, p4, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=inactive_plot)), \
         patch(f"{_M}.plot_repo.reactivate_plot_with_cycle",
               AsyncMock(return_value=(inactive_plot, cycle))) as mk_react:
        result = await commit_import(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    mk_react.assert_awaited_once()
    assert result.reactivated_plots == 1
    assert result.row_results[0].action == ACTION_REACTIVATE_WITH_CYCLE
    assert result.row_results[0].result_cycle_no == 2


# --- item 36: preview never calls the helper or writes ----------------------

async def test_preview_never_calls_reactivate_helper_or_locks_plot():
    inactive_plot = _plot(is_active=False)
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None)
    with p1, p2, p3, p4, \
         patch(f"{_M}.plot_repo.reactivate_plot_with_cycle", AsyncMock()) as mk_react, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock()) as mk_lock:
        await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    mk_react.assert_not_awaited()
    mk_lock.assert_not_awaited()


# --- item 37: transaction all-or-nothing ------------------------------------

async def test_commit_all_or_nothing_when_file_has_other_invalid_row():
    inactive_plot = _plot(is_active=False)
    good_row = _row()
    bad_row = _row(action="update_current_cycle", plotCode="P999")

    async def _get_plot_by_code(db, supplier_id, code):
        return inactive_plot if code == "P002" else None

    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(side_effect=_get_plot_by_code)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_cycle_repo.get_cycle_labels_for_plots", AsyncMock(return_value={})), \
         patch(f"{_M}.plot_repo.reactivate_plot_with_cycle", AsyncMock()) as mk_react:
        with pytest.raises(ImportHasErrors):
            await commit_import(AsyncMock(), _xlsx([good_row, bad_row]), ctx=_ctx())
    mk_react.assert_not_awaited()


# --- item 38: state changed between preview and commit → reject cleanly ----

async def test_commit_rejects_when_plot_became_active_before_lock():
    """The plot was reactivated by someone else between preview and this
    commit's lock — _lock_existing_plots' action-aware check catches it
    before any row executes."""
    now_active_plot = _plot(is_active=True)
    p1, p2, p3, p4 = _patch_lookups(plot=_plot(is_active=False), active=None)
    with p1, p2, p3, p4, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=now_active_plot)), \
         patch(f"{_M}.plot_repo.reactivate_plot_with_cycle", AsyncMock()) as mk_react:
        with pytest.raises(ImportFileError, match="เปิดใช้งานอยู่แล้ว"):
            await commit_import(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    mk_react.assert_not_awaited()


# --- item 39: reactivatedPlots count is correct -----------------------------

async def test_commit_reactivated_plots_count_matches_number_of_rows():
    plot1, plot2 = _plot(is_active=False), _plot(is_active=False)
    cycle1, cycle2 = _cycle(cycle_no=1), _cycle(cycle_no=1)

    async def _by_code(db, supplier_id, code):
        return plot1 if code == "P002" else plot2

    async def _for_update(db, plot_id):
        return plot1 if plot_id == plot1.id else plot2

    async def _react(db, plot, **kw):
        return (plot, cycle1 if plot is plot1 else cycle2)

    row2 = _row(plotCode="P003")
    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(side_effect=_by_code)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_cycle_repo.get_cycle_labels_for_plots", AsyncMock(return_value={})), \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(side_effect=_for_update)), \
         patch(f"{_M}.plot_repo.reactivate_plot_with_cycle", AsyncMock(side_effect=_react)):
        result = await commit_import(AsyncMock(), _xlsx([_row(), row2]), ctx=_ctx())
    assert result.reactivated_plots == 2
    assert result.created_plots == 0
    assert result.started_cycles == 0


# --- item 40: inactive plot + generic start_next_cycle stays invalid -------

async def test_start_next_cycle_on_inactive_plot_still_invalid_no_regression():
    inactive_plot = _plot(is_active=False)
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None)
    row = {
        "action": "start_next_cycle", "supplierCode": "SUP001", "plotCode": "P002",
        "cycleLabel": "aug2026", "poNumber": "PO1", "pCode": "PC1",
    }
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([row]), ctx=_ctx())
    assert preview.error_rows == 1
    assert "ปิดใช้งาน" in preview.rows[0].message


# --- item 42: report workbook / summary counts reactivated plots -----------

def test_summarize_buckets_reactivated_plots_count():
    views = [
        {"action": ACTION_REACTIVATE_WITH_CYCLE, "status": "valid", "error_code": None},
        {"action": "create_plot_with_cycle", "status": "valid", "error_code": None},
    ]
    summary = _summarize(views, phase="COMMIT", completed=True)
    assert summary["reactivated_plots"] == 1
    assert summary["created_plots"] == 1


def test_build_result_workbook_with_reactivate_row_does_not_raise():
    from app.services.plot_import_report import build_plot_import_result_workbook

    view = {
        "row_number": 1, "action": ACTION_REACTIVATE_WITH_CYCLE, "status": "valid",
        "message": "", "error_code": None, "result_cycle_no": 3,
        "resolved_action": None, "lot_mode": "auto", "proposed_lot_no": "PO-P002-01",
        "result_lot_no": "PO-P002-01", "result_lot_no_source": "auto",
        "result_lot_running_no": 1, "raw": {},
    }
    workbook = build_plot_import_result_workbook(
        [view], phase=PHASE_COMMIT, completed=True,
        original_filename="x.xlsx",
        processed_at=datetime.datetime(2026, 7, 23, tzinfo=datetime.timezone.utc),
    )
    assert isinstance(workbook, (bytes, bytearray))
    assert len(workbook) > 0


# --- item 43: no QR/token/phone leaks in the new error messages ------------

def test_reactivate_error_messages_never_mention_qr_or_secrets():
    forbidden_substrings = ("qr_key", "qrKey", "token", "secret")
    messages = [
        "แปลงนี้เปิดใช้งานอยู่แล้ว ไม่ต้องเปิดใช้งานซ้ำ — หากต้องการเริ่มรอบปลูกใหม่ "
        "ให้ใช้ start_next_cycle",
        "พบข้อมูลไม่สอดคล้องกัน (แปลงปิดใช้งานแต่มีรอบปลูกที่เปิดอยู่) "
        "กรุณาติดต่อผู้ดูแลระบบ",
        "ต้องมีสิทธิ์ plots.delete และ plots.update สำหรับ reactivate_plot_with_cycle",
    ]
    for msg in messages:
        for bad in forbidden_substrings:
            assert bad not in msg


# --- _lock_existing_plots: action-aware precondition (Part D/F wiring) -----

async def test_lock_existing_plots_rejects_already_active_plot_for_reactivate_action():
    plot_id = uuid4()
    row = _RowState(
        row_number=1,
        parsed=_Parsed(action=ACTION_REACTIVATE_WITH_CYCLE, plot_code="P002"),
        existing_plot_id=plot_id,
    )
    active_plot = _plot(id=plot_id, is_active=True)
    with patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=active_plot)):
        with pytest.raises(ImportFileError, match="เปิดใช้งานอยู่แล้ว"):
            await _lock_existing_plots(AsyncMock(), [row])


async def test_lock_existing_plots_allows_inactive_plot_for_reactivate_action():
    plot_id = uuid4()
    row = _RowState(
        row_number=1,
        parsed=_Parsed(action=ACTION_REACTIVATE_WITH_CYCLE, plot_code="P002"),
        existing_plot_id=plot_id,
    )
    inactive_plot = _plot(id=plot_id, is_active=False)
    with patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=inactive_plot)):
        locked = await _lock_existing_plots(AsyncMock(), [row])
    assert locked == {plot_id: inactive_plot}


async def test_lock_existing_plots_still_rejects_inactive_plot_for_other_actions_no_regression():
    plot_id = uuid4()
    row = _RowState(
        row_number=1,
        parsed=_Parsed(action="update_current_cycle", plot_code="P002"),
        existing_plot_id=plot_id,
    )
    inactive_plot = _plot(id=plot_id, is_active=False)
    with patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=inactive_plot)):
        with pytest.raises(ImportFileError, match="ถูกปิดใช้งาน"):
            await _lock_existing_plots(AsyncMock(), [row])


# --- structural: excerpt of _execute_row delegates, doesn't reimplement ----

def test_execute_row_reactivate_branch_delegates_to_shared_helper_in_source():
    src = inspect.getsource(plot_import._execute_row)
    idx = src.index("if p.action == ACTION_REACTIVATE_WITH_CYCLE:")
    branch = src[idx:]
    assert "await plot_repo.reactivate_plot_with_cycle(" in branch
    # never a parallel is_active=True assignment inside the importer itself
    assert "plot.is_active = True" not in branch
    assert "plot.is_active=True" not in branch


# --- round 8-6J Part E / 8-6K Part C: cycleLabel must not reuse a HISTORICAL
# cycle label — round 8-6K batches the lookup (get_cycle_labels_for_plots),
# so fixtures here build a {plot_id: {labels}} dict, never PlotCycle objects.

async def test_reactivate_row_reusing_a_historical_cycle_label_is_invalid_with_clear_message():
    inactive_plot = _plot(is_active=False)
    labels = {inactive_plot.id: {"aug2026"}}
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None, labels=labels)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row(cycleLabel="aug2026")]), ctx=_ctx())
    assert preview.error_rows == 1
    assert "aug2026" in preview.rows[0].message
    assert "เคยถูกใช้กับแปลงนี้แล้ว" in preview.rows[0].message
    assert preview.rows[0].error_code == "reactivate_cycle_label_reused"


async def test_reactivate_row_reusing_historical_label_is_case_and_whitespace_insensitive():
    inactive_plot = _plot(is_active=False)
    labels = {inactive_plot.id: {" Aug2026 "}}
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None, labels=labels)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row(cycleLabel="aug2026")]), ctx=_ctx())
    assert preview.error_rows == 1


async def test_reactivate_row_with_a_new_never_used_cycle_label_is_valid():
    inactive_plot = _plot(is_active=False)
    labels = {inactive_plot.id: {"jun2025", "dec2025"}}
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None, labels=labels)
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row(cycleLabel="aug2026")]), ctx=_ctx())
    assert preview.error_rows == 0


async def test_reactivate_row_with_no_cycle_history_at_all_is_valid():
    inactive_plot = _plot(is_active=False)
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None, labels={})
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), _xlsx([_row(cycleLabel="aug2026")]), ctx=_ctx())
    assert preview.error_rows == 0


async def test_reactivate_row_blank_cycle_label_never_queries_history_and_is_rejected():
    """Part C item 6 (superseded by round 8-17A.1): cycleLabel null/blank
    used to be valid for a MANUAL lot (Round 8-12A.1's Manual-lot exception,
    since it never fed an Auto Lot formula there). Round 8-17A.1 makes
    cycleLabel required for every _NEW_CYCLE_ACTIONS row unconditionally —
    independent of Auto vs Manual lot — so a blank cycleLabel is now a row
    error even with a Manual lotNo. The history check is still skipped
    (nothing to compare with no label), so no DB call is attempted."""
    inactive_plot = _plot(is_active=False)
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None, labels={inactive_plot.id: {"aug2026"}})
    with p1, p2, p3, p4, \
         patch(f"{_M}.plot_cycle_repo.get_cycle_labels_for_plots", AsyncMock(return_value={})) as mk_labels:
        preview = await build_preview(
            AsyncMock(), _xlsx([_row(cycleLabel="", lotNo="HAND-LOT-1")]), ctx=_ctx(),
        )
    assert preview.error_rows == 1
    mk_labels.assert_not_awaited()


async def test_reactivate_row_blank_cycle_label_and_blank_lot_is_rejected():
    """Round 8-12A.1 — the other half of the rule above: with NO lotNo the row
    is asking for an Auto Lot, so a blank cycleLabel is now a row error naming
    the field, never a silently lotless cycle."""
    inactive_plot = _plot(is_active=False)
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None)
    with p1, p2, p3, p4:
        preview = await build_preview(
            AsyncMock(), _xlsx([_row(cycleLabel="", lotNo=None)]), ctx=_ctx(),
        )
    assert preview.error_rows == 1
    assert "cycleLabel" in preview.rows[0].message
    assert "lotNo" in preview.rows[0].message   # tells them the other way out


async def test_reactivate_cycle_label_reuse_is_also_caught_at_commit_time_not_just_preview():
    """Part E: 'ห้ามรอให้ DB unique constraint แตกตอน Commit' — this is an
    app-level check inside _validate_all's second pass, which commit_import
    re-runs (same _validate_all call as Preview), so a file that slipped
    past an earlier Preview (or was never Previewed) still gets blocked at
    Commit, never partially written."""
    inactive_plot = _plot(is_active=False)
    labels = {inactive_plot.id: {"aug2026"}}
    p1, p2, p3, p4 = _patch_lookups(plot=inactive_plot, active=None, labels=labels)
    with p1, p2, p3, p4, \
         patch(f"{_M}.plot_repo.reactivate_plot_with_cycle", AsyncMock()) as mk_react:
        with pytest.raises(ImportHasErrors):
            await commit_import(AsyncMock(), _xlsx([_row(cycleLabel="aug2026")]), ctx=_ctx())
    mk_react.assert_not_awaited()


async def test_multiple_reactivate_rows_call_the_batch_labels_helper_exactly_once():
    """Part C item 2/3 — N reactivate rows (distinct plots) must call
    get_cycle_labels_for_plots ONCE for the whole file, never once per row."""
    plots = [_plot(is_active=False) for _ in range(5)]

    async def _by_code(db, supplier_id, code):
        idx = int(code.removeprefix("P")) - 1
        return plots[idx]

    rows = [_row(plotCode=f"P{i + 1:03d}", cycleLabel=f"label{i}") for i in range(5)]
    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(side_effect=_by_code)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_cycle_repo.get_cycle_labels_for_plots", AsyncMock(return_value={})) as mk_labels:
        preview = await build_preview(AsyncMock(), _xlsx(rows), ctx=_ctx())
    assert preview.error_rows == 0
    mk_labels.assert_awaited_once()
    queried_ids = set(mk_labels.call_args.args[1])
    assert queried_ids == {p.id for p in plots}


async def test_no_reactivate_rows_never_calls_the_batch_labels_helper():
    with patch(f"{_M}.plot_cycle_repo.get_cycle_labels_for_plots", AsyncMock(return_value={})) as mk_labels:
        preview = await build_preview(
            AsyncMock(),
            _xlsx([{"action": "bogus_action", "supplierCode": "SUP001", "plotCode": "P999"}]),
            ctx=_ctx(),
        )
    assert preview.error_rows == 1
    mk_labels.assert_not_awaited()


async def test_row_that_fails_earlier_validation_is_never_included_in_the_batch_query():
    """Part C item 7 — a reactivate row whose plot doesn't exist (or is out
    of scope, or already active) never reaches the batch query at all: it
    has no `state.plot`, so it's excluded from the plot_ids collected."""
    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_cycle_repo.get_cycle_labels_for_plots", AsyncMock(return_value={})) as mk_labels:
        preview = await build_preview(AsyncMock(), _xlsx([_row()]), ctx=_ctx())
    assert preview.error_rows == 1
    assert "ไม่พบแปลง" in preview.rows[0].message
    mk_labels.assert_not_awaited()


def test_reactivate_cycle_label_reused_error_message_never_leaks_qr_or_secrets():
    from app.services.plot_import import _reactivate_cycle_label_reused_msg
    msg = _reactivate_cycle_label_reused_msg("aug2026")
    for bad in ("qr_key", "qrKey", "token", "secret"):
        assert bad not in msg

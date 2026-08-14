"""final_plot's preview-state enforcement (round 8-7A.1 Part B/C) — a
final_plot-only commit must carry a preview_state matching the file digest
AND the live Plot/PlotCycle/Record state under lock, exactly mirroring
start_next_cycle's existing contract (see test_plot_import_service.py's
"preview-state" tests for that action). DB-less: mocks the repo lookups, same
pattern as test_plot_import_final_plot_action.py.
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
    ACTION_FINAL,
    IMPORT_COLUMNS,
    ImportContext,
    ImportPreviewStateConflict,
    build_preview,
    commit_import,
)

_M = "app.services.plot_import"


def _xlsx(rows: list[dict[str, str]]) -> bytes:
    data: list[list] = [list(IMPORT_COLUMNS)]
    for r in rows:
        data.append([r.get(c) for c in IMPORT_COLUMNS])
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
    sup = _supplier() if supplier is ... else supplier
    return (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=sup)),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active)),
        patch(f"{_M}.plot_cycle_repo.get_latest_active_record_for_cycle", AsyncMock(return_value=latest_record)),
    )


async def _final_preview_state(rows, *, plot, active, latest_record=None, ctx=None):
    """Build a REAL preview_state via build_preview, using the same patched
    lookups a matching commit will re-validate against."""
    ctx = ctx or _ctx()
    content = _xlsx(rows)
    p1, p2, p3, p4 = _patch_lookups(
        plot=plot, active=active, latest_record=latest_record,
    )
    with p1, p2, p3, p4:
        preview = await build_preview(AsyncMock(), content, ctx=ctx)
    return content, preview.preview_state


def _commit_patches(*, plot, cycle, latest_record=None):
    """Patches EVERY lookup commit_import_execute touches for a final_plot
    row: the fresh re-validation pass (_validate_all — same targets as
    _patch_lookups) PLUS the FOR_UPDATE lock fetches _verify_final_plot_
    snapshot/_execute_row use. All given the CURRENT/live plot+cycle+record —
    which a test can make differ from what an earlier _final_preview_state
    call saw, to simulate drift between Preview and Commit. Re-validation
    doesn't need to match the OLD preview_state (only _verify_final_plot_
    snapshot's explicit comparison does), so one live value per lookup is
    enough — no separate "what re-validation sees" track needed."""
    sup = _supplier()
    return (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=sup)),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)),
        patch(f"{_M}.plot_cycle_repo.get_latest_active_record_for_cycle", AsyncMock(return_value=latest_record)),
        patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)),
        patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()),
    )


# --- item 1/2: missing / stale-digest preview_state -------------------------

async def test_final_plot_only_commit_without_preview_state_is_rejected():
    plot = _plot()
    cycle = _cycle()
    content, _ = await _final_preview_state([_row()], plot=plot, active=cycle)
    p1, p2, p3, p4, p5, p6, p7 = _commit_patches(plot=plot, cycle=cycle)
    with p1, p2, p3, p4, p5, p6, p7:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=None)
    assert exc.value.reason == "missing_preview_state"


async def test_final_plot_only_commit_with_stale_digest_is_rejected():
    plot = _plot()
    cycle = _cycle()
    # preview_state built for a DIFFERENT file (a different finalNote) than
    # the one actually committed → digest mismatch.
    _, stale_state = await _final_preview_state(
        [_row(finalNote="โน้ตเก่า")], plot=plot, active=cycle,
    )
    content = _xlsx([_row(finalNote="โน้ตใหม่")])
    p1, p2, p3, p4, p5, p6, p7 = _commit_patches(plot=plot, cycle=cycle)
    with p1, p2, p3, p4, p5, p6, p7:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=stale_state)
    assert exc.value.reason == "file_digest_mismatch"


# --- item 3/4: row set mismatch ----------------------------------------------

async def test_final_plot_row_missing_from_snapshot_is_rejected():
    plot = _plot()
    cycle = _cycle()
    content, preview_state = await _final_preview_state([_row()], plot=plot, active=cycle)
    empty_state = preview_state.model_copy(update={"final_plot_rows": []})
    p1, p2, p3, p4, p5, p6, p7 = _commit_patches(plot=plot, cycle=cycle)
    with p1, p2, p3, p4, p5, p6, p7:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=empty_state)
    assert exc.value.reason == "row_set_mismatch"
    # row_number is the real Excel row (row 1 = header) — the single data
    # row is row 2, never 1.
    assert exc.value.changed_rows == [2]


async def test_final_plot_extra_row_in_snapshot_is_rejected():
    plot = _plot()
    cycle = _cycle()
    content, preview_state = await _final_preview_state([_row()], plot=plot, active=cycle)
    extra_row = preview_state.final_plot_rows[0].model_copy(update={"row_number": 99})
    two_row_state = preview_state.model_copy(
        update={"final_plot_rows": [*preview_state.final_plot_rows, extra_row]}
    )
    p1, p2, p3, p4, p5, p6, p7 = _commit_patches(plot=plot, cycle=cycle)
    with p1, p2, p3, p4, p5, p6, p7:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=two_row_state)
    assert exc.value.reason == "row_set_mismatch"
    assert 99 in exc.value.changed_rows


# --- item 4: supplierCode/plotCode identity mismatch ------------------------

async def test_final_plot_plot_code_identity_mismatch_is_rejected():
    plot = _plot()
    cycle = _cycle()
    content, preview_state = await _final_preview_state([_row()], plot=plot, active=cycle)
    bad_row = preview_state.final_plot_rows[0].model_copy(update={"plot_code": "P999"})
    bad_state = preview_state.model_copy(update={"final_plot_rows": [bad_row]})
    p1, p2, p3, p4, p5, p6, p7 = _commit_patches(plot=plot, cycle=cycle)
    with p1, p2, p3, p4, p5, p6, p7:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=bad_state)
    assert exc.value.reason == "resolution_changed"
    assert exc.value.changed_rows == [2]  # row 1 is the header


# --- item 5: plot.updatedAt drift --------------------------------------------

async def test_final_plot_plot_updated_at_changed_is_rejected():
    plot = _plot()
    cycle = _cycle()
    content, preview_state = await _final_preview_state([_row()], plot=plot, active=cycle)
    # Something about the plot changed between Preview and Commit (e.g. an
    # unrelated edit) — the SAME plot id, but a newer updated_at under lock.
    plot.updated_at = plot.updated_at + datetime.timedelta(hours=1)
    p1, p2, p3, p4, p5, p6, p7 = _commit_patches(plot=plot, cycle=cycle)
    with p1, p2, p3, p4, p5, p6, p7:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.reason == "resolution_changed"


# --- item 6/7/8: active cycle id / cycle_no / updated_at drift --------------

async def test_final_plot_active_cycle_id_changed_is_rejected():
    plot = _plot()
    preview_cycle = _cycle()
    content, preview_state = await _final_preview_state([_row()], plot=plot, active=preview_cycle)
    different_cycle = _cycle(cycle_label="jul2026")  # fresh id
    p1, p2, p3, p4, p5, p6, p7 = _commit_patches(plot=plot, cycle=different_cycle)
    with p1, p2, p3, p4, p5, p6, p7:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.reason == "resolution_changed"


async def test_final_plot_active_cycle_no_changed_is_rejected():
    plot = _plot()
    preview_cycle = _cycle()
    content, preview_state = await _final_preview_state([_row()], plot=plot, active=preview_cycle)
    drifted_cycle = _cycle(
        id=preview_cycle.id, cycle_label="jul2026",
        updated_at=preview_cycle.updated_at, cycle_no=preview_cycle.cycle_no + 1,
    )
    p1, p2, p3, p4, p5, p6, p7 = _commit_patches(plot=plot, cycle=drifted_cycle)
    with p1, p2, p3, p4, p5, p6, p7:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.reason == "resolution_changed"


async def test_final_plot_active_cycle_updated_at_changed_is_rejected():
    plot = _plot()
    preview_cycle = _cycle()
    content, preview_state = await _final_preview_state([_row()], plot=plot, active=preview_cycle)
    drifted_cycle = _cycle(
        id=preview_cycle.id, cycle_label="jul2026", cycle_no=preview_cycle.cycle_no,
        updated_at=preview_cycle.updated_at + datetime.timedelta(minutes=5),
    )
    p1, p2, p3, p4, p5, p6, p7 = _commit_patches(plot=plot, cycle=drifted_cycle)
    with p1, p2, p3, p4, p5, p6, p7:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.reason == "resolution_changed"


# --- item 10/11: resolved Record drifted after Preview ----------------------

async def test_final_plot_latest_record_changed_after_preview_is_rejected():
    """Blank finalInspectionRecordId — Preview resolves the cycle's latest
    active record; a NEW inspection added to the same cycle before Commit
    must be caught as drift, never silently snapshotted instead."""
    plot = _plot()
    cycle = _cycle()
    old_record = _record(plot_id=plot.id, plot_cycle_id=cycle.id)
    content, preview_state = await _final_preview_state(
        [_row()], plot=plot, active=cycle, latest_record=old_record,
    )
    new_record = _record(plot_id=plot.id, plot_cycle_id=cycle.id)  # a fresh id
    p1, p2, p3, p4, p5, p6, p7 = _commit_patches(plot=plot, cycle=cycle, latest_record=new_record)
    with p1, p2, p3, p4, p5, p6, p7:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.reason == "resolution_changed"


async def test_final_plot_preview_had_no_record_but_commit_finds_one_is_rejected():
    """The mirror of the case above, and the one round 8-10B makes possible:
    Preview resolved NOTHING (the cycle had no inspection record yet), then an
    inspection was submitted before the user hit Commit. Silently snapshotting
    that new record would change what the user approved, so the file is
    rejected instead."""
    plot = _plot()
    cycle = _cycle()
    content, preview_state = await _final_preview_state(
        [_row()], plot=plot, active=cycle, latest_record=None,
    )
    assert preview_state.final_plot_rows[0].resolved_final_inspection_record_id is None

    appeared = _record(plot_id=plot.id, plot_cycle_id=cycle.id)
    p1, p2, p3, p4, p5, p6, p7 = _commit_patches(
        plot=plot, cycle=cycle, latest_record=appeared,
    )
    with p1, p2, p3, p4, p5, p6, p7:
        with pytest.raises(plot_import.ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.reason == "resolution_changed"


async def test_final_plot_no_record_at_preview_and_none_at_commit_succeeds():
    """None == None is agreement, not a conflict — a cycle that never had an
    inspection record still finalizes."""
    plot = _plot()
    cycle = _cycle()
    content, preview_state = await _final_preview_state(
        [_row()], plot=plot, active=cycle, latest_record=None,
    )
    p1, p2, p3, p4, p5, p6, p7 = _commit_patches(
        plot=plot, cycle=cycle, latest_record=None,
    )
    with p1, p2, p3, p4, p5, p6, p7:
        result = await commit_import(
            AsyncMock(), content, ctx=_ctx(), preview_state=preview_state,
        )
    assert result.finalized_plots == 1


# --- item 12/13: conflict happens before ANY row executes -------------------

async def test_conflict_on_one_row_blocks_execution_of_a_valid_row_too():
    """Two final_plot rows, both valid at re-validation time — but ONE has
    drifted since Preview. The whole commit must abort before executing
    EITHER row (never a partial write)."""
    plot_a = _plot()
    cycle_a = _cycle()
    plot_b = _plot()
    cycle_b = _cycle()
    rows = [
        _row(plotCode="P001"),
        _row(plotCode="P002"),
    ]

    async def _get_plot_by_code(db, supplier_id, code):
        return plot_a if code == "P001" else plot_b

    async def _get_active(db, plot_id):
        return cycle_a if plot_id == plot_a.id else cycle_b

    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(side_effect=_get_plot_by_code)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(side_effect=_get_active)), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_for_cycle", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_for_cycle", AsyncMock(return_value=None)):
        content = _xlsx(rows)
        preview = await build_preview(AsyncMock(), content, ctx=_ctx())
    preview_state = preview.preview_state
    assert preview_state is not None and len(preview_state.final_plot_rows) == 2

    # At commit time, plot_b's cycle has drifted to a brand-new cycle (its
    # active_cycle_id no longer matches what Preview approved) while plot_a's
    # is untouched.
    drifted_cycle_b = _cycle(cycle_label="jul2026")

    async def _get_plot_for_update(db, plot_id):
        return plot_a if plot_id == plot_a.id else plot_b

    async def _get_active_for_update(db, plot_id):
        return cycle_a if plot_id == plot_a.id else drifted_cycle_b

    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(side_effect=_get_plot_by_code)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(side_effect=_get_active)), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_for_cycle", AsyncMock(return_value=None)), \
\
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(side_effect=_get_plot_for_update)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(side_effect=_get_active_for_update)), \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()) as mk_close:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(AsyncMock(), content, ctx=_ctx(), preview_state=preview_state)
    # Only plot_b's row (P002, Excel row 3 — row 1 is the header, row 2 is
    # P001) drifted — plot_a's row must NOT have been executed either.
    assert exc.value.changed_rows == [3]
    mk_close.assert_not_awaited()


# --- item 14: legacy (non start_next/final_plot) actions need no state -----

async def test_legacy_action_file_still_needs_no_preview_state_after_this_round():
    """Regression guard: Part B only tightens start_next_cycle/final_plot —
    a file with neither (e.g. update_current_cycle) must still commit with
    preview_state=None, exactly as before round 8-7A.1."""
    plot = _plot()
    cycle = _cycle(crop=None, variety=None, lot_no=None, planting_date=None, plant_count=None,
                   expected_yield_full=None, expected_yield_unit=None)
    row = {
        "action": "update_current_cycle", "supplierCode": "SUP001", "plotCode": "P001",
        "cycleLabel": "jul2026",
    }
    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()), \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         patch(f"{_M}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()):
        result = await commit_import(AsyncMock(), _xlsx([row]), ctx=_ctx(), preview_state=None)
    assert result.updated_cycles == 1

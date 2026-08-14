"""Plot + cycle import service (round 7.5) — parse/validate/commit.

DB-free: the repo lookups (supplier/plot/active-cycle) and the commit write
helpers are patched with AsyncMocks, so these exercise the validation rules,
per-action permission/scope guards, duplicate handling, and all-or-nothing
commit without a database. Rows are built with the real hand-rolled writer.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from app.schemas.plot_import import PlotImportPreviewState, PlotImportPreviewStateRow
from app.services import plot_import
from app.services.excel_workbook import build_xlsx
from app.services.plot_import import (
    IMPORT_COLUMNS,
    ImportContext,
    ImportHasErrors,
    ImportFileError,
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


def _ctx(*, allowed=None, can_create=True, can_update=True) -> ImportContext:
    return ImportContext(allowed_supplier_id=allowed, can_create=can_create, can_update=can_update)


def _supplier(**kw) -> SimpleNamespace:
    return SimpleNamespace(id=kw.get("id", uuid4()), code=kw.get("code", "SUP001"), is_active=kw.get("is_active", True))


def _plot(**kw) -> SimpleNamespace:
    return SimpleNamespace(id=kw.get("id", uuid4()), code=kw.get("code", "SUP001"), is_active=kw.get("is_active", True))


def _cycle(**kw) -> SimpleNamespace:
    # Includes the 8 planting-plan fields (round 8-2.3's duplicate guard reads
    # them); default all-None so a plain _cycle() never matches a row that
    # carries plan values.
    base = dict(
        id=uuid4(), cycle_no=1, crop=None, variety=None, cycle_label=None,
        lot_no=None, planting_date=None, plant_count=None,
        expected_yield_full=None, expected_yield_unit=None,
        # Round 8-5B — validation reads active.po_number, and _capture_lot_result
        # reads lot_no_source/lot_running_no off a created/rolled cycle.
        po_number=None, p_code=None, lot_no_source=None, lot_running_no=None,
        supplier_lot_no=None,
        # Round 8-7A — validation captures active.updated_at (final_plot's
        # preview_state binding); every cycle fixture needs a real value.
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _create_row(**over) -> dict[str, str]:
    base = {
        "action": "create_plot_with_cycle", "supplierCode": "SUP001",
        "plotCode": "P101", "plotName": "แปลงใหม่", "province": "เชียงใหม่",
        "poNumber": "PO25001", "pCode": "Melon-A",
        # Round 8-17A.1 — cycleLabel is now required on every new-cycle
        # action (independent of Auto/Manual lot); see
        # test_plot_import_cycle_label_required.py for the dedicated
        # contract tests. Default here so this shared fixture keeps testing
        # what it was written to test elsewhere.
        "cycleLabel": "jun2026",
        "crop": "พริก", "variety": "พริกขี้หนู", "lotNo": "LOT-01",
        "plantingDate": "2026-06-01", "plantCount": "1000",
        "expectedYieldFull": "800", "expectedYieldUnit": "kg",
    }
    base.update(over)
    return base


def _patch_lookups(*, supplier=..., plot=None, active=None):
    """Patch the three read helpers the validator uses. supplier defaults to a
    fresh active supplier; pass supplier=None to simulate 'not found'."""
    sup = _supplier() if supplier is ... else supplier
    return (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=sup)),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active)),
    )


async def _preview(rows, ctx=None, **lookups):
    ctx = ctx or _ctx()
    p_sup, p_plot, p_active = _patch_lookups(**lookups)
    with p_sup, p_plot, p_active:
        return await build_preview(object(), _xlsx(rows), ctx=ctx)


# --- file-level errors ----------------------------------------------------

async def test_non_xlsx_is_file_error() -> None:
    with pytest.raises(ImportFileError):
        await build_preview(object(), b"not a zip", ctx=_ctx())


async def test_empty_sheet_is_file_error() -> None:
    with pytest.raises(ImportFileError):
        await _preview([])  # header only, no data rows


async def test_wrong_columns_is_file_error() -> None:
    content = build_xlsx([("plots", [["foo", "bar"], ["x", "y"]])])
    with pytest.raises(ImportFileError):
        await build_preview(object(), content, ctx=_ctx())


# --- per-row validation ---------------------------------------------------

async def test_unknown_action_errors() -> None:
    pv = await _preview([_create_row(action="frobnicate")])
    assert pv.error_rows == 1
    assert "action" in pv.rows[0].message


async def test_missing_required_codes_error() -> None:
    pv = await _preview([_create_row(supplierCode=None, plotCode=None)])
    assert pv.rows[0].status == "error"
    assert "supplierCode" in pv.rows[0].message and "plotCode" in pv.rows[0].message


async def test_bad_number_errors() -> None:
    pv = await _preview([_create_row(plantCount="abc")])
    assert pv.rows[0].status == "error"


async def test_yield_without_unit_errors() -> None:
    pv = await _preview([_create_row(expectedYieldUnit=None)])
    assert pv.rows[0].status == "error"
    assert "หน่วย" in pv.rows[0].message


async def test_create_valid_when_plot_absent() -> None:
    pv = await _preview([_create_row()], plot=None)
    assert pv.valid_rows == 1
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].payload.plot_code == "P101"


async def test_create_errors_when_plot_exists() -> None:
    pv = await _preview([_create_row()], plot=_plot())
    assert pv.rows[0].status == "error"
    assert "มีอยู่แล้ว" in pv.rows[0].message


async def test_create_requires_plot_name() -> None:
    pv = await _preview([_create_row(plotName=None)], plot=None)
    assert pv.rows[0].status == "error"
    assert "plotName" in pv.rows[0].message


async def test_start_new_cycle_needs_existing_active_plot_without_active_cycle() -> None:
    ok = await _preview(
        [_create_row(action="start_new_cycle", plotCode="P001", plotName=None)],
        plot=_plot(), active=None,
    )
    assert ok.rows[0].status == "valid"

    bad = await _preview(
        [_create_row(action="start_new_cycle", plotCode="P001")],
        plot=_plot(), active=_cycle(),
    )
    assert bad.rows[0].status == "error"
    assert "เปิดอยู่แล้ว" in bad.rows[0].message


async def test_start_new_cycle_error_explains_both_workflows() -> None:
    # round 8-2.2: when a plot already has an active cycle, start_new_cycle's
    # error must guide the user to the RIGHT action for each intent — the next
    # cycle (close_and_start_new_cycle) AND editing the current one
    # (update_current_cycle) — not only the latter.
    pv = await _preview(
        [_create_row(action="start_new_cycle", plotCode="P001")],
        plot=_plot(), active=_cycle(),
    )
    msg = pv.rows[0].message
    assert pv.rows[0].status == "error"
    assert "close_and_start_new_cycle" in msg
    assert "update_current_cycle" in msg


async def test_start_new_cycle_error_names_the_active_cycle_no_extra_query() -> None:
    # Round 8-2.7: name the already-open cycle in the message using data the
    # validator already loaded (get_active_cycle_for_plot) — no extra query.
    # Prefers cycle_label; falls back to "รอบที่ {cycle_no}" when unlabelled.
    pv = await _preview(
        [_create_row(action="start_new_cycle", plotCode="P001")],
        plot=_plot(), active=_cycle(cycle_label="jun2026"),
    )
    assert "jun2026" in pv.rows[0].message

    pv2 = await _preview(
        [_create_row(action="start_new_cycle", plotCode="P001")],
        plot=_plot(), active=_cycle(cycle_label=None, cycle_no=3),
    )
    assert "รอบที่ 3" in pv2.rows[0].message


async def test_start_new_cycle_errors_when_plot_missing() -> None:
    pv = await _preview(
        [_create_row(action="start_new_cycle", plotCode="P404")],
        plot=None,
    )
    assert pv.rows[0].status == "error"
    assert "ไม่พบแปลง" in pv.rows[0].message


async def test_start_new_cycle_errors_when_plot_inactive() -> None:
    pv = await _preview(
        [_create_row(action="start_new_cycle", plotCode="P001")],
        plot=_plot(is_active=False), active=None,
    )
    assert pv.rows[0].status == "error"
    assert "ปิดถาวร" in pv.rows[0].message


async def test_update_current_cycle_needs_active_cycle() -> None:
    ok = await _preview(
        [_create_row(action="update_current_cycle", plotCode="P002")],
        plot=_plot(), active=_cycle(),
    )
    assert ok.rows[0].status == "valid"
    assert ok.rows[0].active_cycle_id is not None

    bad = await _preview(
        [_create_row(action="update_current_cycle", plotCode="P002")],
        plot=_plot(), active=None,
    )
    assert bad.rows[0].status == "error"
    assert "ยังไม่มีรอบปลูก" in bad.rows[0].message


# --- close_and_start_new_cycle (rollover) validation ----------------------

async def test_rollover_valid_when_plot_has_active_cycle() -> None:
    pv = await _preview(
        [_create_row(action="close_and_start_new_cycle", plotCode="P003")],
        plot=_plot(), active=_cycle(),
    )
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].active_cycle_id is not None


async def test_rollover_errors_when_no_active_cycle() -> None:
    pv = await _preview(
        [_create_row(action="close_and_start_new_cycle", plotCode="P003")],
        plot=_plot(), active=None,
    )
    assert pv.rows[0].status == "error"
    assert "ไม่พบรอบปลูกที่เปิดอยู่สำหรับปิดรอบ" in pv.rows[0].message


async def test_rollover_errors_when_plot_missing() -> None:
    pv = await _preview(
        [_create_row(action="close_and_start_new_cycle", plotCode="P404")],
        plot=None,
    )
    assert pv.rows[0].status == "error"
    assert "ไม่พบแปลง" in pv.rows[0].message


async def test_rollover_errors_when_plot_inactive() -> None:
    pv = await _preview(
        [_create_row(action="close_and_start_new_cycle", plotCode="P003")],
        plot=_plot(is_active=False), active=_cycle(),
    )
    assert pv.rows[0].status == "error"
    assert "ปิดถาวร" in pv.rows[0].message


async def test_rollover_requires_plots_update_permission() -> None:
    pv = await _preview(
        [_create_row(action="close_and_start_new_cycle", plotCode="P003")],
        ctx=_ctx(can_update=False), plot=_plot(), active=_cycle(),
    )
    assert pv.rows[0].status == "error"
    assert "plots.update" in pv.rows[0].message


# --- scope + permission ---------------------------------------------------

async def test_supplier_out_of_scope_errors() -> None:
    sup = _supplier()
    pv = await _preview([_create_row()], ctx=_ctx(allowed=uuid4()), supplier=sup, plot=None)
    assert pv.rows[0].status == "error"
    assert "นอกขอบเขต" in pv.rows[0].message


async def test_in_scope_supplier_ok() -> None:
    sup = _supplier()
    pv = await _preview([_create_row()], ctx=_ctx(allowed=sup.id), supplier=sup, plot=None)
    assert pv.rows[0].status == "valid"


async def test_inactive_supplier_errors() -> None:
    pv = await _preview([_create_row()], supplier=_supplier(is_active=False), plot=None)
    assert pv.rows[0].status == "error"
    assert "ปิดใช้งาน" in pv.rows[0].message


async def test_unknown_supplier_errors() -> None:
    pv = await _preview([_create_row()], supplier=None)
    assert pv.rows[0].status == "error"
    assert "ไม่พบ Supplier" in pv.rows[0].message


async def test_create_action_requires_plots_create_permission() -> None:
    pv = await _preview([_create_row()], ctx=_ctx(can_create=False), plot=None)
    assert pv.rows[0].status == "error"
    assert "plots.create" in pv.rows[0].message


async def test_start_and_update_require_plots_update_permission() -> None:
    pv = await _preview(
        [_create_row(action="start_new_cycle", plotCode="P001")],
        ctx=_ctx(can_update=False), plot=_plot(), active=None,
    )
    assert pv.rows[0].status == "error"
    assert "plots.update" in pv.rows[0].message


# --- duplicate rows -------------------------------------------------------

async def test_duplicate_plot_rows_both_error() -> None:
    pv = await _preview(
        [_create_row(plotCode="P101"), _create_row(plotCode="P101")],
        plot=None,
    )
    assert pv.error_rows == 2
    assert all("ซ้ำ" in r.message for r in pv.rows)


# --- commit (execution, all-or-nothing) -----------------------------------

async def test_commit_create_plot_with_cycle_calls_create_plot_and_cycle() -> None:
    created_plot = _plot()
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock(return_value=created_plot)) as m_create_plot, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=1))) as m_create_cycle:
        result = await commit_import(object(), _xlsx([_create_row()]), ctx=_ctx())

    m_create_plot.assert_awaited_once()
    m_create_cycle.assert_awaited_once()
    assert result.created_plots == 1
    assert result.started_cycles == 0 and result.updated_cycles == 0


async def test_commit_start_new_cycle_clears_snapshot_and_no_plot_create() -> None:
    plot = _plot()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock()) as m_create_plot, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=1))) as m_create_cycle, \
         patch(f"{_M}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()) as m_clear:
        result = await commit_import(
            object(), _xlsx([_create_row(action="start_new_cycle", plotCode="P001")]), ctx=_ctx(),
        )

    m_create_plot.assert_not_awaited()
    m_create_cycle.assert_awaited_once()
    m_clear.assert_awaited_once()
    assert result.started_cycles == 1


async def test_commit_update_current_cycle_updates_and_syncs_not_clears() -> None:
    plot = _plot()
    active = _cycle()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()) as m_update, \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()) as m_sync, \
         patch(f"{_M}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()) as m_clear:
        result = await commit_import(
            object(), _xlsx([_create_row(action="update_current_cycle", plotCode="P002")]), ctx=_ctx(),
        )

    m_update.assert_awaited_once()
    m_sync.assert_awaited_once()
    m_clear.assert_not_awaited()  # a plan edit must NOT wipe the inspection snapshot
    assert result.updated_cycles == 1


async def test_commit_rollover_closes_harvested_then_creates_and_clears() -> None:
    plot = _plot()
    active = _cycle()
    uid = uuid4()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()) as m_close, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=1))) as m_create_cycle, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock()) as m_create_plot, \
         patch(f"{_M}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()) as m_clear:
        result = await commit_import(
            object(),
            _xlsx([_create_row(action="close_and_start_new_cycle", plotCode="P003")]),
            ctx=ImportContext(
                allowed_supplier_id=None, can_create=True, can_update=True, user_id=uid,
            ),
        )

    # Closes the OLD active cycle as harvested, stamping the caller as closer.
    m_close.assert_awaited_once()
    _, close_kwargs = m_close.call_args
    assert close_kwargs["status"] == "harvested"
    assert close_kwargs["closed_by_id"] == uid
    assert close_kwargs["reason"] == plot_import.ROLLOVER_CLOSE_REASON
    # Opens a fresh cycle; never creates a plot; clears the inspection snapshot.
    m_create_cycle.assert_awaited_once()
    m_create_plot.assert_not_awaited()
    m_clear.assert_awaited_once()
    assert result.rolled_over_cycles == 1
    assert result.created_plots == 0 and result.started_cycles == 0 and result.updated_cycles == 0


async def test_commit_rollover_raises_if_active_cycle_vanished_before_commit() -> None:
    # Validated with an active cycle, but it's gone by the time the locked
    # re-fetch runs (raced closed) → the whole file must fail (rollback), not
    # silently skip.
    plot = _plot()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=_cycle())
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()) as m_close, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=1))) as m_create_cycle:
        with pytest.raises(ImportFileError, match="หายไประหว่างนำเข้า"):
            await commit_import(
                object(),
                _xlsx([_create_row(action="close_and_start_new_cycle", plotCode="P003")]),
                ctx=_ctx(),
            )

    m_close.assert_not_awaited()
    m_create_cycle.assert_not_awaited()


async def test_commit_all_or_nothing_when_any_row_invalid() -> None:
    rows = [_create_row(plotCode="P101"), _create_row(action="frobnicate", plotCode="P102")]
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock()) as m_create_plot, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=1))) as m_create_cycle:
        with pytest.raises(ImportHasErrors) as exc:
            await commit_import(object(), _xlsx(rows), ctx=_ctx())

    # Nothing written — not even the valid row.
    m_create_plot.assert_not_awaited()
    m_create_cycle.assert_not_awaited()
    assert exc.value.preview.error_rows == 1


async def test_commit_execute_error_propagates_for_rollback() -> None:
    # A DB error mid-commit must bubble out so the endpoint's transaction rolls
    # the whole file back — the service never swallows it.
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock()), \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(side_effect=RuntimeError("db boom"))):
        with pytest.raises(RuntimeError, match="db boom"):
            await commit_import(object(), _xlsx([_create_row()]), ctx=_ctx())


# --- round 8.0.7: plot-aggregate lock (Plot before PlotCycle) --------------

async def test_commit_locks_existing_plots_in_sorted_id_order() -> None:
    """Every existing plot referenced by the file is locked up front, in one
    deterministic order (sorted by id) — NOT the order the rows appear in
    the file. This is what lets two concurrent imports whose rows reference
    the same plots in a different order avoid deadlocking each other."""
    # Fixed ids (not random uuid4()) so the expected sort direction is known
    # and doesn't depend on chance.
    id_low = UUID("00000000-0000-0000-0000-000000000001")
    id_high = UUID("00000000-0000-0000-0000-000000000002")
    plot_low = _plot(id=id_low)
    plot_high = _plot(id=id_high)
    active = _cycle()
    plots_by_code = {"P001": plot_low, "P002": plot_high}

    async def _get_plot_by_code(db, supplier_id, code):
        return plots_by_code[code]

    lock_calls: list = []

    async def _get_plot_for_update(db, plot_id):
        lock_calls.append(plot_id)
        return plot_low if plot_id == id_low else plot_high

    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", _get_plot_by_code), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_repo.get_plot_for_update", _get_plot_for_update), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()), \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()):
        # File lists the HIGH-id plot (P002) first, LOW-id plot (P001) second —
        # the lock order must still be [low, high], the opposite of file order.
        await commit_import(
            object(),
            _xlsx([
                _create_row(action="update_current_cycle", plotCode="P002"),
                _create_row(action="update_current_cycle", plotCode="P001"),
            ]),
            ctx=_ctx(),
        )

    assert lock_calls == [id_low, id_high]


async def test_commit_raises_when_existing_plot_deactivated_before_lock() -> None:
    """A plot deactivated between preview and commit fails the whole import
    (all-or-nothing) at the lock step — before any row executes — rather
    than surfacing deeper inside a specific action's write."""
    plot = _plot()
    now_inactive = _plot(id=plot.id, is_active=False)
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=_cycle())
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=now_inactive)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()) as m_update:
        with pytest.raises(ImportFileError, match="ปิดใช้งานหรือหายไป"):
            await commit_import(
                object(),
                _xlsx([_create_row(action="update_current_cycle", plotCode="P002")]),
                ctx=_ctx(),
            )
    m_update.assert_not_awaited()


async def test_commit_start_rechecks_no_active_cycle_after_lock() -> None:
    """START was validated with NO active cycle, but a concurrent
    transaction opened one before this file's plot lock was acquired — the
    whole import fails rather than silently attempting a second active
    cycle (the partial unique index remains the final backstop for any
    path that somehow misses this check)."""
    plot = _plot()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=_cycle())), \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=1))) as m_create_cycle:
        with pytest.raises(ImportFileError, match="เกิดขึ้นระหว่างนำเข้า"):
            await commit_import(
                object(), _xlsx([_create_row(action="start_new_cycle", plotCode="P001")]), ctx=_ctx(),
            )
    m_create_cycle.assert_not_awaited()


def test_import_locks_plot_before_any_cycle_call_in_source() -> None:
    """Structural guard: the plot lock (_lock_existing_plots, defined before
    _execute_row) must appear textually before any cycle-lock call in the
    module — locking the cycle first would risk a deadlock against another
    transaction that (correctly) locks the plot first."""
    src = Path(plot_import.__file__).read_text(encoding="utf-8")
    assert "await plot_repo.get_plot_for_update" in src
    assert "get_active_cycle_for_plot_for_update" in src
    assert src.index("await plot_repo.get_plot_for_update") < src.index(
        "get_active_cycle_for_plot_for_update"
    )


def test_service_never_imports_record_or_deactivate_or_qr_paths() -> None:
    # Structural guarantee for the round's hard "do not" list: the importer
    # calls no record-create, plot-deactivate, or QR-regen path. (close_cycle IS
    # now called — but only by the rollover action, and only as harvested; see
    # test_service_only_closes_as_harvested below.)
    # Call-pattern substrings (not bare words) so the module's own prose
    # docstring doesn't trip it.
    src = Path(plot_import.__file__).read_text(encoding="utf-8")
    assert ".create_record(" not in src
    assert "deactivate_plot" not in src
    assert "generate_qr_key(" not in src


def test_service_only_closes_as_harvested() -> None:
    # Rollover closes the old cycle — but must never cancel it. The only close
    # status the importer may pass is harvested (history preserved, records
    # untouched).
    src = Path(plot_import.__file__).read_text(encoding="utf-8")
    assert "CYCLE_STATUS_HARVESTED" in src
    assert "CYCLE_STATUS_CANCELLED" not in src
    assert 'status="cancelled"' not in src


# --- string-length validation (round 8.0) ---------------------------------

async def test_cycle_label_over_100_chars_errors_in_preview() -> None:
    long_label = "x" * 101
    pv = await _preview([_create_row(cycleLabel=long_label)], plot=None)
    assert pv.rows[0].status == "error"
    assert "cycleLabel" in pv.rows[0].message or "ชื่อรอบปลูก" in pv.rows[0].message
    assert "100" in pv.rows[0].message


async def test_cycle_label_exactly_100_chars_is_valid() -> None:
    label_100 = "y" * 100
    pv = await _preview([_create_row(cycleLabel=label_100)], plot=None)
    assert pv.rows[0].status == "valid"


async def test_cycle_label_blank_is_now_an_error_for_a_new_cycle_action() -> None:
    """Round 8-17A.1 superseded this test's original premise ("cycleLabel
    absent is valid") — create_plot_with_cycle now REQUIRES a nonblank
    cycleLabel, independent of Auto/Manual lot. See
    test_plot_import_cycle_label_required.py for the full contract; this is
    pinned here too since it shares _create_row with the rest of this file."""
    pv = await _preview([_create_row(cycleLabel=None)], plot=None)
    assert pv.rows[0].status == "error"
    assert "กรุณาระบุชื่อรอบปลูก" in pv.rows[0].message


async def test_expected_yield_unit_over_20_chars_errors() -> None:
    pv = await _preview([_create_row(expectedYieldUnit="a" * 21)], plot=None)
    assert pv.rows[0].status == "error"
    assert "expectedYieldUnit" in pv.rows[0].message or "20" in pv.rows[0].message


async def test_plot_code_over_50_chars_errors() -> None:
    pv = await _preview([_create_row(plotCode="P" * 51)], plot=None)
    assert pv.rows[0].status == "error"
    assert "plotCode" in pv.rows[0].message


async def test_cycle_label_echoed_in_payload() -> None:
    pv = await _preview([_create_row(cycleLabel="jun2026")], plot=None)
    assert pv.rows[0].payload.cycle_label == "jun2026"


# --- round 8-2.1: template guidance (description) row handling -------------

def _description_cells() -> list:
    """The exact cells the template puts on row 2 (guidance row)."""
    return [plot_import.TEMPLATE_COLUMN_DESCRIPTIONS[c] for c in IMPORT_COLUMNS]


def _xlsx_with_desc(rows: list[dict[str, str]]) -> bytes:
    """Header (row 1) + the template's Thai description row (row 2) + data
    rows (row 3+), mirroring the shipped template's physical layout."""
    data: list[list] = [list(IMPORT_COLUMNS), _description_cells()]
    for r in rows:
        data.append([r.get(c) for c in IMPORT_COLUMNS])
    return build_xlsx([("plots", data)])


async def test_description_row_not_counted_as_preview_data() -> None:
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active:
        pv = await build_preview(object(), _xlsx_with_desc([_create_row()]), ctx=_ctx())
    assert pv.total_rows == 1
    assert pv.rows[0].payload.plot_code == "P101"
    assert pv.rows[0].status == "valid"


async def test_header_plus_description_only_is_empty_file_error() -> None:
    content = build_xlsx([("plots", [list(IMPORT_COLUMNS), _description_cells()])])
    with pytest.raises(ImportFileError, match="ไม่มีข้อมูลในไฟล์"):
        await build_preview(object(), content, ctx=_ctx())


async def test_description_row_excluded_from_duplicate_detection() -> None:
    # Two data rows with the SAME plotCode are the duplicate pair; the
    # description row must neither be a third row nor perturb dedup.
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active:
        pv = await build_preview(
            object(),
            _xlsx_with_desc([_create_row(plotCode="P101"), _create_row(plotCode="P101")]),
            ctx=_ctx(),
        )
    assert pv.total_rows == 2
    assert all("ซ้ำ" in r.message for r in pv.rows)


async def test_description_row_with_pre_827_marker_text_is_still_skipped() -> None:
    # Round 8-2.7 appended "which action do I use" guidance onto the row-2
    # marker cell. A file whose row 2 was generated BEFORE that change (an
    # existing fixture, or a template a user downloaded earlier and kept
    # re-using) carries only the short, older marker text — it must still be
    # recognized and skipped by prefix, not misread as an invalid data row.
    old_row2 = [
        plot_import.TEMPLATE_DESCRIPTION_MARKER if c == "action" else None  # no 8-2.7 suffix
        for c in IMPORT_COLUMNS
    ]
    content = build_xlsx([("plots", [
        list(IMPORT_COLUMNS), old_row2,
        [_create_row(plotCode="P101").get(c) for c in IMPORT_COLUMNS],
    ])])
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active:
        pv = await build_preview(object(), content, ctx=_ctx())
    assert pv.total_rows == 1
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].payload.plot_code == "P101"


async def test_legacy_template_data_at_row_two_still_imports() -> None:
    # A file with NO description row (data starts at Excel row 2, the pre-8-2.1
    # shape) must be unaffected — its row 2 is a real action, not the marker.
    pv = await _preview([_create_row()], plot=None)  # _xlsx puts data at row 2
    assert pv.total_rows == 1
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].payload.plot_code == "P101"


async def test_marker_below_row_two_is_invalid_action_not_skipped() -> None:
    # The marker's skip only applies at Excel row 2. A row carrying the marker
    # anywhere else must validate as an unknown action, never be dropped.
    marker_row = {c: plot_import.TEMPLATE_COLUMN_DESCRIPTIONS[c] for c in IMPORT_COLUMNS}
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active:
        pv = await build_preview(
            object(), _xlsx([_create_row(plotCode="P101"), marker_row]), ctx=_ctx(),
        )
    assert pv.total_rows == 2  # nothing skipped
    errors = [r for r in pv.rows if r.status == "error"]
    assert len(errors) == 1
    assert "action" in errors[0].message


async def test_max_import_rows_counts_data_rows_only(monkeypatch) -> None:
    monkeypatch.setattr(plot_import, "MAX_IMPORT_ROWS", 1)
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active:
        # description + exactly 1 data row: within the limit (row 2 not counted).
        pv = await build_preview(object(), _xlsx_with_desc([_create_row()]), ctx=_ctx())
        assert pv.total_rows == 1
        # description + 2 data rows: exceeds the limit of 1.
        with pytest.raises(ImportFileError, match="เกินจำนวนแถวสูงสุด"):
            await build_preview(
                object(),
                _xlsx_with_desc([_create_row(plotCode="P101"), _create_row(plotCode="P102")]),
                ctx=_ctx(),
            )


async def test_commit_skips_template_description_row() -> None:
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock(return_value=_plot())) as m_create_plot, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=1))) as m_create_cycle:
        result = await commit_import(object(), _xlsx_with_desc([_create_row()]), ctx=_ctx())
    # Exactly the ONE data row executes; the description row never does.
    m_create_plot.assert_awaited_once()
    m_create_cycle.assert_awaited_once()
    assert result.created_plots == 1
    assert len(result.row_results) == 1


# --- round 8-2.3: duplicate close_and_start_new_cycle protection -----------

def _rollover_plan_row(**over) -> dict[str, str]:
    """A close_and_start_new_cycle row whose plan matches _matching_active_cycle."""
    base = {
        "action": "close_and_start_new_cycle", "supplierCode": "SUP001",
        "plotCode": "P003", "poNumber": "PO25004", "pCode": "Chili-D",
        "crop": "พริก", "variety": "พริกขี้หนู",
        "cycleLabel": "qa-cycle-4", "lotNo": "SMOKE-LOT-4",
        "plantingDate": "2026-08-09", "plantCount": "2000",
        "expectedYieldFull": "1600", "expectedYieldUnit": "kg",
    }
    base.update(over)
    return base


def _matching_active_cycle(**over) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=4, crop="พริก", variety="พริกขี้หนู", cycle_label="qa-cycle-4",
        lot_no="SMOKE-LOT-4", planting_date=datetime.date(2026, 8, 9),
        plant_count=2000, expected_yield_full=Decimal("1600.00"),
        expected_yield_unit="kg",
        # Round 8-5B — validation reads active.po_number; _capture reads source/running.
        po_number=None, p_code=None, lot_no_source=None, lot_running_no=None,
        supplier_lot_no=None,
        # Round 8-7A — validation captures active.updated_at.
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    base.update(over)
    return SimpleNamespace(**base)


async def test_rollover_exact_plan_match_is_duplicate_error() -> None:
    pv = await _preview([_rollover_plan_row()], plot=_plot(), active=_matching_active_cycle())
    assert pv.rows[0].status == "error"
    msg = pv.rows[0].message
    # (2) explains the anti-duplicate protection; (3) suggests update_current_cycle
    assert "จบรอบซ้ำ" in msg
    assert "ตรงกับรอบปลูกที่เปิดอยู่" in msg
    assert "update_current_cycle" in msg


async def test_rollover_valid_when_only_cycle_label_differs() -> None:
    pv = await _preview([_rollover_plan_row(cycleLabel="qa-cycle-5")],
                        plot=_plot(), active=_matching_active_cycle())
    assert pv.rows[0].status == "valid"


async def test_rollover_valid_when_only_lot_no_differs() -> None:
    pv = await _preview([_rollover_plan_row(lotNo="SMOKE-LOT-5")],
                        plot=_plot(), active=_matching_active_cycle())
    assert pv.rows[0].status == "valid"


async def test_rollover_valid_when_only_planting_date_differs() -> None:
    pv = await _preview([_rollover_plan_row(plantingDate="2026-09-01")],
                        plot=_plot(), active=_matching_active_cycle())
    assert pv.rows[0].status == "valid"


async def test_rollover_valid_when_only_crop_differs() -> None:
    pv = await _preview([_rollover_plan_row(crop="เมล่อน")],
                        plot=_plot(), active=_matching_active_cycle())
    assert pv.rows[0].status == "valid"


async def test_rollover_decimal_scale_difference_still_counts_as_match() -> None:
    # Decimal("1600.00") (row) vs Decimal("1600") (cycle) — same value → duplicate.
    pv = await _preview([_rollover_plan_row(expectedYieldFull="1600.00")],
                        plot=_plot(), active=_matching_active_cycle(expected_yield_full=Decimal("1600")))
    assert pv.rows[0].status == "error"
    assert "จบรอบซ้ำ" in pv.rows[0].message


async def test_rollover_whitespace_in_cycle_value_still_matches() -> None:
    # Cycle side carries stray whitespace; normalization trims it → still a match.
    pv = await _preview([_rollover_plan_row(crop="พริก")],
                        plot=_plot(), active=_matching_active_cycle(crop="  พริก  "))
    assert pv.rows[0].status == "error"


async def test_rollover_blank_string_equals_none_still_matches() -> None:
    # row lotNo="" → parsed None; cycle lot_no=None → equal (all else identical).
    pv = await _preview([_rollover_plan_row(lotNo="")],
                        plot=_plot(), active=_matching_active_cycle(lot_no=None))
    assert pv.rows[0].status == "error"


async def test_update_current_cycle_with_matching_plan_stays_valid() -> None:
    # The duplicate guard is rollover-only — an idempotent update must NOT be blocked.
    pv = await _preview([_rollover_plan_row(action="update_current_cycle")],
                        plot=_plot(), active=_matching_active_cycle())
    assert pv.rows[0].status == "valid"


async def test_start_new_cycle_not_affected_by_duplicate_guard() -> None:
    # start_new_cycle on a plot with NO active cycle is still valid.
    pv = await _preview([_rollover_plan_row(action="start_new_cycle")],
                        plot=_plot(), active=None)
    assert pv.rows[0].status == "valid"


async def test_rollover_duplicate_with_description_row_reports_row3_error() -> None:
    # Description row (Excel row 2) still skipped; the data row is Excel row 3.
    p_sup, p_plot, p_active = _patch_lookups(plot=_plot(), active=_matching_active_cycle())
    with p_sup, p_plot, p_active:
        pv = await build_preview(object(), _xlsx_with_desc([_rollover_plan_row()]), ctx=_ctx())
    assert pv.total_rows == 1
    assert pv.rows[0].row_number == 3
    assert pv.rows[0].status == "error"
    assert "จบรอบซ้ำ" in pv.rows[0].message


async def test_commit_time_recheck_rejects_duplicate_that_appeared_after_preview() -> None:
    """Race: validation saw a DIFFERENT active cycle (row is valid), but the
    plot-locked re-fetch at execute time now matches the row exactly → the
    commit-time guard rejects, never closing/creating/clearing anything."""
    plot = _plot()
    differing = _matching_active_cycle(cycle_label="different-at-preview-time")
    matching_now = _matching_active_cycle()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=differing)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=matching_now)), \
         patch(f"{_M}.plot_cycle_repo.rollover_cycle", AsyncMock(return_value=(_cycle(), _cycle(cycle_no=2)))) as m_rollover, \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()) as m_close, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=1))) as m_create, \
         patch(f"{_M}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()) as m_clear:
        with pytest.raises(ImportFileError, match="จบรอบซ้ำ"):
            await commit_import(object(), _xlsx([_rollover_plan_row()]), ctx=_ctx())
    m_rollover.assert_not_awaited()
    m_close.assert_not_awaited()
    m_create.assert_not_awaited()
    m_clear.assert_not_awaited()


async def test_mixed_batch_with_duplicate_rollover_fails_all_or_nothing() -> None:
    # A valid create row + a duplicate rollover row → whole file rejected,
    # nothing written (all-or-nothing).
    roll_plot = _plot()

    async def _by_code(db, supplier_id, code):
        return roll_plot if code == "P003" else None

    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", _by_code), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=_matching_active_cycle())), \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock()) as m_create_plot, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=1))) as m_create_cycle, \
         patch(f"{_M}.plot_cycle_repo.rollover_cycle", AsyncMock(return_value=(_cycle(), _cycle(cycle_no=2)))) as m_rollover:
        with pytest.raises(ImportHasErrors) as exc:
            await commit_import(
                object(),
                _xlsx([_create_row(plotCode="P900"), _rollover_plan_row()]),
                ctx=_ctx(),
            )
    m_create_plot.assert_not_awaited()
    m_create_cycle.assert_not_awaited()
    m_rollover.assert_not_awaited()
    assert exc.value.preview.error_rows == 1


# --- round 8-2.4: structured error code + result cycle no + raw ------------

async def test_preview_row_carries_duplicate_error_code() -> None:
    pv = await _preview([_rollover_plan_row()], plot=_plot(), active=_matching_active_cycle())
    assert pv.rows[0].status == "error"
    assert pv.rows[0].error_code == "duplicate_rollover"


async def test_preview_valid_row_has_no_error_code() -> None:
    pv = await _preview([_create_row()], plot=None)
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].error_code is None


async def test_commit_create_sets_result_cycle_no() -> None:
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock(return_value=_plot())), \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=1))):
        result = await commit_import(object(), _xlsx([_create_row()]), ctx=_ctx())
    assert result.row_results[0].result_cycle_no == 1


async def test_commit_start_sets_result_cycle_no() -> None:
    plot = _plot()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=2))), \
         patch(f"{_M}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()):
        result = await commit_import(
            object(), _xlsx([_create_row(action="start_new_cycle", plotCode="P001")]), ctx=_ctx())
    assert result.row_results[0].result_cycle_no == 2


async def test_commit_update_sets_result_cycle_no() -> None:
    plot = _plot()
    active = _cycle(cycle_no=3)
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()), \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()):
        result = await commit_import(
            object(), _xlsx([_create_row(action="update_current_cycle", plotCode="P002")]), ctx=_ctx())
    assert result.row_results[0].result_cycle_no == 3


async def test_commit_rollover_sets_new_cycle_no() -> None:
    plot = _plot()
    active = _cycle(cycle_no=4)  # differs from the create-row plan → not a duplicate
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.rollover_cycle",
               AsyncMock(return_value=(active, _cycle(cycle_no=5)))):
        result = await commit_import(
            object(), _xlsx([_create_row(action="close_and_start_new_cycle", plotCode="P003")]), ctx=_ctx())
    assert result.row_results[0].result_cycle_no == 5


async def test_row_state_keeps_raw_input_for_reporting() -> None:
    # An unparseable date must survive as the raw string (not the parser's None)
    # so report_row_view can echo exactly what the user typed.
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active:
        states = await plot_import.preview_states(
            object(), _xlsx([_create_row(plantingDate="not-a-date")]), ctx=_ctx())
    view = plot_import.report_row_view(states[0])
    assert view["raw"]["plantingDate"] == "not-a-date"
    # JSON payload still normalized (unparseable date → None), no regression.
    assert states[0].parsed.planting_date is None


async def test_result_workbook_reuploaded_is_revalidated_from_input_only() -> None:
    # Round 8-2.4 Step K: the user downloads a result workbook, fixes input,
    # and re-uploads. The importer must read ONLY the 18 input columns, ignore
    # the 5 result columns, skip the description row, and NOT trust a
    # client-supplied resultStatus=COMPLETED.
    from app.services import plot_import_report as report

    raw = {c: None for c in IMPORT_COLUMNS}
    raw.update({
        "action": "create_plot_with_cycle", "supplierCode": "SUP001",
        "plotCode": "P900", "plotName": "แปลงใหม่",
        # cycleLabel is required alongside pCode whenever lotNo is blank
        # (round 8-12A.1 — a blank lot requests an Auto Lot).
        "cycleLabel": "2605",
        "poNumber": "PO25001", "pCode": "Melon-A",
        "expectedYieldFull": "800", "expectedYieldUnit": "kg",
    })
    view = {
        "row_number": 3, "action": "create_plot_with_cycle", "status": "valid",
        "message": "", "error_code": None, "result_cycle_no": 1, "raw": raw,
    }
    # completed=True → the file's resultStatus cell says COMPLETED.
    workbook = report.build_plot_import_result_workbook(
        [view], phase=report.PHASE_COMMIT, completed=True)

    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active:
        pv = await build_preview(object(), workbook, ctx=_ctx())
    assert pv.total_rows == 1            # description row skipped; result cols not a row
    assert pv.rows[0].row_number == 3    # source Excel row preserved
    assert pv.rows[0].status == "valid"  # recomputed from input, COMPLETED not trusted


# --- round 8-2.7.1: unified start_next_cycle action -------------------------
#
# start_next_cycle resolves to whichever of start_new_cycle / close_and_
# start_new_cycle the plot's CURRENT state calls for. Preview computes an
# estimate (resolved_action); commit recomputes it FRESH under the plot lock
# and never trusts the preview value (Part A/D). Two lookup points matter:
#   get_active_cycle_for_plot            — validation (preview AND the
#                                           re-validation commit_import_execute
#                                           always runs first)
#   get_active_cycle_for_plot_for_update — the LOCKED re-check inside
#                                           _execute_row's start_next_cycle
#                                           branch specifically
# A test that wants to simulate "state changed between preview and commit"
# therefore patches these two to DIFFERENT return values.

def _start_next_row(**over) -> dict[str, str]:
    base = {
        "action": "start_next_cycle", "supplierCode": "SUP001",
        "plotCode": "P003", "cycleLabel": "sep2026",
        "poNumber": "PO25009", "pCode": "Melon-I",
        "crop": "แตงโม", "variety": "กินรี", "lotNo": "LOT-09",
        "plantingDate": "2026-09-01", "plantCount": "500",
        "expectedYieldFull": "900", "expectedYieldUnit": "kg",
    }
    base.update(over)
    return base


def _preview_state(content: bytes, snapshot_rows: list[dict]) -> PlotImportPreviewState:
    """Build the approved preview-state the client would echo back on commit
    (round 8-2.7.2): the real SHA-256 of `content` plus one snapshot row per
    start_next_cycle row. Each snapshot dict is {rowNumber, supplierCode,
    plotCode, resolvedAction, activeCycleId}."""
    return PlotImportPreviewState(
        file_sha256=plot_import.file_digest(content),
        start_next_rows=[PlotImportPreviewStateRow(**r) for r in snapshot_rows],
    )


# --- validation --------------------------------------------------------

async def test_start_next_cycle_requires_cycle_label() -> None:
    pv = await _preview(
        [_start_next_row(cycleLabel=None)], plot=_plot(), active=None,
    )
    assert pv.rows[0].status == "error"
    # Round 8-17A.1 — the start_next_cycle-specific message ("ต้องระบุ
    # cycleLabel สำหรับ start_next_cycle") was consolidated into the same
    # unconditional message every _NEW_CYCLE_ACTIONS member now uses.
    assert "กรุณาระบุชื่อรอบปลูก" in pv.rows[0].message


async def test_start_next_cycle_blank_cycle_label_after_trim_is_also_missing() -> None:
    pv = await _preview(
        [_start_next_row(cycleLabel="   ")], plot=_plot(), active=None,
    )
    assert pv.rows[0].status == "error"
    assert "กรุณาระบุชื่อรอบปลูก" in pv.rows[0].message


async def test_start_next_cycle_errors_when_plot_missing() -> None:
    pv = await _preview([_start_next_row()], plot=None)
    assert pv.rows[0].status == "error"
    assert "ไม่พบแปลง" in pv.rows[0].message
    assert "create_plot_with_cycle" in pv.rows[0].message


async def test_start_next_cycle_errors_when_plot_inactive() -> None:
    pv = await _preview(
        [_start_next_row()], plot=_plot(is_active=False), active=None,
    )
    assert pv.rows[0].status == "error"
    assert "ปิดใช้งานอยู่" in pv.rows[0].message


async def test_start_next_cycle_no_active_cycle_resolves_to_start() -> None:
    pv = await _preview([_start_next_row()], plot=_plot(), active=None)
    row = pv.rows[0]
    assert row.status == "valid"
    assert row.resolved_action == "start_new_cycle"
    assert row.current_cycle_no is None
    assert row.current_cycle_label is None


async def test_start_next_cycle_active_cycle_different_label_resolves_to_rollover() -> None:
    active = _cycle(cycle_no=7, cycle_label="aug2026")
    pv = await _preview(
        [_start_next_row(cycleLabel="sep2026")], plot=_plot(), active=active,
    )
    row = pv.rows[0]
    assert row.status == "valid"
    assert row.resolved_action == "close_and_start_new_cycle"
    assert row.current_cycle_no == 7
    assert row.current_cycle_label == "aug2026"


async def test_start_next_cycle_same_active_cycle_label_errors() -> None:
    active = _cycle(cycle_no=7, cycle_label="sep2026")
    pv = await _preview(
        [_start_next_row(cycleLabel="sep2026")], plot=_plot(), active=active,
    )
    row = pv.rows[0]
    assert row.status == "error"
    assert row.error_code == "same_active_cycle_label"
    assert "update_current_cycle" in row.message
    assert row.resolved_action is None  # never resolves when blocked


async def test_start_next_cycle_label_match_is_trimmed_and_case_insensitive() -> None:
    active = _cycle(cycle_no=7, cycle_label="Sep2026")
    pv = await _preview(
        [_start_next_row(cycleLabel="  sep2026  ")], plot=_plot(), active=active,
    )
    row = pv.rows[0]
    assert row.status == "error"
    assert row.error_code == "same_active_cycle_label"


async def test_start_next_cycle_same_label_message_explains_cause_and_fix() -> None:
    """Round 8-6A Part G — the visible message must name cycleLabel
    explicitly, say it's a DUPLICATE with the currently-open cycle (not just
    "matches"), and still point at update_current_cycle as the fix. errorCode
    and the trim/case-insensitive comparison itself are unchanged (see the
    tests directly above/below)."""
    active = _cycle(cycle_no=7, cycle_label="sep2026")
    pv = await _preview(
        [_start_next_row(cycleLabel="sep2026")], plot=_plot(), active=active,
    )
    row = pv.rows[0]
    assert row.error_code == "same_active_cycle_label"
    assert "cycleLabel" in row.message
    assert "ซ้ำ" in row.message
    assert "ชื่อรอบใหม่" in row.message
    assert "update_current_cycle" in row.message


async def test_start_next_cycle_preview_duplicate_label_never_calls_write_helpers() -> None:
    """Part I item 31 — Preview is read-only by construction (build_preview
    never references the commit write helpers at all), pinned explicitly here
    so a future refactor can't accidentally wire a write call into the
    preview path without a test catching it."""
    active = _cycle(cycle_no=7, cycle_label="sep2026")
    with patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()) as m_close, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock()) as m_create, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock()) as m_create_plot:
        pv = await _preview(
            [_start_next_row(cycleLabel="sep2026")], plot=_plot(), active=active,
        )
    assert pv.rows[0].error_code == "same_active_cycle_label"
    m_close.assert_not_awaited()
    m_create.assert_not_awaited()
    m_create_plot.assert_not_awaited()


async def test_start_next_cycle_commit_with_same_label_error_mutates_nothing() -> None:
    """Part I item 32 — commit_import re-validates first (commit_import_
    execute step 1) and raises ImportHasErrors on ANY row error, including a
    same_active_cycle_label row, before locking or executing anything."""
    active = _cycle(cycle_no=7, cycle_label="sep2026")
    p_sup, p_plot, p_active = _patch_lookups(plot=_plot(), active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()) as m_close, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock()) as m_create, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock()) as m_create_plot:
        with pytest.raises(ImportHasErrors) as exc:
            await commit_import(
                object(), _xlsx([_start_next_row(cycleLabel="sep2026")]), ctx=_ctx(),
            )
    assert exc.value.preview.rows[0].error_code == "same_active_cycle_label"
    m_close.assert_not_awaited()
    m_create.assert_not_awaited()
    m_create_plot.assert_not_awaited()


async def test_start_next_cycle_out_of_scope_supplier_still_rejected() -> None:
    sup = _supplier()
    pv = await _preview(
        [_start_next_row()], ctx=_ctx(allowed=uuid4()), supplier=sup, plot=None,
    )
    assert pv.rows[0].status == "error"
    assert "นอกขอบเขต" in pv.rows[0].message


async def test_start_next_cycle_needs_plots_update_permission() -> None:
    pv = await _preview(
        [_start_next_row()], ctx=_ctx(can_update=False), plot=_plot(), active=None,
    )
    assert pv.rows[0].status == "error"
    assert "plots.update" in pv.rows[0].message
    assert "start_next_cycle" in pv.rows[0].message


# --- duplicate protection reuse (round 8-2.3) ---------------------------

async def test_start_next_cycle_reuses_duplicate_rollover_full_plan_helper() -> None:
    """Structural guard: the ACTION_START_NEXT resolution branch reuses
    _cycle_plan_matches_import / ERROR_CODE_DUPLICATE_ROLLOVER as a second,
    defense-in-depth layer behind the cycleLabel check (Part C) — it must not
    reimplement its own duplicate-plan comparison. Note: because cycleLabel is
    required and must differ from the active cycle's to even reach this
    branch (any label match is already caught by same_active_cycle_label,
    which is case-insensitive and therefore strictly broader than the
    case-sensitive comparison _cycle_plan_matches_import's label component
    uses), this second layer is not independently reachable through normal
    input today — it exists purely so the two rollover paths can never drift
    if that precondition ever changes."""
    src = Path(plot_import.__file__).read_text(encoding="utf-8")
    start_next_block = src[src.index("if p.action == ACTION_START_NEXT:"):]
    start_next_block = start_next_block[:start_next_block.index("\n    return state")]
    assert "_cycle_plan_matches_import(active, p)" in start_next_block
    assert "ERROR_CODE_DUPLICATE_ROLLOVER" in start_next_block


async def test_start_next_cycle_reupload_after_commit_is_blocked_via_same_label() -> None:
    # Round 8-2.7.1 Part C/Part I item 18: re-uploading the SAME file after a
    # successful start_next_cycle rollover commit must be blocked. Since the
    # just-created cycle's label is now exactly this row's cycleLabel, the
    # everyday mechanism that catches it is same_active_cycle_label (cycle-
    # Label is start_next_cycle's one required, distinguishing field) — not
    # the full 8-field duplicate_rollover match reused above.
    active_after_commit = _cycle(cycle_no=8, cycle_label="sep2026")
    pv = await _preview(
        [_start_next_row(cycleLabel="sep2026")], plot=_plot(), active=active_after_commit,
    )
    assert pv.rows[0].status == "error"
    assert pv.rows[0].error_code == "same_active_cycle_label"


# --- commit (execution) --------------------------------------------------

async def test_commit_start_next_cycle_no_active_creates_and_clears_snapshot() -> None:
    plot = _plot()
    content = _xlsx([_start_next_row()])
    # Approved preview: this row resolved to start (no active cycle) — matches
    # the commit-time locked state, so the snapshot binding passes.
    preview_state = _preview_state(content, [dict(
        rowNumber=2, supplierCode="SUP001", plotCode="P003",
        resolvedAction="start_new_cycle", activeCycleId=None,
    )])
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock()) as m_create_plot, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=1))) as m_create_cycle, \
         patch(f"{_M}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()) as m_clear:
        result = await commit_import(object(), content, ctx=_ctx(), preview_state=preview_state)

    m_create_plot.assert_not_awaited()
    m_create_cycle.assert_awaited_once()
    m_clear.assert_awaited_once()
    # Bucketed as started_cycles — resolved_action, not the literal action string.
    assert result.started_cycles == 1
    assert result.rolled_over_cycles == 0
    assert result.row_results[0].result_cycle_no == 1


async def test_commit_start_next_cycle_active_uses_shared_rollover_helper() -> None:
    plot = _plot()
    active = _cycle(cycle_no=7, cycle_label="aug2026")
    uid = uuid4()
    content = _xlsx([_start_next_row(cycleLabel="sep2026")])
    # Approved preview: this row resolved to rollover closing cycle `active` —
    # commit-time locked state is the SAME cycle id, so the binding passes.
    preview_state = _preview_state(content, [dict(
        rowNumber=2, supplierCode="SUP001", plotCode="P003",
        resolvedAction="close_and_start_new_cycle", activeCycleId=active.id,
    )])
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()) as m_close, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=8))) as m_create_cycle, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock()) as m_create_plot, \
         patch(f"{_M}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()) as m_clear:
        result = await commit_import(
            object(), content, preview_state=preview_state,
            ctx=ImportContext(allowed_supplier_id=None, can_create=True, can_update=True, user_id=uid),
        )

    # Same close→create→clear-snapshot core as close_and_start_new_cycle,
    # via the shared rollover_cycle helper (Part D) — old cycle closed
    # harvested, never cancelled; a distinct close reason names this action.
    m_close.assert_awaited_once()
    _, close_kwargs = m_close.call_args
    assert close_kwargs["status"] == "harvested"
    assert close_kwargs["closed_by_id"] == uid
    assert close_kwargs["reason"] == plot_import.ROLLOVER_CLOSE_REASON_START_NEXT
    m_create_cycle.assert_awaited_once()
    m_create_plot.assert_not_awaited()
    m_clear.assert_awaited_once()
    assert result.rolled_over_cycles == 1
    assert result.started_cycles == 0
    assert result.row_results[0].result_cycle_no == 8
    # Records/QR/plot.is_active are never touched by this action (structural
    # guarantee already pinned file-wide by test_service_never_imports_
    # record_or_deactivate_or_qr_paths and test_service_only_closes_as_
    # harvested — both re-verified as still passing for this new branch).
    assert plot.is_active is True


async def test_commit_start_next_cycle_locks_plot_before_active_cycle_in_source() -> None:
    """Same structural guard as test_import_locks_plot_before_any_cycle_call_
    in_source, scoped to the ACTION_START_NEXT branch specifically: it must
    call get_plot_for_update (via the shared _lock_existing_plots pass) before
    get_active_cycle_for_plot_for_update inside its own branch body."""
    src = Path(plot_import.__file__).read_text(encoding="utf-8")
    start_next_def = src.index("if p.action == ACTION_START_NEXT:")
    branch_body = src[start_next_def:]
    assert "get_active_cycle_for_plot_for_update" in branch_body
    # The branch itself never calls get_plot_for_update (that's done once,
    # up front, by _lock_existing_plots for every action) — confirm the
    # shared lock helper is defined, and textually precedes _execute_row.
    assert src.index("async def _lock_existing_plots") < src.index("async def _execute_row")


async def test_commit_start_next_cycle_snapshot_match_start_branch_succeeds() -> None:
    # Snapshot binding round-trip (Part G item 13): preview resolved to start
    # (no active cycle) and the commit-time locked state agrees → the start
    # branch executes normally. (The divergent case is the regression tests
    # in the "preview-state binding" section below.)
    plot = _plot()
    content = _xlsx([_start_next_row()])
    preview_state = _preview_state(content, [dict(
        rowNumber=2, supplierCode="SUP001", plotCode="P003",
        resolvedAction="start_new_cycle", activeCycleId=None,
    )])
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=1))), \
         patch(f"{_M}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()):
        result = await commit_import(object(), content, ctx=_ctx(), preview_state=preview_state)

    assert result.started_cycles == 1
    assert result.rolled_over_cycles == 0


async def test_commit_start_next_cycle_integrity_error_propagates_for_409() -> None:
    # A genuine DB-level race (the partial unique index backstop) must
    # propagate uncaught so the endpoint's IntegrityError → 409 handler (same
    # pattern as every other action) catches it — the service never swallows it.
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    plot = _plot()
    active = _cycle(cycle_no=7, cycle_label="aug2026")
    content = _xlsx([_start_next_row(cycleLabel="sep2026")])
    preview_state = _preview_state(content, [dict(
        rowNumber=2, supplierCode="SUP001", plotCode="P003",
        resolvedAction="close_and_start_new_cycle", activeCycleId=active.id,
    )])
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()), \
         patch(f"{_M}.plot_cycle_repo.create_cycle",
               AsyncMock(side_effect=SAIntegrityError("stmt", {}, Exception("dup")))):
        with pytest.raises(SAIntegrityError):
            await commit_import(object(), content, ctx=_ctx(), preview_state=preview_state)


async def test_commit_start_next_cycle_failure_after_close_propagates_for_rollback() -> None:
    # If create_cycle fails AFTER close_cycle succeeded, the exception must
    # still propagate (never swallowed) so get_db's single transaction rolls
    # BOTH the close and the (never-happened) create back — the plot must
    # never end up with zero active cycles.
    plot = _plot()
    active = _cycle(cycle_no=7, cycle_label="aug2026")
    content = _xlsx([_start_next_row(cycleLabel="sep2026")])
    preview_state = _preview_state(content, [dict(
        rowNumber=2, supplierCode="SUP001", plotCode="P003",
        resolvedAction="close_and_start_new_cycle", activeCycleId=active.id,
    )])
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()) as m_close, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(side_effect=RuntimeError("db boom"))):
        with pytest.raises(RuntimeError, match="db boom"):
            await commit_import(object(), content, ctx=_ctx(), preview_state=preview_state)
    m_close.assert_awaited_once()  # close DID run — rollback is the caller's job, not a skip


# --- round 8-2.7.2: preview-state binding (digest + resolution snapshot) ---
#
# A start_next_cycle file's commit is bound to the read-only preview the user
# approved: the file SHA-256 must match, and every start_next row must still
# resolve to the SAME branch / SAME active cycle it was shown. Any divergence
# raises ImportPreviewStateConflict BEFORE any row executes.

def _no_write_patches():
    """The write helpers, all patched so a test can assert ZERO of them ran
    when a commit is rejected pre-execute."""
    return {
        "create_plot": patch(f"{_M}.plot_repo.create_plot", AsyncMock()),
        "create_cycle": patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=1))),
        "close_cycle": patch(f"{_M}.plot_cycle_repo.close_cycle", AsyncMock()),
        "rollover": patch(f"{_M}.plot_cycle_repo.rollover_cycle", AsyncMock(return_value=(_cycle(), _cycle(cycle_no=2)))),
        "update_cycle": patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()),
        "clear": patch(f"{_M}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()),
    }


# 1–4: preview response carries fileSha256 + a snapshot row per start_next row.

async def test_preview_returns_file_sha256_matching_content() -> None:
    content = _xlsx([_start_next_row()])
    p_sup, p_plot, p_active = _patch_lookups(plot=_plot(), active=None)
    with p_sup, p_plot, p_active:
        pv = await build_preview(object(), content, ctx=_ctx())
    assert pv.preview_state is not None
    assert pv.preview_state.file_sha256 == plot_import.file_digest(content)
    # digest is a real 64-hex sha256, never the file bytes.
    assert len(pv.preview_state.file_sha256) == 64
    int(pv.preview_state.file_sha256, 16)  # hex-decodable


async def test_preview_snapshot_has_one_row_per_start_next_row() -> None:
    rows = [
        _start_next_row(plotCode="P003", cycleLabel="a"),
        _create_row(plotCode="P900"),  # non-start_next → not in snapshot
        _start_next_row(plotCode="P004", cycleLabel="b"),
    ]
    # P003 no active → start; P004 has active → rollover; different plots so
    # get_plot_by_code/get_active must vary by code.
    active = _cycle(cycle_no=2, cycle_label="old")
    plots = {"P003": _plot(), "P004": _plot(), "P900": None}
    actives = {"P003": None, "P004": active}

    async def _by_code(db, supplier_id, code):
        return plots[code]

    async def _active(db, plot_id):
        # map plot id back to code via the plots dict
        for code, pl in plots.items():
            if pl is not None and pl.id == plot_id:
                return actives[code]
        return None

    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", _by_code), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", _active):
        pv = await build_preview(object(), _xlsx(rows), ctx=_ctx())

    snap = pv.preview_state.start_next_rows
    assert len(snap) == 2  # only the two start_next rows
    by_plot = {r.plot_code: r for r in snap}
    assert by_plot["P003"].resolved_action == "start_new_cycle"
    assert by_plot["P003"].active_cycle_id is None
    assert by_plot["P004"].resolved_action == "close_and_start_new_cycle"
    assert by_plot["P004"].active_cycle_id == active.id


# 5–7: file-level gates (missing state / malformed handled at endpoint / digest).

async def test_commit_start_next_without_preview_state_is_rejected_zero_mutation() -> None:
    content = _xlsx([_start_next_row()])
    p_sup, p_plot, p_active = _patch_lookups(plot=_plot(), active=None)
    writes = _no_write_patches()
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=_plot())), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         writes["create_plot"] as m_create_plot, writes["create_cycle"] as m_create_cycle, \
         writes["close_cycle"], writes["rollover"], writes["update_cycle"], writes["clear"]:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(object(), content, ctx=_ctx(), preview_state=None)
    assert exc.value.reason == "missing_preview_state"
    m_create_plot.assert_not_awaited()
    m_create_cycle.assert_not_awaited()


async def test_commit_start_next_digest_mismatch_rejected_before_lock_or_execute() -> None:
    content = _xlsx([_start_next_row()])
    # Snapshot for a DIFFERENT file (wrong digest) — must reject before locking.
    stale = _preview_state(_xlsx([_start_next_row(plotCode="P999")]), [dict(
        rowNumber=2, supplierCode="SUP001", plotCode="P003",
        resolvedAction="start_new_cycle", activeCycleId=None,
    )])
    p_sup, p_plot, p_active = _patch_lookups(plot=_plot(), active=None)
    lock_calls: list = []

    async def _lock(db, plot_id):
        lock_calls.append(plot_id)
        return _plot()

    writes = _no_write_patches()
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", _lock), \
         writes["create_plot"], writes["create_cycle"] as m_create_cycle, \
         writes["close_cycle"], writes["rollover"], writes["update_cycle"], writes["clear"]:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(object(), content, ctx=_ctx(), preview_state=stale)
    assert exc.value.reason == "file_digest_mismatch"
    assert lock_calls == []  # rejected BEFORE any plot lock
    m_create_cycle.assert_not_awaited()


# 8–12: resolution/identity divergence under lock.

async def test_commit_preview_start_but_commit_finds_active_rejected() -> None:
    # Part H Case 1: preview saw NO active cycle (resolved start); by commit an
    # active cycle A exists. Must reject; A stays untouched; no cycle B created.
    content = _xlsx([_start_next_row(cycleLabel="sep2026")])
    preview_state = _preview_state(content, [dict(
        rowNumber=2, supplierCode="SUP001", plotCode="P003",
        resolvedAction="start_new_cycle", activeCycleId=None,
    )])
    plot = _plot()
    active_A = _cycle(cycle_no=3, cycle_label="aug2026")
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=None)  # preview: none
    writes = _no_write_patches()
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active_A)), \
         writes["create_plot"], writes["create_cycle"] as m_create_cycle, \
         writes["close_cycle"] as m_close, writes["rollover"] as m_rollover, \
         writes["update_cycle"], writes["clear"]:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(object(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.reason == "resolution_changed"
    assert exc.value.changed_rows == [2]
    m_create_cycle.assert_not_awaited()
    m_close.assert_not_awaited()
    m_rollover.assert_not_awaited()


async def test_commit_preview_rollover_but_commit_finds_no_active_rejected() -> None:
    # Part H Case 2 (inverse): preview saw active A (resolved rollover); by
    # commit the plot has NO active cycle. Must reject, zero mutation.
    content = _xlsx([_start_next_row(cycleLabel="sep2026")])
    active_A = _cycle(cycle_no=3, cycle_label="aug2026")
    preview_state = _preview_state(content, [dict(
        rowNumber=2, supplierCode="SUP001", plotCode="P003",
        resolvedAction="close_and_start_new_cycle", activeCycleId=active_A.id,
    )])
    plot = _plot()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active_A)  # preview: A
    writes = _no_write_patches()
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         writes["create_plot"], writes["create_cycle"] as m_create_cycle, \
         writes["close_cycle"] as m_close, writes["rollover"] as m_rollover, \
         writes["update_cycle"], writes["clear"]:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(object(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.reason == "resolution_changed"
    m_create_cycle.assert_not_awaited()
    m_close.assert_not_awaited()
    m_rollover.assert_not_awaited()


async def test_commit_preview_cycle_A_but_commit_finds_cycle_B_rejected() -> None:
    # Part H Case 2: preview saw active A; by commit A was closed and B is now
    # active. activeCycleId differs (A → B) → reject; B stays active.
    content = _xlsx([_start_next_row(cycleLabel="sep2026")])
    active_A = _cycle(cycle_no=3, cycle_label="aug2026")
    active_B = _cycle(cycle_no=4, cycle_label="nov2026")
    preview_state = _preview_state(content, [dict(
        rowNumber=2, supplierCode="SUP001", plotCode="P003",
        resolvedAction="close_and_start_new_cycle", activeCycleId=active_A.id,
    )])
    plot = _plot()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active_A)  # preview: A
    writes = _no_write_patches()
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active_B)), \
         writes["create_plot"], writes["create_cycle"] as m_create_cycle, \
         writes["close_cycle"] as m_close, writes["rollover"] as m_rollover, \
         writes["update_cycle"], writes["clear"]:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(object(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.reason == "resolution_changed"
    assert exc.value.changed_rows == [2]
    m_close.assert_not_awaited()
    m_rollover.assert_not_awaited()
    m_create_cycle.assert_not_awaited()


async def test_commit_snapshot_row_identity_mismatch_rejected() -> None:
    # Snapshot names a DIFFERENT plot for row 2 than the file does → reject
    # (defense-in-depth on top of the digest, which already guards file edits).
    content = _xlsx([_start_next_row()])
    preview_state = _preview_state(content, [dict(
        rowNumber=2, supplierCode="SUP001", plotCode="P999",  # file says P003
        resolvedAction="start_new_cycle", activeCycleId=None,
    )])
    plot = _plot()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=None)
    writes = _no_write_patches()
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         writes["create_plot"], writes["create_cycle"] as m_create_cycle, \
         writes["close_cycle"], writes["rollover"], writes["update_cycle"], writes["clear"]:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(object(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.reason == "resolution_changed"
    m_create_cycle.assert_not_awaited()


async def test_commit_snapshot_missing_start_next_row_rejected() -> None:
    # File has a start_next row but the snapshot omits it → row-set mismatch.
    content = _xlsx([_start_next_row()])
    preview_state = _preview_state(content, [])  # empty snapshot
    plot = _plot()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=None)
    writes = _no_write_patches()
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         writes["create_plot"], writes["create_cycle"] as m_create_cycle, \
         writes["close_cycle"], writes["rollover"], writes["update_cycle"], writes["clear"]:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(object(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.reason == "row_set_mismatch"
    m_create_cycle.assert_not_awaited()


async def test_commit_snapshot_extra_row_rejected() -> None:
    # Snapshot claims a start_next row the file doesn't have → row-set mismatch.
    content = _xlsx([_start_next_row()])
    preview_state = _preview_state(content, [
        dict(rowNumber=2, supplierCode="SUP001", plotCode="P003",
             resolvedAction="start_new_cycle", activeCycleId=None),
        dict(rowNumber=99, supplierCode="SUP001", plotCode="P888",
             resolvedAction="start_new_cycle", activeCycleId=None),
    ])
    plot = _plot()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=None)
    writes = _no_write_patches()
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         writes["create_plot"], writes["create_cycle"] as m_create_cycle, \
         writes["close_cycle"], writes["rollover"], writes["update_cycle"], writes["clear"]:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(object(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.reason == "row_set_mismatch"
    m_create_cycle.assert_not_awaited()


# 14: matching rollover snapshot succeeds (start success covered above).

async def test_commit_snapshot_match_rollover_branch_succeeds() -> None:
    content = _xlsx([_start_next_row(cycleLabel="sep2026")])
    active = _cycle(cycle_no=4, cycle_label="aug2026")
    preview_state = _preview_state(content, [dict(
        rowNumber=2, supplierCode="SUP001", plotCode="P003",
        resolvedAction="close_and_start_new_cycle", activeCycleId=active.id,
    )])
    plot = _plot()
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.rollover_cycle",
               AsyncMock(return_value=(active, _cycle(cycle_no=5)))):
        result = await commit_import(object(), content, ctx=_ctx(), preview_state=preview_state)
    assert result.rolled_over_cycles == 1


# 15: snapshot verified for ALL rows before the FIRST execute.

async def test_commit_verifies_all_snapshots_before_any_execute() -> None:
    # Two start_next rows: row 2 matches, row 3 diverges. The mismatch must
    # abort the whole file with NOTHING executed — not "row 2 committed, row 3
    # rejected". Proven by asserting create_cycle never ran at all.
    rows = [_start_next_row(plotCode="P003", cycleLabel="a"),
            _start_next_row(plotCode="P004", cycleLabel="b")]
    content = _xlsx(rows)
    plot3, plot4 = _plot(), _plot()
    active4 = _cycle(cycle_no=2, cycle_label="old")
    # Preview: P003 no active (start); P004 active4 (rollover).
    preview_state = _preview_state(content, [
        dict(rowNumber=2, supplierCode="SUP001", plotCode="P003",
             resolvedAction="start_new_cycle", activeCycleId=None),
        dict(rowNumber=3, supplierCode="SUP001", plotCode="P004",
             resolvedAction="close_and_start_new_cycle", activeCycleId=active4.id),
    ])
    plots = {"P003": plot3, "P004": plot4}
    preview_actives = {"P003": None, "P004": active4}
    # Commit-time (locked): P003 still start, but P004's active is now a
    # DIFFERENT cycle → divergence on row 3.
    locked_actives = {plot3.id: None, plot4.id: _cycle(cycle_no=9, cycle_label="new")}

    async def _by_code(db, supplier_id, code):
        return plots[code]

    async def _active(db, plot_id):
        for code, pl in plots.items():
            if pl.id == plot_id:
                return preview_actives[code]
        return None

    async def _lock(db, plot_id):
        return plot3 if plot_id == plot3.id else plot4

    async def _active_locked(db, plot_id):
        return locked_actives[plot_id]

    writes = _no_write_patches()
    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", _by_code), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", _active), \
         patch(f"{_M}.plot_repo.get_plot_for_update", _lock), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", _active_locked), \
         writes["create_plot"], writes["create_cycle"] as m_create_cycle, \
         writes["close_cycle"], writes["rollover"] as m_rollover, \
         writes["update_cycle"], writes["clear"]:
        with pytest.raises(ImportPreviewStateConflict) as exc:
            await commit_import(object(), content, ctx=_ctx(), preview_state=preview_state)
    assert exc.value.changed_rows == [3]
    m_create_cycle.assert_not_awaited()  # row 2's start never executed either
    m_rollover.assert_not_awaited()


# 17: legacy four actions commit WITHOUT previewState (backward compatible).

async def test_commit_legacy_actions_need_no_preview_state() -> None:
    rows = [_create_row(plotCode="P900")]
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock(return_value=_plot())) as m_create_plot, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle())):
        result = await commit_import(object(), _xlsx(rows), ctx=_ctx(), preview_state=None)
    m_create_plot.assert_awaited_once()
    assert result.created_plots == 1


async def test_commit_mixed_file_still_binds_only_start_next_rows() -> None:
    # A file mixing a legacy create row and a start_next row still requires a
    # preview_state (because of the start_next row) — but the snapshot only
    # covers the start_next row; the legacy row needs no snapshot entry.
    rows = [_create_row(plotCode="P900"), _start_next_row(plotCode="P003", cycleLabel="a")]
    content = _xlsx(rows)
    plot3 = _plot()

    async def _by_code(db, supplier_id, code):
        return {"P900": None, "P003": plot3}[code]

    preview_state = _preview_state(content, [dict(
        rowNumber=3, supplierCode="SUP001", plotCode="P003",
        resolvedAction="start_new_cycle", activeCycleId=None,
    )])
    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())), \
         patch(f"{_M}.plot_repo.get_plot_by_code", _by_code), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot3)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock(return_value=_plot())) as m_create_plot, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(cycle_no=1))), \
         patch(f"{_M}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()):
        result = await commit_import(object(), content, ctx=_ctx(), preview_state=preview_state)
    m_create_plot.assert_awaited_once()
    assert result.created_plots == 1
    assert result.started_cycles == 1


def test_commit_verifies_snapshot_before_execute_in_source() -> None:
    """Structural guard (Part D ordering): _verify_start_next_snapshot must be
    called BEFORE the execute loop inside commit_import_execute — verifying
    only after some rows executed would defeat the all-or-nothing guarantee."""
    src = Path(plot_import.__file__).read_text(encoding="utf-8")
    body = src[src.index("async def commit_import_execute"):]
    body = body[:body.index("\n\nasync def commit_import(")]
    assert "_check_preview_state_file" in body
    assert "_lock_existing_plots" in body
    assert "_verify_start_next_snapshot" in body
    # verify happens before the `for state in states:` execute loop.
    assert body.index("_verify_start_next_snapshot") < body.index("for state in states:")
    # digest check happens before locking.
    assert body.index("_check_preview_state_file") < body.index("_lock_existing_plots")


def test_file_digest_is_sha256_and_never_logs_content() -> None:
    import hashlib
    content = b"some file bytes"
    assert plot_import.file_digest(content) == hashlib.sha256(content).hexdigest()


# --- backward compatibility + contract-wide checks -----------------------

def test_supported_actions_has_seven_actions_including_final_plot() -> None:
    # Round 8-6H added reactivate_plot_with_cycle (6th); round 8-7A added
    # final_plot (7th).
    assert len(plot_import.SUPPORTED_ACTIONS) == 7
    assert plot_import.ACTION_START_NEXT == "start_next_cycle"
    assert plot_import.ACTION_REACTIVATE_WITH_CYCLE == "reactivate_plot_with_cycle"
    assert plot_import.ACTION_FINAL == "final_plot"
    assert set(plot_import.SUPPORTED_ACTIONS) == {
        "create_plot_with_cycle", "start_new_cycle", "update_current_cycle",
        "close_and_start_new_cycle", "start_next_cycle", "reactivate_plot_with_cycle",
        "final_plot",
    }


async def test_legacy_template_row_two_marker_from_827_still_skipped() -> None:
    # Round 8-2.7's (pre-8-2.7.1) row-2 text is a prefix-compatible ancestor
    # of today's — confirm THIS round's code still skips it, not just the
    # original pre-8-2.7 short marker (already pinned by test_description_
    # row_with_pre_827_marker_text_is_still_skipped).
    old_827_text = (
        plot_import.TEMPLATE_DESCRIPTION_MARKER + " — action หลักมี 3 แบบ: "
        "create_plot_with_cycle = สร้างแปลงใหม่พร้อมรอบปลูกแรก, "
        "update_current_cycle = แก้ข้อมูลรอบปลูกที่กำลังเปิดอยู่ โดยไม่สร้างรอบใหม่, "
        "close_and_start_new_cycle = จบรอบเดิมและเริ่มรอบใหม่ในครั้งเดียว ประวัติเดิมไม่หาย. "
        "start_new_cycle ใช้เฉพาะกรณีแปลงไม่มีรอบปลูกเปิดอยู่แล้ว"
    )
    old_row2 = [
        old_827_text if c == "action" else None for c in IMPORT_COLUMNS
    ]
    content = build_xlsx([("plots", [
        list(IMPORT_COLUMNS), old_row2,
        [_create_row(plotCode="P101").get(c) for c in IMPORT_COLUMNS],
    ])])
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active:
        pv = await build_preview(object(), content, ctx=_ctx())
    assert pv.total_rows == 1
    assert pv.rows[0].status == "valid"


async def test_result_workbook_round_trip_preserves_start_next_cycle_action() -> None:
    from app.services import plot_import_report as report

    raw = {c: None for c in IMPORT_COLUMNS}
    raw.update(_start_next_row())
    view = {
        "row_number": 3, "action": "start_next_cycle", "status": "valid",
        "message": "", "error_code": None, "result_cycle_no": 9,
        "resolved_action": "close_and_start_new_cycle", "raw": raw,
    }
    workbook = report.build_plot_import_result_workbook(
        [view], phase=report.PHASE_COMMIT, completed=True)

    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active:
        pv = await build_preview(object(), workbook, ctx=_ctx())
    # The action cell round-trips exactly — re-validated fresh from raw input,
    # not the (unrelated, plot=None here) resolved_action/status echoed above.
    assert pv.rows[0].payload.action == "start_next_cycle"


# =====================================================================
# Round 8-5B — PO / P.Code + Auto Lot in Excel import
# =====================================================================

# A missing poNumber on create used to be a 422/error here (round 8-5B) —
# round 8-13A made it optional; see test_create_plot_with_cycle_without_po_is_
# valid further down for the current contract's own dedicated test.


async def test_create_row_missing_pcode_errors() -> None:
    pv = await _preview([_create_row(pCode=None)], plot=None)
    assert pv.rows[0].status == "error"
    assert "pCode" in pv.rows[0].message


async def test_create_manual_lot_preview_is_manual() -> None:
    pv = await _preview([_create_row(lotNo="HAND-01")], plot=None)
    row = pv.rows[0]
    assert row.status == "valid"
    assert row.lot_mode == "manual"
    assert row.proposed_lot_no == "HAND-01"
    assert row.payload.po_number == "PO25001"  # normalized (already upper)


async def test_create_blank_lot_preview_is_auto_v2_formula() -> None:
    """Round 8-12A — the preview shows
    {cycleLabel}-{supplierCode}-{pCode}-### (### = the running number, which is
    allocated only at commit). The supplier code is the AUTHORITATIVE one
    resolved during validation, not the row's own supplierCode cell."""
    pv = await _preview(
        [_create_row(lotNo=None, cycleLabel="2605", pCode="WM-141", plotCode="p101")],
        plot=None,
    )
    row = pv.rows[0]
    assert row.status == "valid"
    assert row.lot_mode == "auto"
    assert row.proposed_lot_no == "2605-SUP001-WM-141-###"
    # the retired V1 shape must never appear again
    assert "XX" not in row.proposed_lot_no
    assert "PO25001" not in (row.proposed_lot_no or "")


async def test_update_blank_lot_preserves_existing_lot_preview_and_execute() -> None:
    plot = _plot()
    active = _cycle(cycle_no=2, lot_no="OLD-LOT", lot_no_source="manual")
    # preview
    pv = await _preview(
        [_create_row(action="update_current_cycle", plotCode="P002", lotNo=None,
                     poNumber=None, pCode=None)],
        plot=plot, active=active,
    )
    row = pv.rows[0]
    assert row.status == "valid"
    assert row.lot_mode == "preserve"
    assert row.proposed_lot_no == "OLD-LOT"
    # execute — update_cycle must be called WITHOUT a lot_no key (preserve),
    # and without po_number/p_code (blank → preserve).
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()) as m_update, \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()):
        await commit_import(object(), _xlsx([_create_row(
            action="update_current_cycle", plotCode="P002", lotNo=None,
            poNumber=None, pCode=None)]), ctx=_ctx())
    fields = m_update.call_args.args[3]
    assert "lot_no" not in fields         # preserve existing lot
    assert "po_number" not in fields      # blank PO → preserve
    assert "p_code" not in fields


async def test_update_manual_lot_and_new_po_are_sent() -> None:
    plot = _plot()
    active = _cycle(cycle_no=2, lot_no="OLD-LOT", lot_no_source="auto")
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", AsyncMock()) as m_update, \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()):
        await commit_import(object(), _xlsx([_create_row(
            action="update_current_cycle", plotCode="P002",
            lotNo="NEW-9", poNumber="po-new", pCode="Melon-Z")]), ctx=_ctx())
    fields = m_update.call_args.args[3]
    assert fields["lot_no"] == "NEW-9"       # Manual
    assert fields["po_number"] == "PO-NEW"   # normalized upper
    assert fields["p_code"] == "Melon-Z"


async def test_commit_result_carries_real_lot_source_running() -> None:
    created = _cycle(cycle_no=1, lot_no="PO25001-P101-01",
                     lot_no_source="auto", lot_running_no=1)
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock(return_value=_plot())), \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=created)):
        result = await commit_import(
            object(), _xlsx([_create_row(lotNo=None, cycleLabel="2605", pCode="WM-141")]),
            ctx=_ctx(),
        )
    row = result.row_results[0]
    assert row.result_lot_no == "PO25001-P101-01"
    assert row.result_lot_no_source == "auto"
    assert row.result_lot_running_no == 1


async def test_old_file_without_po_and_pcode_columns_create_gives_clean_error_not_500() -> None:
    # Round 8-13A — poNumber is no longer required, but pCode still is. A
    # file missing BOTH columns still errors on create (because of pCode),
    # never a crash/500, and the message no longer names poNumber.
    legacy_cols = [c for c in IMPORT_COLUMNS if c not in ("poNumber", "pCode")]
    data = [legacy_cols, [{"action": "create_plot_with_cycle", "supplierCode": "SUP001",
                           "plotCode": "P900", "plotName": "แปลงเก่า"}.get(c) for c in legacy_cols]]
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active:
        pv = await build_preview(object(), build_xlsx([("plots", data)]), ctx=_ctx())
    assert pv.total_rows == 1
    assert pv.rows[0].status == "error"
    assert "pCode" in pv.rows[0].message
    assert "poNumber" not in pv.rows[0].message


async def test_legacy_file_without_po_number_column_but_with_pcode_still_valid() -> None:
    # Round 8-13A — a file that simply never had a poNumber column (dropped,
    # or from before it existed) but DOES have pCode must import cleanly: the
    # reader maps by header name, poNumber is just absent -> None, no error.
    legacy_cols = [c for c in IMPORT_COLUMNS if c != "poNumber"]
    row = {"action": "create_plot_with_cycle", "supplierCode": "SUP001",
           "plotCode": "P900", "plotName": "แปลงเก่า", "pCode": "Melon-A",
           "cycleLabel": "2605"}
    data = [legacy_cols, [row.get(c) for c in legacy_cols]]
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active:
        pv = await build_preview(object(), build_xlsx([("plots", data)]), ctx=_ctx())
    assert pv.total_rows == 1
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].payload.po_number is None


# --- round 8-13A: poNumber optional across every new-cycle action ----------

async def test_create_plot_with_cycle_without_po_is_valid() -> None:
    pv = await _preview(
        [_create_row(poNumber=None)], plot=None,
    )
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].payload.po_number is None


async def test_start_new_cycle_without_po_is_valid() -> None:
    pv = await _preview(
        [_create_row(action="start_new_cycle", plotCode="P001", plotName=None, poNumber=None)],
        plot=_plot(), active=None,
    )
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].payload.po_number is None


async def test_rollover_without_po_is_valid() -> None:
    pv = await _preview(
        [_create_row(action="close_and_start_new_cycle", plotCode="P003", poNumber=None)],
        plot=_plot(), active=_cycle(),
    )
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].payload.po_number is None


async def test_start_next_cycle_without_po_is_valid() -> None:
    pv = await _preview(
        [_create_row(action="start_next_cycle", plotCode="P003", cycleLabel="sep2026",
                     poNumber=None)],
        plot=_plot(), active=None,
    )
    assert pv.rows[0].status == "valid"
    assert pv.rows[0].payload.po_number is None


# reactivate_plot_with_cycle's own no-PO coverage lives in
# test_plot_import_reactivate_action.py, which already has the full mock set
# that action's validation needs (including the cycle-label-history batch
# check) — see test_preview_reactivate_row_without_po_is_valid there.


async def test_new_cycle_without_po_and_without_pcode_is_invalid_for_pcode() -> None:
    # No PO AND no P.Code → still invalid, but ONLY because of P.Code.
    pv = await _preview([_create_row(poNumber=None, pCode=None)], plot=None)
    assert pv.rows[0].status == "error"
    assert "pCode" in pv.rows[0].message
    assert "poNumber" not in pv.rows[0].message


async def test_auto_lot_without_po_proposes_v2_formula() -> None:
    # No PO + cycleLabel + pCode + blank lotNo -> valid Auto Lot preview,
    # formula is V2 and never references PO at all.
    pv = await _preview(
        [_create_row(poNumber=None, cycleLabel="2605", pCode="WM-141", lotNo=None)],
        plot=None,
    )
    row = pv.rows[0]
    assert row.status == "valid"
    assert row.lot_mode == "auto"
    assert row.proposed_lot_no == "2605-SUP001-WM-141-###"
    assert row.payload.po_number is None


async def test_manual_lot_without_po_is_valid() -> None:
    pv = await _preview(
        [_create_row(poNumber=None, lotNo="MANUAL-LOT-77")], plot=None,
    )
    row = pv.rows[0]
    assert row.status == "valid"
    assert row.lot_mode == "manual"
    assert row.proposed_lot_no == "MANUAL-LOT-77"
    assert row.payload.po_number is None


# =====================================================================
# Round 8-5B.1 — Auto Lot 99→100 boundary in Excel preview
# =====================================================================

async def test_excel_auto_lot_pre_check_uses_running_1000_boundary() -> None:
    # cycleLabel(64) + supplierCode(6, "SUP001") + pCode(24) + 3 separators:
    # a real running of 1..999 fits EXACTLY at 100 chars, but 1000 is 101. The
    # pre-check probes at running=1000 (4 digits) so the eventual overflow is
    # caught up front → this row previews as an error, not a commit-time abort.
    # Round 8-12A raised the probe from 100 to 1000 because V2's minimum width
    # is 3, so the growth step that can overflow is 3→4 digits.
    pv = await _preview(
        [_create_row(cycleLabel="L" * 64, pCode="P" * 24, lotNo=None)],
        plot=None,
    )
    assert pv.rows[0].status == "error"
    assert "Auto Lot" in pv.rows[0].message


def test_excel_pre_check_source_uses_running_1000_and_v2_components() -> None:
    import inspect
    src = inspect.getsource(plot_import._validate_row)
    assert "running=1000" in src
    assert "supplier_code=supplier_code_for_lot" in src
    # the V1 positional call must be gone
    assert "format_auto_lot_no(p.plot_code" not in src

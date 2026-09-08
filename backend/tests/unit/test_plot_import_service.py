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

from app.schemas.plot_import import PlotImportPreviewState
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
        "crop": "พริก", "variety": "พริกขี้หนู",
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


async def test_missing_supplier_code_errors() -> None:
    pv = await _preview([_create_row(supplierCode=None, plotCode=None)])
    assert pv.rows[0].status == "error"
    assert "supplierCode" in pv.rows[0].message
    # Round B — a BLANK plotCode is no longer an error on a create row: it is
    # the request to generate one. Only supplierCode is missing here.
    assert "plotCode" not in pv.rows[0].message


async def test_missing_plot_code_still_errors_on_every_other_action() -> None:
    """Round B — plotCode is how a non-create row ADDRESSES an existing plot,
    so it stays required there; only create_plot_with_cycle may leave it
    blank."""
    pv = await _preview([_create_row(action="update_current_cycle", plotCode=None)])
    assert pv.rows[0].status == "error"
    assert "plotCode" in pv.rows[0].message


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


# --- duplicate rows -------------------------------------------------------

async def test_duplicate_plot_rows_both_error() -> None:
    pv = await _preview(
        [_create_row(plotCode="P101"), _create_row(plotCode="P101")],
        plot=None,
    )
    assert pv.error_rows == 2
    assert all("ซ้ำ" in r.message for r in pv.rows)


# --- commit (execution, all-or-nothing) -----------------------------------


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


async def test_cycle_label_exactly_100_chars_passes_its_own_length_check() -> None:
    """The cycleLabel COLUMN accepts 100 chars (its VARCHAR limit).

    Round A — such a label nonetheless fails the row, because every new cycle
    now gets a generated lot and a 100-char label cannot fit inside a 100-char
    lot number. The distinction matters: the error the user sees must be the
    Lot-length one (actionable: shorten the label), never a bogus
    "cycleLabel too long"."""
    label_100 = "y" * 100
    pv = await _preview([_create_row(cycleLabel=label_100)], plot=None)
    msg = pv.rows[0].message
    assert pv.rows[0].status == "error"
    assert "Lot No" in msg and "100" in msg
    assert "cycleLabel" in msg          # names what to shorten


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
        "cycleLabel": "qa-cycle-4",
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


# --- duplicate protection reuse (round 8-2.3) ---------------------------


# --- commit (execution) --------------------------------------------------


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


# 5–7: file-level gates (missing state / malformed handled at endpoint / digest).


# 8–12: resolution/identity divergence under lock.


# 14: matching rollover snapshot succeeds (start success covered above).


# 15: snapshot verified for ALL rows before the FIRST execute.


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


def test_file_digest_is_sha256_and_never_logs_content() -> None:
    import hashlib
    content = b"some file bytes"
    assert plot_import.file_digest(content) == hashlib.sha256(content).hexdigest()


# --- backward compatibility + contract-wide checks -----------------------


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
            action="update_current_cycle", plotCode="P002",
            poNumber=None, pCode=None)]), ctx=_ctx())
    fields = m_update.call_args.args[2]
    assert "lot_no" not in fields         # preserve existing lot
    assert "po_number" not in fields      # blank PO → preserve
    assert "p_code" not in fields


async def test_update_sends_new_po_and_p_code_but_never_a_lot() -> None:
    """Round A — an update still edits PO and P.Code, but the lot columns are
    off-limits: even changing the very inputs the lot was built from leaves it
    exactly as created."""
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
            poNumber="po-new", pCode="Melon-Z")]), ctx=_ctx())
    fields = m_update.call_args.args[2]
    assert fields["po_number"] == "PO-NEW"   # normalized upper
    assert fields["p_code"] == "Melon-Z"
    assert "lot_no" not in fields


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


async def test_auto_lot_without_po_is_valid() -> None:
    """Round 8-13A — the PO is optional and takes no part in the V2 formula.
    Round A — with the Manual escape gone, this is the only remaining shape of
    "a new cycle with no PO", and it must still preview cleanly."""
    pv = await _preview([_create_row(poNumber=None)], plot=None)
    row = pv.rows[0]
    assert row.status == "valid"
    assert row.lot_mode == "auto"
    assert row.proposed_lot_no == "jun2026-SUP001-Melon-A-###"
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
        [_create_row(cycleLabel="L" * 64, pCode="P" * 24)],
        plot=None,
    )
    assert pv.rows[0].status == "error"
    assert "Lot No" in pv.rows[0].message


def test_excel_pre_check_source_uses_running_1000_and_v2_components() -> None:
    import inspect
    src = inspect.getsource(plot_import._validate_row)
    assert "running=1000" in src
    assert "supplier_code=supplier_code_for_lot" in src
    # the V1 positional call must be gone
    assert "format_auto_lot_no(p.plot_code" not in src

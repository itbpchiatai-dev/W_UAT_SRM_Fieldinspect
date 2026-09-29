"""The harvest figures travel in the file, and only a close may change them
(round 31).

Round D made a blank harvestYield mean "use whatever the field team has
reported", so an admin closing a cycle typed nothing. Correct, and unreadable:
the downloaded file showed three empty cells whether the field team had reported
78 kg or nothing at all, and the only way to find out was to open the plot-status
report in another tab. A user hit exactly that on UAT — a plot whose record said
78 kg / 75 kg / 2026-09-28 exported as three blanks.

So the file now carries the reported figures. That makes the cells non-blank,
which breaks the question the importer used to ask ("did the user type here?"):
the answer is yes on every exported row now. The question that still means
something is "did the user CHANGE it", which is how the orange one-time columns
have always worked, so these three follow the same rule:

    leave the exported figure   -> fine
    clear the cell             -> fine (back to "use the latest report")
    write a different figure   -> refused, unless the row closes the cycle

and a final_plot row that disagrees with the current report is allowed but
WARNED, because the likeliest reason to disagree is a file downloaded before a
corrected report landed.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.services import plot_import
from app.services.excel_workbook import build_xlsx
from app.services.plot_import import ImportContext, build_preview

_M = "app.services.plot_import"

REPORTED_YIELD = Decimal("78.00")
REPORTED_CLEAN = Decimal("75.00")
REPORTED_DATE = datetime.date(2026, 9, 28)


def _ctx(**kw) -> ImportContext:
    base = dict(allowed_supplier_id=None, can_create=True, can_update=True, user_id=uuid4())
    base.update(kw)
    return ImportContext(**base)


def _supplier(**kw) -> SimpleNamespace:
    base = dict(id=uuid4(), code="SUP001", name="ซัพพลายเออร์", is_active=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _plot(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), plot_code="P001", name="แปลงหนึ่ง", is_active=True,
        supplier_id=uuid4(),
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _cycle(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=1, cycle_label="jun2026", status="active",
        crop="WM", variety=None, lot_no=None, planting_date=None, plant_count=None,
        expected_yield_full=None, expected_yield_unit=None,
        po_number=None, p_code="WM-078", lot_no_source=None, lot_running_no=None,
        supplier_lot_no=None,
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _xlsx(rows: list[dict[str, str]]) -> bytes:
    headers = list(plot_import.IMPORT_COLUMNS)
    return build_xlsx([("plots", [headers] + [[r.get(h) for h in headers] for r in rows])])


def _update_row(**over) -> dict[str, str]:
    base = {
        "action": "update_current_cycle", "supplierCode": "SUP001",
        "plotCode": "P001", "cycleLabel": "jun2026",
    }
    base.update(over)
    return base


def _final_row(**over) -> dict[str, str]:
    return _update_row(action="final_plot", **over)


def _create_row(**over) -> dict[str, str]:
    base = {
        "action": "create_plot_with_cycle", "supplierCode": "SUP001",
        "plotCode": "", "plotName": "แปลงใหม่", "crop": "WM",
        "pCode": "WM-078", "cycleLabel": "jun2026",
    }
    base.update(over)
    return base


def _reported(**over) -> dict:
    base = {
        "harvest_yield": REPORTED_YIELD,
        "final_yield_after_clean": REPORTED_CLEAN,
        "final_yield_unit": "kg",
        "harvest_date": REPORTED_DATE,
        "final_note": None,
    }
    base.update(over)
    return base


async def _preview(rows, *, plot=None, active=None, reported=..., record=object()):
    """Drive a real preview with the DB calls this path makes stubbed out.

    `reported` is what the field team is currently reporting for the active
    cycle; the default is the 78/75/2026-09-28 from the UAT report that started
    this round. None means "no usable field report".
    """
    resolved = _reported() if reported is ... else reported
    sources = {active.id: record} if (active is not None and resolved is not None) else {}
    with (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=_supplier())),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active)),
        patch(f"{_M}.credential_repo.get_credential_status_for_plots", AsyncMock(return_value={})),
        # A final_plot row also resolves the record its ESTIMATE snapshot will
        # freeze. Unrelated to the harvest figures, but it is a DB call on the
        # same path, so it has to be stubbed for these previews to run.
        patch(f"{_M}.plot_cycle_repo.get_latest_active_record_for_cycle",
              AsyncMock(return_value=None)),
        patch(f"{_M}.plot_cycle_repo.get_actual_harvest_source_records_for_cycles",
              AsyncMock(return_value=sources)) as batched,
        patch(f"{_M}.plot_cycle_repo.actual_harvest_from_record",
              lambda rec: resolved if rec is not None else _reported(
                  harvest_yield=None, final_yield_after_clean=None,
                  harvest_date=None, final_yield_unit=None)),
    ):
        preview = await build_preview(object(), _xlsx(rows), ctx=_ctx())
    return preview, batched


# --- leaving the exported figures alone -----------------------------------

async def test_an_unedited_row_round_trips_without_an_error() -> None:
    """The headline case: download, change nothing about the harvest cells,
    upload. Before this round the cells were blank so this could not arise;
    now every exported row carries them and every one of them must pass."""
    preview, _ = await _preview(
        [_update_row(harvestYield="78.00", finalYieldAfterClean="75.00",
                     harvestDate="2026-09-28")],
        plot=_plot(), active=_cycle(),
    )
    assert preview.rows[0].status == "valid", preview.rows[0].message
    assert preview.error_rows == 0


async def test_retyping_the_same_number_at_a_different_scale_is_still_unchanged() -> None:
    """78 and 78.00 are the same figure. The check compares Decimals, so a user
    who cleans up the cell by hand has not "changed" anything."""
    preview, _ = await _preview(
        [_update_row(harvestYield="78", finalYieldAfterClean="75",
                     harvestDate="2026-09-28")],
        plot=_plot(), active=_cycle(),
    )
    assert preview.rows[0].status == "valid", preview.rows[0].message


async def test_clearing_the_cells_is_allowed_and_means_use_the_latest_report() -> None:
    """Blank keeps round D's meaning exactly — the commit fills the figures from
    whatever the field team reports by then. Emptying the cell must therefore
    stay a legal edit, not a way to trip the new rule."""
    preview, _ = await _preview(
        [_update_row(harvestYield="", finalYieldAfterClean="", harvestDate="")],
        plot=_plot(), active=_cycle(),
    )
    assert preview.rows[0].status == "valid", preview.rows[0].message


# --- changing them on a row that does not close the cycle ------------------

async def test_changing_a_figure_on_an_update_row_is_refused() -> None:
    preview, _ = await _preview(
        [_update_row(harvestYield="82.00", finalYieldAfterClean="75.00",
                     harvestDate="2026-09-28")],
        plot=_plot(), active=_cycle(),
    )
    row = preview.rows[0]
    assert row.status == "error"
    assert "harvestYield" in row.message
    # It names the figure the cell has to hold…
    assert "78.00" in row.message
    # …and the one action that may change it, so the fix is obvious.
    assert "final_plot" in row.message
    # The value the user typed is never echoed back into the message.
    assert "82" not in row.message


async def test_the_error_names_each_changed_column_separately() -> None:
    preview, _ = await _preview(
        [_update_row(harvestYield="82.00", finalYieldAfterClean="1.00",
                     harvestDate="2026-01-01")],
        plot=_plot(), active=_cycle(),
    )
    message = preview.rows[0].message
    for column in ("harvestYield", "finalYieldAfterClean", "harvestDate"):
        assert column in message


async def test_a_figure_is_never_silently_dropped_again() -> None:
    """The defect this round closes: before it, a number typed on an update row
    was parsed, range-checked, then discarded with nothing on screen. The row
    previewed green and the user believed it had been saved."""
    preview, _ = await _preview(
        [_update_row(harvestYield="82.00")],
        plot=_plot(), active=_cycle(),
    )
    assert preview.rows[0].status == "error"


async def test_a_harvest_figure_on_a_create_row_is_refused() -> None:
    """A brand-new plot has no cycle and no field report, so there is nothing
    the number could describe and nowhere for it to be written."""
    preview, _ = await _preview([_create_row(harvestYield="10.00")])
    row = preview.rows[0]
    assert row.status == "error"
    assert "harvestYield" in row.message


async def test_a_cycle_with_no_field_report_refuses_a_typed_figure_on_an_update_row() -> None:
    """Nothing reported means the exported cell was blank, so any number in it
    was typed by hand — still not this row's to write."""
    preview, _ = await _preview(
        [_update_row(harvestYield="10.00")],
        plot=_plot(), active=_cycle(), reported=None,
    )
    row = preview.rows[0]
    assert row.status == "error"
    assert "ว่าง" in row.message


# --- closing the cycle ----------------------------------------------------

async def test_a_final_plot_row_may_change_the_figures() -> None:
    """Closing is exactly when an admin is allowed to overrule the field
    report — the rule must not have taken that away."""
    preview, _ = await _preview(
        [_final_row(harvestYield="82.00", finalYieldAfterClean="80.00",
                    harvestDate="2026-09-29")],
        plot=_plot(), active=_cycle(),
    )
    assert preview.rows[0].status == "valid", preview.rows[0].message


async def test_a_disagreeing_final_plot_row_is_warned_not_blocked() -> None:
    preview, _ = await _preview(
        [_final_row(harvestYield="82.00", finalYieldAfterClean="75.00",
                    harvestDate="2026-09-28")],
        plot=_plot(), active=_cycle(),
    )
    row = preview.rows[0]
    assert row.status == "valid"
    assert row.warning is not None
    assert "harvestYield" in row.warning
    assert "78.00" in row.warning


async def test_a_close_with_nothing_reported_is_not_warned() -> None:
    """When the field team has reported nothing, typing the figures IS the
    normal way to close the cycle — there is no report to contradict. Warning
    here would put a notice on every hand-entered close, which is how warnings
    stop being read."""
    preview, _ = await _preview(
        [_final_row(harvestYield="1000.00", finalYieldAfterClean="900.00",
                    harvestDate="2026-09-29")],
        plot=_plot(), active=_cycle(), reported=None,
    )
    row = preview.rows[0]
    assert row.status == "valid", row.message
    assert row.warning is None


async def test_a_final_plot_row_that_agrees_gets_no_warning() -> None:
    """The unedited download is the common case and must be quiet — a warning
    on every close would be noise nobody reads."""
    preview, _ = await _preview(
        [_final_row(harvestYield="78.00", finalYieldAfterClean="75.00",
                    harvestDate="2026-09-28")],
        plot=_plot(), active=_cycle(),
    )
    row = preview.rows[0]
    assert row.status == "valid"
    assert row.warning is None


async def test_the_new_warning_does_not_replace_the_existing_one() -> None:
    """finalYieldAfterClean > harvestYield already warns. Both facts matter, so
    the row carries both rather than whichever ran last."""
    preview, _ = await _preview(
        [_final_row(harvestYield="82.00", finalYieldAfterClean="90.00",
                    harvestDate="2026-09-28")],
        plot=_plot(), active=_cycle(),
    )
    warning = preview.rows[0].warning
    assert warning is not None
    assert "ผลผลิตหลังทำความสะอาด" in warning      # the pre-existing check
    assert "ภาคสนามรายงานล่าสุด" in warning        # this round's


# --- cost -----------------------------------------------------------------

async def test_the_field_report_is_read_in_one_query_for_the_whole_file() -> None:
    """Every exported row now carries harvest figures, so a per-row lookup here
    would be one query per row on every upload."""
    rows = [
        _update_row(plotCode=f"P{i:03d}", harvestYield="78.00",
                    finalYieldAfterClean="75.00", harvestDate="2026-09-28")
        for i in range(1, 26)
    ]
    preview, batched = await _preview(rows, plot=_plot(), active=_cycle())
    assert preview.total_rows == 25
    assert batched.await_count == 1


async def test_a_file_with_no_harvest_cells_asks_the_database_nothing() -> None:
    """The pass pays for itself only when a row actually carries a figure."""
    _preview_result, batched = await _preview(
        [_update_row()], plot=_plot(), active=_cycle(),
    )
    assert batched.await_count == 0

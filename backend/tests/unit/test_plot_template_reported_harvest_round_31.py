"""The downloaded template carries the field team's harvest figures (round 31).

Round D exported harvestYield / finalYieldAfterClean / harvestDate blank,
because a blank cell means "use whatever the field team has reported" and an
admin closing a cycle therefore types nothing. The rule was right; the file was
unreadable. Three empty cells looked identical whether the field team had
reported 78 kg or had reported nothing at all, and the only way to tell was to
open the plot-status report in another tab. A user hit exactly that on UAT.

The cells now carry what is reported. Blank still means blank — a cycle with no
usable field report exports three empty cells and keeps round D's meaning
untouched.

The other half of this round (what the importer does when someone edits one of
these cells) lives in test_plot_import_reported_harvest_round_31.py.
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

import app.api.v1.plots as plots_module
from app.api.v1.plots import _contextual_plot_template_workbook, _update_cycle_row_values
from app.services.excel_reader import read_first_sheet

_P = "app.api.v1.plots"

REPORTED = {
    "harvest_yield": Decimal("78.00"),
    "final_yield_after_clean": Decimal("75.00"),
    "final_yield_unit": "kg",
    "harvest_date": datetime.date(2026, 9, 28),
    "final_note": None,
}
NOTHING_REPORTED = {
    "harvest_yield": None,
    "final_yield_after_clean": None,
    "final_yield_unit": None,
    "harvest_date": None,
    "final_note": None,
}


def _supplier(**over) -> SimpleNamespace:
    base = dict(code="SUP001", name="Supplier One", is_active=True)
    base.update(over)
    return SimpleNamespace(**base)


def _cycle(**over) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=1, status="active", cycle_label="jun2026",
        crop="WM", variety="แตงโม WM1224", po_number=None, p_code="WM-078",
        supplier_lot_no=None, oracle_supplier_code=None, oracle_invoice=None,
        ref_account=None, lot_no="LOT-01", planting_date=None, plant_count=1000,
        expected_yield_full=Decimal("80.00"), expected_yield_unit="kg",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _plot(**over) -> SimpleNamespace:
    cycle = over.pop("cycle", None) or _cycle()
    base = dict(
        id=uuid4(), plot_code="17334-2608-001", name="นางวงเยาว์ ชินฮาด",
        is_active=True, province="ขอนแก่น",
        village=None, district=None, latitude=None, longitude=None, rai=None,
        supplier=_supplier(), active_cycle=cycle, cycles=[cycle],
        access_phones=[],
    )
    base.update(over)
    return SimpleNamespace(**base)


def _sheet1(content: bytes):
    return read_first_sheet(content)


# --- the row builder ------------------------------------------------------

def test_the_row_carries_the_reported_figures() -> None:
    values = _update_cycle_row_values(_plot(), reported_harvest=REPORTED)
    assert values["harvestYield"] == "78.00"
    assert values["finalYieldAfterClean"] == "75.00"
    assert values["harvestDate"] == "2026-09-28"


def test_the_stored_scale_survives_into_the_cell() -> None:
    """The report shows 78.00; so does the file. A cell that read "78" would
    invite the reader to wonder which of the two numbers is the real one."""
    values = _update_cycle_row_values(_plot(), reported_harvest=REPORTED)
    assert values["harvestYield"] == "78.00"


def test_nothing_reported_still_exports_three_blank_cells() -> None:
    """Round D's meaning, unchanged: blank = "use whatever is reported by the
    time this file is committed"."""
    for reported in (None, NOTHING_REPORTED):
        values = _update_cycle_row_values(_plot(), reported_harvest=reported)
        assert values["harvestYield"] is None
        assert values["finalYieldAfterClean"] is None
        assert values["harvestDate"] is None


def test_a_partial_report_exports_only_what_exists() -> None:
    """A harvest report with no after-cleaning figure is a legitimate gap for
    an admin to fill in — the two cells that DO have values still show them."""
    values = _update_cycle_row_values(
        _plot(),
        reported_harvest={**REPORTED, "final_yield_after_clean": None},
    )
    assert values["harvestYield"] == "78.00"
    assert values["finalYieldAfterClean"] is None
    assert values["harvestDate"] == "2026-09-28"


def test_the_password_column_is_still_never_exported() -> None:
    """Round 31 adds columns to this row; the one rule that must survive every
    such change is that a downloaded template carries no password."""
    values = _update_cycle_row_values(
        _plot(), password_configured=True, reported_harvest=REPORTED,
    )
    assert values["newInspectionPassword"] is None


# --- the workbook --------------------------------------------------------

def test_the_figures_reach_the_sheet_the_importer_reads() -> None:
    """End to end through the real builder: a value that stops at the row dict
    and never lands in sheet 1 would help nobody."""
    plot = _plot()
    content = _contextual_plot_template_workbook(
        [plot], reported_harvest={plot.id: REPORTED},
    )
    headers, rows = _sheet1(content)
    assert {"harvestYield", "finalYieldAfterClean", "harvestDate"} <= set(headers)
    # rows[0] is the template's Thai guidance row; the plot is the one after it.
    _row_no, data = rows[1]
    assert data["harvestYield"] == "78.00"
    assert data["finalYieldAfterClean"] == "75.00"
    assert data["harvestDate"] == "2026-09-28"


def test_a_plot_with_no_report_leaves_its_cells_empty_in_the_sheet() -> None:
    plot = _plot()
    content = _contextual_plot_template_workbook([plot], reported_harvest={})
    _headers, rows = _sheet1(content)
    _row_no, data = rows[1]
    assert not (data.get("harvestYield") or "").strip()
    assert not (data.get("finalYieldAfterClean") or "").strip()
    assert not (data.get("harvestDate") or "").strip()


def test_two_plots_each_get_their_own_figures() -> None:
    """Keyed by plot, not by position — a lookup bug here would put one grower's
    harvest on another grower's row."""
    a, b = _plot(plot_code="P-A"), _plot(plot_code="P-B")
    content = _contextual_plot_template_workbook(
        [a, b],
        reported_harvest={
            a.id: REPORTED,
            b.id: {**REPORTED, "harvest_yield": Decimal("12.00")},
        },
    )
    _headers, rows = _sheet1(content)
    by_code = {data["plotCode"]: data for _n, data in rows[1:]}
    assert by_code["P-A"]["harvestYield"] == "78.00"
    assert by_code["P-B"]["harvestYield"] == "12.00"


# --- cost ----------------------------------------------------------------

async def test_the_report_is_read_in_one_query_for_the_whole_sheet() -> None:
    """A template download renders up to 5,000 rows; a per-plot lookup here
    would be 5,000 queries."""
    plots = [_plot(plot_code=f"P{i:03d}") for i in range(30)]
    with patch(
        f"{_P}.plot_cycle_repo.get_actual_harvest_source_records_for_cycles",
        AsyncMock(return_value={}),
    ) as batched:
        result = await plots_module._reported_harvest_for_plots(MagicMock(), plots)
    assert batched.await_count == 1
    # Every active cycle asked about in that single call.
    assert len(batched.await_args.args[1]) == 30
    assert result == {}


async def test_the_lookup_is_keyed_back_to_the_plot() -> None:
    plot = _plot()
    record = object()
    with (
        patch(f"{_P}.plot_cycle_repo.get_actual_harvest_source_records_for_cycles",
              AsyncMock(return_value={plot.active_cycle.id: record})),
        patch(f"{_P}.plot_cycle_repo.actual_harvest_from_record",
              MagicMock(return_value=REPORTED)),
    ):
        result = await plots_module._reported_harvest_for_plots(MagicMock(), [plot])
    assert result == {plot.id: REPORTED}


async def test_a_plot_with_no_active_cycle_is_never_asked_about() -> None:
    with patch(
        f"{_P}.plot_cycle_repo.get_actual_harvest_source_records_for_cycles",
        AsyncMock(return_value={}),
    ) as batched:
        result = await plots_module._reported_harvest_for_plots(
            MagicMock(), [_plot(active_cycle=None)],
        )
    assert result == {}
    assert batched.await_count == 0


def test_the_export_never_writes() -> None:
    """A download must stay read-only — it renders what is there and changes
    nothing, this round included."""
    src = inspect.getsource(plots_module._reported_harvest_for_plots)
    # Code only — the docstring says the word "committed" while explaining what
    # a blank cell means, and matching prose would make this test lie.
    code = chr(10).join(
        line for line in src.splitlines()
        if not line.lstrip().startswith(("#", '"', "'"))
    )
    for forbidden in (".commit(", ".flush(", ".add(", ".delete("):
        assert forbidden not in code


def test_the_workbook_is_a_real_xlsx() -> None:
    plot = _plot()
    content = _contextual_plot_template_workbook(
        [plot], reported_harvest={plot.id: REPORTED},
    )
    with ZipFile(BytesIO(content)) as zf:
        assert "xl/workbook.xml" in zf.namelist()

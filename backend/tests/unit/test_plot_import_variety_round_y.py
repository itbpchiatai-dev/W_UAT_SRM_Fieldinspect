"""The Excel import has no variety column any more (round Y).

A create row says WM + CTT-507; the variety is read off the P.Code exactly
as it is on the web form, so the two ways into the system cannot disagree.

Two things this file pins that are easy to get wrong:

  - a workbook downloaded BEFORE this round still carries a "variety" header.
    A blank cell is fine (the file is simply older than the contract), but a
    FILLED one must fail loudly — the same rule round A gave the retired
    lotNo column, and for the same reason: a user who typed a variety would
    otherwise believe they had chosen the plant.
  - the batched Master Data check still costs a FIXED number of queries per
    file, never one per row.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import plot_import

pytestmark = pytest.mark.nodefault_crop_variety

_CROP = "WM"
_P_CODE = "CTT-507"
_VARIETY = "แตงโม RWA 412 (D-12)"


def _md(type_, value, parent=None, active=True):
    return SimpleNamespace(type=type_, value=value, parent=parent, active=active)


# --- the column is gone ---------------------------------------------------


def test_variety_is_not_an_import_column() -> None:
    assert "variety" not in plot_import.IMPORT_COLUMNS
    # The P.Code that replaces it stays, and so does the crop the user picks
    # alongside it.
    assert "pCode" in plot_import.IMPORT_COLUMNS
    assert "crop" in plot_import.IMPORT_COLUMNS


def test_every_column_still_has_a_description() -> None:
    assert set(plot_import.TEMPLATE_COLUMN_DESCRIPTIONS) == set(plot_import.IMPORT_COLUMNS)


def test_a_filled_legacy_variety_cell_is_refused_by_name() -> None:
    errors = plot_import._removed_input_column_errors({"variety": "พันธุ์ที่พิมพ์เอง"})
    assert len(errors) == 1
    assert "variety" in errors[0]
    assert "P.Code" in errors[0]
    # The value the user typed is never echoed back into a workbook or a log.
    assert "พันธุ์ที่พิมพ์เอง" not in errors[0]


def test_a_blank_legacy_variety_cell_is_accepted() -> None:
    assert plot_import._removed_input_column_errors({"variety": "   "}) == []
    assert plot_import._removed_input_column_errors({}) == []


# --- the variety a create row ends up with --------------------------------


def _state(crop=_CROP, p_code=_P_CODE):
    parsed = SimpleNamespace(crop=crop, p_code=p_code, variety=None)
    return SimpleNamespace(parsed=parsed, errors=[], needs_master_data_check=True)


async def _run_check(states, rows=None):
    rows = rows if rows is not None else {
        "crop": [_md("crop", _CROP)],
        "variety": [_md("variety", _VARIETY, parent=_CROP)],
        "p_code": [_md("p_code", _P_CODE, parent=_VARIETY)],
    }
    calls: list[tuple[str, set[str]]] = []

    async def fake_list(db, type_, values):
        calls.append((type_, set(values)))
        return [r for r in rows.get(type_, []) if r.value in values]

    with patch(
        "app.repositories.master_data_repository.list_by_type_values", side_effect=fake_list
    ):
        await plot_import._apply_master_data_crop_variety_checks(AsyncMock(), states)
    return calls


async def test_a_valid_row_gets_the_p_codes_variety_written_onto_it() -> None:
    state = _state()
    await _run_check([state])
    assert state.errors == []
    # The parsed row carries the derived variety from here on — it is what
    # _execute_row hands to create_cycle.
    assert state.parsed.variety == _VARIETY


async def test_a_p_code_from_another_crop_errors_and_derives_nothing() -> None:
    state = _state(crop="พริก")
    await _run_check(
        [state],
        rows={
            "crop": [_md("crop", "พริก"), _md("crop", _CROP)],
            "variety": [_md("variety", _VARIETY, parent=_CROP)],
            "p_code": [_md("p_code", _P_CODE, parent=_VARIETY)],
        },
    )
    assert state.errors == ['P.Code "CTT-507" ไม่ได้อยู่ภายใต้ชนิดพืช "พริก"']
    assert state.parsed.variety is None


async def test_an_unknown_p_code_errors_and_derives_nothing() -> None:
    state = _state(p_code="NOPE-1")
    await _run_check([state])
    assert state.errors == ['ไม่พบ P.Code "NOPE-1" ใน Master Data']
    assert state.parsed.variety is None


async def test_the_whole_file_costs_three_queries_however_many_rows() -> None:
    states = [_state() for _ in range(25)]
    calls = await _run_check(states)
    assert len(calls) == 3
    assert [c[0] for c in calls] == ["crop", "p_code", "variety"]
    assert all(s.parsed.variety == _VARIETY for s in states)


async def test_a_file_with_no_rows_to_check_queries_nothing() -> None:
    state = _state()
    state.needs_master_data_check = False
    assert await _run_check([state]) == []

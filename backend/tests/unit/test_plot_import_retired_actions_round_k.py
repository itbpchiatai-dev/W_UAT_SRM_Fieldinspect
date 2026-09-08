"""The four retired import actions are refused, with a message that helps.

Round E narrowed what the app OFFERS to three actions — create, update,
final — because of "one plot, one cycle": a plot is registered for a season,
inspected through it, and finalized, never reopened for a second season. It
kept ACCEPTING the other four so that a file downloaded the week before still
imported.

Round K closes that door. The four actions whose only job was to open ANOTHER
cycle on an existing plot are refused at validation, and the code behind them
is gone from the importer.

What this file pins:

  * each retired action is refused, individually and by name;
  * the message is its OWN message, not "action must be one of ...". These
    were valid until recently and are still sitting in files on people's
    machines; being told the value is unrecognised sends someone hunting for
    a typo that isn't there;
  * a refused row is an error row, so nothing in the file executes;
  * the three offered actions are untouched.

The single-plot API endpoints these actions shared code with still exist
(round E hid their buttons; the endpoints and repository helpers were never
removed), so this is about the IMPORT contract only.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services import plot_import
from app.services.excel_workbook import build_xlsx
from app.services.plot_import import (
    IMPORT_COLUMNS,
    OFFERED_ACTIONS,
    RETIRED_ACTIONS,
    ImportContext,
    build_preview,
)

_M = "app.services.plot_import"


def _xlsx(action: str) -> bytes:
    row = {col: None for col in IMPORT_COLUMNS}
    row.update({
        "action": action,
        "supplierCode": "SUP001",
        "plotCode": "P001",
        "cycleLabel": "sep2026",
        "pCode": "PC1",
        "crop": "พริก",
        "variety": "พริกขี้หนู",
    })
    return build_xlsx([("plots", [
        list(IMPORT_COLUMNS),
        [row[col] for col in IMPORT_COLUMNS],
    ])])


async def _preview(action: str):
    supplier = SimpleNamespace(id=uuid4(), code="SUP001", is_active=True)
    plot = SimpleNamespace(id=uuid4(), is_active=True)
    ctx = ImportContext(
        allowed_supplier_id=None, can_create=True, can_update=True, can_reactivate=True
    )
    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_cycle_repo.get_cycle_labels_for_plots", AsyncMock(return_value={})):
        return await build_preview(AsyncMock(), _xlsx(action), ctx=ctx)


@pytest.mark.parametrize("action", RETIRED_ACTIONS)
async def test_every_retired_action_is_refused(action):
    preview = await _preview(action)

    assert preview.error_rows == 1
    assert preview.valid_rows == 0
    assert preview.rows[0].status == "error"


@pytest.mark.parametrize("action", RETIRED_ACTIONS)
async def test_the_message_names_the_action_and_what_replaced_it(action):
    """Someone opening a months-old file needs to know what to do, not that
    they typed something wrong."""
    preview = await _preview(action)
    message = preview.rows[0].message

    assert action in message
    assert "ถูกยกเลิกแล้ว" in message
    for offered in OFFERED_ACTIONS:
        assert offered in message
    # The sheet's shape changed too, so a find-and-replace on the old file
    # is not enough.
    assert "ดาวน์โหลดเทมเพลตใหม่" in message


@pytest.mark.parametrize("action", RETIRED_ACTIONS)
async def test_a_refused_row_is_not_reported_as_an_unknown_action(action):
    """The generic message would send the user looking for a typo."""
    preview = await _preview(action)

    assert "action ต้องเป็นหนึ่งใน" not in preview.rows[0].message


async def test_an_actually_unknown_action_still_gets_the_generic_message():
    preview = await _preview("teleport_plot")
    message = preview.rows[0].message

    assert "action ต้องเป็นหนึ่งใน" in message
    assert "ถูกยกเลิกแล้ว" not in message


def test_the_two_action_sets_do_not_overlap():
    assert set(OFFERED_ACTIONS).isdisjoint(RETIRED_ACTIONS)
    assert len(OFFERED_ACTIONS) == 3
    assert len(RETIRED_ACTIONS) == 4


def test_no_retired_action_survives_anywhere_in_the_importer():
    """The branches are gone, not merely unreachable — a leftover would be
    dead code that still looks maintained, and tested."""
    import pathlib
    import re

    source = pathlib.Path(plot_import.__file__).read_text("utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )

    for name in ("ACTION_START", "ACTION_ROLLOVER", "ACTION_START_NEXT",
                 "ACTION_REACTIVATE_WITH_CYCLE"):
        # Word-bounded: a plain substring count would score ACTION_START twice
        # for every ACTION_START_NEXT and never go green.
        hits = len(re.findall(rf"\b{name}\b", code))
        # Only the constant definition and the RETIRED_ACTIONS tuple may
        # mention each one; nothing may branch on one.
        assert hits <= 2, f"{name} is still used in importer logic ({hits} hits)"

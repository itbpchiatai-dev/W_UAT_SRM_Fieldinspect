"""Round 8-17A.1 — Plot Excel Import cycleLabel contract.

Audited allowlist from source (`app/services/plot_import.py`,
`_NEW_CYCLE_ACTIONS`), not guessed:
    ACTION_CREATE, ACTION_START, ACTION_ROLLOVER, ACTION_START_NEXT,
    ACTION_REACTIVATE_WITH_CYCLE
— every one of these now requires a nonblank cycleLabel, independent of
Auto vs Manual lot (a nonblank lotNo cell no longer exempts it).
ACTION_UPDATE keeps effective-value semantics but must never let a blank
cell CLEAR an existing label. ACTION_FINAL is untouched — it never opens or
edits a cycle's label at all.

DB-free: supplier/plot/active-cycle lookups are patched with AsyncMocks
(same pattern as test_plot_import_service.py / test_plot_import_master_data_
enforcement.py). Master Data validation is bypassed via
tests/unit/conftest.py's permissive default (these tests are not about
crop/variety).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import datetime

import pytest

from app.services.excel_workbook import build_xlsx
from app.services.plot_import import (
    IMPORT_COLUMNS,
    ImportContext,
    ImportHasErrors,
    build_preview,
    commit_import,
)

_M = "app.services.plot_import"

_REQUIRED_LABEL_MSG = "กรุณาระบุชื่อรอบปลูก เนื่องจากใช้ระบุรอบและสร้าง Lot No อัตโนมัติ"


def _xlsx(rows: list[dict[str, str]]) -> bytes:
    data: list[list] = [list(IMPORT_COLUMNS)]
    for r in rows:
        data.append([r.get(c) for c in IMPORT_COLUMNS])
    return build_xlsx([("plots", data)])


def _ctx(**kw) -> ImportContext:
    return ImportContext(
        allowed_supplier_id=None, can_create=True, can_update=True, can_reactivate=True, **kw,
    )


def _supplier(**kw) -> SimpleNamespace:
    return SimpleNamespace(id=kw.get("id", uuid4()), code=kw.get("code", "SUP001"),
                            is_active=kw.get("is_active", True))


def _cycle(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=1, crop=None, variety=None, cycle_label=None,
        lot_no=None, planting_date=None, plant_count=None,
        expected_yield_full=None, expected_yield_unit=None,
        po_number=None, p_code=None, lot_no_source=None, lot_running_no=None,
        supplier_lot_no=None,
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _patch_lookups(*, plot=None, active=None, supplier=...):
    sup = _supplier() if supplier is ... else supplier
    return (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=sup)),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active)),
    )


def _row(preview, index: int = 0):
    return preview.rows[index]


def _base_row(**over) -> dict[str, str]:
    base = {
        "action": "create_plot_with_cycle", "supplierCode": "SUP001",
        "plotCode": "P101", "plotName": "แปลงใหม่",
        "poNumber": "PO25001", "pCode": "Melon-A",
        "cycleLabel": "jun2026", "lotNo": "LOT-01",
    }
    base.update(over)
    return base


# --- every _NEW_CYCLE_ACTIONS member requires cycleLabel --------------------

@pytest.mark.parametrize("action", [
    "create_plot_with_cycle", "start_new_cycle", "close_and_start_new_cycle",
    "start_next_cycle", "reactivate_plot_with_cycle",
])
async def test_new_cycle_action_rejects_blank_cycle_label(action: str) -> None:
    plot = None
    active = None
    if action != "create_plot_with_cycle":
        plot = SimpleNamespace(id=uuid4(), is_active=(action != "reactivate_plot_with_cycle"))
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    row = _base_row(action=action, cycleLabel=None, plotName=None)
    with p_sup, p_plot, p_active:
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status == "error"
    assert _REQUIRED_LABEL_MSG in result.message


async def test_new_cycle_action_required_independent_of_manual_lot() -> None:
    """Round 8-17A.1's core new rule — a Manual lot (nonblank lotNo) must NOT
    exempt cycleLabel; only Auto used to require it via the old Auto-Lot-
    missing-component check."""
    row = _base_row(action="create_plot_with_cycle", cycleLabel=None, lotNo="HAND-01")
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active:
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status == "error"
    assert _REQUIRED_LABEL_MSG in result.message


async def test_new_cycle_action_passes_with_a_real_label() -> None:
    row = _base_row(action="create_plot_with_cycle")
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    with p_sup, p_plot, p_active:
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status != "error"


# --- update_current_cycle: effective-value semantics, never clear ----------

async def test_update_current_cycle_rejects_clearing_an_existing_label() -> None:
    plot = SimpleNamespace(id=uuid4(), is_active=True)
    active = _cycle(cycle_label="jun2026")
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    row = _base_row(action="update_current_cycle", cycleLabel=None, plotName=None,
                     poNumber=None, pCode=None, lotNo=None)
    with p_sup, p_plot, p_active:
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status == "error"
    assert _REQUIRED_LABEL_MSG in result.message


async def test_update_current_cycle_blank_label_on_legacy_unlabeled_cycle_is_a_no_op() -> None:
    """The active cycle is ALREADY unlabeled (legacy, cycle_label=None) — a
    row that also leaves the cell blank must NOT be flagged as 'clearing'
    (there is nothing to clear); no forced backfill via Excel."""
    plot = SimpleNamespace(id=uuid4(), is_active=True)
    active = _cycle(cycle_label=None)
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    row = _base_row(action="update_current_cycle", cycleLabel=None, plotName=None,
                     poNumber=None, pCode=None, lotNo=None)
    with p_sup, p_plot, p_active:
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status != "error"


async def test_update_current_cycle_changing_to_a_new_label_is_allowed() -> None:
    plot = SimpleNamespace(id=uuid4(), is_active=True)
    active = _cycle(cycle_label="jun2026")
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    row = _base_row(action="update_current_cycle", cycleLabel="jul2026", plotName=None,
                     poNumber=None, pCode=None, lotNo=None)
    with p_sup, p_plot, p_active:
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status != "error"


# --- final_plot: untouched by this round ------------------------------------

async def test_final_plot_never_requires_cycle_label() -> None:
    plot = SimpleNamespace(
        id=uuid4(), is_active=True,
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    active = _cycle(cycle_label=None)
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    row = {
        "action": "final_plot", "supplierCode": "SUP001", "plotCode": "P101",
        "harvestYield": "500", "finalYieldAfterClean": "480", "harvestDate": "2026-07-01",
    }
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_for_cycle", AsyncMock(return_value=None)):
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status != "error"
    assert _REQUIRED_LABEL_MSG not in (result.message or "")


# --- all-or-nothing: one bad row blocks the whole commit ---------------------

async def test_commit_blocked_when_any_row_is_missing_cycle_label() -> None:
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    rows = [
        _base_row(plotCode="P101"),                       # valid
        _base_row(plotCode="P102", cycleLabel=None),       # missing label
    ]
    with p_sup, p_plot, p_active, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock()) as mk_create_plot, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock()) as mk_create_cycle:
        with pytest.raises(ImportHasErrors):
            await commit_import(object(), _xlsx(rows), ctx=_ctx())
    mk_create_plot.assert_not_awaited()
    mk_create_cycle.assert_not_awaited()


# --- template description text mentions the new contract -------------------

def test_template_description_documents_the_requirement() -> None:
    from app.services.plot_import import TEMPLATE_COLUMN_DESCRIPTIONS
    desc = TEMPLATE_COLUMN_DESCRIPTIONS["cycleLabel"]
    assert "เริ่มรอบปลูกใหม่ทุกกรณี" in desc
    assert "Auto" in desc and "Manual" in desc

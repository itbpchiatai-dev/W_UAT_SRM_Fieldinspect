"""Round 8-15D — Plot Excel Import wiring for crop/variety-vs-Master-Data
enforcement: every new-cycle action (create/start/rollover/reactivate) and
CHANGED crop/variety on update_current_cycle is checked against Master Data;
final_plot is never blocked by it; the check batches into exactly TWO
queries per file (never N+1); an errored row blocks the whole commit
(all-or-nothing), matching every other row-level error in this file.

DB-free: supplier/plot/active-cycle lookups are patched with AsyncMocks (same
pattern as test_plot_import_service.py); `master_data_repository.
list_by_type_values` is ALSO patched here (real active/inactive/parent
fixtures) rather than relying on tests/unit/conftest.py's permissive
default — hence `nodefault_crop_variety`.
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.excel_workbook import build_xlsx
from app.services.plot_import import (
    IMPORT_COLUMNS,
    ImportContext,
    ImportHasErrors,
    build_preview,
    commit_import,
)

pytestmark = pytest.mark.nodefault_crop_variety

_M = "app.services.plot_import"


def _xlsx(rows: list[dict[str, str]]) -> bytes:
    data: list[list] = [list(IMPORT_COLUMNS)]
    for r in rows:
        data.append([r.get(c) for c in IMPORT_COLUMNS])
    return build_xlsx([("plots", data)])


def _ctx(**kw) -> ImportContext:
    return ImportContext(allowed_supplier_id=None, can_create=True, can_update=True,
                          can_reactivate=True, **kw)


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


def _create_row(**over) -> dict[str, str]:
    base = {
        "action": "create_plot_with_cycle", "supplierCode": "SUP001",
        "plotCode": "P101", "plotName": "แปลงใหม่", "province": "เชียงใหม่",
        "poNumber": "PO25001", "pCode": "Melon-A",
        "crop": "พริก", "variety": "พริกขี้หนู", "lotNo": "LOT-01",
    }
    base.update(over)
    return base


def _update_row(**over) -> dict[str, str]:
    base = {
        "action": "update_current_cycle", "supplierCode": "SUP001", "plotCode": "P101",
        "crop": "พริก", "variety": "พริกขี้หนู",
    }
    base.update(over)
    return base


def _md(type_, value, parent=None, active=True):
    return SimpleNamespace(type=type_, value=value, parent=parent, active=active)


# Round 8-26C — every row fixture in this file carries pCode "Melon-A", and
# a P.Code is now validated against Master Data like crop/variety are. This
# file's subject is the CROP/VARIETY rules, so the default pool seeds that one
# P.Code as valid under the same fixtures' variety; a test that wants to
# exercise the P.Code rules passes its own `existing_p_codes`.
_DEFAULT_P_CODES = [("Melon-A", "พริกขี้หนู")]


def _patch_master_data(existing_crops=None, existing_varieties=None, existing_p_codes=None):
    pools = {
        "crop": existing_crops or [],
        "variety": existing_varieties or [],
        "p_code": existing_p_codes if existing_p_codes is not None else [
            _md("p_code", value, parent=parent) for value, parent in _DEFAULT_P_CODES
        ],
    }
    call_count = {"n": 0}

    async def fake(db, type_, values):
        call_count["n"] += 1
        return [m for m in pools.get(type_, []) if m.value in values]

    return (
        patch(
            "app.services.master_data_validation.master_data_repo.list_by_type_values",
            AsyncMock(side_effect=fake),
        ),
        call_count,
    )


def _patch_lookups(*, plot=None, active=None, supplier=...):
    sup = _supplier() if supplier is ... else supplier
    return (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=sup)),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active)),
    )


def _row(preview, index: int = 0):
    return preview.rows[index]


# --- new-cycle actions checked against Master Data --------------------------

async def test_create_rejects_inactive_crop() -> None:
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    p_md, _ = _patch_master_data(existing_crops=[_md("crop", "พริก", active=False)])
    with p_sup, p_plot, p_active, p_md:
        preview = await build_preview(object(), _xlsx([_create_row()]), ctx=_ctx())
    row = _row(preview)
    assert row.status == "error"
    assert "ปิดใช้งาน" in row.message


async def test_start_rejects_missing_variety() -> None:
    plot = SimpleNamespace(id=uuid4(), is_active=True)
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=None)
    p_md, _ = _patch_master_data(existing_crops=[_md("crop", "พริก")])  # variety not seeded
    row = {**_create_row(action="start_new_cycle"), "plotName": None}
    with p_sup, p_plot, p_active, p_md:
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status == "error"
    assert "ไม่พบพันธุ์" in result.message


async def test_rollover_new_cycle_rejects_inactive_crop() -> None:
    plot = SimpleNamespace(id=uuid4(), is_active=True)
    active = _cycle(crop="เดิม", variety=None, cycle_label="เดิม-label", lot_no="OLD-LOT")
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    p_md, _ = _patch_master_data(existing_crops=[_md("crop", "พริก", active=False)])
    row = _create_row(action="close_and_start_new_cycle", cycleLabel="ใหม่-label")
    with p_sup, p_plot, p_active, p_md:
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status == "error"
    assert "ปิดใช้งาน" in result.message


async def test_reactivate_new_cycle_has_no_current_pair() -> None:
    """reactivate_plot_with_cycle opens a first NEW cycle on a reopened
    plot — always a full check (current=None), never a legacy exemption."""
    plot = SimpleNamespace(id=uuid4(), is_active=False)
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=None)
    p_md, _ = _patch_master_data(existing_crops=[_md("crop", "พริก")],
                                  existing_varieties=[_md("variety", "พริกขี้หนู", parent="พริก")])
    row = _create_row(action="reactivate_plot_with_cycle", cycleLabel="jun2026")
    with p_sup, p_plot, p_active, p_md, \
         patch(f"{_M}.plot_cycle_repo.get_cycle_labels_for_plots", AsyncMock(return_value={})):
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status != "error"


# --- update_current_cycle: effective-pair / legacy exemption ----------------

async def test_update_unchanged_legacy_pair_passes_even_if_inactive() -> None:
    plot = SimpleNamespace(id=uuid4(), is_active=True)
    active = _cycle(crop="เมล่อน", variety="ญี่ปุ่น")
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    # Master Data lookup finds NOTHING (as if deactivated/removed) — still
    # passes because the row's crop/variety are IDENTICAL to the cycle's own.
    p_md, call_count = _patch_master_data()
    row = _update_row(crop="เมล่อน", variety="ญี่ปุ่น")
    with p_sup, p_plot, p_active, p_md:
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status != "error"


async def test_update_changed_to_inactive_value_rejected() -> None:
    plot = SimpleNamespace(id=uuid4(), is_active=True)
    active = _cycle(crop="เมล่อน", variety="ญี่ปุ่น")
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    p_md, _ = _patch_master_data(existing_crops=[_md("crop", "ทุเรียน", active=False)])
    row = _update_row(crop="ทุเรียน", variety=None)
    with p_sup, p_plot, p_active, p_md:
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status == "error"
    assert "ปิดใช้งาน" in result.message


async def test_update_crop_changed_variety_string_unchanged_revalidates_parent() -> None:
    """'ถ้าเปลี่ยน crop แต่ไม่ส่ง variety และ variety เดิมไม่เข้ากับ crop ใหม่ ต้อง 422' —
    the row repeats the SAME variety string as the active cycle's, but
    changes crop; the variety's real parent doesn't match the new crop."""
    plot = SimpleNamespace(id=uuid4(), is_active=True)
    active = _cycle(crop="พริก", variety="พริกขี้หนู")
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    p_md, _ = _patch_master_data(
        existing_crops=[_md("crop", "เมล่อน")],
        existing_varieties=[_md("variety", "พริกขี้หนู", parent="พริก")],  # still parented to OLD crop
    )
    row = _update_row(crop="เมล่อน", variety="พริกขี้หนู")
    with p_sup, p_plot, p_active, p_md:
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status == "error"
    assert "ไม่ได้อยู่ภายใต้" in result.message


async def test_update_editing_other_field_passes_with_inactive_legacy_crop() -> None:
    """'แก้ field อื่นโดยไม่เปลี่ยน Crop/Variety ได้' — the row repeats the SAME
    crop/variety (update_current_cycle sends the whole plan every time) while
    changing an unrelated field; the pair is unchanged so it's exempt."""
    plot = SimpleNamespace(id=uuid4(), is_active=True)
    active = _cycle(crop="เมล่อน", variety="ญี่ปุ่น")
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    p_md, _ = _patch_master_data(existing_crops=[_md("crop", "เมล่อน", active=False)])
    row = _update_row(crop="เมล่อน", variety="ญี่ปุ่น", plantCount="500")
    with p_sup, p_plot, p_active, p_md:
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status != "error"


# --- final_plot: never blocked ------------------------------------------------

async def test_final_plot_never_blocked_by_crop_variety_status() -> None:
    plot = SimpleNamespace(
        id=uuid4(), is_active=True,
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    active = _cycle(crop="ค่าไม่มีจริง", variety=None, cycle_label=None)
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    p_md, call_count = _patch_master_data()  # empty Master Data entirely
    row = {
        "action": "final_plot", "supplierCode": "SUP001", "plotCode": "P101",
        "harvestYield": "500", "finalYieldAfterClean": "480", "harvestDate": "2026-07-01",
    }
    with p_sup, p_plot, p_active, p_md, \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_for_cycle", AsyncMock(return_value=None)):
        preview = await build_preview(object(), _xlsx([row]), ctx=_ctx())
    result = _row(preview)
    assert result.status != "error"
    # final_plot never even queries Master Data (needs_master_data_check is
    # never set for it) — locks in it's not merely "happened to pass".
    assert call_count["n"] == 0


# --- all-or-nothing: one bad row blocks the whole commit ---------------------

async def test_commit_blocked_when_any_row_has_master_data_error() -> None:
    p_sup, p_plot, p_active = _patch_lookups(plot=None)
    p_md, _ = _patch_master_data(existing_crops=[_md("crop", "มะเขือเทศ")])
    rows = [
        _create_row(plotCode="P101", crop="มะเขือเทศ", variety=None),  # valid
        _create_row(plotCode="P102", crop="ไม่มีในระบบ", variety=None),  # rejected
    ]
    with p_sup, p_plot, p_active, p_md, \
         patch(f"{_M}.plot_repo.create_plot", AsyncMock()) as mk_create_plot, \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock()) as mk_create_cycle:
        with pytest.raises(ImportHasErrors):
            await commit_import(object(), _xlsx(rows), ctx=_ctx())
    mk_create_plot.assert_not_awaited()
    mk_create_cycle.assert_not_awaited()


# --- batch query strategy: exactly 2 queries per file, never per-row --------

async def test_master_data_lookup_batches_into_one_query_per_type() -> None:
    """5 rows, several distinct crop/variety values — list_by_type_values
    must be called once per TYPE for the WHOLE file, never once per row
    (N+1). Three types since round 8-26C added p_code."""
    plot = SimpleNamespace(id=uuid4(), is_active=True)
    active = _cycle(crop="เมล่อน", variety="ญี่ปุ่น")
    p_sup, p_plot, p_active = _patch_lookups(plot=plot, active=active)
    p_md, call_count = _patch_master_data(
        existing_crops=[_md("crop", "พริก"), _md("crop", "เมล่อน"), _md("crop", "ทุเรียน")],
        existing_varieties=[
            _md("variety", "พริกขี้หนู", parent="พริก"),
            _md("variety", "ญี่ปุ่น", parent="เมล่อน"),
        ],
    )
    rows = [
        _update_row(plotCode="P101", crop="พริก", variety="พริกขี้หนู"),
        _update_row(plotCode="P102", crop="เมล่อน", variety="ญี่ปุ่น"),
        _update_row(plotCode="P103", crop="ทุเรียน", variety=None),
        _update_row(plotCode="P104", crop="เมล่อน", variety="ญี่ปุ่น"),
        _update_row(plotCode="P105", crop="พริก", variety="พริกขี้หนู"),
    ]
    with p_sup, p_plot, p_active, p_md:
        await build_preview(object(), _xlsx(rows), ctx=_ctx())
    assert call_count["n"] == 3

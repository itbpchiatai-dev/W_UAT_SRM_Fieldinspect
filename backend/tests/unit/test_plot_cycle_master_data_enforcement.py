"""Round 8-15D — Lifecycle API wiring for crop/variety-vs-Master-Data
enforcement: create/start/rollover/reactivate (new cycle) and update
(effective-pair) all call `master_data_validation` BEFORE any mutation;
close_cycle/final_plot never call it at all.

DB-less: mocks repo helpers and calls the route functions directly (same
pattern as test_plot_cycle_lifecycle.py). Carries `nodefault_crop_variety`
so tests/unit/conftest.py's permissive default doesn't hide what's actually
being asserted — every test here patches
`app.api.v1.plots.master_data_validation` itself.
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.plots import (
    close_plot_cycle,
    create_plot_with_cycle,
    reactivate_plot_with_cycle,
    rollover_plot_cycle,
    start_plot_cycle,
    update_plot_cycle,
)
from app.schemas.plot import (
    PlotCreate,
    PlotCycleClose,
    PlotCycleCreate,
    PlotCycleRollover,
    PlotCycleUpdate,
    PlotWithCycleCreate,
)

pytestmark = pytest.mark.nodefault_crop_variety

_NOW = datetime.datetime(2026, 7, 14, tzinfo=datetime.timezone.utc)
_P = "app.api.v1.plots"
_REJECT = HTTPException(status_code=422, detail='ชนิดพืช "X" ถูกปิดใช้งาน กรุณาเปิดใช้งานใน Master Data ก่อน')


def _plot(**o):
    d = dict(
        id=uuid4(), supplier_id=uuid4(), plot_code="P101", name="แปลง A",
        village=None, district=None, province=None,
        latitude=None, longitude=None, rai=None,
        is_active=True, assignments=[], supplier=None, active_cycle=None,
        qr_key="qr-abc", created_at=_NOW, updated_at=_NOW,
    )
    d.update(o)
    return SimpleNamespace(**d)


def _cycle(**o):
    d = dict(
        id=uuid4(), plot_id=uuid4(), cycle_no=1, status="active",
        crop="เมล่อน", variety="ญี่ปุ่น", cycle_label=None, lot_no="L1",
        planting_date=datetime.date(2026, 5, 1), plant_count=100,
        expected_yield_full=None, expected_yield_unit="kg",
        started_at=_NOW, closed_at=None, closed_by_id=None, close_reason=None,
        created_at=_NOW, updated_at=_NOW,
    )
    d.update(o)
    return SimpleNamespace(**d)


def _db():
    return AsyncMock()


def _user(**o):
    d = dict(id=uuid4(), roles=[SimpleNamespace(name="internal:super_admin")], supplier_id=None)
    d.update(o)
    return SimpleNamespace(**d)


def _ccreate(**o) -> PlotCycleCreate:
    d = dict(poNumber="PO25001", pCode="Melon-A", cycleLabel="jun2026", crop="พริก", variety="พริกขี้หนู")
    d.update(o)
    return PlotCycleCreate(**d)


def _permissive():
    return patch(f"{_P}.master_data_validation.assert_crop_variety_valid", AsyncMock(return_value=None))


def _rejecting():
    return patch(f"{_P}.master_data_validation.assert_crop_variety_valid", AsyncMock(side_effect=_REJECT))


# --- create_plot_with_cycle -------------------------------------------------

async def test_create_with_cycle_rejects_inactive_crop_no_mutation() -> None:
    payload = PlotWithCycleCreate(
        plot=PlotCreate(supplierId=uuid4(), plotCode="P101", name="แปลง A"),
        cycle=_ccreate(),
    )
    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.create_plot", AsyncMock()) as mk_create_plot, \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock()) as mk_create_cycle, \
         _rejecting():
        with pytest.raises(HTTPException) as exc:
            await create_plot_with_cycle(payload=payload, current_user=_user(), db=_db())
    assert exc.value.status_code == 422
    mk_create_plot.assert_not_awaited()
    mk_create_cycle.assert_not_awaited()


async def test_create_with_cycle_passes_when_master_data_valid() -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id)
    payload = PlotWithCycleCreate(
        plot=PlotCreate(supplierId=plot.supplier_id, plotCode="P101", name="แปลง A"),
        cycle=_ccreate(),
    )
    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.create_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=cycle)), \
         _permissive() as mk_assert:
        result = await create_plot_with_cycle(payload=payload, current_user=_user(), db=_db())
    assert result.cycle.id == cycle.id
    mk_assert.assert_awaited_once()
    assert mk_assert.call_args.args[1:] == ("พริก", "พริกขี้หนู")


# --- start_plot_cycle --------------------------------------------------------

async def test_start_cycle_rejects_inactive_variety_no_mutation() -> None:
    plot = _plot()
    payload = _ccreate()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock()) as mk_create, \
         _rejecting():
        with pytest.raises(HTTPException) as exc:
            await start_plot_cycle(plot_id=plot.id, payload=payload, db=_db())
    assert exc.value.status_code == 422
    mk_create.assert_not_awaited()


async def test_start_cycle_checked_before_active_cycle_conflict_still_422() -> None:
    """Order doesn't matter for the caller — either guard can fire first;
    this just locks in that the 409 active-cycle guard runs BEFORE the
    Master Data check (an already-active plot never reaches the new check)."""
    plot = _plot()
    existing = _cycle(plot_id=plot.id)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=existing)), \
         _rejecting() as mk_assert:
        with pytest.raises(HTTPException) as exc:
            await start_plot_cycle(plot_id=plot.id, payload=_ccreate(), db=_db())
    assert exc.value.status_code == 409
    mk_assert.assert_not_awaited()


# --- update_plot_cycle -------------------------------------------------------

async def test_update_cycle_unchanged_pair_passes_field_only_edit() -> None:
    """Editing plant_count only (crop/variety absent from the PATCH body)
    must pass the cycle's OWN current crop/variety through as BOTH the
    effective and current value — even if that legacy value is, in reality,
    inactive; the real business rule is exercised in
    test_master_data_validation.py, this test only locks in the WIRING."""
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, crop="เมล่อน", variety="ญี่ปุ่น")
    payload = PlotCycleUpdate(plantCount=200)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.update_cycle", AsyncMock()) as mk_update, \
         patch(f"{_P}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         _permissive() as mk_assert:
        result = await update_plot_cycle(plot_id=plot.id, cycle_id=cycle.id, payload=payload, db=_db())
    mk_assert.assert_awaited_once()
    kw = mk_assert.call_args
    # effective crop/variety fall back to the cycle's own (absent from payload)
    assert kw.args[1] == "เมล่อน"
    assert kw.args[2] == "ญี่ปุ่น"
    assert kw.kwargs["current_crop"] == "เมล่อน"
    assert kw.kwargs["current_variety"] == "ญี่ปุ่น"
    mk_update.assert_awaited_once()
    assert result.id == cycle.id


async def test_update_cycle_changed_crop_rejected_no_mutation() -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, crop="เมล่อน", variety="ญี่ปุ่น")
    payload = PlotCycleUpdate(crop="ทุเรียน")
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.update_cycle", AsyncMock()) as mk_update, \
         _rejecting() as mk_assert:
        with pytest.raises(HTTPException) as exc:
            await update_plot_cycle(plot_id=plot.id, cycle_id=cycle.id, payload=payload, db=_db())
    assert exc.value.status_code == 422
    mk_assert.assert_awaited_once()
    assert mk_assert.call_args.args[1] == "ทุเรียน"      # effective (changed)
    assert mk_assert.call_args.kwargs["current_crop"] == "เมล่อน"  # cycle's own
    mk_update.assert_not_awaited()


# --- rollover_plot_cycle ------------------------------------------------------

async def test_rollover_rejects_inactive_new_crop_no_mutation() -> None:
    plot = _plot()
    active = _cycle(plot_id=plot.id, status="active")
    payload = PlotCycleRollover(closeStatus="harvested", newCycle=_ccreate())
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.rollover_cycle", AsyncMock()) as mk_rollover, \
         _rejecting():
        with pytest.raises(HTTPException) as exc:
            await rollover_plot_cycle(
                plot_id=plot.id, cycle_id=active.id, payload=payload, current_user=_user(), db=_db(),
            )
    assert exc.value.status_code == 422
    mk_rollover.assert_not_awaited()


async def test_rollover_new_cycle_has_no_current_pair() -> None:
    """The fresh cycle a rollover opens has no 'current' — always validated
    as a brand-new pair (current_crop/current_variety default None)."""
    plot = _plot()
    active = _cycle(plot_id=plot.id, status="active")
    closed = _cycle(plot_id=plot.id, status="harvested")
    new_cycle = _cycle(plot_id=plot.id, cycle_no=2)
    payload = PlotCycleRollover(closeStatus="harvested", newCycle=_ccreate())
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.rollover_cycle", AsyncMock(return_value=(closed, new_cycle))), \
         _permissive() as mk_assert:
        await rollover_plot_cycle(
            plot_id=plot.id, cycle_id=active.id, payload=payload, current_user=_user(), db=_db(),
        )
    mk_assert.assert_awaited_once()
    assert mk_assert.call_args.kwargs.get("current_crop") is None
    assert mk_assert.call_args.kwargs.get("current_variety") is None


# --- reactivate_plot_with_cycle ------------------------------------------------

async def test_reactivate_with_cycle_rejects_inactive_no_mutation() -> None:
    plot = _plot(is_active=False)
    payload = _ccreate()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.repo.reactivate_plot_with_cycle", AsyncMock()) as mk_react, \
         _rejecting():
        with pytest.raises(HTTPException) as exc:
            await reactivate_plot_with_cycle(plot_id=plot.id, payload=payload, current_user=_user(), db=_db())
    assert exc.value.status_code == 422
    mk_react.assert_not_awaited()


# --- close_plot_cycle: no regression -----------------------------------------

async def test_close_cycle_never_calls_master_data_validation() -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, status="active")
    closed = _cycle(plot_id=plot.id, status="harvested")
    payload = PlotCycleClose(status="harvested")
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.close_cycle", AsyncMock(return_value=closed)), \
         patch(f"{_P}.master_data_validation.assert_crop_variety_valid", AsyncMock()) as mk_assert:
        await close_plot_cycle(
            plot_id=plot.id, cycle_id=cycle.id, payload=payload, current_user=_user(), db=_db(),
        )
    mk_assert.assert_not_awaited()

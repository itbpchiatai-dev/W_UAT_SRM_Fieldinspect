"""Every "start a cycle" endpoint stores the variety the P.Code derives to
(round Y).

Four endpoints open a new planting cycle — create-plot-with-cycle, start,
rollover's new cycle, and reactivate-with-cycle — and all four take the same
PlotCycleCreate body. This file pins the two halves of the round at that
shared seam:

  - the variety written to the cycle is the one Master Data derives from the
    chosen P.Code, never something the caller sent;
  - a caller that DOES send a variety is refused, loudly. Silently dropping
    it would let an old client (or a script) believe it chose the plant while
    the server chose a different one — the same reasoning that made round A
    refuse a filled `lotNo` cell instead of ignoring it.

DB-less, like test_plot_cycle_master_data_enforcement.py: the repo helpers
are mocked and the route functions are called directly.
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.plots import (
    create_plot_with_cycle,
    reactivate_plot_with_cycle,
    rollover_plot_cycle,
    start_plot_cycle,
)
from app.schemas.plot import (
    PlotCreate,
    PlotCycleCreate,
    PlotCycleRollover,
    PlotWithCycleCreate,
)

pytestmark = pytest.mark.nodefault_crop_variety

_NOW = datetime.datetime(2026, 9, 16, tzinfo=datetime.timezone.utc)
_P = "app.api.v1.plots"
_CROP = "WM"
_P_CODE = "CTT-507"
_VARIETY = "แตงโม RWA 412 (D-12)"


def _plot(**o):
    d = dict(
        id=uuid4(), supplier_id=uuid4(), plot_code="JPS-2609-0001", name="แปลง A",
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
        crop=_CROP, variety=_VARIETY, cycle_label="sep2026", lot_no="L1",
        planting_date=datetime.date(2026, 9, 1), plant_count=100,
        expected_yield_full=None, expected_yield_unit="kg", p_code=_P_CODE,
        started_at=_NOW, closed_at=None, closed_by_id=None, close_reason=None,
        created_at=_NOW, updated_at=_NOW,
    )
    d.update(o)
    return SimpleNamespace(**d)


def _user(**o):
    d = dict(id=uuid4(), roles=[SimpleNamespace(name="internal:super_admin")], supplier_id=None)
    d.update(o)
    return SimpleNamespace(**d)


def _body(**o) -> PlotCycleCreate:
    d = dict(pCode=_P_CODE, cycleLabel="sep2026", crop=_CROP)
    d.update(o)
    return PlotCycleCreate(**d)


def _deriving():
    """The validator as it behaves for a good pair: no error, and the variety
    handed back for the endpoint to store."""
    return patch(
        f"{_P}.master_data_validation.assert_crop_p_code_valid",
        AsyncMock(return_value=_VARIETY),
    )


# --- the caller cannot choose the variety ---------------------------------


def test_a_submitted_variety_is_refused_by_the_schema() -> None:
    with pytest.raises(ValidationError) as exc:
        _body(variety="พันธุ์ที่พิมพ์เอง")
    assert "พันธุ์" in str(exc.value)
    assert "P.Code" in str(exc.value)


def test_an_omitted_or_blank_variety_is_fine() -> None:
    # An older SPA build that still sends the key with nothing in it keeps
    # working — exactly how round V handled PlotCreate.plot_code.
    assert _body().variety is None
    assert _body(variety=None).variety is None
    assert _body(variety="   ").variety is None


# --- all four endpoints store the DERIVED variety -------------------------


async def test_create_plot_with_cycle_stores_the_derived_variety() -> None:
    plot = _plot()
    payload = PlotWithCycleCreate(
        plot=PlotCreate(supplierId=plot.supplier_id, name="แปลง A"), cycle=_body(),
    )
    with patch(f"{_P}.repo.create_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle())) as mk, \
         _deriving() as mk_check:
        await create_plot_with_cycle(payload=payload, current_user=_user(), db=AsyncMock())
    assert mk.call_args.kwargs["variety"] == _VARIETY
    assert mk.call_args.kwargs["crop"] == _CROP
    # Checked with what the user actually picked: crop + P.Code.
    assert mk_check.call_args.args[1:] == (_CROP, _P_CODE)


async def test_start_cycle_stores_the_derived_variety() -> None:
    plot = _plot()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=None)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle())) as mk, \
         _deriving():
        await start_plot_cycle(plot_id=plot.id, payload=_body(), db=AsyncMock())
    assert mk.call_args.kwargs["variety"] == _VARIETY


async def test_rollover_stores_the_derived_variety_on_the_new_cycle() -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id)
    payload = PlotCycleRollover(closeStatus="harvested", newCycle=_body(cycleLabel="oct2026"))
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.rollover_cycle",
               AsyncMock(return_value=(cycle, _cycle(cycle_no=2)))) as mk, \
         patch(f"{_P}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         _deriving():
        await rollover_plot_cycle(
            plot_id=plot.id, cycle_id=cycle.id, payload=payload,
            current_user=_user(), db=AsyncMock(),
        )
    assert mk.call_args.kwargs["variety"] == _VARIETY


async def test_reactivate_stores_the_derived_variety() -> None:
    plot = _plot(is_active=False)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.repo.reactivate_plot_with_cycle",
               AsyncMock(return_value=(plot, _cycle()))) as mk, \
         _deriving():
        await reactivate_plot_with_cycle(
            plot_id=plot.id, payload=_body(), current_user=_user(), db=AsyncMock(),
        )
    assert mk.call_args.kwargs["variety"] == _VARIETY


# --- a bad pair never reaches a write -------------------------------------


async def test_a_p_code_from_another_crop_stops_before_any_mutation() -> None:
    plot = _plot()
    reject = HTTPException(status_code=422, detail='P.Code "CTT-507" ไม่ได้อยู่ภายใต้ชนิดพืช "WM"')
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=None)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock()) as mk, \
         patch(f"{_P}.master_data_validation.assert_crop_p_code_valid",
               AsyncMock(side_effect=reject)):
        with pytest.raises(HTTPException) as exc:
            await start_plot_cycle(plot_id=plot.id, payload=_body(), db=AsyncMock())
    assert exc.value.status_code == 422
    mk.assert_not_awaited()

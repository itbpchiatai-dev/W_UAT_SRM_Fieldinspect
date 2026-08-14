"""GET /api/v1/plots/{plotId}/cycles — round 7.2A read foundation.

DB-less: mocks the repo layer and calls the route function directly (same
pattern as test_plot_lookup.py). RLS/permission wiring asserted by source
inspection.
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.api.v1.plots as plots_module
from app.api.v1.plots import list_plot_cycles

_NOW = datetime.datetime(2026, 7, 14, tzinfo=datetime.timezone.utc)


def _cycle(**overrides):
    defaults = dict(
        id=uuid4(), plot_id=uuid4(), cycle_no=1, status="active",
        crop="เมล่อน", variety="ญี่ปุ่น", cycle_label=None, lot_no="L1",
        planting_date=datetime.date(2026, 5, 1), plant_count=100,
        expected_yield_full=None, expected_yield_unit="kg",
        started_at=_NOW, closed_at=None, closed_by_id=None, close_reason=None,
        created_at=_NOW, updated_at=_NOW,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_list_plot_cycles_returns_cycles_for_the_plot() -> None:
    plot = SimpleNamespace(id=uuid4())
    cycles = [_cycle(plot_id=plot.id, cycle_no=2, status="active"),
              _cycle(plot_id=plot.id, cycle_no=1, status="cancelled", closed_at=_NOW)]
    with patch("app.api.v1.plots.repo.get_plot", AsyncMock(return_value=plot)), \
         patch("app.api.v1.plots.plot_cycle_repo.list_cycles_for_plot",
               AsyncMock(return_value=cycles)):
        result = await list_plot_cycles(plot_id=plot.id, db=AsyncMock())

    assert [c.cycle_no for c in result] == [2, 1]
    assert result[0].status == "active"
    assert result[1].status == "cancelled"
    # camelCase serialisation of the read model
    dumped = result[0].model_dump(by_alias=True)
    assert "plotCycleId" not in dumped  # this IS the cycle; no such field
    assert set(dumped) >= {"id", "plotId", "cycleNo", "status", "startedAt"}


async def test_list_plot_cycles_404_when_plot_out_of_scope_or_missing() -> None:
    """An out-of-scope/unknown plot is a generic 404 — the cycle list is never
    even queried, so it can't leak a foreign plot's cycles."""
    with patch("app.api.v1.plots.repo.get_plot", AsyncMock(return_value=None)), \
         patch("app.api.v1.plots.plot_cycle_repo.list_cycles_for_plot",
               AsyncMock()) as mocked_list:
        with pytest.raises(HTTPException) as exc:
            await list_plot_cycles(plot_id=uuid4(), db=AsyncMock())

    assert exc.value.status_code == 404
    assert exc.value.detail == "Plot not found"
    mocked_list.assert_not_awaited()


def test_route_requires_plots_read_and_rls_context() -> None:
    src = inspect.getsource(plots_module)
    block = src[src.index('@router.get("/{plot_id}/cycles"'):src.index("async def list_plot_cycles")]
    assert "require_permission(PermissionKey.PLOTS_READ)" in block
    assert "get_rls_context" in block


def test_route_verifies_plot_scope_then_uses_scoped_list_helper() -> None:
    src = inspect.getsource(plots_module.list_plot_cycles)
    assert "repo.get_plot(db, plot_id)" in src           # plot in scope first
    assert 'detail="Plot not found"' in src
    assert "plot_cycle_repo.list_cycles_for_plot" in src  # scoped list helper


def test_cycles_are_not_exposed_through_any_public_router() -> None:
    import app.api.v1.public_plots as public_plots
    import app.api.v1.public_records as public_records
    for mod in (public_plots, public_records):
        assert not any("cycle" in r.path.lower() for r in mod.router.routes), mod.__name__

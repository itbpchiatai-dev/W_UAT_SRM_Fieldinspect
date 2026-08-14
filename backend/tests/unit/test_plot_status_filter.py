"""GET /api/v1/plots & /api/v1/plots/provinces — plot_status filter (round
8-6I Part B). Additive query param alongside the existing active_only bool:
plot_status=all|active|inactive, default 'all' (unchanged list behavior).

DB-less: repository-level tests compile the SQLAlchemy statement (literal
binds) to inspect the WHERE clause without a real database — same technique
used to prove `Plot.current_crop == crop` filters (test_plot_list_filters.py).
Endpoint-level tests call the route function directly and patch the
repository, matching this repo's existing convention.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.v1.plots import list_plot_provinces, list_plots
from app.db.models.plot import Plot
from app.repositories.plot_repository import _apply_plot_status_filter, list_plots as repo_list_plots


def _compiled(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


# --- repository: _apply_plot_status_filter ---------------------------------

def test_plot_status_all_applies_no_is_active_filter():
    stmt = _apply_plot_status_filter(select(Plot), plot_status="all", active_only=False)
    # is_active appears in the SELECTed column list regardless — assert no
    # WHERE clause was added at all (never "is_active IS true/false").
    assert "WHERE" not in _compiled(stmt)


def test_plot_status_active_filters_to_true():
    stmt = _apply_plot_status_filter(select(Plot), plot_status="active", active_only=False)
    assert "plots.is_active IS true" in _compiled(stmt)


def test_plot_status_inactive_filters_to_false():
    stmt = _apply_plot_status_filter(select(Plot), plot_status="inactive", active_only=False)
    assert "plots.is_active IS false" in _compiled(stmt)


def test_active_only_true_behaves_like_plot_status_active_backward_compat():
    """active_only=True must still filter to active plots even when
    plot_status is left at its default 'all' — every pre-8-6I caller that
    only ever passed active_only is unaffected."""
    stmt = _apply_plot_status_filter(select(Plot), plot_status="all", active_only=True)
    assert "plots.is_active IS true" in _compiled(stmt)


def test_active_only_true_and_plot_status_active_is_not_contradictory():
    """Redundant, not conflicting — both ask for the same thing."""
    stmt = _apply_plot_status_filter(select(Plot), plot_status="active", active_only=True)
    assert "plots.is_active IS true" in _compiled(stmt)


def test_default_plot_status_is_all_in_list_plots_signature():
    import inspect
    sig = inspect.signature(repo_list_plots)
    assert sig.parameters["plot_status"].default == "all"


# --- endpoint: list_plots ---------------------------------------------------

def _plot(**overrides):
    defaults = dict(
        id=uuid4(), supplier_id=uuid4(), plot_code="SUP001-P001", name="Plot One",
        village=None, district=None, province="Chiang Mai", latitude=None, longitude=None,
        is_active=True, assignments=[], qr_key="qr_key", current_yield_pct=None,
        expected_yield_full=None, expected_yield_unit=None, plant_count=None,
        current_crop=None, current_variety=None, current_lot_no=None, current_planting_date=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_list_plots_plot_status_all_returns_both_active_and_inactive():
    active = _plot(is_active=True)
    inactive = _plot(is_active=False)
    with patch("app.api.v1.plots.repo.list_plots", AsyncMock(return_value=[active, inactive])) as mocked:
        result = await list_plots(db=AsyncMock(), plot_status="all")
    mocked.assert_awaited_once_with(
        ANY, supplier_id=None, province=None, crop=None, variety=None,
        limit=50, offset=0, q=None, active_only=False, plot_status="all",
        cycle_label=None,
    )
    assert {p.is_active for p in result} == {True, False}


async def test_list_plots_plot_status_active_returns_only_active():
    active = _plot(is_active=True)
    with patch("app.api.v1.plots.repo.list_plots", AsyncMock(return_value=[active])) as mocked:
        result = await list_plots(db=AsyncMock(), plot_status="active")
    assert mocked.call_args.kwargs["plot_status"] == "active"
    assert all(p.is_active for p in result)


async def test_list_plots_plot_status_inactive_returns_only_inactive():
    inactive = _plot(is_active=False)
    with patch("app.api.v1.plots.repo.list_plots", AsyncMock(return_value=[inactive])) as mocked:
        result = await list_plots(db=AsyncMock(), plot_status="inactive")
    assert mocked.call_args.kwargs["plot_status"] == "inactive"
    assert all(not p.is_active for p in result)


async def test_list_plots_active_only_true_no_regression_when_plot_status_omitted():
    active = _plot(is_active=True)
    with patch("app.api.v1.plots.repo.list_plots", AsyncMock(return_value=[active])) as mocked:
        await list_plots(db=AsyncMock(), active_only=True)
    assert mocked.call_args.kwargs["active_only"] is True
    assert mocked.call_args.kwargs["plot_status"] == "all"  # default, unchanged


async def test_list_plots_conflicting_active_only_and_plot_status_inactive_is_422():
    with patch("app.api.v1.plots.repo.list_plots", AsyncMock()) as mocked:
        with pytest.raises(HTTPException) as exc:
            await list_plots(db=AsyncMock(), active_only=True, plot_status="inactive")
    assert exc.value.status_code == 422
    mocked.assert_not_awaited()


async def test_list_plots_active_only_true_and_plot_status_active_is_allowed():
    active = _plot(is_active=True)
    with patch("app.api.v1.plots.repo.list_plots", AsyncMock(return_value=[active])):
        result = await list_plots(db=AsyncMock(), active_only=True, plot_status="active")
    assert len(result) == 1


async def test_list_plots_supplier_and_other_filters_still_forwarded_with_plot_status():
    supplier_id = uuid4()
    with patch("app.api.v1.plots.repo.list_plots", AsyncMock(return_value=[])) as mocked:
        await list_plots(
            db=AsyncMock(), supplier_id=supplier_id, province="Chiang Mai",
            crop="พริก", variety="พริกขี้หนู", q="P001", plot_status="inactive",
        )
    mocked.assert_awaited_once_with(
        ANY, supplier_id=supplier_id, province="Chiang Mai", crop="พริก", variety="พริกขี้หนู",
        limit=50, offset=0, q="P001", active_only=False, plot_status="inactive",
        cycle_label=None,
    )


# --- endpoint: list_plot_provinces ------------------------------------------

async def test_list_plot_provinces_plot_status_inactive_forwarded():
    with patch(
        "app.api.v1.plots.repo.list_plot_provinces", AsyncMock(return_value=["Chiang Mai"]),
    ) as mocked:
        result = await list_plot_provinces(db=AsyncMock(), plot_status="inactive")
    mocked.assert_awaited_once_with(ANY, supplier_id=None, active_only=False, plot_status="inactive")
    assert result == ["Chiang Mai"]


async def test_list_plot_provinces_conflicting_params_is_422():
    with patch("app.api.v1.plots.repo.list_plot_provinces", AsyncMock()) as mocked:
        with pytest.raises(HTTPException) as exc:
            await list_plot_provinces(db=AsyncMock(), active_only=True, plot_status="inactive")
    assert exc.value.status_code == 422
    mocked.assert_not_awaited()


async def test_list_plot_provinces_supplier_scope_still_forwarded():
    supplier_id = uuid4()
    with patch(
        "app.api.v1.plots.repo.list_plot_provinces", AsyncMock(return_value=[]),
    ) as mocked:
        await list_plot_provinces(db=AsyncMock(), supplier_id=supplier_id, plot_status="active")
    mocked.assert_awaited_once_with(
        ANY, supplier_id=supplier_id, active_only=False, plot_status="active",
    )


# --- structural: RLS/permission wiring unchanged, Literal rejects garbage --

def test_list_plots_route_still_requires_plots_read_and_rls_context():
    import inspect
    import app.api.v1.plots as plots_module
    src = inspect.getsource(plots_module)
    block = src[src.index('@router.get("", response_model=list[PlotSummary]'):src.index("async def list_plots(")]
    assert "PermissionKey.PLOTS_READ" in block
    assert "get_rls_context" in block


def test_plot_status_query_param_is_a_literal_type():
    """Literal[...] on the query param itself rejects any value outside
    {'all','active','inactive'} with FastAPI's own 422 — never a silent
    fall-through to some other filter. `from __future__ import annotations`
    in plots.py turns annotations into strings, so resolve with
    get_type_hints rather than raw .annotation (same pattern as round
    8-6H's schema-reuse test)."""
    import typing
    import app.api.v1.plots as plots_module
    hints = typing.get_type_hints(plots_module.list_plots)
    annotation = hints["plot_status"]
    assert typing.get_origin(annotation) is typing.Literal
    assert set(typing.get_args(annotation)) == {"all", "active", "inactive"}

"""GET /api/v1/plots list filters.

DB-less route tests matching this repo's existing pattern: call the route
function directly and patch the repository layer. RLS/permission wiring is
handled by the FastAPI dependencies on the real route.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch
from uuid import uuid4

from app.api.v1.plots import list_plot_provinces, list_plots


def _plot(**overrides):
    defaults = dict(
        id=uuid4(),
        supplier_id=uuid4(),
        plot_code="SUP001-P001",
        name="Plot One",
        village=None,
        district=None,
        province="Chiang Mai",
        latitude=None,
        longitude=None,
        is_active=True,
        assignments=[],
        qr_key="qr_key",
        current_yield_pct=None,
        expected_yield_full=None,
        expected_yield_unit=None,
        plant_count=None,
        current_crop=None,
        current_variety=None,
        current_lot_no=None,
        current_planting_date=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_list_plots_passes_province_filter_to_repository() -> None:
    supplier_id = uuid4()
    plot = _plot(supplier_id=supplier_id)
    with patch("app.api.v1.plots.repo.list_plots", AsyncMock(return_value=[plot])) as mocked:
        result = await list_plots(
            db=AsyncMock(),
            supplier_id=supplier_id,
            province="Chiang Mai",
            limit=20,
            offset=40,
            q="P001",
            active_only=True,
        )

    mocked.assert_awaited_once_with(
        ANY,
        supplier_id=supplier_id,
        province="Chiang Mai",
        crop=None,
        variety=None,
        limit=20,
        offset=40,
        q="P001",
        active_only=True,
        plot_status="all",
        cycle_label=None,
        planting_date_from=None,
        planting_date_to=None,
    )
    assert result[0].province == "Chiang Mai"


async def test_list_plots_passes_crop_and_variety_filters_to_repository() -> None:
    plot = _plot(current_crop="พริก", current_variety="พริกขี้หนู")
    with patch("app.api.v1.plots.repo.list_plots", AsyncMock(return_value=[plot])) as mocked:
        result = await list_plots(
            db=AsyncMock(),
            crop="พริก",
            variety="พริกขี้หนู",
        )

    mocked.assert_awaited_once_with(
        ANY,
        supplier_id=None,
        province=None,
        crop="พริก",
        variety="พริกขี้หนู",
        limit=50,
        offset=0,
        q=None,
        active_only=False,
        plot_status="all",
        cycle_label=None,
        planting_date_from=None,
        planting_date_to=None,
    )
    assert result[0].current_crop == "พริก"


def test_plot_summary_exposes_latest_inspection_context() -> None:
    """The list response carries current_stage/last_inspected_at alongside
    current_yield_pct so the new-record form can default its Yield % from
    the plot's latest inspection and label the source ("ตรวจล่าสุด <date>
    · ระยะ <stage>") without an extra per-plot PlotRead fetch."""
    from app.schemas.plot import PlotSummary

    fields = set(PlotSummary.model_fields)
    assert {"current_yield_pct", "current_stage", "last_inspected_at"} <= fields


def test_repository_filters_on_the_master_data_columns_exactly() -> None:
    """The repo must filter current_crop/current_variety by exact equality
    (master-data-driven values, no case drift) — mirroring
    report_repository.plot_status_rows' crop filter."""
    import inspect

    from app.repositories import plot_repository

    src = inspect.getsource(plot_repository.list_plots)
    assert "Plot.current_crop == crop" in src
    assert "Plot.current_variety == variety" in src


async def test_list_plot_provinces_passes_scope_filters_to_repository() -> None:
    supplier_id = uuid4()
    with patch(
        "app.api.v1.plots.repo.list_plot_provinces",
        AsyncMock(return_value=["Chiang Mai", "Tak"]),
    ) as mocked:
        result = await list_plot_provinces(
            db=AsyncMock(),
            supplier_id=supplier_id,
            active_only=True,
        )

    mocked.assert_awaited_once_with(
        ANY,
        supplier_id=supplier_id,
        active_only=True,
        plot_status="all",
    )
    assert result == ["Chiang Mai", "Tak"]

"""Round 6.1 — PlotSummary/PlotRead carry denormalised supplierCode/
supplierName, populated by the API layer from Plot.supplier so the list/
detail don't depend on the frontend's capped active-suppliers fetch.

No DB fixture: build a transient Plot, attach a fake supplier + assignments,
and exercise the real _to_summary / _to_read mappers + the repo's eager-load.
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.plots import _to_read, _to_summary
from app.db.models.plot import Plot
from app.repositories import plot_repository as repo
from app.schemas.plot import PlotRead, PlotSummary


def _plot(**overrides) -> Plot:
    now = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    plot = Plot(
        supplier_id=uuid4(), plot_code="SUP001-P001", name="Plot One",
        province="เชียงใหม่", is_active=True,
    )
    plot.id = uuid4()
    plot.created_at = now
    plot.updated_at = now
    plot.assignments = []
    plot.supplier = SimpleNamespace(code="SUP001", name="Supplier One")
    for k, v in overrides.items():
        setattr(plot, k, v)
    return plot


def test_schemas_expose_supplier_code_and_name() -> None:
    for schema in (PlotSummary, PlotRead):
        assert "supplier_code" in schema.model_fields
        assert "supplier_name" in schema.model_fields
    # Not client-writable — absent from the create/update contract.
    from app.schemas.plot import PlotCreate, PlotUpdate
    for schema in (PlotCreate, PlotUpdate):
        assert "supplier_code" not in schema.model_fields
        assert "supplier_name" not in schema.model_fields


def test_to_summary_populates_supplier_code_and_name() -> None:
    summary = _to_summary(_plot())
    assert summary.supplier_code == "SUP001"
    assert summary.supplier_name == "Supplier One"


def test_to_read_populates_supplier_code_and_name() -> None:
    read = _to_read(_plot())
    assert read.supplier_code == "SUP001"
    assert read.supplier_name == "Supplier One"


def test_mappers_default_to_blank_when_supplier_not_loaded() -> None:
    # Defensive: a plot without the relationship loaded (e.g. a unit-test
    # transient) must not crash — blank denormalised fields instead.
    plot = _plot()
    plot.supplier = None
    assert _to_summary(plot).supplier_code == ""
    assert _to_read(plot).supplier_name == ""


def test_repo_eager_loads_supplier_for_list_and_get() -> None:
    list_src = inspect.getsource(repo.list_plots)
    # Round 8.0.7 — get_plot and get_plot_for_update share their loader
    # options via _plot_read_options() (see test_plot_repository_loading.py).
    options_src = inspect.getsource(repo._plot_read_options)
    assert "selectinload(Plot.supplier)" in list_src
    assert "selectinload(Plot.supplier)" in options_src


def test_list_plots_scope_and_filters_unchanged() -> None:
    # Round 6.1 only adds an eager-load; the supplier scope/RLS filter path
    # (scope_conditions applied by the endpoint's ScopeFilter) and the
    # existing where-clauses must be untouched.
    src = inspect.getsource(repo.list_plots)
    assert "Plot.supplier_id == supplier_id" in src
    assert "active_only" in src

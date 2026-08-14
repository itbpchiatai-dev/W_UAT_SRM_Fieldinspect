"""Round 7.3.1 — PlotSummary/PlotRead carry an active_cycle_* read-model,
populated by the API layer from the filtered Plot.active_cycle relationship
so the frontend reads the active-cycle truth directly instead of inferring it
from the current_* mirror columns.

No DB fixture: build a transient Plot, attach a fake active cycle, and
exercise the real _to_summary / _to_read mappers + the model/repo wiring.
"""
from __future__ import annotations

import datetime
import inspect
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.orm import attributes

from app.api.v1.plots import _to_read, _to_summary
from app.db.models.plot import Plot
from app.repositories import plot_repository as repo
from app.schemas.plot import PlotCreate, PlotRead, PlotSummary, PlotUpdate

_ACTIVE_CYCLE_FIELDS = [
    "active_cycle_id", "active_cycle_no", "active_cycle_status",
    "active_cycle_crop", "active_cycle_variety", "active_cycle_label",
    "active_cycle_lot_no",
    # Round 8-5A — denormalized PO / P.Code read mirror.
    "active_cycle_po_number", "active_cycle_p_code",
    "active_cycle_planting_date", "active_cycle_plant_count",
    "active_cycle_expected_yield_full", "active_cycle_expected_yield_unit",
]


def _cycle(**overrides):
    defaults = dict(
        id=uuid4(), cycle_no=2, status="active",
        crop="เมล่อน", variety="ญี่ปุ่น", cycle_label="jun2026", lot_no="LOT-02",
        po_number="PO25001", p_code="Melon-A",
        supplier_lot_no="SUP-OWN-1",
        planting_date=datetime.date(2026, 5, 1), plant_count=250,
        expected_yield_full=Decimal("1000.00"), expected_yield_unit="kg",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


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
    # Simulate an eager-loaded value on the viewonly active_cycle relationship
    # (a plain assignment would route through the ORM setter, which rejects a
    # non-ORM value). `active_cycle=None` in overrides → no active cycle.
    active_cycle = overrides.pop("active_cycle", _cycle())
    attributes.set_committed_value(plot, "active_cycle", active_cycle)
    for k, v in overrides.items():
        setattr(plot, k, v)
    return plot


def test_schemas_expose_active_cycle_fields() -> None:
    for schema in (PlotSummary, PlotRead):
        for field in _ACTIVE_CYCLE_FIELDS:
            assert field in schema.model_fields, f"{schema.__name__}.{field}"
    # Never client-writable — absent from the create/update contract.
    for schema in (PlotCreate, PlotUpdate):
        for field in _ACTIVE_CYCLE_FIELDS:
            assert field not in schema.model_fields


def test_to_summary_populates_active_cycle() -> None:
    s = _to_summary(_plot())
    assert s.active_cycle_no == 2
    assert s.active_cycle_status == "active"
    assert s.active_cycle_crop == "เมล่อน"
    assert s.active_cycle_variety == "ญี่ปุ่น"
    assert s.active_cycle_label == "jun2026"
    assert s.active_cycle_lot_no == "LOT-02"
    assert s.active_cycle_po_number == "PO25001"
    assert s.active_cycle_p_code == "Melon-A"
    assert s.active_cycle_planting_date == datetime.date(2026, 5, 1)
    assert s.active_cycle_plant_count == 250
    assert s.active_cycle_expected_yield_full == Decimal("1000.00")
    assert s.active_cycle_expected_yield_unit == "kg"


def test_to_read_populates_active_cycle_id() -> None:
    cycle = _cycle()
    read = _to_read(_plot(active_cycle=cycle))
    assert read.active_cycle_id == cycle.id
    assert read.active_cycle_no == cycle.cycle_no
    assert read.active_cycle_po_number == "PO25001"
    assert read.active_cycle_p_code == "Melon-A"


def test_mappers_null_when_no_active_cycle() -> None:
    # A plot with no active cycle (only closed ones, or none) → the filtered
    # relationship loads None → every active_cycle_* field stays null. The
    # frontend reads active_cycle_id == null as "no active cycle".
    plot = _plot(active_cycle=None)
    for target in (_to_summary(plot), _to_read(plot)):
        for field in _ACTIVE_CYCLE_FIELDS:
            assert getattr(target, field) is None, field


def test_active_cycle_relationship_is_filtered_to_active_status() -> None:
    # The relationship's primaryjoin filters status='active', so a plot whose
    # only cycle is harvested/cancelled never loads it as active_cycle — the
    # model, not the API layer, enforces "active only".
    rel = Plot.__mapper__.relationships["active_cycle"]
    assert rel.viewonly is True
    assert rel.uselist is False
    compiled = str(rel.primaryjoin.compile(compile_kwargs={"literal_binds": True}))
    assert "status = 'active'" in compiled


def test_repo_eager_loads_active_cycle_for_list_and_get() -> None:
    list_src = inspect.getsource(repo.list_plots)
    # Round 8.0.7 — get_plot and get_plot_for_update share their loader
    # options via _plot_read_options(), so that's what carries this assertion
    # for the get-single-plot path now (see test_plot_repository_loading.py).
    options_src = inspect.getsource(repo._plot_read_options)
    assert "selectinload(Plot.active_cycle)" in list_src
    assert "selectinload(Plot.active_cycle)" in options_src
    # create_plot refreshes it too (avoids an async lazy-load on a new plot).
    assert "active_cycle" in inspect.getsource(repo.create_plot)


def test_current_mirror_columns_kept() -> None:
    # Round 7.3.1 keeps the current_* mirror (the active cycle syncs it);
    # it must NOT have been removed alongside adding the read-model.
    for field in ("current_crop", "current_variety", "current_lot_no"):
        assert field in PlotSummary.model_fields
        assert field in PlotRead.model_fields

"""Round 8-3A — PlotRead/PlotSummary carry primaryPhone/additionalPhones,
populated by the API layer from the filtered Plot.access_phones relationship
(active rows, primary-first). No DB fixture: build a transient Plot, attach fake
access phones, and exercise the real _to_read/_to_summary mappers + model/repo
wiring.
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.orm import attributes

from app.api.v1.plots import _to_read, _to_summary
from app.db.models.plot import Plot
from app.db.models.plot_access_phone import PlotAccessPhone
from app.repositories import plot_repository as repo
from app.schemas.plot import PlotCreate, PlotRead, PlotSummary, PlotUpdate

_T0 = datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc)


def _phone(phone: str, access_type: str, is_active: bool = True, **o) -> SimpleNamespace:
    d = dict(
        id=uuid4(), phone_normalized=phone, access_type=access_type,
        is_active=is_active, created_at=_T0, updated_at=_T0,
    )
    d.update(o)
    return SimpleNamespace(**d)


def _plot(access_phones=None, **overrides) -> Plot:
    plot = Plot(
        supplier_id=uuid4(), plot_code="SUP001-P001", name="Plot One",
        province="เชียงใหม่", is_active=True,
    )
    plot.id = uuid4()
    plot.created_at = _T0
    plot.updated_at = _T0
    plot.assignments = []
    plot.supplier = SimpleNamespace(code="SUP001", name="Supplier One")
    attributes.set_committed_value(plot, "active_cycle", None)
    # viewonly relationship — plain assignment routes through the setter which
    # rejects non-ORM values, so set the committed value directly.
    attributes.set_committed_value(plot, "access_phones", access_phones or [])
    for k, v in overrides.items():
        setattr(plot, k, v)
    return plot


def test_schemas_expose_phone_fields_and_never_client_writable() -> None:
    for schema in (PlotSummary, PlotRead):
        assert "primary_phone" in schema.model_fields
        assert "additional_phones" in schema.model_fields
    for schema in (PlotCreate, PlotUpdate):
        assert "primary_phone" not in schema.model_fields
        assert "additional_phones" not in schema.model_fields


def test_to_read_populates_primary_and_additional() -> None:
    phones = [
        _phone("0845552162", "primary"),
        _phone("0812345678", "additional"),
        _phone("0912345678", "additional"),
    ]
    read = _to_read(_plot(access_phones=phones))
    assert read.primary_phone == "0845552162"
    assert read.additional_phones == ["0812345678", "0912345678"]


def test_to_summary_populates_primary_and_additional() -> None:
    phones = [_phone("0845552162", "primary"), _phone("0812345678", "additional")]
    s = _to_summary(_plot(access_phones=phones))
    assert s.primary_phone == "0845552162"
    assert s.additional_phones == ["0812345678"]


def test_no_primary_yields_null_primary() -> None:
    s = _to_summary(_plot(access_phones=[_phone("0812345678", "additional")]))
    assert s.primary_phone is None
    assert s.additional_phones == ["0812345678"]


def test_empty_when_no_phones() -> None:
    for target in (_to_read(_plot()), _to_summary(_plot())):
        assert target.primary_phone is None
        assert target.additional_phones == []


def test_inactive_phone_is_excluded() -> None:
    # Defense-in-depth: the relationship already filters is_active, and the
    # mapper drops any inactive row it's handed too.
    phones = [
        _phone("0845552162", "primary", is_active=True),
        _phone("0899999999", "additional", is_active=False),
    ]
    read = _to_read(_plot(access_phones=phones))
    assert read.primary_phone == "0845552162"
    assert read.additional_phones == []


def test_access_phones_relationship_is_filtered_to_active() -> None:
    rel = Plot.__mapper__.relationships["access_phones"]
    assert rel.viewonly is True
    compiled = str(rel.primaryjoin.compile(compile_kwargs={"literal_binds": True}))
    assert "is_active" in compiled


def test_repo_eager_loads_access_phones_no_n_plus_1() -> None:
    list_src = inspect.getsource(repo.list_plots)
    options_src = inspect.getsource(repo._plot_read_options)
    assert "selectinload(Plot.access_phones)" in list_src
    assert "selectinload(Plot.access_phones)" in options_src
    # create_plot refreshes it too (avoids an async lazy-load on a new plot).
    assert "access_phones" in inspect.getsource(repo.create_plot)


def test_record_relationship_to_access_phone_exists() -> None:
    from app.db.models.record import Record

    rel = Record.__mapper__.relationships["plot_access_phone"]
    assert rel.mapper.class_ is PlotAccessPhone

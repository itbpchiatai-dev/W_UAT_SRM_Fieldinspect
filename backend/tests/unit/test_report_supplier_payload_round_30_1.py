"""The Supplier's JSON really has no variety in it (round 30.1).

Round 30 set `response_model_exclude` on both Supplier routes and asserted the
route object carried it. It did — and it did nothing.

Both routes answer with `list[Row]`, and pydantic reads a bare set of names
against a SEQUENCE as a set of indices. "current_variety" is not an index, so
nothing matched, every row kept every field, and no error was raised anywhere.
The workbook was correct the whole time (it reads the hidden set directly), so
the failure was invisible from the server side: only a Supplier looking at the
screen could see it, and one did.

The lesson is what this file tests. Asserting that a route is CONFIGURED to hide
a field is not the same as asserting the payload lacks it — the first passed
while the second would have failed. So these tests serialize through FastAPI
using each real route's own response_model and response_model_exclude, and read
the resulting JSON.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import reports as reports_api
from app.schemas.report import ReportCycleYieldRow, ReportPlotStatusRow

VARIETY_VALUE = "แตงโม WM1224"
P_CODE_VALUE = "WM-078"


def _plot_status_row() -> ReportPlotStatusRow:
    return ReportPlotStatusRow(
        plot_id=uuid4(), supplier_code="23813", supplier_name="JP Produce Seed",
        plot_code="23813-2609-001", plot_name="นางอัมพร บุปผา", province="ขอนแก่น",
        current_crop="WM", current_variety=VARIETY_VALUE, current_stage="เจริญเติบโต",
        current_yield_pct=Decimal("100"),
        current_field_prep_score=10, current_weather_score=10,
        current_care_score=10, current_variety_resistance_score=10,
        expected_yield_full=Decimal("48"), expected_yield_unit="kg", plant_count=100,
        last_inspected_at=datetime.datetime(2026, 9, 25, tzinfo=datetime.timezone.utc),
        last_inspected_by_code="W01", is_inspected=True,
        p_code=P_CODE_VALUE,
    )


def _cycle_yield_row() -> ReportCycleYieldRow:
    return ReportCycleYieldRow(
        supplier_id=uuid4(), supplier_code="23813", supplier_name="JP Produce Seed",
        plot_id=uuid4(), plot_code="23813-2609-001", plot_name="นางอัมพร บุปผา",
        province="ขอนแก่น", plot_is_active=True,
        cycle_id=uuid4(), cycle_no=1, cycle_label="2609", cycle_status="harvested",
        crop="WM", variety=VARIETY_VALUE, lot_no="2609-23813-WM-078-001",
        planting_date=datetime.date(2026, 6, 1), plant_count=100,
        expected_yield_full=Decimal("48"), expected_yield_unit="kg",
        started_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        closed_at=datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc),
        close_reason="เก็บเกี่ยวเสร็จ",
        final_yield_pct=Decimal("100"), final_estimated_yield=Decimal("48"),
        final_inspection_record_id=uuid4(),
        harvest_yield=None, final_yield_after_clean=None, final_yield_unit=None,
        harvest_date=None, final_note=None,
        p_code=P_CODE_VALUE,
    )


# (route path, the row builder, the camelCase key that must be gone,
#  the camelCase key that must remain)
VARIANTS = [
    pytest.param("/supplier/plot-status", _plot_status_row,
                 "currentVariety", "pCode", id="plot-status"),
    pytest.param("/supplier/cycle-yield", _cycle_yield_row,
                 "variety", "pCode", id="cycle-yield"),
]


def _route(path: str):
    return next(
        r for r in reports_api.router.routes
        if getattr(r, "path", None) == path and "GET" in getattr(r, "methods", set())
    )


def _payload(path: str, row) -> list[dict]:
    """Serialize one row through a route that copies the REAL route's
    response_model and response_model_exclude — the two settings that decide
    what a Supplier's browser receives. The real route's auth and RLS
    dependencies are not part of that decision and are deliberately left out."""
    real = _route(path)
    app = FastAPI()

    @app.get(
        "/probe",
        response_model=real.response_model,
        response_model_exclude=real.response_model_exclude,
    )
    def probe():
        return [row]

    return TestClient(app).get("/probe").json()


@pytest.mark.parametrize("path,build,gone,kept", VARIANTS)
def test_the_supplier_payload_has_no_variety_key(path, build, gone, kept) -> None:
    body = _payload(path, build())
    assert len(body) == 1
    # The KEY is absent, not merely null: a null would still tell the reader
    # there is a variety field they are not being shown.
    assert gone not in body[0]


@pytest.mark.parametrize("path,build,gone,kept", VARIANTS)
def test_the_variety_VALUE_appears_nowhere_in_the_supplier_payload(
    path, build, gone, kept,
) -> None:
    """The check that survives a rename: whatever the field is called, the
    string a Supplier must not read is not in the bytes they receive."""
    import json

    assert VARIETY_VALUE not in json.dumps(_payload(path, build()), ensure_ascii=False)


@pytest.mark.parametrize("path,build,gone,kept", VARIANTS)
def test_the_p_code_survives_the_exclusion(path, build, gone, kept) -> None:
    """The exclusion must take the variety and nothing else — the P.Code is the
    column standing in its place, so losing it would be the same bug twice."""
    body = _payload(path, build())
    assert body[0].get(kept) == P_CODE_VALUE


@pytest.mark.parametrize("path,build,gone,kept", VARIANTS)
def test_the_rest_of_the_row_is_untouched(path, build, gone, kept) -> None:
    """A wrong exclusion shape could also drop everything or nothing. One field
    leaves; the row is otherwise whole."""
    row = build()
    body = _payload(path, row)
    expected = len(type(row).model_fields) - 1
    assert len(body[0]) == expected
    assert body[0]["supplierCode"] == "23813"
    assert body[0]["plotCode"] == "23813-2609-001"


@pytest.mark.parametrize("path,build,gone,kept", VARIANTS)
def test_a_bare_set_on_a_list_route_is_the_bug_this_file_exists_for(
    path, build, gone, kept,
) -> None:
    """Pin the actual defect, so nobody "simplifies" the wrapper away again.

    Handing the same names to a list route as a bare set excludes NOTHING — no
    error, no warning, every field still there. That is exactly what shipped."""
    real = _route(path)
    flat = frozenset(next(iter(real.response_model_exclude.values())))
    app = FastAPI()

    @app.get("/probe", response_model=real.response_model, response_model_exclude=flat)
    def probe():
        return [build()]

    body = TestClient(app).get("/probe").json()
    assert gone in body[0], "a bare set is expected to (wrongly) keep the field"


@pytest.mark.parametrize("path", [v.values[0] for v in VARIANTS])
def test_every_supplier_route_uses_the_per_item_shape(path: str) -> None:
    """Structural guard: a list route's exclusion must be keyed by __all__."""
    exclude = _route(path).response_model_exclude
    assert isinstance(exclude, dict), "a bare set silently does nothing on a list"
    assert set(exclude) == {"__all__"}


@pytest.mark.parametrize("path,name", [
    ("/supplier/plot-status", "SUPPLIER_HIDDEN_PLOT_STATUS"),
    ("/supplier/cycle-yield", "SUPPLIER_HIDDEN_CYCLE_YIELD"),
])
def test_the_route_still_hides_exactly_what_the_workbook_hides(
    path: str, name: str,
) -> None:
    """The reshaping must not become a second place to edit: the names inside
    __all__ are the report's own hidden set, unchanged."""
    exclude = _route(path).response_model_exclude
    assert exclude["__all__"] == set(getattr(reports_api, name))

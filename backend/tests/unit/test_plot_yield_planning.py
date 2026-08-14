"""Plot planning-field ownership lock (round 8.0.4): PlotCreate/PlotUpdate no
longer carry planting-cycle identity (current_crop/current_variety/
current_lot_no/current_planting_date) or yield-planning base data
(plant_count/expected_yield_full/expected_yield_unit) — those are PlotCycle-
owned now (see plot_cycle_repository.create_cycle/update_cycle +
sync_plot_mirror_from_cycle). A client that still sends one of these fields
gets a 422 (extra="forbid"), not a silent ignore.

PlotRead/PlotSummary keep exposing the mirror for backward-compatible reads —
that part of round 17 is unchanged.

Same mocking approach as test_plot_repository_inspection_code.py — no DB
fixture exists in this repo; only the add/flush/refresh I/O boundary is
mocked, since constructing/mutating a Plot() instance doesn't touch the DB.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.repositories.plot_repository import create_plot
from app.schemas.plot import PlotCreate, PlotRead, PlotSummary, PlotUpdate

_BASE = dict(supplier_id=uuid4(), plot_code="P001", name="Plot One")


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


# --- PlotCreate/PlotUpdate no longer carry planning fields -----------------

def test_plot_create_has_no_planning_fields() -> None:
    fields = set(PlotCreate.model_fields)
    assert not fields & {
        "current_crop", "current_variety", "current_lot_no", "current_planting_date",
        "plant_count", "expected_yield_full", "expected_yield_unit",
    }


def test_plot_update_has_no_planning_fields() -> None:
    fields = set(PlotUpdate.model_fields)
    assert not fields & {
        "current_crop", "current_variety", "current_lot_no", "current_planting_date",
        "plant_count", "expected_yield_full", "expected_yield_unit",
    }


@pytest.mark.parametrize("field,value", [
    ("current_crop", "พริก"),
    ("current_variety", "พริกขี้หนู"),
    ("current_lot_no", "LOT-01"),
    ("plant_count", 500),
    ("expected_yield_full", Decimal("1000.00")),
    ("expected_yield_unit", "kg"),
])
def test_plot_create_rejects_a_stray_planning_field(field: str, value) -> None:
    # extra="forbid" — a client still sending a planning field gets a clean
    # 422 (ValidationError at the FastAPI request-body layer), never a
    # silent ignore. Covers both the snake_case attr name (this test) and
    # the camelCase alias FastAPI actually receives over the wire (next).
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        PlotCreate(**_BASE, **{field: value})


def test_plot_create_rejects_a_stray_planning_field_via_camelcase_alias() -> None:
    # Same as above but through model_validate with the camelCase keys the
    # real HTTP request body uses (currentCrop/expectedYieldFull) — proves
    # extra="forbid" catches the field regardless of alias vs attr name.
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        PlotCreate.model_validate({
            "supplierId": str(_BASE["supplier_id"]),
            "plotCode": _BASE["plot_code"],
            "name": _BASE["name"],
            "currentCrop": "พริก",
            "expectedYieldFull": "1000.00",
        })


def test_plot_update_rejects_a_stray_planning_field() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        PlotUpdate(plant_count=800, expected_yield_full=Decimal("500.50"))


def test_plot_update_rejects_a_stray_planning_field_via_camelcase_alias() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        PlotUpdate.model_validate({"currentVariety": "เมล่อนญี่ปุ่น"})


# --- extra="forbid" doesn't break legitimate camelCase physical fields -----

def test_plot_create_still_accepts_physical_fields_via_camelcase_alias() -> None:
    # Regression guard for the extra="forbid" addition — physical (still
    # allowed) fields must keep working through their camelCase alias, both
    # by attribute name (populate_by_name) and by the alias FastAPI parses
    # a real request body with.
    plot = PlotCreate.model_validate({
        "supplierId": str(_BASE["supplier_id"]),
        "plotCode": "P002",
        "name": "แปลง B",
        "village": "ต.ตัวอย่าง",
        "rai": "5.5",
    })
    assert plot.plot_code == "P002"
    assert plot.village == "ต.ตัวอย่าง"
    assert plot.rai == Decimal("5.5")


def test_plot_update_still_accepts_physical_fields_via_snake_case_attr() -> None:
    update = PlotUpdate(name="แปลงใหม่", province="เชียงใหม่")
    assert update.name == "แปลงใหม่"
    assert update.province == "เชียงใหม่"
    assert "plant_count" not in update.model_fields_set


# --- create_plot repository no longer writes planning columns --------------

async def test_create_plot_leaves_planning_columns_null() -> None:
    plot = await create_plot(_mock_db(), PlotCreate(**_BASE))
    assert plot.current_crop is None
    assert plot.current_lot_no is None
    assert plot.plant_count is None
    assert plot.expected_yield_full is None
    assert plot.expected_yield_unit is None


# --- read models still expose the PlotCycle-owned mirror (unchanged) -------

def test_plot_read_exposes_yield_planning_fields() -> None:
    fields = set(PlotRead.model_fields)
    assert {"plant_count", "expected_yield_full", "expected_yield_unit"} <= fields


def test_plot_summary_exposes_yield_summary_fields() -> None:
    """Plots list needs these (no current_expected_yield stored field —
    computed client-side from expected_yield_full * current_yield_pct / 100)."""
    fields = set(PlotSummary.model_fields)
    assert {"current_yield_pct", "expected_yield_full", "expected_yield_unit", "plant_count"} <= fields


def test_plot_summary_exposes_planting_cycle_master_fields() -> None:
    """Round 18 — Plots list shows compact crop/variety/lot/planting-date
    without a per-plot fetch."""
    fields = set(PlotSummary.model_fields)
    assert {"current_crop", "current_variety", "current_lot_no", "current_planting_date"} <= fields

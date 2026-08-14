"""report_repository.plot_status_rows sources identity + yield plan from the
plot's ACTIVE cycle (round 7.4), null when the plot is between cycles — while
the inspection-derived snapshot (stage/yield%/scores/last-inspection) stays
from the plot mirror. DB-free: mocks db.execute to return
(plot, supplier_code, supplier_name) tuples so the row-building logic is
exercised without a database.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.repositories.report_repository import plot_status_rows


def _cycle(**overrides) -> SimpleNamespace:
    defaults = dict(
        id=uuid4(), cycle_no=3, status="active",
        crop="พริก", variety="พริกขี้หนู", lot_no="LOT-03",
        planting_date=datetime.date(2026, 6, 1), plant_count=500,
        expected_yield_full=Decimal("1000.00"), expected_yield_unit="kg",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _plot(*, active_cycle, **overrides) -> SimpleNamespace:
    defaults = dict(
        id=uuid4(), plot_code="P001", name="แปลงหนึ่ง", province="เชียงใหม่",
        active_cycle=active_cycle,
        # inspection-derived snapshot (mirror columns)
        current_stage="ออกดอก", current_yield_pct=Decimal("80"),
        current_field_prep_score=8, current_weather_score=7,
        current_care_score=6, current_variety_resistance_score=5,
        last_inspected_at=datetime.datetime(2026, 6, 15, tzinfo=datetime.timezone.utc),
        last_inspected_by_code="W01",
        last_inspection_record_id=uuid4(),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_db(rows: list) -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.anyio
async def test_active_cycle_sources_identity_and_plan() -> None:
    cycle = _cycle()
    db = _mock_db([(_plot(active_cycle=cycle), "SUP001", "Supplier One")])

    rows = await plot_status_rows(db)

    assert len(rows) == 1
    row = rows[0]
    # cycle presence signal
    assert row.active_cycle_id == cycle.id
    assert row.active_cycle_no == 3
    assert row.active_cycle_status == "active"
    # identity + plan come from the cycle
    assert row.current_crop == "พริก"
    assert row.current_variety == "พริกขี้หนู"
    assert row.expected_yield_full == Decimal("1000.00")
    assert row.expected_yield_unit == "kg"
    assert row.plant_count == 500
    # inspection snapshot stays from the plot mirror
    assert row.current_stage == "ออกดอก"
    assert row.current_yield_pct == Decimal("80")
    assert row.current_field_prep_score == 8
    assert row.is_inspected is True


@pytest.mark.anyio
async def test_no_active_cycle_nulls_identity_and_plan() -> None:
    # A plot between cycles: the mirror was cleared on close, so identity/plan
    # report null and the frontend shows "รอเริ่มรอบปลูก" off active_cycle_id.
    plot = _plot(
        active_cycle=None,
        current_stage=None, current_yield_pct=None,
        current_field_prep_score=None, current_weather_score=None,
        current_care_score=None, current_variety_resistance_score=None,
        last_inspected_at=None, last_inspected_by_code=None,
        last_inspection_record_id=None,
    )
    db = _mock_db([(plot, "SUP001", "Supplier One")])

    rows = await plot_status_rows(db)
    row = rows[0]
    assert row.active_cycle_id is None
    assert row.active_cycle_no is None
    assert row.active_cycle_status is None
    assert row.current_crop is None
    assert row.current_variety is None
    assert row.expected_yield_full is None
    assert row.expected_yield_unit is None
    assert row.plant_count is None
    assert row.is_inspected is False

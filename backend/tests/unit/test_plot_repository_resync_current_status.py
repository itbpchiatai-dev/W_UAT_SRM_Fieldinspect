"""plot_repository.resync_current_status_from_latest — snapshot follows the
latest ACTIVE record OF THE PLOT'S CURRENT ACTIVE CYCLE (round 8.0.5
cycle-aware lock) after a mutation (deactivate — round 8.0.5 removed PATCH).

No DB fixture exists in this repo — mocks the record query, get_plot and a
fake DB session, matching tests/unit/test_plot_repository_sync_current_status.py.
"""
from __future__ import annotations

import datetime
import inspect
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.repositories import plot_repository as repo

_MODULE = "app.repositories.plot_repository"

_UNSET = object()


def _cycle(**overrides):
    defaults = dict(id=uuid4())
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _plot(**overrides):
    active_cycle = overrides.pop("active_cycle", _UNSET)
    if active_cycle is _UNSET:
        active_cycle = _cycle()
    defaults = dict(
        id=uuid4(),
        active_cycle=active_cycle,
        # Plot MASTER / planting-cycle fields — must survive a resync-to-empty
        # untouched (round 17.1 field-ownership split; round 8.0.4 relocated
        # ownership to PlotCycle, but the "never touched by resync" rule
        # still holds).
        current_crop="มะละกอ", current_variety="มะละกอฮอลแลนด์", current_lot_no="EXISTING-LOT",
        current_planting_date=datetime.date(2026, 5, 1),
        # Inspection-derived snapshot fields, pre-filled as if a (now
        # deactivated) record had synced them.
        current_stage="ออกดอก", current_yield_pct=Decimal("95.5"),
        current_field_prep_score=8, current_weather_score=7,
        current_care_score=6, current_variety_resistance_score=5,
        current_gps_lat=Decimal("13.7563"), current_gps_lng=Decimal("100.5018"),
        last_inspected_at=datetime.datetime(2026, 7, 4, 10, 30, tzinfo=datetime.timezone.utc),
        last_inspected_by_code="FIELD007",
        last_inspection_record_id=uuid4(),
        plant_count=500, expected_yield_full=Decimal("1000.00"), expected_yield_unit="kg",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _record(plot, **overrides):
    defaults = dict(
        id=uuid4(), plot_id=plot.id, is_active=True,
        # Round 8.0.5 — must match plot.active_cycle.id for a "found" test;
        # override explicitly to simulate an off-cycle record.
        plot_cycle_id=plot.active_cycle.id if getattr(plot, "active_cycle", None) else uuid4(),
        crop="พริก", variety="พริกขี้หนู",
        planting_date=datetime.date(2026, 6, 1),
        growth_stage="เก็บเกี่ยว",
        yield_pct=Decimal("80.0"),
        field_prep_score=4, weather_score=3, care_score=2, variety_resistance_score=1,
        latitude=Decimal("14.0"), longitude=Decimal("101.0"),
        submitted_by_code="FIELD001",
        created_at=datetime.datetime(2026, 7, 1, 9, 0, tzinfo=datetime.timezone.utc),
        record_date=datetime.date(2026, 6, 30),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_db(latest_record=None) -> MagicMock:
    """DB whose one execute() call answers the latest-active-record query."""
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = latest_record
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


async def test_delegates_to_sync_with_the_latest_active_record_of_the_active_cycle() -> None:
    plot = _plot()
    latest = _record(plot)
    db = _mock_db(latest_record=latest)

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.sync_current_status_from_record",
               AsyncMock(return_value=plot)) as mocked_sync:
        result = await repo.resync_current_status_from_latest(db, plot.id)

    assert result is plot
    mocked_sync.assert_awaited_once()
    sync_args, _ = mocked_sync.call_args
    # The exact record the query returned — not a re-fetch, not the plot.
    assert sync_args[1] is latest


async def test_query_scoped_to_active_cycle_newest_created_first() -> None:
    """The "latest" pick must exclude deactivated records, exclude records
    outside the plot's active cycle, and order by created_at (monotonic
    insert time — record_date can be backdated), matching
    sync_current_status_from_record's semantic. The query runs against a
    mocked session here, so pin the statement's shape at the source level
    instead."""
    src = inspect.getsource(repo.resync_current_status_from_latest)
    assert "Record.is_active.is_(True)" in src
    assert "Record.plot_cycle_id == active_cycle.id" in src
    assert "Record.created_at.desc()" in src
    assert ".limit(1)" in src
    # The docstring may explain record_date; the query must never USE it.
    assert "Record.record_date" not in src


async def test_clears_inspection_derived_fields_when_no_active_record_remains() -> None:
    plot = _plot()
    db = _mock_db(latest_record=None)

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=plot)):
        result = await repo.resync_current_status_from_latest(db, plot.id)

    assert result is plot
    assert plot.current_stage is None
    assert plot.current_yield_pct is None
    assert plot.current_field_prep_score is None
    assert plot.current_weather_score is None
    assert plot.current_care_score is None
    assert plot.current_variety_resistance_score is None
    assert plot.current_gps_lat is None
    assert plot.current_gps_lng is None
    assert plot.last_inspected_at is None
    assert plot.last_inspected_by_code is None
    assert plot.last_inspection_record_id is None
    db.flush.assert_awaited()


async def test_clearing_never_touches_master_or_yield_planning_fields() -> None:
    """Same round 17.1/8.0.4 field-ownership split the sync function
    observes: current_crop/variety/lot_no/planting_date and the
    yield-planning trio are PlotCycle-owned — resync-to-empty must leave
    them exactly as they were."""
    plot = _plot()
    db = _mock_db(latest_record=None)

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=plot)):
        await repo.resync_current_status_from_latest(db, plot.id)

    assert plot.current_crop == "มะละกอ"
    assert plot.current_variety == "มะละกอฮอลแลนด์"
    assert plot.current_lot_no == "EXISTING-LOT"
    assert plot.current_planting_date == datetime.date(2026, 5, 1)
    assert plot.plant_count == 500
    assert plot.expected_yield_full == Decimal("1000.00")
    assert plot.expected_yield_unit == "kg"


async def test_raises_if_plot_not_found_when_clearing() -> None:
    """Must propagate so the caller's transaction (including the record
    mutation that triggered the resync) rolls back — same contract as
    sync_current_status_from_record."""
    db = _mock_db(latest_record=None)

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=None)):
        with pytest.raises(ValueError):
            await repo.resync_current_status_from_latest(db, uuid4())

    db.flush.assert_not_awaited()


# --- round 8.0.5: cycle-aware resync ----------------------------------------

async def test_no_active_cycle_clears_snapshot_without_querying_records() -> None:
    """No active cycle at all → straight to clearing; never queries records
    (there's nothing to scope the query to)."""
    plot = _plot(active_cycle=None)
    db = _mock_db(latest_record=None)

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=plot)):
        result = await repo.resync_current_status_from_latest(db, plot.id)

    assert result is plot
    assert plot.current_stage is None
    assert plot.last_inspection_record_id is None
    db.execute.assert_not_awaited()


async def test_active_cycle_with_no_records_of_its_own_clears_not_falls_back() -> None:
    """The active cycle has zero active records — even if an older/closed
    cycle has plenty, resync must clear rather than reach back into it. The
    mocked db.execute stands in for "the cycle-scoped query found nothing";
    a query that ignored the cycle filter would (in a real DB) have found
    the old cycle's record instead."""
    plot = _plot()
    db = _mock_db(latest_record=None)

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=plot)):
        result = await repo.resync_current_status_from_latest(db, plot.id)

    assert result is plot
    assert plot.current_stage is None
    assert plot.current_yield_pct is None
    assert plot.last_inspection_record_id is None

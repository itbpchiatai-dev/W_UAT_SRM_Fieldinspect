"""plot_repository.sync_current_status_from_record — round 12, field
ownership locked down round 17.1.

No DB fixture exists in this repo — mocks get_plot and a fake DB session,
exercising the real field-mapping logic directly.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.repositories import plot_repository as repo

_MODULE = "app.repositories.plot_repository"


def _cycle(**overrides):
    defaults = dict(id=uuid4())
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_UNSET = object()


def _plot(**overrides):
    active_cycle = overrides.pop("active_cycle", _UNSET)
    if active_cycle is _UNSET:
        active_cycle = _cycle()
    defaults = dict(
        id=uuid4(),
        # Round 8.0.5 — sync_current_status_from_record now requires the
        # record's plot_cycle_id to match this. eager-loaded via get_plot's
        # selectinload(Plot.active_cycle) in the real repository.
        active_cycle=active_cycle,
        # Plot MASTER / planting-cycle fields (round 17, ownership locked
        # down round 17.1) — set here to admin-chosen values distinct from
        # whatever the record below carries, so tests can prove sync never
        # overwrites them.
        current_crop="มะละกอ", current_variety="มะละกอฮอลแลนด์", current_lot_no="EXISTING-LOT",
        current_planting_date=datetime.date(2026, 5, 1),
        # True inspection-derived snapshot fields.
        current_stage=None, current_yield_pct=None,
        current_field_prep_score=None, current_weather_score=None,
        current_care_score=None, current_variety_resistance_score=None,
        current_gps_lat=None, current_gps_lng=None,
        last_inspected_at=None, last_inspected_by_code=None,
        last_inspection_record_id=None,
        # Yield-planning master data (round 17) — no records column sources
        # these; sync must never touch them, same as the plot-master fields
        # above.
        plant_count=500, expected_yield_full=Decimal("1000.00"), expected_yield_unit="kg",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _record(plot, **overrides):
    defaults = dict(
        id=uuid4(), plot_id=plot.id,
        # Round 8.0.5 — must match plot.active_cycle.id or sync refuses.
        plot_cycle_id=plot.active_cycle.id if getattr(plot, "active_cycle", None) else uuid4(),
        # Deliberately different from _plot()'s master crop/variety/
        # planting_date above — the whole point is proving these values
        # never leak into the plot's master columns.
        crop="พริก", variety="พริกขี้หนู",
        planting_date=datetime.date(2026, 6, 1),
        growth_stage="ออกดอก",
        yield_pct=Decimal("95.5"),
        field_prep_score=8, weather_score=7, care_score=6, variety_resistance_score=5,
        latitude=Decimal("13.7563"), longitude=Decimal("100.5018"),
        submitted_by_code="FIELD007",
        created_at=datetime.datetime(2026, 7, 4, 10, 30, tzinfo=datetime.timezone.utc),
        record_date=datetime.date(2026, 7, 3),  # deliberately different from created_at's date
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


async def test_copies_inspection_derived_fields_from_record_onto_plot() -> None:
    plot = _plot()
    record = _record(plot)
    db = _mock_db()

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=plot)):
        result = await repo.sync_current_status_from_record(db, record)

    assert result is plot
    assert plot.current_stage == "ออกดอก"
    assert plot.current_yield_pct == Decimal("95.5")
    assert plot.current_field_prep_score == 8
    assert plot.current_weather_score == 7
    assert plot.current_care_score == 6
    assert plot.current_variety_resistance_score == 5
    assert plot.current_gps_lat == Decimal("13.7563")
    assert plot.current_gps_lng == Decimal("100.5018")
    assert plot.last_inspected_by_code == "FIELD007"
    assert plot.last_inspection_record_id == record.id
    db.flush.assert_awaited()


async def test_syncs_yield_pct_over_150_verbatim() -> None:
    """Round 8-8B.1 — 150% used to be the effective business ceiling; a real
    harvest over plan must still sync onto plots.current_yield_pct verbatim
    (this function has no range check of its own — always overwrites
    unconditionally — so no code change was needed, only this explicit
    regression proof)."""
    plot = _plot()
    record = _record(plot, yield_pct=Decimal("510.0"))
    db = _mock_db()

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=plot)):
        await repo.sync_current_status_from_record(db, record)

    assert plot.current_yield_pct == Decimal("510.0")


async def test_last_inspected_at_uses_created_at_not_record_date() -> None:
    """record_date can be backdated by the field worker; created_at (actual
    insert time) is what keeps the snapshot monotonically advancing."""
    plot = _plot()
    record = _record(plot)
    db = _mock_db()

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=plot)):
        await repo.sync_current_status_from_record(db, record)

    assert plot.last_inspected_at == record.created_at
    assert plot.last_inspected_at != record.record_date


async def test_master_planting_cycle_fields_are_never_touched() -> None:
    """Round 17.1 — current_crop/current_variety/current_lot_no/
    current_planting_date are plot MASTER data, set only via Plot
    Create/Edit. Before this fix, sync copied crop/variety/planting_date
    straight from the record, silently clobbering whatever an admin had
    set the moment any inspection was submitted. The record here
    deliberately carries different crop/variety/planting_date than the
    plot already has (see _plot()/_record() above) — none of it must land
    on the plot."""
    plot = _plot()
    record = _record(plot)
    db = _mock_db()

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=plot)):
        await repo.sync_current_status_from_record(db, record)

    assert plot.current_crop == "มะละกอ"
    assert plot.current_variety == "มะละกอฮอลแลนด์"
    assert plot.current_lot_no == "EXISTING-LOT"
    assert plot.current_planting_date == datetime.date(2026, 5, 1)


async def test_master_planting_cycle_fields_stay_none_if_never_set() -> None:
    """A brand-new plot with no admin-entered master data yet must stay
    None after its first inspection — sync must never backfill these from
    the record either."""
    plot = _plot(current_crop=None, current_variety=None, current_lot_no=None, current_planting_date=None)
    record = _record(plot)
    db = _mock_db()

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=plot)):
        await repo.sync_current_status_from_record(db, record)

    assert plot.current_crop is None
    assert plot.current_variety is None
    assert plot.current_lot_no is None
    assert plot.current_planting_date is None


async def test_yield_planning_fields_are_never_touched() -> None:
    """plant_count/expected_yield_full/expected_yield_unit (round 17) have
    no records column to source them from — sync must leave them exactly
    as they were, same treatment as the plot-master fields above."""
    plot = _plot(plant_count=250, expected_yield_full=Decimal("400.00"), expected_yield_unit="ตัน")
    record = _record(plot)
    db = _mock_db()

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=plot)):
        await repo.sync_current_status_from_record(db, record)

    assert plot.plant_count == 250
    assert plot.expected_yield_full == Decimal("400.00")
    assert plot.expected_yield_unit == "ตัน"


async def test_raises_if_plot_not_found() -> None:
    """Must propagate (not swallow) so the caller's transaction rolls back
    the record insert too — see get_db's commit/rollback wrapper."""
    record = _record(_plot())
    db = _mock_db()

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=None)):
        with pytest.raises(ValueError):
            await repo.sync_current_status_from_record(db, record)

    db.flush.assert_not_awaited()


async def test_inspection_derived_fields_overwrite_unconditionally() -> None:
    """No 'is this newer' comparison for the true inspection-derived
    fields — the caller decides when to call this, always reflecting
    whichever record was just created. (Plot-master fields are exempt from
    this entirely — see test_master_planting_cycle_fields_are_never_touched.)"""
    plot = _plot(
        current_stage="เก็บเกี่ยว",
        last_inspected_at=datetime.datetime(2099, 1, 1, tzinfo=datetime.timezone.utc),
    )
    record = _record(plot)
    db = _mock_db()

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=plot)):
        await repo.sync_current_status_from_record(db, record)

    assert plot.current_stage == "ออกดอก"
    assert plot.last_inspected_at == record.created_at


# --- round 8.0.5: cycle-aware guard ----------------------------------------

async def test_raises_when_record_belongs_to_a_different_cycle_than_active() -> None:
    """A record from a closed/older cycle must never sync onto the plot —
    raises instead of silently skipping so the caller's transaction rolls
    back rather than leaving a record inserted with a mismatched snapshot."""
    plot = _plot()  # active_cycle = _cycle() with a fresh id
    old_cycle_record = _record(plot, plot_cycle_id=uuid4())  # a DIFFERENT cycle
    db = _mock_db()

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=plot)):
        with pytest.raises(ValueError):
            await repo.sync_current_status_from_record(db, old_cycle_record)

    db.flush.assert_not_awaited()


async def test_raises_when_plot_has_no_active_cycle_at_all() -> None:
    plot = _plot(active_cycle=None)
    record = _record(plot, plot_cycle_id=uuid4())
    db = _mock_db()

    with patch(f"{_MODULE}.get_plot_for_update", AsyncMock(return_value=plot)):
        with pytest.raises(ValueError):
            await repo.sync_current_status_from_record(db, record)

    db.flush.assert_not_awaited()

"""Round 7.1.1 — seed scripts are cycle-aware after migration 0034 made
records.plot_cycle_id NOT NULL. Behavior test of the shared helper +
source inspection that every seed that builds Record() rows sets plot_cycle_id.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.db import seed_helpers

_SEED_DIR = Path(seed_helpers.__file__).resolve().parent
_SEED_FILES = [
    "seed.py",
    "seed_mock_farmlog.py",
    "seed_mock_records_extra.py",
    "seed_reset_farmlog_full.py",
]


def _plot(**overrides):
    defaults = dict(
        id=uuid4(), current_crop=None, current_variety=None, current_lot_no=None,
        current_planting_date=None, plant_count=None, expected_yield_full=None,
        expected_yield_unit=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_build_active_cycle_mirrors_plot_master_fields() -> None:
    plot = _plot(
        current_crop="เมล่อน", current_variety="ญี่ปุ่น", current_lot_no="L1",
        current_planting_date=datetime.date(2026, 5, 1), plant_count=100,
    )
    cycle = seed_helpers.build_active_cycle(plot)
    assert cycle.plot_id == plot.id
    assert cycle.cycle_no == 1
    assert cycle.status == "active"
    assert cycle.crop == "เมล่อน"
    assert cycle.variety == "ญี่ปุ่น"
    assert cycle.lot_no == "L1"
    assert cycle.planting_date == datetime.date(2026, 5, 1)
    assert cycle.plant_count == 100
    # started_at derived from planting date when present
    assert cycle.started_at.date() == datetime.date(2026, 5, 1)


def test_build_active_cycle_started_at_falls_back_to_now() -> None:
    cycle = seed_helpers.build_active_cycle(_plot())  # no planting date
    assert cycle.started_at is not None


async def test_active_cycle_map_returns_existing_and_creates_missing() -> None:
    p1 = _plot(current_crop="A")
    p2 = _plot(current_crop="B")
    existing = SimpleNamespace(plot_id=p1.id)  # p1 already has an active cycle

    result = MagicMock()
    result.scalars.return_value.all.return_value = [existing]
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    session.flush = AsyncMock()

    mp = await seed_helpers.active_cycle_map(session, [p1, p2])

    assert mp[p1.id] is existing              # reused, not recreated
    assert mp[p2.id].plot_id == p2.id         # created for the missing one
    assert mp[p2.id].status == "active"
    session.add.assert_called_once()          # only p2 got a new cycle
    session.flush.assert_awaited_once()


def test_every_seed_that_builds_records_sets_plot_cycle_id() -> None:
    for name in _SEED_FILES:
        src = (_SEED_DIR / name).read_text(encoding="utf-8")
        record_ctors = len(re.findall(r"Record\(\s*\n", src))
        if record_ctors == 0:
            continue
        assert "active_cycle_map" in src, f"{name} builds Record() but doesn't import the cycle helper"
        cycle_assigns = src.count("plot_cycle_id=cycle_by_plot")
        assert cycle_assigns >= record_ctors, (
            f"{name}: {record_ctors} Record() ctor(s) but only {cycle_assigns} "
            f"plot_cycle_id assignment(s)"
        )

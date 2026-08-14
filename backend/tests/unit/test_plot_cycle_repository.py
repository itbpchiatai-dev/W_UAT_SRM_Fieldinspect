"""plot_cycle_repository — round 7.1 helpers (get active / create / close /
mirror / clear-snapshot). No DB fixture: query helpers mock db.execute; the
mutation helpers run for real against SimpleNamespace stand-ins with a mock
db so the exact field-ownership split (mirror vs inspection snapshot) is
verifiable.
"""
from __future__ import annotations

import datetime
import inspect
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.repositories import plot_cycle_repository as repo

_MOD = "app.repositories.plot_cycle_repository"


def _result(value):
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=value)
    return r


async def test_get_active_cycle_returns_the_row() -> None:
    cycle = SimpleNamespace(id=uuid4(), status="active")
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(cycle))
    assert await repo.get_active_cycle_for_plot(db, uuid4()) is cycle


async def test_get_active_cycle_none_when_absent() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(None))
    assert await repo.get_active_cycle_for_plot(db, uuid4()) is None


def test_get_active_cycle_filters_on_active_status() -> None:
    src = inspect.getsource(repo.get_active_cycle_for_plot)
    assert "CYCLE_STATUS_ACTIVE" in src
    assert "PlotCycle.status" in src


def test_get_cycles_orders_newest_cycle_first() -> None:
    src = inspect.getsource(repo.get_cycles_for_plot)
    assert "cycle_no.desc()" in src


async def test_create_cycle_next_number_active_and_syncs_mirror() -> None:
    plot = SimpleNamespace(id=uuid4())
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=3)), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()) as mocked_sync:
        cycle = await repo.create_cycle(
            db, plot, crop="เมล่อน", variety="V", lot_no="L1",
            planting_date=datetime.date(2026, 5, 1), plant_count=100,
            expected_yield_full=Decimal("800.00"), expected_yield_unit="kg",
        )

    assert cycle.cycle_no == 3
    assert cycle.status == "active"
    assert cycle.plot_id == plot.id
    assert cycle.crop == "เมล่อน"
    assert cycle.started_at is not None
    db.add.assert_called_once()
    mocked_sync.assert_awaited_once()
    _, plot_arg, cycle_arg = mocked_sync.call_args[0]
    assert plot_arg is plot and cycle_arg is cycle


async def test_close_cycle_sets_terminal_status_and_close_fields() -> None:
    cycle = SimpleNamespace(
        id=uuid4(), status="active", closed_at=None, closed_by_id=None, close_reason=None,
        expected_yield_full=None, final_yield_pct="X", final_estimated_yield="X",
        final_inspection_record_id="X",
    )
    db = MagicMock()
    db.flush = AsyncMock()
    # No inspection for this cycle → snapshot clears the final_* fields.
    db.execute = AsyncMock(return_value=_result(None))
    uid = uuid4()

    out = await repo.close_cycle(db, cycle, status="harvested", closed_by_id=uid, reason="done")

    assert out.status == "harvested"
    assert out.closed_by_id == uid
    assert out.close_reason == "done"
    assert out.closed_at is not None
    # round 8-2.8A: snapshot ran (no inspection → all NULL)
    assert out.final_yield_pct is None
    assert out.final_estimated_yield is None
    assert out.final_inspection_record_id is None
    db.flush.assert_awaited_once()


@pytest.mark.parametrize("bad_status", ["active", "paused", ""])
async def test_close_cycle_rejects_non_terminal_status(bad_status: str) -> None:
    db = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    with pytest.raises(ValueError):
        await repo.close_cycle(db, SimpleNamespace(), status=bad_status, closed_by_id=None)
    db.flush.assert_not_awaited()
    # snapshot never runs for an invalid status (status check is first)
    db.execute.assert_not_awaited()


# --- round 8-2.8A: final estimated-yield snapshot on close ------------------

def _rec(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), yield_pct=Decimal("100.0"), is_active=True,
        created_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _cycle_ns(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), status="active", closed_at=None, closed_by_id=None, close_reason=None,
        expected_yield_full=Decimal("1000.00"),
        final_yield_pct=None, final_estimated_yield=None, final_inspection_record_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _db_with_latest(record) -> MagicMock:
    db = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock(return_value=_result(record))
    return db


@pytest.mark.parametrize("expected,pct,estimate", [
    (Decimal("1000.00"), Decimal("100.0"), Decimal("1000.00")),
    (Decimal("1000.00"), Decimal("50.0"), Decimal("500.00")),
    (Decimal("1000.00"), Decimal("109.0"), Decimal("1090.00")),
    (Decimal("1.00"), Decimal("50.0"), Decimal("0.50")),
    (Decimal("0.00"), Decimal("50.0"), Decimal("0.00")),      # expected 0 → 0.00
    (Decimal("1000.00"), Decimal("0.0"), Decimal("0.00")),     # yield 0% → 0.00, not NULL
])
async def test_snapshot_estimate_arithmetic(expected, pct, estimate) -> None:
    cycle = _cycle_ns(expected_yield_full=expected)
    rec = _rec(yield_pct=pct)
    await repo._snapshot_final_estimate(_db_with_latest(rec), cycle)
    assert cycle.final_yield_pct == pct
    assert cycle.final_estimated_yield == estimate
    assert cycle.final_inspection_record_id == rec.id
    # exactly 2 decimal places
    assert cycle.final_estimated_yield.as_tuple().exponent == -2


async def test_snapshot_no_inspection_all_null() -> None:
    cycle = _cycle_ns(final_yield_pct="X", final_estimated_yield="X", final_inspection_record_id="X")
    await repo._snapshot_final_estimate(_db_with_latest(None), cycle)
    assert cycle.final_yield_pct is None
    assert cycle.final_estimated_yield is None
    assert cycle.final_inspection_record_id is None


async def test_snapshot_yield_pct_null_keeps_source_but_null_estimate() -> None:
    # yield_pct NULL → estimate NULL, final_yield_pct NULL, but the source
    # record id is still recorded (traceability).
    cycle = _cycle_ns()
    rec = _rec(yield_pct=None)
    await repo._snapshot_final_estimate(_db_with_latest(rec), cycle)
    assert cycle.final_yield_pct is None
    assert cycle.final_estimated_yield is None
    assert cycle.final_inspection_record_id == rec.id


# --- round 8-8B.1: final snapshot supports Yield > 150% (warning, not cap) --

async def test_snapshot_supports_yield_pct_over_150_verbatim() -> None:
    """150% used to be records.yield_pct's effective business ceiling; a real
    harvest over plan (round 8-8B.1) must still snapshot into
    final_yield_pct verbatim — this function never validated a range at all,
    so no code change was needed here, only this explicit regression proof."""
    cycle = _cycle_ns(expected_yield_full=Decimal("1000.00"))
    rec = _rec(yield_pct=Decimal("510.0"))
    await repo._snapshot_final_estimate(_db_with_latest(rec), cycle)
    assert cycle.final_yield_pct == Decimal("510.0")
    assert cycle.final_estimated_yield == Decimal("5100.00")


async def test_snapshot_supports_yield_pct_at_9999_point_9_without_overflow() -> None:
    """A realistic-magnitude target combined with the maximum storable
    percentage must not crash/overflow when computing the ESTIMATE column
    (NUMERIC(14,2) — comfortably larger than NUMERIC(5,1), round 8-8B.1
    Part E)."""
    cycle = _cycle_ns(expected_yield_full=Decimal("1000.00"))
    rec = _rec(yield_pct=Decimal("9999.9"))
    await repo._snapshot_final_estimate(_db_with_latest(rec), cycle)
    assert cycle.final_yield_pct == Decimal("9999.9")
    assert cycle.final_estimated_yield == Decimal("99999.00")


async def test_snapshot_expected_null_sets_pct_but_null_estimate() -> None:
    cycle = _cycle_ns(expected_yield_full=None)
    rec = _rec(yield_pct=Decimal("75.0"))
    await repo._snapshot_final_estimate(_db_with_latest(rec), cycle)
    assert cycle.final_yield_pct == Decimal("75.0")
    assert cycle.final_estimated_yield is None
    assert cycle.final_inspection_record_id == rec.id


def test_snapshot_selects_latest_active_of_this_cycle_by_created_at() -> None:
    """Round 8-7A refactor: the query itself moved into the shared
    get_latest_active_record_for_cycle helper (also reused by the
    final_plot Excel action) — _snapshot_final_estimate now just calls it."""
    src = inspect.getsource(repo.get_latest_active_record_for_cycle)
    assert "Record.plot_cycle_id == cycle_id" in src   # only THIS cycle's records
    assert "Record.is_active.is_(True)" in src          # ignore inactive
    assert "created_at.desc()" in src                   # latest by created_at
    assert ".limit(1)" in src
    snapshot_src = inspect.getsource(repo._snapshot_final_estimate)
    assert "get_latest_active_record_for_cycle(db, cycle.id)" in snapshot_src
    # inspect the CODE only (the docstring legitimately names record_date /
    # current_yield_pct to explain why they are NOT used).
    body = snapshot_src.split('"""')[2]
    assert "record_date" not in body                    # never record_date
    assert "current_yield_pct" not in body              # never the plot mirror


async def test_close_cycle_snapshots_for_harvested() -> None:
    cycle = _cycle_ns()
    rec = _rec(yield_pct=Decimal("80.0"))
    await repo.close_cycle(_db_with_latest(rec), cycle, status="harvested", closed_by_id=uuid4())
    assert cycle.status == "harvested"
    assert cycle.final_yield_pct == Decimal("80.0")
    assert cycle.final_estimated_yield == Decimal("800.00")
    assert cycle.final_inspection_record_id == rec.id


async def test_close_cycle_snapshots_for_cancelled() -> None:
    # cancelled also freezes the last estimate ("ประมาณการล่าสุดก่อนยกเลิก")
    cycle = _cycle_ns()
    rec = _rec(yield_pct=Decimal("40.0"))
    await repo.close_cycle(_db_with_latest(rec), cycle, status="cancelled", closed_by_id=uuid4())
    assert cycle.status == "cancelled"
    assert cycle.final_yield_pct == Decimal("40.0")
    assert cycle.final_estimated_yield == Decimal("400.00")
    assert cycle.final_inspection_record_id == rec.id


# --- round 8-7A.1: explicit final_estimate_record override (final_plot) -----
# Every pre-existing caller (single-plot close, rollover, Excel close_and_
# start_new_cycle / start_next_cycle-resolved-rollover) never passes this
# param, so its default must keep resolving "latest" internally, unchanged.
# final_plot is the one caller that DOES pass it — an already-resolved+
# verified Record (or None) it insists close_cycle snapshot VERBATIM, with no
# second internal query that could disagree.

async def test_close_cycle_default_still_resolves_latest_internally() -> None:
    """item 23 — no override given → unchanged behavior (every existing
    caller): close_cycle resolves the cycle's own latest active record."""
    cycle = _cycle_ns()
    rec = _rec(yield_pct=Decimal("60.0"))
    db = _db_with_latest(rec)
    await repo.close_cycle(db, cycle, status="harvested", closed_by_id=uuid4())
    db.execute.assert_awaited()  # the internal "latest" resolve query ran
    assert cycle.final_inspection_record_id == rec.id
    assert cycle.final_yield_pct == Decimal("60.0")


async def test_close_cycle_explicit_record_overrides_internal_resolution() -> None:
    """item 16/17 — an explicit, already-resolved Record is snapshotted
    directly; close_cycle must NOT re-query "latest" at all (never validate
    record A then snapshot from a second, independently queried record B)."""
    cycle = _cycle_ns()
    explicit_rec = _rec(yield_pct=Decimal("90.0"))
    db = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    await repo.close_cycle(
        db, cycle, status="harvested", closed_by_id=uuid4(),
        final_estimate_record=explicit_rec,
    )
    db.execute.assert_not_awaited()
    assert cycle.final_inspection_record_id == explicit_rec.id
    assert cycle.final_yield_pct == Decimal("90.0")
    assert cycle.final_estimated_yield == Decimal("900.00")


async def test_close_cycle_explicit_none_clears_all_three_without_querying() -> None:
    """item 19 — no Record resolved at all (final_plot's cycle has zero
    active records) → explicit None clears the snapshot directly, without
    close_cycle falling back to its own internal query."""
    cycle = _cycle_ns(
        final_yield_pct="X", final_estimated_yield="X", final_inspection_record_id="X",
    )
    db = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    await repo.close_cycle(
        db, cycle, status="harvested", closed_by_id=uuid4(), final_estimate_record=None,
    )
    db.execute.assert_not_awaited()
    assert cycle.final_yield_pct is None
    assert cycle.final_estimated_yield is None
    assert cycle.final_inspection_record_id is None


async def test_close_cycle_explicit_record_yield_pct_null_keeps_source_id_only() -> None:
    """item 20 — explicit record with yield_pct NULL → id kept (traceability),
    yield_pct/estimate NULL."""
    cycle = _cycle_ns()
    explicit_rec = _rec(yield_pct=None)
    db = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    await repo.close_cycle(
        db, cycle, status="harvested", closed_by_id=uuid4(),
        final_estimate_record=explicit_rec,
    )
    assert cycle.final_inspection_record_id == explicit_rec.id
    assert cycle.final_yield_pct is None
    assert cycle.final_estimated_yield is None


async def test_close_cycle_explicit_record_expected_yield_null_keeps_pct_only() -> None:
    """item 21 — cycle's own expected_yield_full NULL → estimate NULL, but
    id/pct from the explicit record are still stamped."""
    cycle = _cycle_ns(expected_yield_full=None)
    explicit_rec = _rec(yield_pct=Decimal("70.0"))
    db = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    await repo.close_cycle(
        db, cycle, status="harvested", closed_by_id=uuid4(),
        final_estimate_record=explicit_rec,
    )
    assert cycle.final_inspection_record_id == explicit_rec.id
    assert cycle.final_yield_pct == Decimal("70.0")
    assert cycle.final_estimated_yield is None


def test_close_cycle_signature_has_final_estimate_record_kwarg_only() -> None:
    src = inspect.getsource(repo.close_cycle)
    assert "final_estimate_record" in src
    assert "_RESOLVE_ESTIMATE_INTERNALLY" in src


async def test_sync_plot_mirror_copies_cycle_master_only() -> None:
    plot = SimpleNamespace(
        current_crop=None, current_variety=None, current_lot_no=None,
        current_planting_date=None, plant_count=None, expected_yield_full=None,
        expected_yield_unit=None,
        # inspection snapshot — must be left untouched
        current_stage="งอก", current_yield_pct=Decimal("50"),
    )
    cycle = SimpleNamespace(
        crop="เมล่อน", variety="V", lot_no="L1",
        planting_date=datetime.date(2026, 5, 1), plant_count=100,
        expected_yield_full=Decimal("800.00"), expected_yield_unit="kg",
    )
    db = MagicMock()
    db.flush = AsyncMock()

    await repo.sync_plot_mirror_from_cycle(db, plot, cycle)

    assert plot.current_crop == "เมล่อน"
    assert plot.current_variety == "V"
    assert plot.current_lot_no == "L1"
    assert plot.current_planting_date == datetime.date(2026, 5, 1)
    assert plot.plant_count == 100
    assert plot.expected_yield_full == Decimal("800.00")
    assert plot.expected_yield_unit == "kg"
    # inspection snapshot NOT touched by the mirror
    assert plot.current_stage == "งอก"
    assert plot.current_yield_pct == Decimal("50")


# --- round 7.2A scoped helpers ----------------------------------------------

def test_list_cycles_for_plot_scopes_and_paginates() -> None:
    src = inspect.getsource(repo.list_cycles_for_plot)
    assert "PlotCycle.plot_id == plot_id" in src
    assert "cycle_no.desc()" in src
    assert ".limit(limit)" in src and ".offset(offset)" in src


def test_get_cycle_for_plot_requires_matching_plot() -> None:
    src = inspect.getsource(repo.get_cycle_for_plot)
    # both the cycle id AND its plot must match — a cycle from another plot
    # (even in scope) must not resolve.
    assert "PlotCycle.id == cycle_id" in src
    assert "PlotCycle.plot_id == plot_id" in src


async def test_get_cycle_for_plot_returns_the_row() -> None:
    cycle = SimpleNamespace(id=uuid4())
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(cycle))
    assert await repo.get_cycle_for_plot(db, uuid4(), cycle.id) is cycle


def test_active_for_update_takes_a_row_lock() -> None:
    src = inspect.getsource(repo.get_active_cycle_for_plot_for_update)
    assert "with_for_update()" in src
    assert "CYCLE_STATUS_ACTIVE" in src


async def test_assert_no_active_cycle_raises_when_one_exists() -> None:
    with patch(f"{_MOD}.get_active_cycle_for_plot", AsyncMock(return_value=SimpleNamespace())):
        with pytest.raises(ValueError):
            await repo.assert_no_active_cycle(MagicMock(), uuid4())


async def test_assert_no_active_cycle_passes_when_none() -> None:
    with patch(f"{_MOD}.get_active_cycle_for_plot", AsyncMock(return_value=None)):
        await repo.assert_no_active_cycle(MagicMock(), uuid4())  # must not raise


async def test_clear_mirror_and_snapshot_clears_both_halves() -> None:
    plot = SimpleNamespace(
        current_crop="เมล่อน", current_variety="V", current_lot_no="L1",
        current_planting_date=datetime.date(2026, 5, 1), plant_count=100,
        expected_yield_full=Decimal("800"), expected_yield_unit="kg",
        current_stage="งอก", current_yield_pct=Decimal("90"),
        current_field_prep_score=8, current_weather_score=7, current_care_score=9,
        current_variety_resistance_score=6, current_gps_lat=Decimal("1"),
        current_gps_lng=Decimal("2"), last_inspected_at="X",
        last_inspected_by_code="C", last_inspection_record_id="R",
    )
    db = MagicMock()
    db.flush = AsyncMock()

    await repo.clear_plot_cycle_mirror_and_inspection_snapshot(db, plot)

    # BOTH halves cleared (unlike clear_plot_inspection_snapshot which keeps the mirror)
    for f in ("current_crop", "current_variety", "current_lot_no",
              "current_planting_date", "plant_count", "expected_yield_full",
              "expected_yield_unit", "current_stage", "current_yield_pct",
              "last_inspection_record_id"):
        assert getattr(plot, f) is None, f"{f} should be cleared"


def test_no_repository_helper_commits() -> None:
    """Helpers flush, never commit — the request/transaction owns the commit
    (same rule as plot_repository)."""
    src = inspect.getsource(repo)
    assert ".commit(" not in src


async def test_clear_inspection_snapshot_clears_only_inspection_fields() -> None:
    plot = SimpleNamespace(
        # master mirror — must survive
        current_crop="เมล่อน", plant_count=100, expected_yield_full=Decimal("800"),
        # inspection snapshot — must be cleared
        current_stage="งอก", current_yield_pct=Decimal("90"),
        current_field_prep_score=8, current_weather_score=7, current_care_score=9,
        current_variety_resistance_score=6, current_gps_lat=Decimal("1"),
        current_gps_lng=Decimal("2"), last_inspected_at="X",
        last_inspected_by_code="C", last_inspection_record_id="R",
    )
    db = MagicMock()
    db.flush = AsyncMock()

    await repo.clear_plot_inspection_snapshot(db, plot)

    for cleared in (
        "current_stage", "current_yield_pct", "current_field_prep_score",
        "current_weather_score", "current_care_score",
        "current_variety_resistance_score", "current_gps_lat", "current_gps_lng",
        "last_inspected_at", "last_inspected_by_code", "last_inspection_record_id",
    ):
        assert getattr(plot, cleared) is None, f"{cleared} should be cleared"
    # master mirror survives
    assert plot.current_crop == "เมล่อน"
    assert plot.plant_count == 100
    assert plot.expected_yield_full == Decimal("800")

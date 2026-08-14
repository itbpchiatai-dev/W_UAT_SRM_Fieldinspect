"""Plot cycle ROLLOVER (round 7.9B) — the atomic close+start single-plot
endpoint plus the shared rollover_cycle repo helper.

DB-less: mocks the repo helpers and calls the route function directly (same
pattern as test_plot_cycle_lifecycle.py). The all-or-nothing transaction is a
property of the caller's single get_db session — the helper only flushes — so
the "rollback" guarantees are asserted structurally (no commit; exceptions
propagate) rather than against a live DB.
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

import app.api.v1.plots as plots_module
import app.repositories.plot_cycle_repository as cycle_repo
from app.api.v1.plots import rollover_plot_cycle
from app.schemas.plot import PlotCycleCreate, PlotCycleRollover

_NOW = datetime.datetime(2026, 7, 14, tzinfo=datetime.timezone.utc)
_P = "app.api.v1.plots"


def _plot(**o):
    d = dict(id=uuid4(), is_active=True)
    d.update(o)
    return SimpleNamespace(**d)


def _cycle(**o):
    d = dict(
        id=uuid4(), plot_id=uuid4(), cycle_no=1, status="active",
        crop="เมล่อน", variety="ญี่ปุ่น", cycle_label=None, lot_no="L1",
        planting_date=datetime.date(2026, 5, 1), plant_count=100,
        expected_yield_full=None, expected_yield_unit="kg",
        started_at=_NOW, closed_at=None, closed_by_id=None, close_reason=None,
        created_at=_NOW, updated_at=_NOW,
    )
    d.update(o)
    return SimpleNamespace(**d)


def _db():
    return AsyncMock()


def _user():
    return SimpleNamespace(id=uuid4())


def _payload(**o) -> PlotCycleRollover:
    d = dict(
        closeStatus="harvested",
        # Round 8-17A.1 — cycleLabel is now required on PlotCycleCreate.
        newCycle=PlotCycleCreate(crop="ทุเรียน", plantingDate=datetime.date(2026, 8, 1),
                                 poNumber="PO25001", pCode="Melon-A", cycleLabel="aug2026"),
    )
    d.update(o)
    return PlotCycleRollover(**d)


# --- schema validation ------------------------------------------------------

def test_rollover_schema_rejects_active_or_bogus_close_status_422() -> None:
    for bad in ("active", "paused", ""):
        with pytest.raises(ValidationError):
            PlotCycleRollover(closeStatus=bad, newCycle=PlotCycleCreate())


def test_rollover_schema_requires_unit_when_expected_yield_set() -> None:
    with pytest.raises(ValidationError):
        PlotCycleRollover(
            closeStatus="harvested",
            newCycle=PlotCycleCreate(expectedYieldFull=500, cycleLabel="aug2026"),  # no unit
        )
    # …but fine with a unit
    ok = PlotCycleRollover(
        closeStatus="harvested",
        newCycle=PlotCycleCreate(expectedYieldFull=500, expectedYieldUnit="kg",
                                 poNumber="PO25001", pCode="Melon-A", cycleLabel="aug2026"),
    )
    assert ok.new_cycle.expected_yield_full == 500


def test_rollover_schema_trims_close_reason() -> None:
    p = PlotCycleRollover(closeStatus="cancelled", closeReason="  โดนน้ำท่วม  ",
                          newCycle=PlotCycleCreate(poNumber="PO", pCode="PC", cycleLabel="aug2026"))
    assert p.close_reason == "โดนน้ำท่วม"
    blank = PlotCycleRollover(closeStatus="cancelled", closeReason="   ",
                              newCycle=PlotCycleCreate(poNumber="PO", pCode="PC", cycleLabel="aug2026"))
    assert blank.close_reason is None


# --- endpoint success -------------------------------------------------------

async def test_rollover_success_returns_both_cycles_and_derives_started_at() -> None:
    plot = _plot()
    active = _cycle(plot_id=plot.id, cycle_no=1, status="active")
    closed = _cycle(plot_id=plot.id, cycle_no=1, status="harvested",
                    closed_at=_NOW, closed_by_id=uuid4())
    new_cycle = _cycle(plot_id=plot.id, cycle_no=2, status="active")
    user = _user()

    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.rollover_cycle",
               AsyncMock(return_value=(closed, new_cycle))) as mk_roll:
        result = await rollover_plot_cycle(
            plot_id=plot.id, cycle_id=active.id, payload=_payload(),
            current_user=user, db=_db(),
        )

    assert result.plot_id == plot.id
    assert result.active_cycle_id == new_cycle.id
    assert result.active_cycle_no == 2
    assert result.closed_cycle.status == "harvested"
    assert result.new_cycle.status == "active" and result.new_cycle.cycle_no == 2
    # rollover_cycle got the locked active cycle, the user's id, and the
    # plantingDate-derived startedAt (00:00 UTC).
    kw = mk_roll.call_args.kwargs
    assert mk_roll.call_args.args[2] is active
    assert kw["close_status"] == "harvested"
    assert kw["closed_by_id"] == user.id
    assert kw["started_at"] == datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc)
    assert kw["crop"] == "ทุเรียน"


async def test_rollover_threads_po_and_pcode_to_new_cycle() -> None:  # round 8-5A
    plot = _plot()
    active = _cycle(plot_id=plot.id, cycle_no=1, status="active")
    closed = _cycle(plot_id=plot.id, cycle_no=1, status="harvested", closed_at=_NOW)
    new_cycle = _cycle(plot_id=plot.id, cycle_no=2, status="active")
    payload = _payload(newCycle=PlotCycleCreate(poNumber="po25001", pCode="Melon-A", lotNo=None, cycleLabel="aug2026"))

    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.rollover_cycle",
               AsyncMock(return_value=(closed, new_cycle))) as mk_roll:
        await rollover_plot_cycle(
            plot_id=plot.id, cycle_id=active.id, payload=payload,
            current_user=_user(), db=_db(),
        )

    # PO normalized (upper) + pCode trimmed, threaded to the new cycle so a
    # rollover can start its fresh cycle with an Auto Lot.
    assert mk_roll.call_args.kwargs["po_number"] == "PO25001"
    assert mk_roll.call_args.kwargs["p_code"] == "Melon-A"


async def test_rollover_auto_lot_too_long_returns_422() -> None:  # round 8-5A
    from app.services.lot_number import LotNumberTooLongError

    plot = _plot()
    active = _cycle(plot_id=plot.id, cycle_no=1, status="active")

    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.rollover_cycle",
               AsyncMock(side_effect=LotNumberTooLongError("too long"))):
        with pytest.raises(HTTPException) as exc:
            await rollover_plot_cycle(
                plot_id=plot.id, cycle_id=active.id,
                payload=_payload(newCycle=PlotCycleCreate(poNumber="PO25001", pCode="Melon-A", lotNo=None, cycleLabel="aug2026")),
                current_user=_user(), db=_db(),
            )
    assert exc.value.status_code == 422


async def test_rollover_defaults_close_reason_when_absent() -> None:
    plot = _plot()
    active = _cycle(plot_id=plot.id, status="active")
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.rollover_cycle",
               AsyncMock(return_value=(active, _cycle(cycle_no=2)))) as mk_roll:
        await rollover_plot_cycle(plot_id=plot.id, cycle_id=active.id,
                                  payload=_payload(closeReason=None), current_user=_user(), db=_db())
    assert mk_roll.call_args.kwargs["close_reason"] == "Closed by rollover"


async def test_rollover_refreshes_both_cycles_before_serialise() -> None:
    plot = _plot()
    active = _cycle(plot_id=plot.id, status="active")
    closed = _cycle(plot_id=plot.id, status="harvested")
    new_cycle = _cycle(plot_id=plot.id, cycle_no=2, status="active")
    db = _db()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.rollover_cycle", AsyncMock(return_value=(closed, new_cycle))):
        await rollover_plot_cycle(plot_id=plot.id, cycle_id=active.id,
                                  payload=_payload(), current_user=_user(), db=db)
    # both returned cycles refreshed (round 7.7 MissingGreenlet guard)
    assert db.refresh.await_args_list == [call(closed), call(new_cycle)]


# --- endpoint guards --------------------------------------------------------

async def test_rollover_rejects_inactive_plot_409() -> None:
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=_plot(is_active=False))), \
         patch(f"{_P}.plot_cycle_repo.rollover_cycle", AsyncMock()) as mk_roll:
        with pytest.raises(HTTPException) as exc:
            await rollover_plot_cycle(plot_id=uuid4(), cycle_id=uuid4(),
                                      payload=_payload(), current_user=_user(), db=_db())
    assert exc.value.status_code == 409
    assert exc.value.detail == "Plot is inactive"
    mk_roll.assert_not_awaited()


async def test_rollover_missing_plot_404() -> None:
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await rollover_plot_cycle(plot_id=uuid4(), cycle_id=uuid4(),
                                      payload=_payload(), current_user=_user(), db=_db())
    assert exc.value.status_code == 404
    assert exc.value.detail == "Plot not found"


async def test_rollover_foreign_or_missing_cycle_404() -> None:
    plot = _plot()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await rollover_plot_cycle(plot_id=plot.id, cycle_id=uuid4(),
                                      payload=_payload(), current_user=_user(), db=_db())
    assert exc.value.status_code == 404
    assert exc.value.detail == "Plot cycle not found"


async def test_rollover_rejects_non_active_cycle_409() -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, status="harvested")
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.rollover_cycle", AsyncMock()) as mk_roll:
        with pytest.raises(HTTPException) as exc:
            await rollover_plot_cycle(plot_id=plot.id, cycle_id=cycle.id,
                                      payload=_payload(), current_user=_user(), db=_db())
    assert exc.value.status_code == 409
    assert "rolled over" in exc.value.detail
    mk_roll.assert_not_awaited()


async def test_rollover_race_lock_lost_becomes_409() -> None:
    """The active cycle was closed between our read and the row lock — the
    locked active cycle is now a different one (or None) → clean 409, no
    rollover attempted."""
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, status="active")
    other = _cycle(plot_id=plot.id, cycle_no=2, status="active")  # someone else's new one
    for locked in (None, other):
        with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
             patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
             patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=locked)), \
             patch(f"{_P}.plot_cycle_repo.rollover_cycle", AsyncMock()) as mk_roll:
            with pytest.raises(HTTPException) as exc:
                await rollover_plot_cycle(plot_id=plot.id, cycle_id=cycle.id,
                                          payload=_payload(), current_user=_user(), db=_db())
        assert exc.value.status_code == 409
        mk_roll.assert_not_awaited()


async def test_rollover_integrityerror_becomes_409() -> None:
    plot = _plot()
    active = _cycle(plot_id=plot.id, status="active")
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.rollover_cycle",
               AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("dup")))):
        with pytest.raises(HTTPException) as exc:
            await rollover_plot_cycle(plot_id=plot.id, cycle_id=active.id,
                                      payload=_payload(), current_user=_user(), db=_db())
    assert exc.value.status_code == 409
    assert "active planting cycle" in exc.value.detail


async def test_rollover_non_integrity_error_propagates_for_rollback() -> None:
    """A create failure that ISN'T an IntegrityError must propagate uncaught so
    the get_db transaction rolls the whole close+create back — the endpoint has
    no try/except that would swallow it and leave the plot with no active cycle."""
    plot = _plot()
    active = _cycle(plot_id=plot.id, status="active")
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_P}.plot_cycle_repo.rollover_cycle", AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError, match="boom"):
            await rollover_plot_cycle(plot_id=plot.id, cycle_id=active.id,
                                      payload=_payload(), current_user=_user(), db=_db())


# --- shared rollover_cycle repo helper --------------------------------------

async def test_rollover_cycle_helper_closes_then_creates_then_clears() -> None:
    plot = _plot()
    current = _cycle(plot_id=plot.id, status="active")
    closed = _cycle(plot_id=plot.id, status="harvested")
    new_cycle = _cycle(plot_id=plot.id, cycle_no=2, status="active")
    db = _db()
    order: list[str] = []

    async def _close(*a, **k):
        order.append("close")
        return closed

    async def _create(*a, **k):
        order.append("create")
        return new_cycle

    async def _clear(*a, **k):
        order.append("clear")

    with patch.object(cycle_repo, "close_cycle", AsyncMock(side_effect=_close)) as mk_close, \
         patch.object(cycle_repo, "create_cycle", AsyncMock(side_effect=_create)) as mk_create, \
         patch.object(cycle_repo, "clear_plot_inspection_snapshot", AsyncMock(side_effect=_clear)):
        result = await cycle_repo.rollover_cycle(
            db, plot, current,
            close_status="harvested", closed_by_id=uuid4(), close_reason="r",
            crop="ทุเรียน", plant_count=200, started_at=_NOW,
        )

    assert result == (closed, new_cycle)
    assert order == ["close", "create", "clear"]  # close old → open new → clear snapshot
    assert mk_close.call_args.kwargs["status"] == "harvested"
    assert mk_create.call_args.kwargs["crop"] == "ทุเรียน"
    # helper must not commit — the caller's transaction owns that
    db.commit.assert_not_called()


# --- permission / wiring / no-public ----------------------------------------

def test_rollover_route_requires_plots_update_and_rls_context() -> None:
    src = inspect.getsource(plots_module)
    block = src[
        src.index('@router.post(\n    "/{plot_id}/cycles/{cycle_id}/rollover"'):
        src.index("async def rollover_plot_cycle")
    ]
    assert "require_permission(PermissionKey.PLOTS_UPDATE)" in block
    assert "get_rls_context" in block


def test_rollover_refreshes_before_model_validate_in_source() -> None:
    src = inspect.getsource(rollover_plot_cycle)
    assert "await db.refresh(new_cycle)" in src
    assert src.index("await db.refresh(new_cycle)") < src.index(
        "PlotCycleRead.model_validate(new_cycle)"
    )


def test_no_public_rollover_route() -> None:
    import app.api.v1.public_plots as public_plots
    import app.api.v1.public_records as public_records
    for mod in (public_plots, public_records):
        assert not any("rollover" in r.path.lower() for r in mod.router.routes), mod.__name__


def test_rollover_locks_plot_before_any_cycle_lookup_in_source() -> None:
    """Round 8.0.7 — same plot-before-cycle lock order guard as
    test_plot_cycle_lifecycle.py's lifecycle-endpoint version, anchored on
    the `await ...` call form so docstring prose can't make it pass
    vacuously."""
    src = inspect.getsource(rollover_plot_cycle)
    assert "await repo.get_plot_for_update" in src
    assert "await plot_cycle_repo." in src
    assert src.index("await repo.get_plot_for_update") < src.index("await plot_cycle_repo.")

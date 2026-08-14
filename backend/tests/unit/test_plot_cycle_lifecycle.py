"""Plot cycle lifecycle write API (round 7.2B) — start / edit / close.

DB-less: mocks repo helpers and calls the route functions directly (same
pattern as test_plot_cycles_endpoint.py). Permission/RLS wiring by source
inspection.
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

import app.api.v1.plots as plots_module
from app.api.v1.plots import close_plot_cycle, start_plot_cycle, update_plot_cycle
from app.schemas.plot import PlotCycleClose, PlotCycleCreate, PlotCycleUpdate

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


def _ccreate(**o) -> PlotCycleCreate:
    """Round 8-5B — PlotCycleCreate now REQUIRES nonblank poNumber/pCode.
    Round 8-17A.1 added cycleLabel to that list. This helper supplies valid
    defaults so tests focused on other behavior (guards, refresh,
    started_at) construct a valid payload."""
    d = dict(poNumber="PO25001", pCode="Melon-A", cycleLabel="jun2026")
    d.update(o)
    return PlotCycleCreate(**d)


# --- start (POST /{plotId}/cycles) ------------------------------------------

async def test_start_cycle_success_creates_and_clears_snapshot() -> None:
    plot = _plot()
    created = _cycle(plot_id=plot.id, cycle_no=3, status="active")
    payload = _ccreate(crop="เมล่อน", plantCount=100,
                       plantingDate=datetime.date(2026, 5, 1))

    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=None)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=created)) as mk_create, \
         patch(f"{_P}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()) as mk_clear:
        result = await start_plot_cycle(plot_id=plot.id, payload=payload, db=_db())

    assert result.cycle_no == 3
    assert result.status == "active"
    # a fresh cycle clears the inspection snapshot (mirror handled by create_cycle)
    mk_clear.assert_awaited_once()
    # startedAt derived from plantingDate at 00:00 UTC
    assert mk_create.call_args.kwargs["started_at"] == datetime.datetime(
        2026, 5, 1, tzinfo=datetime.timezone.utc
    )
    assert mk_create.call_args.kwargs["crop"] == "เมล่อน"


async def test_start_cycle_threads_po_and_pcode_to_create(  # round 8-5A
) -> None:
    plot = _plot()
    created = _cycle(plot_id=plot.id, cycle_no=1, status="active")
    payload = PlotCycleCreate(poNumber="po25001", pCode="Melon-A", cycleLabel="jun2026", lotNo=None)

    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=None)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=created)) as mk_create, \
         patch(f"{_P}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()):
        await start_plot_cycle(plot_id=plot.id, payload=payload, db=_db())

    # PO normalized at the schema boundary (upper), pCode trimmed (case kept),
    # both threaded to the shared create path.
    assert mk_create.call_args.kwargs["po_number"] == "PO25001"
    assert mk_create.call_args.kwargs["p_code"] == "Melon-A"


async def test_start_cycle_auto_lot_too_long_returns_422() -> None:  # round 8-5A
    from app.services.lot_number import LotNumberTooLongError

    plot = _plot()
    payload = PlotCycleCreate(poNumber="PO25001", pCode="Melon-A", cycleLabel="jun2026", lotNo=None)

    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=None)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle",
               AsyncMock(side_effect=LotNumberTooLongError("too long"))):
        with pytest.raises(HTTPException) as exc:
            await start_plot_cycle(plot_id=plot.id, payload=payload, db=_db())
    assert exc.value.status_code == 422


async def test_start_cycle_no_planting_date_uses_default_started_at() -> None:
    plot = _plot()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle(plot_id=plot.id))) as mk_create, \
         patch(f"{_P}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()):
        await start_plot_cycle(plot_id=plot.id, payload=_ccreate(), db=_db())
    assert mk_create.call_args.kwargs["started_at"] is None  # → create_cycle defaults to now()


async def test_start_cycle_rejects_inactive_plot_409() -> None:
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=_plot(is_active=False))), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock()) as mk_create:
        with pytest.raises(HTTPException) as exc:
            await start_plot_cycle(plot_id=uuid4(), payload=_ccreate(), db=_db())
    assert exc.value.status_code == 409
    assert exc.value.detail == "Plot is inactive"
    mk_create.assert_not_awaited()


async def test_start_cycle_rejects_existing_active_cycle_409() -> None:
    plot = _plot()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=_cycle(plot_id=plot.id))), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock()) as mk_create:
        with pytest.raises(HTTPException) as exc:
            await start_plot_cycle(plot_id=plot.id, payload=_ccreate(), db=_db())
    assert exc.value.status_code == 409
    assert "active planting cycle" in exc.value.detail
    mk_create.assert_not_awaited()


async def test_start_cycle_out_of_scope_plot_404() -> None:
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await start_plot_cycle(plot_id=uuid4(), payload=_ccreate(), db=_db())
    assert exc.value.status_code == 404
    assert exc.value.detail == "Plot not found"


async def test_start_cycle_race_integrityerror_becomes_409() -> None:
    """A concurrent start that loses the partial-unique-index race gets a clean
    409, not a 500."""
    plot = _plot()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle",
               AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("dup")))):
        with pytest.raises(HTTPException) as exc:
            await start_plot_cycle(plot_id=plot.id, payload=_ccreate(), db=_db())
    assert exc.value.status_code == 409
    assert "active planting cycle" in exc.value.detail


# --- edit (PATCH /{plotId}/cycles/{cycleId}) --------------------------------

async def test_update_cycle_success_syncs_mirror_not_snapshot() -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, status="active")
    payload = PlotCycleUpdate(crop="ทุเรียน", expectedYieldFull=500)

    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.update_cycle", AsyncMock(return_value=cycle)) as mk_upd, \
         patch(f"{_P}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()) as mk_sync, \
         patch(f"{_P}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()) as mk_clear:
        await update_plot_cycle(plot_id=plot.id, cycle_id=cycle.id, payload=payload, db=_db())

    # update_cycle(db, plot, cycle, fields) — round 8-5A added `plot` (2nd
    # positional, for Auto Lot's plot_code). The plot+cycle are threaded
    # through and only the provided fields are passed (exclude_unset).
    assert mk_upd.call_args[0][1] is plot
    assert mk_upd.call_args[0][2] is cycle
    passed = mk_upd.call_args[0][3]
    assert passed == {"crop": "ทุเรียน", "expected_yield_full": 500}
    mk_sync.assert_awaited_once()
    # editing a plan must NOT wipe the plot's latest inspection status
    mk_clear.assert_not_awaited()


async def test_update_cycle_auto_lot_missing_component_maps_422() -> None:
    """Round 8-5B.1 (message updated round 8-12A) — an edit asked to regenerate
    an Auto Lot but a V2 component is blank. Clean 422 naming the FIELD, never
    a 500 and never a cleared lot."""
    from app.services.lot_number import AutoLotMissingComponentError

    plot = _plot()
    cycle = _cycle(plot_id=plot.id, status="active")
    payload = PlotCycleUpdate(lotNo=None)  # regenerate Auto, component missing
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.update_cycle",
               AsyncMock(side_effect=AutoLotMissingComponentError(("pCode",)))), \
         patch(f"{_P}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()) as mk_sync:
        with pytest.raises(HTTPException) as exc:
            await update_plot_cycle(plot_id=plot.id, cycle_id=cycle.id, payload=payload, db=_db())
    assert exc.value.status_code == 422  # never 500
    # Round 8-12A.1 — the detail names the field in the user's own words
    # ("P.Code"), not the raw API key, and never mentions the PO.
    assert "P.Code" in exc.value.detail
    assert "PO" not in exc.value.detail
    # mirror sync must NOT run after a refused update.
    mk_sync.assert_not_awaited()


async def test_update_cycle_rejects_non_active_409() -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, status="harvested")
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.update_cycle", AsyncMock()) as mk_upd:
        with pytest.raises(HTTPException) as exc:
            await update_plot_cycle(plot_id=plot.id, cycle_id=cycle.id,
                                    payload=PlotCycleUpdate(crop="X"), db=_db())
    assert exc.value.status_code == 409
    mk_upd.assert_not_awaited()


async def test_update_cycle_from_another_plot_404() -> None:
    """get_cycle_for_plot returns None when the cycle isn't this plot's (or is
    out of scope) → 'Plot cycle not found'."""
    plot = _plot()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await update_plot_cycle(plot_id=plot.id, cycle_id=uuid4(),
                                    payload=PlotCycleUpdate(crop="X"), db=_db())
    assert exc.value.status_code == 404
    assert exc.value.detail == "Plot cycle not found"


async def test_update_cycle_missing_plot_404() -> None:
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await update_plot_cycle(plot_id=uuid4(), cycle_id=uuid4(),
                                    payload=PlotCycleUpdate(), db=_db())
    assert exc.value.status_code == 404
    assert exc.value.detail == "Plot not found"


async def test_update_cycle_race_lock_lost_becomes_409() -> None:
    """Round 8.0.7 — the unlocked get_cycle_for_plot lookup found an active
    cycle, but it was closed/rolled over by a concurrent transaction between
    that lookup and the row-locked re-check just below it → clean 409, no
    update executed."""
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, status="active")
    other = _cycle(plot_id=plot.id, cycle_no=2, status="active")
    for locked in (None, other):
        with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
             patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
             patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
                   AsyncMock(return_value=locked)), \
             patch(f"{_P}.plot_cycle_repo.update_cycle", AsyncMock()) as mk_upd:
            with pytest.raises(HTTPException) as exc:
                await update_plot_cycle(plot_id=plot.id, cycle_id=cycle.id,
                                        payload=PlotCycleUpdate(crop="X"), db=_db())
        assert exc.value.status_code == 409
        mk_upd.assert_not_awaited()


# --- close (POST /{plotId}/cycles/{cycleId}/close) --------------------------

@pytest.mark.parametrize("close_status", ["harvested", "cancelled"])
async def test_close_cycle_success_clears_mirror_and_snapshot(close_status: str) -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, status="active")
    user = SimpleNamespace(id=uuid4())

    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.close_cycle", AsyncMock(return_value=cycle)) as mk_close, \
         patch(f"{_P}.plot_cycle_repo.clear_plot_cycle_mirror_and_inspection_snapshot",
               AsyncMock()) as mk_clear:
        await close_plot_cycle(plot_id=plot.id, cycle_id=cycle.id,
                               payload=PlotCycleClose(status=close_status, closeReason="  done  "),
                               current_user=user, db=_db())

    assert mk_close.call_args.kwargs["status"] == close_status
    assert mk_close.call_args.kwargs["closed_by_id"] == user.id
    assert mk_close.call_args.kwargs["reason"] == "done"   # trimmed
    mk_clear.assert_awaited_once()
    # the endpoint never touches plot.is_active (permanent-close is separate)
    assert plot.is_active is True


async def test_close_cycle_race_lock_lost_becomes_409() -> None:
    """Round 8.0.7 — same race guard as update_plot_cycle: the unlocked
    lookup found an active cycle, but a concurrent transition changed it
    before the row-locked re-check → clean 409, close never executed."""
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, status="active")
    other = _cycle(plot_id=plot.id, cycle_no=2, status="active")
    for locked in (None, other):
        with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
             patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
             patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
                   AsyncMock(return_value=locked)), \
             patch(f"{_P}.plot_cycle_repo.close_cycle", AsyncMock()) as mk_close:
            with pytest.raises(HTTPException) as exc:
                await close_plot_cycle(plot_id=plot.id, cycle_id=cycle.id,
                                       payload=PlotCycleClose(status="harvested"),
                                       current_user=SimpleNamespace(id=uuid4()), db=_db())
        assert exc.value.status_code == 409
        mk_close.assert_not_awaited()


async def test_close_cycle_rejects_non_active_409() -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, status="cancelled")
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.close_cycle", AsyncMock()) as mk_close:
        with pytest.raises(HTTPException) as exc:
            await close_plot_cycle(plot_id=plot.id, cycle_id=cycle.id,
                                   payload=PlotCycleClose(status="harvested"),
                                   current_user=SimpleNamespace(id=uuid4()), db=_db())
    assert exc.value.status_code == 409
    mk_close.assert_not_awaited()


async def test_close_cycle_missing_or_foreign_cycle_404() -> None:
    plot = _plot()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await close_plot_cycle(plot_id=plot.id, cycle_id=uuid4(),
                                   payload=PlotCycleClose(status="harvested"),
                                   current_user=SimpleNamespace(id=uuid4()), db=_db())
    assert exc.value.status_code == 404
    assert exc.value.detail == "Plot cycle not found"


def test_close_schema_rejects_active_or_bogus_status_422() -> None:
    for bad in ("active", "paused", ""):
        with pytest.raises(ValidationError):
            PlotCycleClose(status=bad)


# --- permission / wiring / no-public ----------------------------------------

def test_write_routes_require_plots_update_and_rls_context() -> None:
    src = inspect.getsource(plots_module)
    for decorator, fn in (
        ('@router.post("/{plot_id}/cycles"', "async def start_plot_cycle"),
        ('@router.patch("/{plot_id}/cycles/{cycle_id}"', "async def update_plot_cycle"),
        ('@router.post("/{plot_id}/cycles/{cycle_id}/close"', "async def close_plot_cycle"),
    ):
        block = src[src.index(decorator):src.index(fn)]
        assert "require_permission(PermissionKey.PLOTS_UPDATE)" in block, fn
        assert "get_rls_context" in block, fn


def test_get_cycles_still_plots_read() -> None:
    src = inspect.getsource(plots_module)
    block = src[src.index('@router.get("/{plot_id}/cycles"'):src.index("async def list_plot_cycles")]
    assert "require_permission(PermissionKey.PLOTS_READ)" in block


def test_no_public_cycle_lifecycle_route() -> None:
    import app.api.v1.public_plots as public_plots
    import app.api.v1.public_records as public_records
    for mod in (public_plots, public_records):
        assert not any("cycle" in r.path.lower() for r in mod.router.routes), mod.__name__


# --- plot-before-cycle lock order (round 8.0.7) ------------------------------

def test_lifecycle_endpoints_lock_plot_before_any_cycle_lookup_in_source() -> None:
    """Structural guard: in each write endpoint's CODE (not docstring prose),
    the plot row lock must be awaited before any plot_cycle_repo call —
    locking the cycle first would risk a deadlock against another
    transaction that (correctly) locks the plot first. Anchored on the
    `await ...` call form so mentioning either name in the docstring can't
    make this pass vacuously."""
    for fn in (start_plot_cycle, update_plot_cycle, close_plot_cycle):
        src = inspect.getsource(fn)
        assert "await repo.get_plot_for_update" in src, fn.__name__
        assert "await plot_cycle_repo." in src, fn.__name__
        plot_lock_at = src.index("await repo.get_plot_for_update")
        first_cycle_call = src.index("await plot_cycle_repo.")
        assert plot_lock_at < first_cycle_call, fn.__name__


async def test_update_cycle_locks_plot_before_active_cycle_call_order() -> None:
    """Same guarantee as the source check above, but proven by actual call
    order at runtime rather than just text position."""
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, status="active")
    order: list[str] = []

    async def _get_plot_for_update(*a, **k):
        order.append("lock_plot")
        return plot

    async def _get_active_cycle_locked(*a, **k):
        order.append("lock_cycle")
        return cycle

    with patch(f"{_P}.repo.get_plot_for_update", side_effect=_get_plot_for_update), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               side_effect=_get_active_cycle_locked), \
         patch(f"{_P}.plot_cycle_repo.update_cycle", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()):
        await update_plot_cycle(plot_id=plot.id, cycle_id=cycle.id,
                                payload=PlotCycleUpdate(crop="X"), db=_db())

    assert order == ["lock_plot", "lock_cycle"]


# --- refresh-before-serialize (round 7.7 regression guard) ------------------
# The three write endpoints flush the cycle, which EXPIRES server-computed /
# onupdate columns (created_at/updated_at). Serialising with
# PlotCycleRead.model_validate(cycle) then lazy-loads them, which does async IO
# OUTSIDE the greenlet against a real asyncpg session → MissingGreenlet + a
# Pydantic ValidationError on updated_at → HTTP 500. The fix is an explicit
# `await db.refresh(cycle)` right before serialising. The DB-less endpoint
# tests above can't reproduce MissingGreenlet (repo + session are mocked), so
# these lock in the ordering directly: refresh must be awaited exactly once,
# with the same cycle object that gets serialised.

async def _assert_refreshes_cycle_before_serialise(call, expected_cycle) -> None:
    db = _db()
    result = await call(db)
    db.refresh.assert_awaited_once_with(expected_cycle)
    # the refreshed object is the one that came back serialised
    assert result.id == expected_cycle.id


async def test_start_cycle_refreshes_cycle_before_serialise() -> None:
    plot = _plot()
    created = _cycle(plot_id=plot.id, cycle_no=2, status="active")
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=created)), \
         patch(f"{_P}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()):
        await _assert_refreshes_cycle_before_serialise(
            lambda db: start_plot_cycle(plot_id=plot.id, payload=_ccreate(), db=db),
            created,
        )


async def test_update_cycle_refreshes_cycle_before_serialise() -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, status="active")
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.update_cycle", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()):
        await _assert_refreshes_cycle_before_serialise(
            lambda db: update_plot_cycle(
                plot_id=plot.id, cycle_id=cycle.id,
                payload=PlotCycleUpdate(crop="X"), db=db,
            ),
            cycle,
        )


async def test_close_cycle_refreshes_cycle_before_serialise() -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, status="active")
    user = SimpleNamespace(id=uuid4())
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.close_cycle", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.clear_plot_cycle_mirror_and_inspection_snapshot", AsyncMock()):
        await _assert_refreshes_cycle_before_serialise(
            lambda db: close_plot_cycle(
                plot_id=plot.id, cycle_id=cycle.id,
                payload=PlotCycleClose(status="harvested", closeReason="x"),
                current_user=user, db=db,
            ),
            cycle,
        )


def test_write_endpoints_refresh_before_model_validate_in_source() -> None:
    """Belt-and-braces: the refresh must textually precede model_validate in
    each write endpoint, so a future edit that reorders them (reintroducing the
    MissingGreenlet 500) trips this even if the mocked behavior test above is
    deleted."""
    for fn in (start_plot_cycle, update_plot_cycle, close_plot_cycle):
        src = inspect.getsource(fn)
        assert "await db.refresh(cycle)" in src, fn.__name__
        assert src.index("await db.refresh(cycle)") < src.index(
            "PlotCycleRead.model_validate(cycle)"
        ), fn.__name__


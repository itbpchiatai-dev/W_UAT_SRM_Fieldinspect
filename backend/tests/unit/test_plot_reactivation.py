"""Plot reactivation (round 8-6H) — reactivate-only + atomic reactivate-with-
cycle API endpoints, the shared plot_repository helpers behind them, and the
hardened deactivate invariant.

DB-less: mocks repo/module helpers and calls route/repository functions
directly (same pattern as test_plot_cycle_rollover.py /
test_plot_repository_sync_current_status.py).
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

import app.api.v1.plots as plots_module
import app.repositories.plot_repository as repo
from app.api.v1.plots import deactivate_plot, reactivate_plot, reactivate_plot_with_cycle
from app.schemas.plot import PlotCycleCreate, PlotRead, PlotWithCycleCreateResult
from app.services.lot_number import LotNumberTooLongError

_P = "app.api.v1.plots"
_R = "app.repositories.plot_repository"
_NOW = datetime.datetime(2026, 7, 23, tzinfo=datetime.timezone.utc)


def _plot(**o):
    d = dict(id=uuid4(), is_active=False, qr_key="QR-ORIGINAL", access_phones=["0812345678"])
    d.update(o)
    return SimpleNamespace(**d)


def _plot_read(**o) -> PlotRead:
    """A real PlotRead instance for tests that construct
    PlotWithCycleCreateResult(plot=..., ...) directly (unlike a plain
    `-> PlotRead` return, Pydantic validates this field eagerly at
    construction time, so a bare SimpleNamespace won't do)."""
    d = dict(
        id=uuid4(), supplier_id=uuid4(), plot_code="P002", name="แปลงทดสอบ",
        village=None, district=None, province=None, latitude=None,
        longitude=None, rai=None, is_active=True, created_at=_NOW, updated_at=_NOW,
    )
    d.update(o)
    return PlotRead(**d)


def _cycle(**o):
    d = dict(
        id=uuid4(), plot_id=uuid4(), cycle_no=1, status="active",
        crop=None, variety=None, cycle_label=None, lot_no=None,
        po_number=None, p_code=None, lot_no_source=None, lot_running_no=None,
        supplier_lot_no=None,
        planting_date=None, plant_count=None, expected_yield_full=None,
        expected_yield_unit=None, started_at=_NOW, closed_at=None,
        closed_by_id=None, close_reason=None, created_at=_NOW, updated_at=_NOW,
    )
    d.update(o)
    return SimpleNamespace(**d)


def _user(**o):
    d = dict(id=uuid4())
    d.update(o)
    return SimpleNamespace(**d)


def _db():
    return AsyncMock()


def _cycle_payload(**o) -> PlotCycleCreate:
    d = dict(
        poNumber="PO25001", pCode="Melon-A", cycleLabel="aug2026",
        plantingDate=datetime.date(2026, 8, 1),
    )
    d.update(o)
    return PlotCycleCreate(**d)


# =============================================================================
# plot_repository.reactivate_plot (shared helper — Part E)
# =============================================================================

async def test_reactivate_plot_flips_is_active_and_clears_mirror():
    plot = _plot(is_active=False)
    db = _db()
    with patch(f"{_R}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_R}.plot_cycle_repo.clear_plot_cycle_mirror_and_inspection_snapshot", AsyncMock()) as mk_clear:
        result = await repo.reactivate_plot(db, plot)
    assert result is plot
    assert plot.is_active is True
    mk_clear.assert_awaited_once_with(db, plot)


async def test_reactivate_plot_already_active_raises_without_mutation():  # item 2
    plot = _plot(is_active=True)
    with patch(f"{_R}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock()) as mk_active:
        with pytest.raises(repo.PlotAlreadyActiveError):
            await repo.reactivate_plot(_db(), plot)
    assert plot.is_active is True
    mk_active.assert_not_awaited()


async def test_reactivate_plot_inconsistent_active_cycle_raises():  # item 3
    plot = _plot(is_active=False)
    with patch(f"{_R}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=_cycle())):
        with pytest.raises(repo.PlotHasActiveCycleError):
            await repo.reactivate_plot(_db(), plot)
    assert plot.is_active is False  # never flipped


async def test_reactivate_plot_leaves_qr_key_and_access_phones_untouched():  # items 6/7
    plot = _plot(is_active=False, qr_key="QR-ABC123", access_phones=["0891112222"])
    with patch(f"{_R}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_R}.plot_cycle_repo.clear_plot_cycle_mirror_and_inspection_snapshot", AsyncMock()):
        await repo.reactivate_plot(_db(), plot)
    assert plot.qr_key == "QR-ABC123"
    assert plot.access_phones == ["0891112222"]


async def test_reactivate_plot_never_commits():  # item 11
    plot = _plot(is_active=False)
    db = _db()
    with patch(f"{_R}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_R}.plot_cycle_repo.clear_plot_cycle_mirror_and_inspection_snapshot", AsyncMock()):
        await repo.reactivate_plot(db, plot)
    db.commit.assert_not_called()


# =============================================================================
# plot_repository.reactivate_plot_with_cycle (shared helper — Part E)
# =============================================================================

async def test_reactivate_plot_with_cycle_success_flips_and_creates_cycle():  # item 12
    plot = _plot(is_active=False)
    cycle = _cycle(cycle_no=1)
    db = _db()
    with patch(f"{_R}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_R}.plot_cycle_repo.create_cycle", AsyncMock(return_value=cycle)) as mk_create, \
         patch(f"{_R}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()) as mk_clear:
        result_plot, result_cycle = await repo.reactivate_plot_with_cycle(
            db, plot, crop="ทุเรียน", po_number="PO25001", p_code="Melon-A",
        )
    assert result_plot is plot
    assert plot.is_active is True
    assert result_cycle is cycle
    mk_create.assert_awaited_once()
    assert mk_create.call_args.kwargs["crop"] == "ทุเรียน"
    mk_clear.assert_awaited_once_with(db, plot)


async def test_reactivate_plot_with_cycle_already_active_raises_before_create():  # item 2 (with-cycle)
    plot = _plot(is_active=True)
    with patch(f"{_R}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock()) as mk_active, \
         patch(f"{_R}.plot_cycle_repo.create_cycle", AsyncMock()) as mk_create:
        with pytest.raises(repo.PlotAlreadyActiveError):
            await repo.reactivate_plot_with_cycle(_db(), plot)
    mk_active.assert_not_awaited()
    mk_create.assert_not_awaited()


async def test_reactivate_plot_with_cycle_inconsistent_active_cycle_raises_before_create():  # item 3 (with-cycle)
    plot = _plot(is_active=False)
    with patch(f"{_R}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=_cycle())), \
         patch(f"{_R}.plot_cycle_repo.create_cycle", AsyncMock()) as mk_create:
        with pytest.raises(repo.PlotHasActiveCycleError):
            await repo.reactivate_plot_with_cycle(_db(), plot)
    assert plot.is_active is False
    mk_create.assert_not_awaited()


async def test_reactivate_plot_with_cycle_create_failure_propagates_for_rollback():  # item 17
    """create_cycle failing leaves is_active=True on the in-memory object —
    the guarantee that the plot "comes back inactive" is TRANSACTIONAL (the
    caller's whole request rolls back on the propagated exception), not a
    manual revert. This proves the exception propagates uncaught."""
    plot = _plot(is_active=False)
    with patch(f"{_R}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_R}.plot_cycle_repo.create_cycle", AsyncMock(side_effect=LotNumberTooLongError("too long"))):
        with pytest.raises(LotNumberTooLongError):
            await repo.reactivate_plot_with_cycle(_db(), plot, po_number="PO", p_code="PC")
    assert plot.is_active is True  # uncommitted — DB never sees this


async def test_reactivate_plot_with_cycle_threads_lot_po_pcode_to_create_cycle():  # items 13/14/15/16
    """cycle_no=max+1 and the Auto/Manual/legacy lot resolution are
    create_cycle's own, already fully unit-tested logic
    (test_plot_cycle_repository.py) — reactivate_plot_with_cycle must not
    reimplement any of it, only thread the row's values through unchanged."""
    plot = _plot(is_active=False)
    cycle = _cycle()
    with patch(f"{_R}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_R}.plot_cycle_repo.create_cycle", AsyncMock(return_value=cycle)) as mk_create, \
         patch(f"{_R}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()):
        await repo.reactivate_plot_with_cycle(
            _db(), plot, lot_no="MANUAL-LOT-9", po_number="po25001", p_code="Melon-A",
            crop="ทุเรียน",
        )
    kw = mk_create.call_args.kwargs
    assert kw["lot_no"] == "MANUAL-LOT-9"
    assert kw["po_number"] == "po25001"
    assert kw["p_code"] == "Melon-A"


async def test_reactivate_plot_with_cycle_leaves_qr_and_phones_untouched():  # items 24
    plot = _plot(is_active=False, qr_key="QR-XYZ", access_phones=["0899998888"])
    cycle = _cycle()
    with patch(f"{_R}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_R}.plot_cycle_repo.create_cycle", AsyncMock(return_value=cycle)), \
         patch(f"{_R}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()):
        await repo.reactivate_plot_with_cycle(_db(), plot, po_number="PO", p_code="PC")
    assert plot.qr_key == "QR-XYZ"
    assert plot.access_phones == ["0899998888"]


async def test_reactivate_plot_with_cycle_clears_inspection_snapshot_only():  # item 25
    """create_cycle already syncs the master/planting mirror to the new
    cycle — only the inspection-derived snapshot needs a separate clear
    (same as start_plot_cycle's own post-create step)."""
    plot = _plot(is_active=False)
    cycle = _cycle()
    db = _db()
    with patch(f"{_R}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_R}.plot_cycle_repo.create_cycle", AsyncMock(return_value=cycle)), \
         patch(f"{_R}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()) as mk_clear:
        await repo.reactivate_plot_with_cycle(db, plot, po_number="PO", p_code="PC")
    mk_clear.assert_awaited_once_with(db, plot)


async def test_reactivate_plot_with_cycle_never_commits():  # item 46
    plot = _plot(is_active=False)
    cycle = _cycle()
    db = _db()
    with patch(f"{_R}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_R}.plot_cycle_repo.create_cycle", AsyncMock(return_value=cycle)), \
         patch(f"{_R}.plot_cycle_repo.clear_plot_inspection_snapshot", AsyncMock()):
        await repo.reactivate_plot_with_cycle(db, plot, po_number="PO", p_code="PC")
    db.commit.assert_not_called()


def test_reactivate_helpers_never_lock_plot_themselves():  # item 47
    """The caller (API endpoint / Excel importer) must already hold the Plot
    row lock — these helpers never call get_plot_for_update themselves,
    which is what guarantees the Plot-before-PlotCycle order can never be
    violated by calling them in the wrong place."""
    src = inspect.getsource(repo.reactivate_plot) + inspect.getsource(repo.reactivate_plot_with_cycle)
    assert "get_plot_for_update" not in src


def test_reactivate_helpers_never_touch_qr_access_phones_or_records_in_source():  # items 6/7/8
    src = inspect.getsource(repo.reactivate_plot) + inspect.getsource(repo.reactivate_plot_with_cycle)
    for forbidden in ("qr_key", "access_phone", "Record"):
        assert forbidden not in src


def test_reactivate_and_deactivate_never_hard_delete():  # item 50
    src = (
        inspect.getsource(reactivate_plot) + inspect.getsource(reactivate_plot_with_cycle)
        + inspect.getsource(deactivate_plot) + inspect.getsource(repo.reactivate_plot)
        + inspect.getsource(repo.reactivate_plot_with_cycle)
    )
    assert "db.delete(" not in src
    assert ".delete()" not in src


# =============================================================================
# POST /plots/{plotId}/reactivate — endpoint (Part C)
# =============================================================================

async def test_reactivate_endpoint_success_returns_plot_read():  # item 1
    plot = _plot(is_active=False)
    reactivated = _plot(id=plot.id, is_active=True)
    db = _db()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.repo.reactivate_plot", AsyncMock(return_value=reactivated)) as mk_react, \
         patch(f"{_P}._to_read", MagicMock(return_value=SimpleNamespace(is_active=True, id=plot.id))):
        result = await reactivate_plot(plot_id=plot.id, current_user=_user(), db=db)
    assert result.is_active is True
    mk_react.assert_awaited_once_with(db, plot)
    assert db.refresh.await_args_list == [
        call(reactivated),
        call(
            reactivated,
            attribute_names=["assignments", "supplier", "active_cycle", "access_phones"],
        ),
    ]


async def test_reactivate_endpoint_already_active_409():  # item 2
    plot = _plot(is_active=True)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.repo.reactivate_plot", AsyncMock(side_effect=repo.PlotAlreadyActiveError("x"))):
        with pytest.raises(HTTPException) as exc:
            await reactivate_plot(plot_id=plot.id, current_user=_user(), db=_db())
    assert exc.value.status_code == 409


async def test_reactivate_endpoint_inconsistent_active_cycle_409():  # item 3
    plot = _plot(is_active=False)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.repo.reactivate_plot", AsyncMock(side_effect=repo.PlotHasActiveCycleError("x"))):
        with pytest.raises(HTTPException) as exc:
            await reactivate_plot(plot_id=plot.id, current_user=_user(), db=_db())
    assert exc.value.status_code == 409


async def test_reactivate_endpoint_missing_plot_404():  # item 4
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await reactivate_plot(plot_id=uuid4(), current_user=_user(), db=_db())
    assert exc.value.status_code == 404


def test_reactivate_route_requires_plots_delete_and_rls():  # item 5 / 44
    src = inspect.getsource(plots_module)
    block = src[
        src.index('@router.post("/{plot_id}/reactivate", response_model=PlotRead'):
        src.index("async def reactivate_plot(")
    ]
    assert "PermissionKey.PLOTS_DELETE" in block
    assert "get_rls_context" in block


async def test_reactivate_endpoint_logs_activity():
    plot = _plot(is_active=False)
    reactivated = _plot(id=plot.id, is_active=True)
    user = _user()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.repo.reactivate_plot", AsyncMock(return_value=reactivated)), \
         patch(f"{_P}._to_read", MagicMock(return_value=SimpleNamespace())), \
         patch(f"{_P}.ActivityLogger") as mk_logger_cls:
        mk_logger = mk_logger_cls.return_value
        mk_logger.log = AsyncMock()
        await reactivate_plot(plot_id=plot.id, current_user=user, db=_db())
    mk_logger.log.assert_awaited_once()
    assert mk_logger.log.call_args.kwargs["action"] == "plot.reactivated"
    assert mk_logger.log.call_args.kwargs["user"] is user


def test_reactivate_endpoint_refreshes_before_to_read_in_source():  # item 10
    src = inspect.getsource(reactivate_plot)
    assert "await db.refresh(" in src
    assert src.index("await db.refresh(") < src.index("_to_read(plot)")


# =============================================================================
# POST /plots/{plotId}/reactivate-with-cycle — endpoint (Part D)
# =============================================================================

async def test_reactivate_with_cycle_success_returns_plot_and_cycle():  # items 12/26
    plot = _plot(id=uuid4(), is_active=True)
    cycle = _cycle(cycle_no=2, status="active")
    db = _db()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=_plot(id=plot.id, is_active=False))), \
         patch(f"{_P}.repo.reactivate_plot_with_cycle", AsyncMock(return_value=(plot, cycle))) as mk_react, \
         patch(f"{_P}._to_read", MagicMock(return_value=_plot_read(id=plot.id, is_active=True))):
        result = await reactivate_plot_with_cycle(
            plot_id=plot.id, payload=_cycle_payload(crop="ทุเรียน"), current_user=_user(), db=db,
        )
    assert isinstance(result, PlotWithCycleCreateResult)
    assert result.plot.is_active is True
    assert result.cycle.cycle_no == 2
    assert result.cycle.status == "active"
    mk_react.assert_awaited_once()
    assert mk_react.call_args.kwargs["crop"] == "ทุเรียน"


async def test_reactivate_with_cycle_endpoint_never_touches_qr_or_phones_of_response_plot():
    """The response plot object is whatever reactivate_plot_with_cycle
    returned — the endpoint itself does no further field manipulation."""
    plot = _plot(is_active=True, qr_key="QR-KEEP", access_phones=["0898887777"])
    cycle = _cycle(cycle_no=2)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=_plot(id=plot.id, is_active=False))), \
         patch(f"{_P}.repo.reactivate_plot_with_cycle", AsyncMock(return_value=(plot, cycle))), \
         patch(f"{_P}._to_read", MagicMock(side_effect=lambda p: _plot_read(
             id=p.id, is_active=p.is_active, qr_key=p.qr_key,
         ))):
        result = await reactivate_plot_with_cycle(
            plot_id=plot.id, payload=_cycle_payload(), current_user=_user(), db=_db(),
        )
    assert result.plot.qr_key == "QR-KEEP"


async def test_reactivate_with_cycle_missing_plot_404():  # item 4 (with-cycle)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await reactivate_plot_with_cycle(
                plot_id=uuid4(), payload=_cycle_payload(), current_user=_user(), db=_db(),
            )
    assert exc.value.status_code == 404


async def test_reactivate_with_cycle_already_active_409():  # item 2 (with-cycle) / 21
    plot = _plot(is_active=False)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.repo.reactivate_plot_with_cycle", AsyncMock(side_effect=repo.PlotAlreadyActiveError("x"))):
        with pytest.raises(HTTPException) as exc:
            await reactivate_plot_with_cycle(
                plot_id=plot.id, payload=_cycle_payload(), current_user=_user(), db=_db(),
            )
    assert exc.value.status_code == 409


async def test_reactivate_with_cycle_inconsistent_active_cycle_409():  # item 3 (with-cycle)
    plot = _plot(is_active=False)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.repo.reactivate_plot_with_cycle", AsyncMock(side_effect=repo.PlotHasActiveCycleError("x"))):
        with pytest.raises(HTTPException) as exc:
            await reactivate_plot_with_cycle(
                plot_id=plot.id, payload=_cycle_payload(), current_user=_user(), db=_db(),
            )
    assert exc.value.status_code == 409


async def test_reactivate_with_cycle_lot_too_long_returns_422_no_success_path():  # item 17 (endpoint)
    plot = _plot(is_active=False)
    db = _db()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.repo.reactivate_plot_with_cycle", AsyncMock(side_effect=LotNumberTooLongError("x"))):
        with pytest.raises(HTTPException) as exc:
            await reactivate_plot_with_cycle(
                plot_id=plot.id, payload=_cycle_payload(), current_user=_user(), db=db,
            )
    assert exc.value.status_code == 422
    db.refresh.assert_not_awaited()


async def test_reactivate_with_cycle_integrityerror_becomes_409():  # item 18
    plot = _plot(is_active=False)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.repo.reactivate_plot_with_cycle",
               AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("dup")))):
        with pytest.raises(HTTPException) as exc:
            await reactivate_plot_with_cycle(
                plot_id=plot.id, payload=_cycle_payload(), current_user=_user(), db=_db(),
            )
    assert exc.value.status_code == 409


async def test_reactivate_with_cycle_unexpected_error_propagates():  # item 19
    plot = _plot(is_active=False)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.repo.reactivate_plot_with_cycle", AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError, match="boom"):
            await reactivate_plot_with_cycle(
                plot_id=plot.id, payload=_cycle_payload(), current_user=_user(), db=_db(),
            )


def test_reactivate_with_cycle_locks_plot_before_cycle_in_source():  # item 20
    src = inspect.getsource(reactivate_plot_with_cycle)
    assert "await repo.get_plot_for_update" in src
    assert "await repo.reactivate_plot_with_cycle" in src
    assert src.index("await repo.get_plot_for_update") < src.index("await repo.reactivate_plot_with_cycle")


def test_reactivate_with_cycle_route_requires_both_permissions_and_rls():  # items 22/23/44
    src = inspect.getsource(plots_module)
    block = src[
        src.index('"/{plot_id}/reactivate-with-cycle"'):
        src.index("async def reactivate_plot_with_cycle(")
    ]
    assert block.count("PermissionKey.PLOTS_DELETE") == 1
    assert block.count("PermissionKey.PLOTS_UPDATE") == 1
    assert "get_rls_context" in block


def test_reactivate_with_cycle_reuses_existing_camelcase_schemas():  # item 48
    """No new request/response schema was introduced this round — the
    endpoint reuses PlotCycleCreate (same as POST /{plotId}/cycles) and
    PlotWithCycleCreateResult (same as POST /plots/with-cycle) verbatim.
    `from __future__ import annotations` in plots.py turns annotations into
    strings, so resolve them with get_type_hints rather than raw .annotation.
    """
    import typing
    hints = typing.get_type_hints(reactivate_plot_with_cycle)
    assert hints["payload"] is PlotCycleCreate
    assert hints["return"] is PlotWithCycleCreateResult


async def test_reactivate_with_cycle_refreshes_before_serialise():  # item 10 (with-cycle)
    plot = _plot(is_active=False)
    cycle = _cycle()
    db = _db()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.repo.reactivate_plot_with_cycle", AsyncMock(return_value=(plot, cycle))), \
         patch(f"{_P}._to_read", MagicMock(return_value=_plot_read())):
        await reactivate_plot_with_cycle(
            plot_id=plot.id, payload=_cycle_payload(), current_user=_user(), db=db,
        )
    assert db.refresh.await_args_list == [
        call(plot),
        call(plot, attribute_names=["assignments", "supplier", "active_cycle", "access_phones"]),
        call(cycle),
    ]


async def test_reactivate_with_cycle_endpoint_logs_activity():
    plot = _plot(is_active=False)
    cycle = _cycle()
    user = _user()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.repo.reactivate_plot_with_cycle", AsyncMock(return_value=(plot, cycle))), \
         patch(f"{_P}._to_read", MagicMock(return_value=_plot_read())), \
         patch(f"{_P}.ActivityLogger") as mk_logger_cls:
        mk_logger = mk_logger_cls.return_value
        mk_logger.log = AsyncMock()
        await reactivate_plot_with_cycle(
            plot_id=plot.id, payload=_cycle_payload(), current_user=user, db=_db(),
        )
    assert mk_logger.log.call_args.kwargs["action"] == "plot.reactivated_with_cycle"


def test_no_public_reactivate_route():  # item 45
    import app.api.v1.public_plots as public_plots
    import app.api.v1.public_records as public_records
    for mod in (public_plots, public_records):
        assert not any("reactivate" in r.path.lower() for r in mod.router.routes), mod.__name__


# =============================================================================
# Deactivate invariant (Part B)
# =============================================================================

async def test_deactivate_rejects_when_active_cycle_exists_409_no_mutation():  # item 27
    plot = _plot(is_active=True)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=_cycle())), \
         patch(f"{_P}.repo.update_plot", AsyncMock()) as mk_update:
        with pytest.raises(HTTPException) as exc:
            await deactivate_plot(plot_id=plot.id, db=_db())
    assert exc.value.status_code == 409
    assert exc.value.detail == "กรุณาปิดรอบปลูกปัจจุบันก่อนปิดใช้งานแปลง"
    mk_update.assert_not_awaited()


async def test_deactivate_succeeds_when_no_active_cycle_and_clears_snapshot():  # item 28
    plot = _plot(is_active=True)
    updated_plot = _plot(id=plot.id, is_active=False)
    db = _db()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.update_plot", AsyncMock(return_value=updated_plot)) as mk_update, \
         patch(f"{_P}.plot_cycle_repo.clear_plot_cycle_mirror_and_inspection_snapshot", AsyncMock()) as mk_clear, \
         patch(f"{_P}._to_read", MagicMock(return_value=SimpleNamespace(is_active=False))):
        result = await deactivate_plot(plot_id=plot.id, db=db)
    assert result.is_active is False
    mk_update.assert_awaited_once()
    mk_clear.assert_awaited_once()
    assert db.refresh.await_args_list == [
        call(updated_plot),
        call(
            updated_plot,
            attribute_names=["assignments", "supplier", "active_cycle", "access_phones"],
        ),
    ]


def test_plot_activation_endpoints_refresh_scalars_before_serialising():
    for endpoint in (deactivate_plot, reactivate_plot, reactivate_plot_with_cycle):
        src = inspect.getsource(endpoint)
        assert "await db.refresh(plot)" in src
        assert src.index("await db.refresh(plot)") < src.index("_to_read(plot)")


def test_deactivate_locks_plot_before_checking_active_cycle_in_source():  # item 29
    src = inspect.getsource(deactivate_plot)
    assert "await repo.get_plot_for_update" in src
    assert "await plot_cycle_repo.get_active_cycle_for_plot_for_update" in src
    assert src.index("await repo.get_plot_for_update") < src.index(
        "await plot_cycle_repo.get_active_cycle_for_plot_for_update"
    )


# =============================================================================
# Security effects (Part H) — public_inspection_access.py is NOT touched this
# round; these prove the existing guard gives the right answer for the two
# new states reactivation can produce.
# =============================================================================

def test_public_inspection_guard_source_unchanged_still_requires_active_plot_and_cycle():
    """Confirms the EXISTING guard in public_inspection_access.py (untouched
    by round 8-6H) still requires BOTH plot.is_active AND an active_cycle —
    exactly what makes reactivate-only insufficient and reactivate-with-cycle
    sufficient for public inspection access to resume."""
    import app.api.v1.public_inspection_access as pia
    src = inspect.getsource(pia)
    assert "not plot.is_active" in src
    assert 'getattr(plot, "active_cycle", None)' in src
    assert '"no_active_cycle"' in src


def test_reactivate_only_outcome_still_blocks_public_guard_condition():
    """Mirrors the exact boolean guard public_inspection_access.py evaluates
    against the state reactivate_plot (Part C, no cycle) produces."""
    plot_after_reactivate_only = SimpleNamespace(is_active=True, active_cycle=None)
    can_inspect = (
        plot_after_reactivate_only.is_active
        and getattr(plot_after_reactivate_only, "active_cycle", None) is not None
    )
    assert can_inspect is False


def test_reactivate_with_cycle_outcome_satisfies_public_guard_condition():
    """Same guard, against the state reactivate_plot_with_cycle (Part D)
    produces — plot active AND a real active cycle."""
    plot_after_reactivate_with_cycle = SimpleNamespace(
        is_active=True, active_cycle=SimpleNamespace(id=uuid4()),
    )
    can_inspect = (
        plot_after_reactivate_with_cycle.is_active
        and getattr(plot_after_reactivate_with_cycle, "active_cycle", None) is not None
    )
    assert can_inspect is True

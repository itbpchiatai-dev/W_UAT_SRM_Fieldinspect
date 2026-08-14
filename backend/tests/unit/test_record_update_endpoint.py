"""POST /{id}/deactivate — plot-snapshot resync wiring, and confirmation
that the generic PATCH /api/v1/records/{recordId} route no longer exists
(round 8.0.5 append-only lock: a record's inspection fields can never
change after creation — the only allowed mutation is deactivate).

No DB fixture exists in this repo — mocks the repository calls, matching
tests/unit/test_record_create_endpoint.py. The deactivate route is not
rate-limited, so it's callable directly with no .__wrapped__ unwrap.
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.api.v1.records as records_module
from app.api.v1.records import deactivate_record

_MODULE = "app.api.v1.records"


def _fake_record(**overrides):
    defaults = dict(
        id=uuid4(), plot_id=uuid4(), supplier_id=uuid4(),
        record_date=datetime.date(2026, 7, 1),
        submitted_by_code="FIELD01", submitted_by_name=None,
        created_at=datetime.datetime.now(datetime.timezone.utc),
        plot=None, supplier=None, recorded_by=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.refresh = AsyncMock()
    return db


# --- append-only lock: no PATCH route ---------------------------------

def test_no_patch_record_route_exists() -> None:
    """The generic edit route must not exist at all — not disabled, not
    permission-gated to nobody, simply absent from the router."""
    assert not any(
        "PATCH" in r.methods and r.path == "/{record_id}"
        for r in records_module.router.routes
    )


def test_records_module_has_no_update_record_route_function() -> None:
    assert not hasattr(records_module, "update_record")


# --- deactivate: the only allowed mutation -----------------------------

async def test_deactivate_calls_the_dedicated_deactivate_helper_not_generic_update() -> None:
    """Round 8.0.5 — deactivate must go through record_repository.
    deactivate_record (is_active=False only), never the generic
    update_record(record, RecordUpdate(...)) field-loop."""
    record = _fake_record()

    with patch(f"{_MODULE}.repo.get_record_scoped", AsyncMock(return_value=record)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock()), \
         patch(f"{_MODULE}.repo.deactivate_record", AsyncMock()) as mocked_deactivate, \
         patch(f"{_MODULE}.repo.update_record", AsyncMock()) as mocked_generic_update, \
         patch(f"{_MODULE}.plot_repo.resync_current_status_from_latest", AsyncMock()), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=record)), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await deactivate_record(record_id=record.id, scope=[], db=_mock_db())

    mocked_deactivate.assert_awaited_once()
    assert mocked_deactivate.call_args.args[1] is record
    mocked_generic_update.assert_not_awaited()


async def test_deactivate_resyncs_plot_snapshot() -> None:
    """Deactivating (possibly the latest) record must roll the plot snapshot
    back to the newest record that still counts."""
    record = _fake_record()

    with patch(f"{_MODULE}.repo.get_record_scoped", AsyncMock(return_value=record)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock()), \
         patch(f"{_MODULE}.repo.deactivate_record", AsyncMock()) as mocked_deactivate, \
         patch(f"{_MODULE}.plot_repo.resync_current_status_from_latest", AsyncMock()) as mocked_resync, \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=record)), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await deactivate_record(record_id=record.id, scope=[], db=_mock_db())

    mocked_deactivate.assert_awaited_once()
    mocked_resync.assert_awaited_once()
    resync_args, _ = mocked_resync.call_args
    assert resync_args[1] == record.plot_id


async def test_deactivate_resync_runs_after_the_deactivate_not_before() -> None:
    """Ordering matters: resync re-reads the records table — running it
    before is_active flips would recompute the snapshot from the still-active
    record."""
    call_order: list[str] = []

    async def _fake_deactivate(*args, **kwargs):
        call_order.append("deactivate")

    async def _fake_resync(*args, **kwargs):
        call_order.append("resync")

    with patch(f"{_MODULE}.repo.get_record_scoped", AsyncMock(return_value=_fake_record())), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock()), \
         patch(f"{_MODULE}.repo.deactivate_record", _fake_deactivate), \
         patch(f"{_MODULE}.plot_repo.resync_current_status_from_latest", _fake_resync), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record())), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await deactivate_record(record_id=uuid4(), scope=[], db=_mock_db())

    assert call_order == ["deactivate", "resync"]


async def test_deactivate_locks_the_plot_before_flipping_is_active() -> None:
    """Round 8.0.7 — the plot row must be locked (get_plot_for_update)
    BEFORE the deactivate write, so a concurrent rollover on the same plot
    can't interleave between this deactivate and its resync."""
    record = _fake_record()
    call_order: list[str] = []

    async def _fake_lock(*args, **kwargs):
        call_order.append("lock_plot")

    async def _fake_deactivate(*args, **kwargs):
        call_order.append("deactivate")

    async def _fake_resync(*args, **kwargs):
        call_order.append("resync")

    with patch(f"{_MODULE}.repo.get_record_scoped", AsyncMock(return_value=record)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", _fake_lock), \
         patch(f"{_MODULE}.repo.deactivate_record", _fake_deactivate), \
         patch(f"{_MODULE}.plot_repo.resync_current_status_from_latest", _fake_resync), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=record)), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await deactivate_record(record_id=record.id, scope=[], db=_mock_db())

    assert call_order == ["lock_plot", "deactivate", "resync"]


async def test_deactivate_404_outside_scope_touches_nothing() -> None:
    with patch(f"{_MODULE}.repo.get_record_scoped", AsyncMock(return_value=None)), \
         patch(f"{_MODULE}.repo.deactivate_record", AsyncMock()) as mocked_deactivate, \
         patch(f"{_MODULE}.plot_repo.resync_current_status_from_latest", AsyncMock()) as mocked_resync:
        with pytest.raises(HTTPException) as exc_info:
            await deactivate_record(record_id=uuid4(), scope=[], db=_mock_db())

    assert exc_info.value.status_code == 404
    mocked_deactivate.assert_not_awaited()
    mocked_resync.assert_not_awaited()


async def test_deactivate_resync_failure_propagates_instead_of_being_swallowed() -> None:
    """If the snapshot recompute fails, the exception must reach FastAPI's
    get_db dependency uncaught so the whole transaction — including the
    deactivate — rolls back, never leaving the two out of step."""
    with patch(f"{_MODULE}.repo.get_record_scoped", AsyncMock(return_value=_fake_record())), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock()), \
         patch(f"{_MODULE}.repo.deactivate_record", AsyncMock()), \
         patch(f"{_MODULE}.plot_repo.resync_current_status_from_latest",
               AsyncMock(side_effect=ValueError("plot not found"))):
        with pytest.raises(ValueError):
            await deactivate_record(record_id=uuid4(), scope=[], db=_mock_db())

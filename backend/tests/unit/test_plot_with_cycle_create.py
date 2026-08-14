"""POST /plots/with-cycle (round 8.0.4) — atomically create a physical Plot
AND its first active PlotCycle in one transaction.

DB-less: mocks the repo helpers and calls the route function directly (same
pattern as test_plot_cycle_rollover.py). The all-or-nothing transaction is a
property of the caller's single get_db session — create_plot/create_cycle
only flush — so "rollback on cycle failure" is asserted structurally (the
exception propagates uncaught) rather than against a live DB.
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
from app.api.v1.plots import create_plot_with_cycle
from app.schemas.plot import (
    PlotAccessPhoneConfig,
    PlotCreate,
    PlotCycleCreate,
    PlotWithCycleCreate,
)
from app.services.lot_number import AutoLotMissingComponentError, LotNumberTooLongError

_NOW = datetime.datetime(2026, 7, 15, tzinfo=datetime.timezone.utc)
_P = "app.api.v1.plots"


def _plot(**o):
    d = dict(
        id=uuid4(), supplier_id=uuid4(), plot_code="P101", name="แปลง A",
        village=None, district=None, province=None,
        latitude=None, longitude=None, rai=None,
        is_active=True, assignments=[], supplier=None, active_cycle=None,
        qr_key="qr-abc", created_at=_NOW, updated_at=_NOW,
    )
    d.update(o)
    return SimpleNamespace(**d)


def _cycle(**o):
    d = dict(
        id=uuid4(), plot_id=uuid4(), cycle_no=1, status="active",
        crop="พริก", variety="พริกขี้หนู", cycle_label="jun2026", lot_no="LOT-01",
        planting_date=datetime.date(2026, 6, 1), plant_count=1000,
        expected_yield_full=800, expected_yield_unit="kg",
        started_at=_NOW, closed_at=None, closed_by_id=None, close_reason=None,
        created_at=_NOW, updated_at=_NOW,
    )
    d.update(o)
    return SimpleNamespace(**d)


def _db():
    return AsyncMock()


def _user(**o):
    d = dict(id=uuid4(), roles=[SimpleNamespace(name="internal:super_admin")], supplier_id=None)
    d.update(o)
    return SimpleNamespace(**d)


def _payload(**o) -> PlotWithCycleCreate:
    d = dict(
        plot=PlotCreate(supplierId=uuid4(), plotCode="P101", name="แปลง A"),
        cycle=PlotCycleCreate(
            cycleLabel="jun2026", crop="พริก", variety="พริกขี้หนู", lotNo="LOT-01",
            poNumber="PO25001", pCode="Melon-A",
            plantingDate=datetime.date(2026, 6, 1), plantCount=1000,
            expectedYieldFull=800, expectedYieldUnit="kg",
        ),
    )
    d.update(o)
    return PlotWithCycleCreate(**d)


# --- schema shape -----------------------------------------------------------

def test_with_cycle_create_rejects_extra_top_level_fields() -> None:
    with pytest.raises(ValidationError):
        PlotWithCycleCreate(
            plot=PlotCreate(supplierId=uuid4(), plotCode="P1", name="A"),
            cycle=PlotCycleCreate(),
            qrKey="sneaky",
        )


def test_with_cycle_create_nested_camelcase_round_trips() -> None:
    sid = uuid4()
    p = PlotWithCycleCreate.model_validate({
        "plot": {"supplierId": str(sid), "plotCode": "P1", "name": "A"},
        "cycle": {"cycleLabel": "aug2026", "poNumber": "po25001", "pCode": "Melon-A", "expectedYieldFull": "500", "expectedYieldUnit": "kg"},
    })
    assert p.plot.supplier_id == sid
    assert p.cycle.cycle_label == "aug2026"


# --- endpoint success ---------------------------------------------------

async def test_with_cycle_create_success_returns_201_shape() -> None:
    sid = uuid4()
    plot = _plot(supplier_id=sid)
    cycle = _cycle(plot_id=plot.id, cycle_no=1, status="active")
    payload = _payload(plot=PlotCreate(supplierId=sid, plotCode="P101", name="แปลง A"))

    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.create_plot", AsyncMock(return_value=plot)) as mk_create_plot, \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=cycle)) as mk_create_cycle:
        result = await create_plot_with_cycle(payload=payload, current_user=_user(), db=_db())

    mk_create_plot.assert_awaited_once()
    assert mk_create_plot.call_args.args[1] is payload.plot
    assert result.cycle.id == cycle.id
    assert result.cycle.status == "active"
    assert result.cycle.cycle_no == 1
    assert result.plot.id == plot.id
    mk_create_cycle.assert_awaited_once()
    kw = mk_create_cycle.call_args.kwargs
    assert kw["cycle_label"] == "jun2026"
    assert kw["crop"] == "พริก"
    assert kw["plant_count"] == 1000
    assert kw["expected_yield_full"] == 800
    assert kw["expected_yield_unit"] == "kg"
    # plantingDate-derived startedAt (00:00 UTC), same rule as start_plot_cycle.
    assert kw["started_at"] == datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)


async def test_with_cycle_create_passes_the_created_plot_into_create_cycle() -> None:
    """create_cycle must be called with the just-created Plot object (so its
    mirror gets synced), not some other/stale plot."""
    plot = _plot()
    cycle = _cycle(plot_id=plot.id)
    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.create_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=cycle)) as mk_create_cycle:
        await create_plot_with_cycle(payload=_payload(), current_user=_user(), db=_db())
    assert mk_create_cycle.call_args.args[1] is plot


async def test_with_cycle_create_refreshes_plot_and_cycle_before_serialise() -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id)
    db = _db()
    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.create_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=cycle)):
        await create_plot_with_cycle(payload=_payload(), current_user=_user(), db=db)
    # round 8-3A: access_phones is refreshed too, so PlotRead's primary/
    # additionalPhones reflect any created config.
    assert call(
        plot, attribute_names=["assignments", "supplier", "active_cycle", "access_phones"]
    ) in db.refresh.await_args_list
    assert call(cycle) in db.refresh.await_args_list


# --- round 8-17A.1: MissingGreenlet fix regression coverage -----------------
#
# Root cause (confirmed against real source, not guessed): create_cycle's
# sync_plot_mirror_from_cycle UPDATEs the plot row (mirror columns), which
# expires plot's server-side onupdate column (updated_at, via TimestampMixin)
# — SQLAlchemy cannot know the new server-computed value without a re-SELECT.
# `db.refresh(plot, attribute_names=[...])` only reloads the NAMED
# attributes, never the scalar columns it omits, so `updated_at` stayed
# expired and PlotRead.model_validate's read of it triggered a lazy SQL load
# outside the request's greenlet on a REAL asyncpg connection — a 500
# MissingGreenlet. A DB-less unit test with SimpleNamespace fixtures cannot
# reproduce the actual asyncpg failure mode (there is no lazy-loading
# machinery to trigger) — see Part G of the round report for the real
# PostgreSQL verification. What these CAN and do assert: the exact call
# order the fix depends on, and a faithful simulation of "an unrefreshed
# scalar stays wrong" using a fake session that mimics the real
# attribute_names-scoping rule.

class _ExpiringDb:
    """A fake AsyncSession that mimics ONE real SQLAlchemy behavior:
    `refresh(obj)` (no attribute_names) reloads EVERY tracked scalar;
    `refresh(obj, attribute_names=[...])` reloads ONLY those names. Started
    with `plot.updated_at` set to a sentinel that is not a valid datetime —
    if code serializes the plot without ever calling the bare `refresh(plot)`
    variant, `updated_at` is still the sentinel and PlotRead.model_validate
    raises a pydantic ValidationError, exactly the "stayed wrong because
    never refreshed" failure this fixture exists to catch (a stand-in for
    the real MissingGreenlet, which needs a live asyncpg connection — Part
    G). Every other AsyncSession method (add/flush/commit/etc.) is a no-op
    AsyncMock, same as the plain `_db()` helper elsewhere in this file.
    """

    _UNREFRESHED = "UNREFRESHED-SENTINEL-NOT-A-DATETIME"

    def __init__(self, plot: SimpleNamespace, fresh_updated_at: datetime.datetime) -> None:
        self._plot = plot
        self._fresh_updated_at = fresh_updated_at
        plot.updated_at = self._UNREFRESHED
        self.refresh_calls: list[tuple] = []
        for name in ("add", "flush", "commit", "rollback", "execute"):
            setattr(self, name, AsyncMock())

    async def refresh(self, obj, attribute_names=None):
        self.refresh_calls.append((obj, attribute_names))
        if obj is self._plot and attribute_names is None:
            # The bare call — the ONE variant that reloads scalar columns
            # like updated_at (round 8-17A.1's fix).
            self._plot.updated_at = self._fresh_updated_at


async def test_with_cycle_create_scalar_refresh_runs_before_relationship_refresh_and_serialise() -> None:
    """The regression this round fixes, reproduced at the unit level: without
    a bare `db.refresh(plot)` BEFORE the attribute_names-scoped one,
    `plot.updated_at` stays the un-refreshed sentinel and
    PlotWithCycleCreateResult(plot=_to_read(plot), ...) fails to serialize
    it as a datetime. With the fix in place (current source), it must not
    raise, and the bare refresh must be the FIRST refresh call — before both
    the attribute_names-scoped one and the cycle refresh."""
    plot = _plot()
    cycle = _cycle(plot_id=plot.id)
    db = _ExpiringDb(plot, fresh_updated_at=_NOW)

    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.create_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=cycle)):
        result = await create_plot_with_cycle(payload=_payload(), current_user=_user(), db=db)

    # Serialized successfully — updated_at is the FRESH value, not the sentinel.
    assert result.plot.updated_at == _NOW

    # Order: bare refresh(plot) first, THEN the attribute_names-scoped one,
    # THEN the cycle refresh. Any other order reintroduces the bug.
    assert len(db.refresh_calls) == 3
    (obj0, names0), (obj1, names1), (obj2, names2) = db.refresh_calls
    assert obj0 is plot and names0 is None
    assert obj1 is plot and names1 == ["assignments", "supplier", "active_cycle", "access_phones"]
    assert obj2 is cycle and names2 is None


async def test_with_cycle_create_would_fail_without_the_scalar_refresh() -> None:
    """Negative control for the test above — proves _ExpiringDb's sentinel
    mechanism actually fires when the bare refresh is skipped, so the
    positive test isn't passing vacuously. Calls `_to_read` directly (the
    same function the endpoint calls) on a plot that was only ever given the
    attribute_names-scoped refresh, which is exactly the pre-fix code path's
    end state."""
    plot = _plot()
    db = _ExpiringDb(plot, fresh_updated_at=_NOW)
    await db.refresh(plot, attribute_names=["assignments", "supplier", "active_cycle", "access_phones"])

    with pytest.raises(ValidationError):
        plots_module._to_read(plot)


# --- guards -------------------------------------------------------------

async def test_with_cycle_create_duplicate_plot_code_409() -> None:
    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=_plot())), \
         patch(f"{_P}.repo.create_plot", AsyncMock()) as mk_create_plot:
        with pytest.raises(HTTPException) as exc:
            await create_plot_with_cycle(payload=_payload(), current_user=_user(), db=_db())
    assert exc.value.status_code == 409
    mk_create_plot.assert_not_awaited()


async def test_with_cycle_create_supplier_scoped_user_cannot_target_foreign_supplier_403() -> None:
    own_supplier = uuid4()
    other_supplier = uuid4()
    user = _user(
        roles=[SimpleNamespace(name="supplier:owner")],
        supplier_id=own_supplier,
    )
    payload = _payload(plot=PlotCreate(supplierId=other_supplier, plotCode="P1", name="A"))
    with patch(f"{_P}.repo.create_plot", AsyncMock()) as mk_create_plot:
        with pytest.raises(HTTPException) as exc:
            await create_plot_with_cycle(payload=payload, current_user=user, db=_db())
    assert exc.value.status_code == 403
    mk_create_plot.assert_not_awaited()


async def test_with_cycle_create_cycle_failure_propagates_for_rollback() -> None:
    """A cycle-create failure that ISN'T an IntegrityError must propagate
    uncaught so the get_db transaction rolls the plot insert back too — the
    endpoint has no try/except that would swallow it and strand a plot with
    no cycle."""
    plot = _plot()
    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.create_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError, match="boom"):
            await create_plot_with_cycle(payload=_payload(), current_user=_user(), db=_db())


async def test_with_cycle_create_integrityerror_becomes_clean_409() -> None:
    plot = _plot()
    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.create_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle",
               AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("dup")))):
        with pytest.raises(HTTPException) as exc:
            await create_plot_with_cycle(payload=_payload(), current_user=_user(), db=_db())
    assert exc.value.status_code == 409
    assert exc.value.detail  # non-empty, no raw stack trace leaked


async def test_with_cycle_create_auto_lot_missing_component_becomes_clean_422() -> None:
    """Round 8-17A.1 Part C item 7 — pinned for THIS endpoint specifically
    (existing coverage was only on start_plot_cycle/update_plot_cycle)."""
    plot = _plot()
    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.create_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle",
               AsyncMock(side_effect=AutoLotMissingComponentError(("cycleLabel",)))):
        with pytest.raises(HTTPException) as exc:
            await create_plot_with_cycle(payload=_payload(), current_user=_user(), db=_db())
    assert exc.value.status_code == 422
    assert exc.value.detail


async def test_with_cycle_create_lot_number_too_long_becomes_clean_422() -> None:
    plot = _plot()
    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.create_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle",
               AsyncMock(side_effect=LotNumberTooLongError("too long"))):
        with pytest.raises(HTTPException) as exc:
            await create_plot_with_cycle(payload=_payload(), current_user=_user(), db=_db())
    assert exc.value.status_code == 422
    assert exc.value.detail


# --- access phones (round 8-3A) --------------------------------------------

async def test_with_cycle_create_omitted_access_phones_keeps_old_behavior() -> None:
    """No accessPhones → the phone repo is never called; behavior unchanged."""
    plot = _plot()
    cycle = _cycle(plot_id=plot.id)
    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.create_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.phone_repo.replace_plot_access_phones", AsyncMock()) as mk_phones:
        await create_plot_with_cycle(payload=_payload(), current_user=_user(), db=_db())
    mk_phones.assert_not_awaited()


async def test_with_cycle_create_provided_access_phones_creates_them() -> None:
    """accessPhones provided → replace_plot_access_phones is called with the
    just-created plot and the config, in the same transaction."""
    plot = _plot()
    cycle = _cycle(plot_id=plot.id)
    cfg = PlotAccessPhoneConfig(primaryPhone="0845552162", additionalPhones=["0812345678"])
    payload = _payload(accessPhones=cfg)
    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.create_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.phone_repo.replace_plot_access_phones", AsyncMock(return_value=[])) as mk_phones:
        await create_plot_with_cycle(payload=payload, current_user=_user(), db=_db())
    mk_phones.assert_awaited_once()
    assert mk_phones.call_args.args[1] is plot          # the created plot
    assert mk_phones.call_args.args[2] is payload.access_phones


async def test_with_cycle_create_phone_failure_propagates_for_rollback() -> None:
    """A phone-config failure that isn't an IntegrityError must propagate
    uncaught so the get_db transaction rolls the plot AND cycle back too."""
    plot = _plot()
    cycle = _cycle(plot_id=plot.id)
    cfg = PlotAccessPhoneConfig(primaryPhone="0845552162")
    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.create_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.phone_repo.replace_plot_access_phones",
               AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError, match="boom"):
            await create_plot_with_cycle(
                payload=_payload(accessPhones=cfg), current_user=_user(), db=_db()
            )


async def test_with_cycle_create_phone_integrityerror_becomes_clean_409() -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id)
    cfg = PlotAccessPhoneConfig(primaryPhone="0845552162")
    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch(f"{_P}.repo.create_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.create_cycle", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.phone_repo.replace_plot_access_phones",
               AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("dup")))):
        with pytest.raises(HTTPException) as exc:
            await create_plot_with_cycle(
                payload=_payload(accessPhones=cfg), current_user=_user(), db=_db()
            )
    assert exc.value.status_code == 409


# --- round 8-17C: invalid accessPhones is 422, never echoed, fails first ---

async def test_with_cycle_create_invalid_access_phones_is_422_never_echoed_before_any_db_call() -> None:
    """normalize_and_validate_phone_config runs first thing in the endpoint —
    before the scope check, the plot_code uniqueness lookup, or create_plot —
    exactly where Pydantic's automatic validation used to run (before the
    endpoint function was even called at all)."""
    secret_bad_phone = "0712345678-not-a-real-number"
    cfg = PlotAccessPhoneConfig(primaryPhone=secret_bad_phone)
    with patch(f"{_P}.repo.get_plot_by_code", AsyncMock()) as mk_lookup, \
         patch(f"{_P}.repo.create_plot", AsyncMock()) as mk_create:
        with pytest.raises(HTTPException) as exc:
            await create_plot_with_cycle(
                payload=_payload(accessPhones=cfg), current_user=_user(), db=_db()
            )
    assert exc.value.status_code == 422
    assert secret_bad_phone not in exc.value.detail
    mk_lookup.assert_not_awaited()
    mk_create.assert_not_awaited()


def test_with_cycle_create_access_phones_lives_on_wrapper_not_plotcreate() -> None:
    """Phone access is a sub-resource — accessPhones is on the wrapper, never on
    PlotCreate."""
    assert "access_phones" in PlotWithCycleCreate.model_fields
    assert "access_phones" not in PlotCreate.model_fields


# --- permission / wiring / no-public --------------------------------------

def test_with_cycle_create_route_requires_plots_create_and_rls_context() -> None:
    src = inspect.getsource(plots_module)
    block = src[
        src.index('@router.post(\n    "/with-cycle"'):
        src.index("async def create_plot_with_cycle")
    ]
    assert "require_permission(PermissionKey.PLOTS_CREATE)" in block
    assert "get_rls_context" in block
    assert "status_code=status.HTTP_201_CREATED" in block


def test_with_cycle_create_route_registered_before_plot_id_path() -> None:
    """/with-cycle must be registered before /{plot_id} or FastAPI would try
    to parse "with-cycle" as a plot UUID (same reasoning as /lookup,
    /provinces, /import-template already documented in this module)."""
    paths = [r.path for r in plots_module.router.routes if "with-cycle" in r.path or r.path.endswith("/{plot_id}")]
    with_cycle_index = next(i for i, p in enumerate(paths) if "with-cycle" in p)
    plot_id_index = next(i for i, p in enumerate(paths) if p.endswith("/{plot_id}"))
    assert with_cycle_index < plot_id_index


def test_no_public_with_cycle_route() -> None:
    import app.api.v1.public_plots as public_plots
    import app.api.v1.public_records as public_records
    for mod in (public_plots, public_records):
        assert not any("with-cycle" in r.path.lower() for r in mod.router.routes), mod.__name__


def test_with_cycle_create_never_accepts_qr_key_from_client() -> None:
    assert "qr_key" not in PlotCreate.model_fields
    assert "qr_key" not in PlotCycleCreate.model_fields

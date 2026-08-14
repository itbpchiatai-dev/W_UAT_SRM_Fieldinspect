"""POST /api/v1/public/records — offline submission (round 8-4A).

DB-less, same style as test_public_record_create_endpoint.py: mocks the repo
calls and exercises the real endpoint logic via `.__wrapped__`. Covers the
offline request contract, idempotent replay, cycle-conflict, captured_at
window, and the concurrent-insert race backstop.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import asyncpg.exceptions as asyncpg_exc
import pytest
from fastapi import HTTPException, Response
from sqlalchemy.exc import IntegrityError

from app.api.v1.public_records import (
    create_record_public,
    create_record_with_photos_public,
)
from app.auth.inspection_session import encode_inspection_session_token
from app.schemas.record import PublicRecordCreate

_create = create_record_public.__wrapped__
_create_with_photos = create_record_with_photos_public.__wrapped__
_M = "app.api.v1.public_records"


# --- fixtures / builders ----------------------------------------------------

@pytest.fixture(autouse=True)
def _stub_protocol_map():
    from app.services.inspection_protocols import default_protocol_map
    with patch(f"{_M}.protocol_service.get_protocol_map",
               AsyncMock(return_value=default_protocol_map())):
        yield


def _cycle(**o):
    # expected_yield_full/unit (round 8-8A) default None — no test in this
    # file sends yield_quantity_kg.
    d = dict(id=uuid4(), crop="พริก", variety="พริกขี้หนู",
             planting_date=datetime.date(2026, 1, 1),
             expected_yield_full=None, expected_yield_unit=None)
    d.update(o)
    return SimpleNamespace(**d)


def _supplier(**o):
    d = dict(id=uuid4(), code="SUP001", name="Supplier One", is_active=True)
    d.update(o)
    return SimpleNamespace(**d)


def _plot(supplier_id, **o):
    d = dict(id=uuid4(), plot_code="PLOT001", name="Plot One", is_active=True,
             supplier_id=supplier_id)
    d.update(o)
    return SimpleNamespace(**d)


def _access(**o):
    d = dict(id=uuid4(), phone_normalized="0812345678", access_type="primary")
    d.update(o)
    return SimpleNamespace(**d)


def _system_user():
    return SimpleNamespace(id=uuid4())


def _record(plot, access, cycle, *, client_submission_id, captured_at,
            inspector_type="farmer", **o):
    d = dict(
        id=uuid4(), plot_id=plot.id, record_date=datetime.date(2026, 7, 1),
        submitted_by_name=None,
        created_at=datetime.datetime.now(datetime.timezone.utc),
        client_submission_id=client_submission_id, captured_at=captured_at,
        plot_access_phone_id=access.id, inspector_type=inspector_type,
    )
    d.update(o)
    return SimpleNamespace(**d)


def _token(plot, supplier, cycle, access, inspector_type="farmer"):
    token, _ = encode_inspection_session_token(
        plot_id=plot.id, supplier_id=supplier.id, plot_cycle_id=cycle.id,
        plot_access_phone_id=access.id, inspector_type=inspector_type,
    )
    return token


def _offline_payload(token, *, key, captured_at, captured_cycle_id, **o):
    d = dict(
        inspection_session_token=token,
        record_date=datetime.date(2026, 7, 1),
        client_submission_id=key,
        captured_at=captured_at,
        captured_plot_cycle_id=captured_cycle_id,
    )
    d.update(o)
    return PublicRecordCreate(**d)


class _AsyncCM:
    """Stand-in for db.begin_nested()'s async savepoint context manager."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False  # never suppress — let IntegrityError propagate


def _db():
    db = MagicMock()
    db.refresh = AsyncMock()
    db.begin_nested = MagicMock(return_value=_AsyncCM())
    return db


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


_CLIENT_SUBMISSION_INDEX = "uq_records_client_submission_id"


def _integrity_error(constraint_name, exc_class=asyncpg_exc.UniqueViolationError):
    """Reproduce the REAL runtime exception shape (verified round 8-4A.1 against
    a live partial-unique-index violation on this DB): a SQLAlchemy
    IntegrityError whose `.orig.__cause__` is an asyncpg PostgresError carrying
    `constraint_name`. NOT a bare Exception("duplicate key") — the classifier
    reads constraint_name off this exact chain, so a faithful shape is required
    to test it (Part 8)."""
    pg_err = exc_class("violation")
    pg_err.constraint_name = constraint_name
    # Stands in for the sqlalchemy.dialects.postgresql.asyncpg.IntegrityError
    # layer, whose __cause__ is the asyncpg driver error.
    dialect_err = Exception("asyncpg dialect wrapper")
    dialect_err.__cause__ = pg_err
    return IntegrityError("INSERT INTO records ...", {}, dialect_err)


def _dup_key_error():
    """The idempotency-index collision (our unique index)."""
    return _integrity_error(_CLIENT_SUBMISSION_INDEX)


# --- schema-level validation (422) ------------------------------------------

def test_offline_fields_incomplete_rejected_422() -> None:
    """All three offline fields must arrive together — a partial set (here only
    client_submission_id) is a schema ValidationError (→ 422)."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PublicRecordCreate(
            inspection_session_token="t", record_date=datetime.date(2026, 7, 1),
            client_submission_id=uuid4(),  # captured_at + captured_plot_cycle_id missing
        )


def test_naive_captured_at_rejected_422() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PublicRecordCreate(
            inspection_session_token="t", record_date=datetime.date(2026, 7, 1),
            client_submission_id=uuid4(),
            captured_at=datetime.datetime(2026, 7, 1, 10, 0, 0),  # naive
            captured_plot_cycle_id=uuid4(),
        )


def test_online_payload_still_valid_without_any_offline_field() -> None:
    """Backward compatibility: an online client omits all three and is fine."""
    p = PublicRecordCreate(
        inspection_session_token="t", record_date=datetime.date(2026, 7, 1),
    )
    assert p.client_submission_id is None
    assert p.captured_at is None
    assert p.captured_plot_cycle_id is None


# --- captured_at window (422 with structured code) --------------------------

async def test_captured_at_in_future_beyond_skew_rejected_422() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    payload = _offline_payload(
        token, key=uuid4(), captured_at=_now() + datetime.timedelta(minutes=30),
        captured_cycle_id=cycle.id,
    )

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", AsyncMock(return_value=None)), \
         patch(f"{_M}.record_repo.create_record", AsyncMock()) as mk_create, \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await _create(payload=payload, request=AsyncMock(), db=_db())

    assert exc.value.status_code == 422
    assert exc.value.detail == {"code": "offline_captured_at_invalid"}
    mk_create.assert_not_awaited()


async def test_captured_at_older_than_seven_days_rejected_422_draft_expired() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    payload = _offline_payload(
        token, key=uuid4(), captured_at=_now() - datetime.timedelta(days=8),
        captured_cycle_id=cycle.id,
    )

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", AsyncMock(return_value=None)), \
         patch(f"{_M}.record_repo.create_record", AsyncMock()) as mk_create, \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await _create(payload=payload, request=AsyncMock(), db=_db())

    assert exc.value.status_code == 422
    assert exc.value.detail == {"code": "offline_draft_expired"}
    mk_create.assert_not_awaited()


# --- first submit (201) -----------------------------------------------------

async def test_first_offline_submit_creates_record_201_stores_key_and_captured_at() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(hours=2)
    payload = _offline_payload(token, key=key, captured_at=captured, captured_cycle_id=cycle.id)
    created = _record(plot, access, cycle, client_submission_id=key, captured_at=captured)
    # Seed with the route's declared 201 (FastAPI applies status_code=201 in a
    # real request; a direct .__wrapped__ call doesn't) — the create handler
    # must leave it untouched, only a replay/duplicate forces it to 200.
    response = Response()
    response.status_code = 201

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", AsyncMock(return_value=None)), \
         patch(f"{_M}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_M}.record_repo.create_record", AsyncMock(return_value=created)) as mk_create, \
         patch(f"{_M}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        result = await _create(payload=payload, request=AsyncMock(), db=_db(), response=response)

    # The create handler must NOT force 200 — it leaves the route's 201.
    assert response.status_code == 201
    assert result.client_submission_id == key
    assert result.captured_at == captured
    # create_record got the key + capture time as kwargs.
    assert mk_create.call_args.kwargs["client_submission_id"] == key
    assert mk_create.call_args.kwargs["captured_at"] == captured


# --- idempotent replay (200) ------------------------------------------------

async def test_same_key_retry_returns_existing_record_200_no_new_insert() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(hours=1)
    existing = _record(plot, access, cycle, client_submission_id=key, captured_at=captured)
    payload = _offline_payload(token, key=key, captured_at=captured, captured_cycle_id=cycle.id)
    response = Response()

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", AsyncMock(return_value=existing)), \
         patch(f"{_M}.record_repo.create_record", AsyncMock()) as mk_create, \
         patch(f"{_M}.plot_repo.sync_current_status_from_record", AsyncMock()) as mk_sync, \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        result = await _create(payload=payload, request=AsyncMock(), db=_db(), response=response)

    assert response.status_code == 200
    assert result.id == existing.id
    assert result.client_submission_id == key
    # A replay creates nothing and never re-syncs the plot snapshot (Part D.7).
    mk_create.assert_not_awaited()
    mk_sync.assert_not_awaited()


async def test_kg_offline_replay_returns_existing_record_no_new_insert() -> None:
    """Round 8-8A — an offline draft carrying yield_quantity_kg, replayed with
    the same client_submission_id, must short-circuit exactly like a
    yieldPct-only draft: no second insert, and (since the short-circuit skips
    _finish_creating_record entirely) the yield-kg derivation never re-runs
    on the replay either."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(hours=1)
    existing = _record(
        plot, access, cycle, client_submission_id=key, captured_at=captured,
        yield_pct=Decimal("80.0"),
    )
    payload = _offline_payload(
        token, key=key, captured_at=captured, captured_cycle_id=cycle.id,
        yield_quantity_kg=Decimal("800"),
    )
    response = Response()

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", AsyncMock(return_value=existing)), \
         patch(f"{_M}.record_repo.create_record", AsyncMock()) as mk_create, \
         patch(f"{_M}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        result = await _create(payload=payload, request=AsyncMock(), db=_db(), response=response)

    assert response.status_code == 200
    assert result.id == existing.id
    mk_create.assert_not_awaited()


async def test_replay_never_resolves_or_locks_the_active_cycle() -> None:
    """A replay short-circuits before cycle resolution — proven by the active
    cycle lookups never being awaited (this is what lets a replay succeed even
    after a rollover, Part D.5)."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(hours=1)
    existing = _record(plot, access, cycle, client_submission_id=key, captured_at=captured)
    payload = _offline_payload(token, key=key, captured_at=captured, captured_cycle_id=cycle.id)

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", AsyncMock(return_value=existing)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock()) as mk_cycle, \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        await _create(payload=payload, request=AsyncMock(), db=_db(), response=Response())

    mk_cycle.assert_not_awaited()


async def test_already_successful_replay_after_rollover_returns_original_record() -> None:
    """The plot has since rolled over (the fresh token is bound to cycle 2, and
    captured_plot_cycle_id names the old cycle 1) — but because a record already
    exists for this key with matching identity, it's an idempotent replay (200),
    NOT a planting_cycle_changed 409. The cycle guards are never reached."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    old_cycle = _cycle()
    new_cycle = _cycle()
    access = _access()
    # Fresh token minted after reconnecting → bound to the NEW active cycle.
    token = _token(plot, supplier, new_cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(days=1)
    existing = _record(plot, access, old_cycle, client_submission_id=key, captured_at=captured)
    payload = _offline_payload(
        token, key=key, captured_at=captured, captured_cycle_id=old_cycle.id,
    )
    response = Response()

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", AsyncMock(return_value=existing)), \
         patch(f"{_M}.record_repo.create_record", AsyncMock()) as mk_create, \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        result = await _create(payload=payload, request=AsyncMock(), db=_db(), response=response)

    assert response.status_code == 200
    assert result.id == existing.id
    mk_create.assert_not_awaited()


# --- key reused with a different identity (409 generic) ---------------------

async def test_same_key_different_plot_identity_rejected_409_generic() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    other_plot = _plot(supplier.id)  # a DIFFERENT plot
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(hours=1)
    # The existing record for this key belongs to a different plot.
    existing = _record(other_plot, access, cycle, client_submission_id=key, captured_at=captured)
    payload = _offline_payload(token, key=key, captured_at=captured, captured_cycle_id=cycle.id)

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", AsyncMock(return_value=existing)), \
         patch(f"{_M}.record_repo.create_record", AsyncMock()) as mk_create, \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await _create(payload=payload, request=AsyncMock(), db=_db())

    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "idempotency_conflict"}
    # generic — never names the plot/phone the key actually belongs to
    assert str(other_plot.id) not in str(exc.value.detail)
    mk_create.assert_not_awaited()


async def test_same_key_different_inspector_type_rejected_409() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access, inspector_type="farmer")
    key = uuid4()
    captured = _now() - datetime.timedelta(hours=1)
    existing = _record(plot, access, cycle, client_submission_id=key,
                       captured_at=captured, inspector_type="supplier")
    payload = _offline_payload(token, key=key, captured_at=captured, captured_cycle_id=cycle.id)

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", AsyncMock(return_value=existing)), \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await _create(payload=payload, request=AsyncMock(), db=_db())

    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "idempotency_conflict"}


# --- cycle conflict (409 planting_cycle_changed) ----------------------------

async def test_new_submission_after_cycle_changed_rejected_409_no_record() -> None:
    """A brand-new key (no existing record) whose captured_plot_cycle_id no
    longer matches the plot's active cycle → 409 planting_cycle_changed, and no
    record is created."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    active_cycle = _cycle()
    old_captured_cycle_id = uuid4()  # the cycle active when the draft was captured
    access = _access()
    # Fresh token bound to the current active cycle (passes the 8-0.6 guard).
    token = _token(plot, supplier, active_cycle, access)
    payload = _offline_payload(
        token, key=uuid4(), captured_at=_now() - datetime.timedelta(hours=3),
        captured_cycle_id=old_captured_cycle_id,
    )

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active_cycle)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", AsyncMock(return_value=None)), \
         patch(f"{_M}.record_repo.create_record", AsyncMock()) as mk_create, \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await _create(payload=payload, request=AsyncMock(), db=_db())

    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "planting_cycle_changed"}
    mk_create.assert_not_awaited()


# --- concurrent-insert race backstop (Part D.8/D.10) ------------------------

async def test_concurrent_duplicate_insert_returns_winner_200_not_500() -> None:
    """Two concurrent submits of the same key both pass the idempotency lookup
    (both saw no existing record), so both try to insert. The partial-unique
    index rejects the loser with IntegrityError — which must resolve to the
    winner's record (200), never a 500 or a duplicate row."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(minutes=30)
    winner = _record(plot, access, cycle, client_submission_id=key, captured_at=captured)
    payload = _offline_payload(token, key=key, captured_at=captured, captured_cycle_id=cycle.id)
    response = Response()

    # get_record_by_client_submission_id: None on the pre-insert lookup, then
    # the winner's row when we re-read after the IntegrityError.
    lookup = AsyncMock(side_effect=[None, winner])
    integrity = _dup_key_error()

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", lookup), \
         patch(f"{_M}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_M}.record_repo.create_record", AsyncMock(side_effect=integrity)), \
         patch(f"{_M}.plot_repo.sync_current_status_from_record", AsyncMock()) as mk_sync, \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        result = await _create(payload=payload, request=AsyncMock(), db=_db(), response=response)

    assert response.status_code == 200
    assert result.id == winner.id
    # the loser never syncs the plot snapshot (that's the winner's job)
    mk_sync.assert_not_awaited()


# --- with-photos endpoint (Part G) ------------------------------------------

async def test_with_photos_replay_returns_200_without_saving_any_photo() -> None:
    """A replay on the multipart endpoint must return the existing record (200)
    and NEVER touch photo storage (Part G: idempotent replay ห้าม save รูปซ้ำ)."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(hours=1)
    existing = _record(plot, access, cycle, client_submission_id=key, captured_at=captured)
    payload_json = _offline_payload(
        token, key=key, captured_at=captured, captured_cycle_id=cycle.id,
    ).model_dump_json()
    response = Response()
    response.status_code = 201

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", AsyncMock(return_value=existing)), \
         patch(f"{_M}.get_photo_storage", MagicMock()) as mk_storage, \
         patch(f"{_M}.validate_and_save_photos", AsyncMock()) as mk_save, \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        result = await _create_with_photos(
            request=AsyncMock(), payload=payload_json, photos=[MagicMock()],
            db=_db(), response=response,
        )

    assert response.status_code == 200
    assert result.id == existing.id
    mk_storage.assert_not_called()
    mk_save.assert_not_awaited()


async def test_with_photos_concurrent_duplicate_cleans_up_photos_and_returns_200() -> None:
    """The multipart race loser saved photos before the insert failed — those
    orphans must be cleaned up, and the winner's record returned with 200."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(minutes=20)
    winner = _record(plot, access, cycle, client_submission_id=key, captured_at=captured)
    payload_json = _offline_payload(
        token, key=key, captured_at=captured, captured_cycle_id=cycle.id,
    ).model_dump_json()
    response = Response()
    response.status_code = 201
    lookup = AsyncMock(side_effect=[None, winner])
    integrity = _dup_key_error()

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", lookup), \
         patch(f"{_M}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_M}.record_repo.create_record", AsyncMock(side_effect=integrity)), \
         patch(f"{_M}.get_photo_storage", MagicMock(return_value=MagicMock())), \
         patch(f"{_M}.validate_and_save_photos", AsyncMock(return_value=["/media/a.jpg"])), \
         patch(f"{_M}.cleanup_photos", AsyncMock()) as mk_cleanup, \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        result = await _create_with_photos(
            request=AsyncMock(), payload=payload_json, photos=[MagicMock()],
            db=_db(), response=response,
        )

    assert response.status_code == 200
    assert result.id == winner.id
    mk_cleanup.assert_awaited_once()


# --- round 8-4A.1: identity re-check on the concurrent-race winner ----------

async def test_pre_lookup_access_phone_mismatch_rejected_409() -> None:
    """The key resolves to a record made under a DIFFERENT access phone — a
    generic 409 (not a replay), even though plot + inspector match."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    other_access = _access()  # different access-phone id
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(hours=1)
    existing = _record(plot, other_access, cycle, client_submission_id=key, captured_at=captured)
    payload = _offline_payload(token, key=key, captured_at=captured, captured_cycle_id=cycle.id)

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", AsyncMock(return_value=existing)), \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await _create(payload=payload, request=AsyncMock(), db=_db())

    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "idempotency_conflict"}


async def _run_race(payload, *, supplier, plot, cycle, access, lookup, integrity):
    """Common driver for a concurrent-race test: pre-lookup returns None, the
    insert raises `integrity`, and the re-read returns whatever `lookup`'s
    second side-effect yields (winner, mismatched record, or None)."""
    response = Response()
    response.status_code = 201
    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", lookup), \
         patch(f"{_M}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_M}.record_repo.create_record", AsyncMock(side_effect=integrity)), \
         patch(f"{_M}.plot_repo.sync_current_status_from_record", AsyncMock()) as mk_sync, \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        result = await _create(payload=payload, request=AsyncMock(), db=_db(), response=response)
        return result, response, mk_sync


async def test_race_winner_plot_mismatch_rejected_409() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    other_plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(minutes=20)
    winner = _record(other_plot, access, cycle, client_submission_id=key, captured_at=captured)
    payload = _offline_payload(token, key=key, captured_at=captured, captured_cycle_id=cycle.id)

    with pytest.raises(HTTPException) as exc:
        await _run_race(
            payload, supplier=supplier, plot=plot, cycle=cycle, access=access,
            lookup=AsyncMock(side_effect=[None, winner]), integrity=_dup_key_error(),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "idempotency_conflict"}


async def test_race_winner_phone_mismatch_rejected_409() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    other_access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(minutes=20)
    winner = _record(plot, other_access, cycle, client_submission_id=key, captured_at=captured)
    payload = _offline_payload(token, key=key, captured_at=captured, captured_cycle_id=cycle.id)

    with pytest.raises(HTTPException) as exc:
        await _run_race(
            payload, supplier=supplier, plot=plot, cycle=cycle, access=access,
            lookup=AsyncMock(side_effect=[None, winner]), integrity=_dup_key_error(),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "idempotency_conflict"}


async def test_race_winner_inspector_mismatch_rejected_409() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access, inspector_type="farmer")
    key = uuid4()
    captured = _now() - datetime.timedelta(minutes=20)
    winner = _record(plot, access, cycle, client_submission_id=key, captured_at=captured,
                     inspector_type="chiatai")
    payload = _offline_payload(token, key=key, captured_at=captured, captured_cycle_id=cycle.id)

    with pytest.raises(HTTPException) as exc:
        await _run_race(
            payload, supplier=supplier, plot=plot, cycle=cycle, access=access,
            lookup=AsyncMock(side_effect=[None, winner]), integrity=_dup_key_error(),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "idempotency_conflict"}


async def test_race_winner_hidden_by_rls_returns_409_not_500() -> None:
    """Our idempotency index rejected the insert, but the re-read returns None
    because RLS hides the winner (a different supplier's row). Must be a generic
    409, NEVER a 500, and NEVER a bypass-RLS re-read."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(minutes=20)
    payload = _offline_payload(token, key=key, captured_at=captured, captured_cycle_id=cycle.id)

    with pytest.raises(HTTPException) as exc:
        await _run_race(
            payload, supplier=supplier, plot=plot, cycle=cycle, access=access,
            lookup=AsyncMock(side_effect=[None, None]), integrity=_dup_key_error(),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "idempotency_conflict"}


async def test_unrelated_integrity_error_propagates_not_masked_as_conflict() -> None:
    """An IntegrityError on a DIFFERENT constraint (an FK here) must propagate
    unchanged — never rewritten to a 409/200 idempotency conflict, and never
    trigger a winner re-read."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(minutes=20)
    payload = _offline_payload(token, key=key, captured_at=captured, captured_cycle_id=cycle.id)
    fk_error = _integrity_error("fk_records_plot_id_plots", asyncpg_exc.ForeignKeyViolationError)
    lookup = AsyncMock(side_effect=[None, AssertionError("must not re-read on unrelated error")])

    with pytest.raises(IntegrityError):
        await _run_race(
            payload, supplier=supplier, plot=plot, cycle=cycle, access=access,
            lookup=lookup, integrity=fk_error,
        )


async def test_race_conflict_detail_leaks_no_plot_phone_or_supplier_identity() -> None:
    """The 409 body is exactly {"code": "idempotency_conflict"} — it never
    carries the winning record's plot/phone/supplier/inspector."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    other_plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    other_access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(minutes=20)
    winner = _record(other_plot, other_access, cycle, client_submission_id=key,
                     captured_at=captured, inspector_type="chiatai")
    payload = _offline_payload(token, key=key, captured_at=captured, captured_cycle_id=cycle.id)

    with pytest.raises(HTTPException) as exc:
        await _run_race(
            payload, supplier=supplier, plot=plot, cycle=cycle, access=access,
            lookup=AsyncMock(side_effect=[None, winner]), integrity=_dup_key_error(),
        )

    detail_str = str(exc.value.detail)
    assert exc.value.detail == {"code": "idempotency_conflict"}
    for leaked in (str(other_plot.id), str(other_access.id), str(supplier.id),
                   str(winner.id), "chiatai"):
        assert leaked not in detail_str


async def test_race_winner_identity_match_still_returns_200_and_no_sync() -> None:
    """The good race path still works after the hardening: a matching winner is
    returned with 200, the receipt is internally consistent, and the loser never
    syncs the plot snapshot."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(minutes=20)
    winner = _record(plot, access, cycle, client_submission_id=key, captured_at=captured)
    payload = _offline_payload(token, key=key, captured_at=captured, captured_cycle_id=cycle.id)

    result, response, mk_sync = await _run_race(
        payload, supplier=supplier, plot=plot, cycle=cycle, access=access,
        lookup=AsyncMock(side_effect=[None, winner]), integrity=_dup_key_error(),
    )
    assert response.status_code == 200
    assert result.id == winner.id
    assert result.plot_id == winner.plot_id == plot.id  # receipt internally consistent
    mk_sync.assert_not_awaited()


# --- multipart variants (Part 7) --------------------------------------------

async def _run_multipart_race(payload_json, *, supplier, plot, cycle, access,
                              lookup, integrity, cleanup):
    from contextlib import ExitStack
    response = Response()
    response.status_code = 201
    patches = [
        patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)),
        patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)),
        patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)),
        patch(f"{_M}.record_repo.get_record_by_client_submission_id", lookup),
        patch(f"{_M}.get_external_submission_user", AsyncMock(return_value=_system_user())),
        patch(f"{_M}.record_repo.create_record", AsyncMock(side_effect=integrity)),
        patch(f"{_M}.plot_repo.sync_current_status_from_record", AsyncMock()),
        patch(f"{_M}.cleanup_photos", cleanup),
        patch(f"{_M}.set_public_record_rls_context", AsyncMock()),
        patch(f"{_M}.get_photo_storage", MagicMock(return_value=MagicMock())),
        patch(f"{_M}.validate_and_save_photos", AsyncMock(return_value=["/media/a.jpg"])),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return await _create_with_photos(
            request=AsyncMock(), payload=payload_json, photos=[MagicMock()],
            db=_db(), response=response,
        ), response


async def test_multipart_race_identity_mismatch_cleans_up_and_409() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    other_plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(minutes=20)
    winner = _record(other_plot, access, cycle, client_submission_id=key, captured_at=captured)
    payload_json = _offline_payload(
        token, key=key, captured_at=captured, captured_cycle_id=cycle.id).model_dump_json()
    cleanup = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _run_multipart_race(
            payload_json, supplier=supplier, plot=plot, cycle=cycle, access=access,
            lookup=AsyncMock(side_effect=[None, winner]), integrity=_dup_key_error(),
            cleanup=cleanup,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "idempotency_conflict"}
    cleanup.assert_awaited_once()


async def test_multipart_race_hidden_winner_cleans_up_and_409() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(minutes=20)
    payload_json = _offline_payload(
        token, key=key, captured_at=captured, captured_cycle_id=cycle.id).model_dump_json()
    cleanup = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _run_multipart_race(
            payload_json, supplier=supplier, plot=plot, cycle=cycle, access=access,
            lookup=AsyncMock(side_effect=[None, None]), integrity=_dup_key_error(),
            cleanup=cleanup,
        )
    assert exc.value.status_code == 409
    cleanup.assert_awaited_once()


async def test_multipart_unrelated_integrity_error_cleans_up_and_propagates() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    key = uuid4()
    captured = _now() - datetime.timedelta(minutes=20)
    payload_json = _offline_payload(
        token, key=key, captured_at=captured, captured_cycle_id=cycle.id).model_dump_json()
    cleanup = AsyncMock()
    fk_error = _integrity_error("fk_records_plot_cycle_id_plot_cycles", asyncpg_exc.ForeignKeyViolationError)

    with pytest.raises(IntegrityError):
        await _run_multipart_race(
            payload_json, supplier=supplier, plot=plot, cycle=cycle, access=access,
            lookup=AsyncMock(side_effect=[None, AssertionError("no re-read")]),
            integrity=fk_error, cleanup=cleanup,
        )
    cleanup.assert_awaited_once()


# --- helper unit tests + online regression ----------------------------------

def test_classifier_matches_only_the_idempotency_index() -> None:
    from app.api.v1.public_records import _is_client_submission_unique_violation
    assert _is_client_submission_unique_violation(_dup_key_error()) is True
    assert _is_client_submission_unique_violation(
        _integrity_error("fk_records_plot_id_plots", asyncpg_exc.ForeignKeyViolationError)
    ) is False
    assert _is_client_submission_unique_violation(
        _integrity_error("some_other_unique_idx")
    ) is False
    # A bare Exception with no constraint chain is never our violation.
    assert _is_client_submission_unique_violation(
        IntegrityError("INSERT", {}, Exception("duplicate key"))
    ) is False


def test_identity_helper_requires_all_three_to_match() -> None:
    from app.api.v1.public_records import _matches_replay_identity
    plot = _plot(uuid4())
    access = _access()
    rec = _record(plot, access, _cycle(), client_submission_id=uuid4(),
                  captured_at=_now(), inspector_type="farmer")
    assert _matches_replay_identity(rec, plot, access.id, "farmer") is True
    assert _matches_replay_identity(rec, _plot(uuid4()), access.id, "farmer") is False
    assert _matches_replay_identity(rec, plot, uuid4(), "farmer") is False
    assert _matches_replay_identity(rec, plot, access.id, "supplier") is False


async def test_online_submission_without_offline_fields_still_creates_201() -> None:
    """Regression guard: an online payload (no offline fields) never enters the
    idempotency/savepoint path and still creates a record."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _token(plot, supplier, cycle, access)
    created = _record(plot, access, cycle, client_submission_id=None, captured_at=None)
    online_payload = PublicRecordCreate(
        inspection_session_token=token, record_date=datetime.date(2026, 7, 1),
    )
    db = _db()

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.record_repo.get_record_by_client_submission_id", AsyncMock()) as mk_lookup, \
         patch(f"{_M}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_M}.record_repo.create_record", AsyncMock(return_value=created)) as mk_create, \
         patch(f"{_M}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        result = await _create(payload=online_payload, request=AsyncMock(), db=db)

    assert result.client_submission_id is None
    assert result.captured_at is None
    # online never consults the idempotency key nor opens a savepoint
    mk_lookup.assert_not_called()
    db.begin_nested.assert_not_called()
    mk_create.assert_awaited_once()

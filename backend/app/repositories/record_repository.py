"""Record CRUD repository."""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.record import Record
from app.schemas.record import RecordCreate, RecordUpdate


def _with_relations(*extra):
    """selectinload options for response serialisation."""
    return [
        selectinload(Record.plot),
        # plot_cycle for RecordRead.cycle_no (round 7.1) — one IN-query, not
        # N+1; Record.plot_cycle is lazy="select" which can't lazy-load async.
        selectinload(Record.plot_cycle),
        selectinload(Record.supplier),
        selectinload(Record.recorded_by),
        *extra,
    ]


async def get_record_full(db: AsyncSession, record_id: UUID) -> Record | None:
    """Load one record with all display relationships (no scope filter).

    Use for building responses after a create / update — scope is already
    verified by the endpoint before calling this.  RLS still applies via
    the session config set by RLSContext / ScopeFilter.
    """
    result = await db.execute(
        select(Record).options(*_with_relations()).where(Record.id == record_id)
    )
    return result.scalar_one_or_none()


async def list_records(
    db: AsyncSession,
    scope_conditions: list[Any],
    plot_id: UUID | None = None,
    supplier_id: UUID | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    active_only: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[Record]:
    stmt = (
        select(Record)
        .options(
            selectinload(Record.plot),
            # plot_cycle for RecordSummary.cycle_no (round 7.1) — one IN-query
            # for the whole page, not N+1.
            selectinload(Record.plot_cycle),
            selectinload(Record.supplier),
        )
        .order_by(Record.record_date.desc(), Record.created_at.desc())
    )
    for cond in scope_conditions:
        stmt = stmt.where(cond)
    if plot_id is not None:
        stmt = stmt.where(Record.plot_id == plot_id)
    if supplier_id is not None:
        stmt = stmt.where(Record.supplier_id == supplier_id)
    if date_from is not None:
        stmt = stmt.where(Record.record_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(Record.record_date <= date_to)
    if active_only:
        stmt = stmt.where(Record.is_active.is_(True))
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_record_scoped(
    db: AsyncSession, record_id: UUID, scope_conditions: list[Any]
) -> Record | None:
    """Scope-aware lookup — returns None (→ 404) if outside the user's scope."""
    stmt = select(Record).where(Record.id == record_id)
    for cond in scope_conditions:
        stmt = stmt.where(cond)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_record_by_client_submission_id(
    db: AsyncSession, client_submission_id: UUID
) -> Record | None:
    """Idempotency lookup (round 8-4A) — the record already created for one
    offline draft's client_submission_id, or None. Eager-loads the display
    relationships so the endpoint can build the receipt for an idempotent
    replay without a second round-trip. Runs under whatever RLS context the
    caller set (the public flow scopes to the token's supplier before calling
    this), so a key that exists but is out of scope reads as None — never a
    cross-supplier oracle."""
    result = await db.execute(
        select(Record)
        .options(*_with_relations())
        .where(Record.client_submission_id == client_submission_id)
    )
    return result.scalar_one_or_none()


async def create_record(
    db: AsyncSession,
    payload: RecordCreate,
    recorded_by_id: UUID,
    submitted_ip: str | None = None,
    plot_cycle_id: UUID | None = None,
    *,
    plot_access_phone_id: UUID | None = None,
    submitted_phone_snapshot: str | None = None,
    submitted_phone_type: str | None = None,
    inspector_type: str | None = None,
    client_submission_id: UUID | None = None,
    captured_at: Any = None,
    yield_target_kg_snapshot: Decimal | None = None,
) -> Record:
    # submitted_ip and plot_cycle_id are separate kwargs (like recorded_by_id),
    # not RecordCreate fields — both are server-derived (the connection's IP;
    # the plot's active cycle, resolved in the endpoint), never accepted from
    # the client body. plot_cycle_id is NOT NULL at the DB level after
    # migration 0034; both create endpoints always pass it.
    #
    # The four phone-access fields (round 8-3B, required on every public token
    # since round 8-3G) are also KEYWORD-ONLY and server-derived — from the
    # verified access phone the inspection token is bound to, never from the
    # client. All default None: the logged-in flow leaves them NULL (it has
    # no phone binding at all); the public flow always supplies them now.
    # They are deliberately NOT on RecordCreate, so a client can't forge them.
    #
    # client_submission_id / captured_at (round 8-4A, migration 0041) are also
    # keyword-only: the offline public flow passes the client-reported key +
    # capture time AFTER the endpoint has validated them (completeness, tz,
    # window) and confirmed no existing record for the key; both stay NULL for
    # the online and logged-in flows. captured_at is a separate column from
    # created_at, which the DB still stamps at commit time.
    #
    # yield_target_kg_snapshot (round 8-8A, migration 0044) is likewise
    # keyword-only and server-derived — the endpoint computes it via
    # app/services/yield_calculation.derive_yield from the active PlotCycle's
    # own expected_yield_full/expected_yield_unit and passes it here; it is
    # deliberately NOT a RecordCreate/PublicRecordCreate field, so a client
    # can never forge it. payload.yield_pct/yield_quantity_kg, by contrast,
    # ARE real RecordCreate fields — the endpoint overwrites them (via
    # model_copy/reconstruction) with the same derivation's output before
    # calling this function, so **data below already carries the
    # server-derived values, not whatever the client originally sent.
    data = payload.model_dump()
    record = Record(
        recorded_by_id=recorded_by_id,
        submitted_ip=submitted_ip,
        plot_cycle_id=plot_cycle_id,
        plot_access_phone_id=plot_access_phone_id,
        submitted_phone_snapshot=submitted_phone_snapshot,
        submitted_phone_type=submitted_phone_type,
        inspector_type=inspector_type,
        client_submission_id=client_submission_id,
        captured_at=captured_at,
        yield_target_kg_snapshot=yield_target_kg_snapshot,
        **data,
    )
    db.add(record)
    await db.flush()
    return record


async def update_record(
    db: AsyncSession, record: Record, payload: RecordUpdate
) -> Record:
    """Generic field-loop updater — kept internal/deprecated (round 8.0.5
    append-only lock): no API route accepts a RecordUpdate body anymore, so
    this has no live caller. Retained only because RecordUpdate itself is
    kept for its schema-level validation tests (submitted_by_name trim
    rules, security-boundary field checks — submitted_by_code was retired
    entirely in round 8-3G). Use deactivate_record for the one mutation
    that's still allowed on an existing record.
    """
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(record, field, value)
    await db.flush()
    return record


async def deactivate_record(db: AsyncSession, record: Record) -> Record:
    """The ONLY mutation path for an existing record (round 8.0.5 append-only
    lock) — flips is_active to False and nothing else. Inspection fields
    (crop/variety/stage/yield/scores/GPS/photos/notes/custom_fields) can
    never be changed once a record is created; a new inspection always means
    a new Record. Administrative correction only (gated by records.delete),
    not a general-purpose update."""
    record.is_active = False
    await db.flush()
    return record

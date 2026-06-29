"""Record CRUD repository."""
from __future__ import annotations

import datetime
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
        .options(selectinload(Record.plot), selectinload(Record.supplier))
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


async def create_record(
    db: AsyncSession, payload: RecordCreate, recorded_by_id: UUID
) -> Record:
    data = payload.model_dump()
    record = Record(
        recorded_by_id=recorded_by_id,
        **data,
    )
    db.add(record)
    await db.flush()
    return record


async def update_record(
    db: AsyncSession, record: Record, payload: RecordUpdate
) -> Record:
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(record, field, value)
    await db.flush()
    return record

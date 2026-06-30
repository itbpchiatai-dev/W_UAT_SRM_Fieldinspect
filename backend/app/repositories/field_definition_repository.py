"""FieldDefinition CRUD repository (Step 12)."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.field_definition import FieldDefinition
from app.schemas.field_definition import FieldDefinitionCreate, FieldDefinitionUpdate


async def get(db: AsyncSession, field_id: UUID) -> FieldDefinition | None:
    result = await db.execute(select(FieldDefinition).where(FieldDefinition.id == field_id))
    return result.scalar_one_or_none()


async def get_by_key(db: AsyncSession, key: str) -> FieldDefinition | None:
    result = await db.execute(
        select(FieldDefinition).where(func.lower(FieldDefinition.key) == key.strip().lower())
    )
    return result.scalar_one_or_none()


async def list_fields(
    db: AsyncSession, active_only: bool = False
) -> list[FieldDefinition]:
    stmt = select(FieldDefinition).order_by(
        FieldDefinition.order_index.asc(), FieldDefinition.label.asc()
    )
    if active_only:
        stmt = stmt.where(FieldDefinition.active.is_(True))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def create(db: AsyncSession, payload: FieldDefinitionCreate) -> FieldDefinition:
    field = FieldDefinition(
        key=payload.key,
        label=payload.label.strip(),
        field_type=payload.field_type,
        required=payload.required,
        options_source=payload.options_source,
        options=payload.options,
        is_core=False,  # custom fields only — core rows are seed-managed
        list_default=payload.list_default,
        order_index=payload.order_index,
    )
    db.add(field)
    await db.flush()
    await db.refresh(field)
    return field


async def update(
    db: AsyncSession, field: FieldDefinition, payload: FieldDefinitionUpdate
) -> FieldDefinition:
    data = payload.model_dump(exclude_unset=True)
    for attr, value in data.items():
        setattr(field, attr, value)
    await db.flush()
    await db.refresh(field)
    return field


async def delete(db: AsyncSession, field: FieldDefinition) -> None:
    await db.delete(field)
    await db.flush()

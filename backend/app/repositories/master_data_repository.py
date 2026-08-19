"""MasterData CRUD repository (Step 12.5)."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.master_data import MasterData
from app.schemas.master_data import MasterDataCreate, MasterDataUpdate


async def get(db: AsyncSession, item_id: UUID) -> MasterData | None:
    result = await db.execute(select(MasterData).where(MasterData.id == item_id))
    return result.scalar_one_or_none()


async def list_items(
    db: AsyncSession,
    type: str | None = None,
    parent: str | None = None,
    active_only: bool = False,
) -> list[MasterData]:
    stmt = select(MasterData).order_by(
        MasterData.type.asc(), MasterData.order_index.asc(), MasterData.value.asc()
    )
    if type is not None:
        stmt = stmt.where(MasterData.type == type)
    if parent is not None:
        stmt = stmt.where(MasterData.parent == parent)
    if active_only:
        stmt = stmt.where(MasterData.active.is_(True))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_by_type_value(db: AsyncSession, type: str, value: str) -> MasterData | None:
    """Exact (type, value) lookup — mirrors the DB's own
    uq_master_data_type_value unique index (migration 0019), active or
    inactive either way. Used by the create/update endpoints to give a
    friendly, specific duplicate message BEFORE attempting the write (round
    8-22A) — the unique index itself remains the actual guard against a
    concurrent duplicate insert."""
    result = await db.execute(
        select(MasterData).where(MasterData.type == type, MasterData.value == value)
    )
    return result.scalar_one_or_none()


async def list_by_type_values(db: AsyncSession, type: str, values: set[str]) -> list[MasterData]:
    """Batch-fetch existing rows of `type` whose value is in `values` — ONE
    query regardless of row count (round 8-15A's crop/variety Excel import:
    every crop/variety cell in the file is looked up in a single round trip,
    never per-row). Empty `values` short-circuits without a query."""
    if not values:
        return []
    stmt = select(MasterData).where(MasterData.type == type, MasterData.value.in_(values))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def create(db: AsyncSession, payload: MasterDataCreate) -> MasterData:
    item = MasterData(
        type=payload.type.strip(),
        value=payload.value.strip(),
        parent=payload.parent,
        order_index=payload.order_index,
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)
    return item


async def update(db: AsyncSession, item: MasterData, payload: MasterDataUpdate) -> MasterData:
    data = payload.model_dump(exclude_unset=True)
    for attr, value in data.items():
        setattr(item, attr, value)
    await db.flush()
    await db.refresh(item)
    return item


async def delete(db: AsyncSession, item: MasterData) -> None:
    await db.delete(item)
    await db.flush()

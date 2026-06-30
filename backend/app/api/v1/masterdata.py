"""Master Data — editable dropdown option source (Step 12.5, Spec §3.5).

Read is gated by `records.read` so the record form can load options. Mutations
are gated by `masterdata.*` (Super Admin / Supervisor — manageMaster).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.auth.permissions import PermissionKey
from app.db.session import get_db
from app.repositories import master_data_repository as repo
from app.schemas.master_data import MasterDataCreate, MasterDataRead, MasterDataUpdate

router = APIRouter(tags=["masterdata"])


@router.get("", response_model=list[MasterDataRead], dependencies=[
    Depends(require_permission(PermissionKey.RECORDS_READ))
])
async def list_master_data(
    db: AsyncSession = Depends(get_db),
    type: str | None = None,
    parent: str | None = None,
    active_only: bool = False,
) -> list[MasterDataRead]:
    items = await repo.list_items(db, type=type, parent=parent, active_only=active_only)
    return [MasterDataRead.model_validate(i) for i in items]


@router.post("", response_model=MasterDataRead, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission(PermissionKey.MASTERDATA_CREATE))])
async def create_master_data(
    payload: MasterDataCreate,
    db: AsyncSession = Depends(get_db),
) -> MasterDataRead:
    item = await repo.create(db, payload)
    return MasterDataRead.model_validate(item)


@router.patch("/{item_id}", response_model=MasterDataRead, dependencies=[
    Depends(require_permission(PermissionKey.MASTERDATA_UPDATE))
])
async def update_master_data(
    item_id: UUID,
    payload: MasterDataUpdate,
    db: AsyncSession = Depends(get_db),
) -> MasterDataRead:
    item = await repo.get(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="master data not found")
    item = await repo.update(db, item, payload)
    return MasterDataRead.model_validate(item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[
    Depends(require_permission(PermissionKey.MASTERDATA_DELETE))
])
async def delete_master_data(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    item = await repo.get(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="master data not found")
    await repo.delete(db, item)

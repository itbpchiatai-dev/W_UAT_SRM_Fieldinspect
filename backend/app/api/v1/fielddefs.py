"""Field Definitions — schema-driven form field catalog (Step 12, Field Master).

Read is gated by `records.read` so anyone who fills in a record can fetch the
active field catalog to render the form. Mutations are gated by `fielddefs.*`
(Super Admin / manageFields) — and only CUSTOM fields (is_core=False) may be
created/edited/deleted; core rows are seed-managed and partially immutable.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_any_permission, require_permission
from app.auth.permissions import PermissionKey
from app.db.session import get_db
from app.repositories import field_definition_repository as repo
from app.schemas.field_definition import (
    FieldDefinitionCreate,
    FieldDefinitionRead,
    FieldDefinitionUpdate,
)

router = APIRouter(tags=["fielddefs"])


# Read accepts records.read OR records.create (round 5.6) — the RecordForm
# renders from this catalog and /farmlog/records/new is opened with
# records.create. Mutations below stay gated by fielddefs.* (unchanged).
@router.get("", response_model=list[FieldDefinitionRead], dependencies=[
    Depends(require_any_permission(PermissionKey.RECORDS_READ, PermissionKey.RECORDS_CREATE))
])
async def list_field_definitions(
    db: AsyncSession = Depends(get_db),
    active_only: bool = False,
) -> list[FieldDefinitionRead]:
    fields = await repo.list_fields(db, active_only=active_only)
    return [FieldDefinitionRead.model_validate(f) for f in fields]


@router.post("", response_model=FieldDefinitionRead, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission(PermissionKey.FIELDDEFS_CREATE))])
async def create_field_definition(
    payload: FieldDefinitionCreate,
    db: AsyncSession = Depends(get_db),
) -> FieldDefinitionRead:
    existing = await repo.get_by_key(db, payload.key)
    if existing is not None:
        raise HTTPException(status_code=409, detail="field key already exists")
    field = await repo.create(db, payload)
    return FieldDefinitionRead.model_validate(field)


@router.patch("/{field_id}", response_model=FieldDefinitionRead, dependencies=[
    Depends(require_permission(PermissionKey.FIELDDEFS_UPDATE))
])
async def update_field_definition(
    field_id: UUID,
    payload: FieldDefinitionUpdate,
    db: AsyncSession = Depends(get_db),
) -> FieldDefinitionRead:
    field = await repo.get(db, field_id)
    if field is None:
        raise HTTPException(status_code=404, detail="field definition not found")
    field = await repo.update(db, field, payload)
    return FieldDefinitionRead.model_validate(field)


@router.delete("/{field_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[
    Depends(require_permission(PermissionKey.FIELDDEFS_DELETE))
])
async def delete_field_definition(
    field_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    field = await repo.get(db, field_id)
    if field is None:
        raise HTTPException(status_code=404, detail="field definition not found")
    if field.is_core:
        raise HTTPException(status_code=403, detail="core field cannot be deleted")
    await repo.delete(db, field)

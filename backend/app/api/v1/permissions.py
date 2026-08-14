"""Permission catalog — read-only."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser  # noqa: F401 (auth required)
from app.db.models.permission import Permission
from app.db.session import get_db
from app.schemas.auth import PermissionRead

router = APIRouter(tags=["permissions"])


@router.get("", response_model=list[PermissionRead])
async def list_permissions(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[PermissionRead]:
    result = await db.execute(select(Permission).order_by(Permission.key))
    return [PermissionRead.model_validate(p) for p in result.scalars().all()]

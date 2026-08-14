"""Role CRUD — super_admin only for create/delete; admin can update perms."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import CurrentUser, require_any_permission, require_permission
from app.auth.permissions import PermissionKey
from app.db.models.permission import Permission
from app.db.models.role import Role
from app.db.models.user_role import UserRole
from app.db.session import get_db
from app.schemas.auth import RoleCreate, RoleRead, RoleSummary, RoleUpdate
from app.services.loggers.activity_logger import ActivityLogger

router = APIRouter(tags=["roles"])


@router.get("", response_model=list[RoleSummary], dependencies=[
    # Two legitimate callers: the Roles admin page (roles.read)
    # and the Edit User role picker (users.update / roles.assign). Either is
    # enough — admin without the Roles menu still needs the list to pick from.
    Depends(require_any_permission(
        PermissionKey.ROLES_READ,
        PermissionKey.USERS_UPDATE,
        PermissionKey.ROLES_ASSIGN,
    ))
])
async def list_roles(db: AsyncSession = Depends(get_db)) -> list[RoleSummary]:
    result = await db.execute(select(Role).order_by(Role.name))
    return [RoleSummary.model_validate(r) for r in result.scalars().all()]


@router.get("/{role_id}", response_model=RoleRead, dependencies=[
    Depends(require_permission(PermissionKey.ROLES_READ))
])
async def get_role(role_id: UUID, db: AsyncSession = Depends(get_db)) -> RoleRead:
    stmt = select(Role).where(Role.id == role_id).options(selectinload(Role.permissions))
    result = await db.execute(stmt)
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return RoleRead.model_validate(role)


def _validate_role_name_scope(name: str, provider_scope: str) -> None:
    """Enforce the role-naming contract at the API boundary.

    The SPA's zod schema applies the same rule, but that is only UX — the
    API is the real boundary and must reject mismatches itself:
        provider_scope == "internal" -> name MUST start "internal:"
        provider_scope == "external" -> name MUST start "external:"
        provider_scope == "any"      -> name must NOT carry an
                                         internal:/external: prefix
    """
    if provider_scope not in ("internal", "external", "any"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider_scope '{provider_scope}' (expected internal|external|any)",
        )
    has_internal = name.startswith("internal:")
    has_external = name.startswith("external:")
    ok = (
        (provider_scope == "internal" and has_internal)
        or (provider_scope == "external" and has_external)
        or (provider_scope == "any" and not has_internal and not has_external)
    )
    if not ok:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Role name '{name}' must match provider_scope '{provider_scope}' "
                "(internal:* / external:* / no scope prefix for 'any')"
            ),
        )


@router.post("", response_model=RoleRead, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission(PermissionKey.ROLES_CREATE))])
async def create_role(
    payload: RoleCreate,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> RoleRead:
    _validate_role_name_scope(payload.name, payload.provider_scope)
    existing = await db.execute(select(Role).where(Role.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Role name already exists")

    perms = []
    if payload.permission_keys:
        perms_result = await db.execute(
            select(Permission).where(Permission.key.in_(payload.permission_keys))
        )
        perms = list(perms_result.scalars().all())

    role = Role(
        name=payload.name,
        display_name=payload.display_name,
        provider_scope=payload.provider_scope,
        description=payload.description,
        is_system=False,
    )
    role.permissions = perms
    db.add(role)
    await db.flush()

    audit = ActivityLogger(db)
    await audit.log(
        action="role.created", action_type="role_change", resource_type="role",
        resource_id=str(role.id), user=user, request=request,
        is_security_event=True, risk_level="medium",
    )

    refreshed = (await db.execute(
        select(Role).where(Role.id == role.id).options(selectinload(Role.permissions))
    )).scalar_one()
    return RoleRead.model_validate(refreshed)


@router.patch("/{role_id}", response_model=RoleRead, dependencies=[
    Depends(require_permission(PermissionKey.ROLES_UPDATE))
])
async def patch_role(
    role_id: UUID,
    payload: RoleUpdate,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> RoleRead:
    stmt = select(Role).where(Role.id == role_id).options(selectinload(Role.permissions))
    role = (await db.execute(stmt)).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_system and payload.permission_keys is not None:
        raise HTTPException(status_code=400, detail="Cannot change permissions on system role")

    if payload.display_name is not None:
        role.display_name = payload.display_name
    if payload.description is not None:
        role.description = payload.description
    if payload.permission_keys is not None:
        perms_result = await db.execute(
            select(Permission).where(Permission.key.in_(payload.permission_keys))
        )
        role.permissions = list(perms_result.scalars().all())

    audit = ActivityLogger(db)
    await audit.log(
        action="role.updated", action_type="role_change", resource_type="role",
        resource_id=str(role.id), user=user, request=request,
        is_security_event=True, risk_level="medium",
    )
    return RoleRead.model_validate(role)


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[
    Depends(require_permission(PermissionKey.ROLES_DELETE))
])
async def delete_role(
    role_id: UUID,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_system:
        raise HTTPException(status_code=400, detail="Cannot delete system role")

    in_use = (
        await db.execute(select(func.count()).select_from(UserRole).where(UserRole.role_id == role_id))
    ).scalar_one()
    if in_use:
        raise HTTPException(status_code=400, detail="Role is assigned to users")

    await db.delete(role)
    audit = ActivityLogger(db)
    await audit.log(
        action="role.deleted", action_type="role_change", resource_type="role",
        resource_id=str(role_id), user=user, request=request,
        is_security_event=True, risk_level="high",
    )

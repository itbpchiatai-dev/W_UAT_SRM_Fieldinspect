"""FastAPI dependencies — get_current_user, require_permission, require_role.

`get_current_user` decodes the bearer token, loads the user with roles +
overrides eagerly (selectin), and computes effective permissions once
per request (cached on the User instance via `_effective_permissions`).
"""
from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.jwt_service import decode_token
from app.db.models.role import Role
from app.db.models.user import User
from app.db.models.user_permission_override import UserPermissionOverride
from app.db.session import get_db


def _compute_effective_permissions(user: User) -> set[str]:
    perms: set[str] = set()
    for role in user.roles:
        for perm in role.permissions:
            perms.add(perm.key)
    for override in user.permission_overrides:
        if override.granted:
            perms.add(override.permission.key)
        else:
            perms.discard(override.permission.key)
    return perms


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_token(token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if claims.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Wrong token type")
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Token has no subject")

    try:
        uid = UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Bad subject claim") from exc

    stmt = (
        select(User)
        .where(User.id == uid)
        .options(
            selectinload(User.roles).selectinload(Role.permissions),
            selectinload(User.permission_overrides).selectinload(
                UserPermissionOverride.permission
            ),
        )
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="User not found or inactive")

    # Cache effective perms on the instance for downstream require_permission.
    setattr(user, "_effective_permissions", _compute_effective_permissions(user))
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def _audit_permission_denied(
    db: AsyncSession, user: User, request: Request, missing: str,
) -> None:
    """Record the deny so an admin can see "who tried to access what" in
    the Activity Logs page. Commits immediately because the surrounding
    request is about to raise — the get_db dependency would otherwise
    roll back the audit row along with the failed request.
    """
    # Imported here to avoid a circular import with services that depend
    # on dependencies (require_permission is used everywhere).
    from app.services.loggers.activity_logger import ActivityLogger

    await ActivityLogger(db).log(
        action="auth.permission_denied",
        action_type="permission_denied",
        user=user,
        request=request,
        risk_level="medium",
        metadata={"missing": missing},
    )
    await db.commit()


def require_permission(key: str) -> Any:
    async def _checker(
        user: CurrentUser,
        request: Request,
        db: AsyncSession = Depends(get_db),
    ) -> User:
        perms: set[str] = getattr(user, "_effective_permissions", set())
        if key not in perms:
            await _audit_permission_denied(db, user, request, key)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {key}",
            )
        return user
    return _checker


def require_any_permission(*keys: str) -> Any:
    """Pass-if-any guard — caller needs ANY ONE of the listed perms.

    Useful when the same data has multiple legitimate callers (e.g. the
    Roles admin page wants roles.read; the Users edit form
    wants the same list as a picker and has users.update instead). Keeps
    the perm catalog flat — no need to invent overlapping wrapper perms.
    """
    async def _checker(
        user: CurrentUser,
        request: Request,
        db: AsyncSession = Depends(get_db),
    ) -> User:
        perms: set[str] = getattr(user, "_effective_permissions", set())
        if not any(k in perms for k in keys):
            await _audit_permission_denied(db, user, request, ",".join(keys))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing one of: {', '.join(keys)}",
            )
        return user
    return _checker


def require_role(name: str) -> Any:
    async def _checker(user: CurrentUser) -> User:
        if not any(r.name == name for r in user.roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing role: {name}",
            )
        return user
    return _checker

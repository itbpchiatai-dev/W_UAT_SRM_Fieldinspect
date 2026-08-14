"""Admin settings — Pattern C key/value store.

Used to toggle `auth.local.enabled` / `auth.sso.enabled` etc. at runtime.
`AUTH_SCOPE` env acts as a ceiling: if the project was scaffolded with
`internal_only`, local auth cannot be re-enabled here; if `external_only`,
sso cannot. See docs/admin-config.md §C.7.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_permission
from app.auth.permissions import PermissionKey
from app.db.models.app_setting import AppSetting
from app.db.session import get_db
from app.schemas.auth import AppSettingRead, AppSettingUpdate
from app.services.loggers.activity_logger import ActivityLogger

router = APIRouter(tags=["admin_settings"])


_SCOPE_LOCKED_KEYS = {
    # internal_only = Azure AD only  -> local auth is locked OFF.
    # external_only = local only     -> SSO is locked OFF.
    # (Must match the seed defaults and the SPA's VITE_AUTH_SCOPE gating —
    # see docs/admin-config.md §C.7 / docs/auth.md §12.)
    "internal_only": {"auth.local.enabled": False},
    "external_only": {"auth.sso.enabled": False},
}


def _scope_locked_value(key: str) -> tuple[bool, object | None]:
    scope = os.getenv("AUTH_SCOPE", "both").lower()
    locked = _SCOPE_LOCKED_KEYS.get(scope, {})
    if key in locked:
        return True, locked[key]
    return False, None


_PUBLIC_KEY_MAP = {
    "auth.local.enabled": "authLocalEnabled",
    "auth.sso.enabled": "authSsoEnabled",
}


@router.get("/public", response_model=dict[str, bool])
async def get_public_settings(db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    """Auth-free subset for the Login page — only auth.*.enabled flags so the
    SPA can decide which sign-in options to render before the user is known.
    Returns camelCase keys to match the SPA's PublicAuthSettings interface.
    """
    result = await db.execute(
        select(AppSetting).where(AppSetting.key.in_(_PUBLIC_KEY_MAP.keys()))
    )
    out: dict[str, bool] = {camel: True for camel in _PUBLIC_KEY_MAP.values()}
    for s in result.scalars().all():
        out[_PUBLIC_KEY_MAP[s.key]] = bool(s.value)
    return out


@router.get("", response_model=list[AppSettingRead], dependencies=[
    Depends(require_permission(PermissionKey.ADMIN_SETTINGS_READ))
])
async def list_settings(db: AsyncSession = Depends(get_db)) -> list[AppSettingRead]:
    result = await db.execute(select(AppSetting).order_by(AppSetting.key))
    return [AppSettingRead.model_validate(s) for s in result.scalars().all()]


@router.put("/{key}", response_model=AppSettingRead, dependencies=[
    Depends(require_permission(PermissionKey.ADMIN_SETTINGS_UPDATE))
])
async def update_setting(
    key: str,
    payload: AppSettingUpdate,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> AppSettingRead:
    locked, ceiling = _scope_locked_value(key)
    if locked and payload.value != ceiling:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Setting `{key}` is locked by AUTH_SCOPE env (compile-time ceiling)",
        )

    setting = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if setting is None:
        raise HTTPException(status_code=404, detail="Setting not found")
    setting.value = payload.value
    setting.updated_by_user_id = user.id

    audit = ActivityLogger(db)
    await audit.log(
        action="admin_setting.updated", action_type="update",
        resource_type="app_setting", resource_id=key,
        user=user, request=request, risk_level="medium",
        metadata={"key": key},
    )
    return AppSettingRead.model_validate(setting)

"""/me — profile, effective permissions, filtered menu tree."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.db.models.menu_item import MenuItem
from app.db.session import get_db
from app.schemas.auth import MenuRead, MePermissionsResponse, UserRead

router = APIRouter(tags=["me"])


@router.get("", response_model=UserRead)
async def get_me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.get("/permissions", response_model=MePermissionsResponse)
async def get_my_permissions(user: CurrentUser) -> MePermissionsResponse:
    perms: set[str] = getattr(user, "_effective_permissions", set())
    return MePermissionsResponse(permission_keys=sorted(perms))


def _build_tree(items: list[MenuItem], allowed: set[str]) -> list[MenuRead]:
    """Filter the menu tree by the caller's effective perms.

    Rules (v3.0.3):
      - empty required_permission_key (None or "") → no perm gate; always
        keep (used for Dashboard + Settings parent).
      - non-empty perm → keep only if user has that perm.
      - After per-node filtering, drop any node whose perm gate was empty
        AND now has no visible children — keeps the sidebar from showing
        an empty 'Settings' parent for a role with no settings access.
    """
    by_parent: dict = {}
    for item in items:
        perm = item.required_permission_key or ""
        if perm and perm not in allowed:
            continue
        by_parent.setdefault(item.parent_id, []).append(item)

    def walk(parent_id) -> list[MenuRead]:
        nodes = sorted(by_parent.get(parent_id, []), key=lambda i: i.order_index)
        out: list[MenuRead] = []
        for n in nodes:
            children = walk(n.id)
            # Drop empty-perm parents whose children were all filtered out
            # (they'd render as a dead-end chevron in the UI otherwise).
            had_children = n.id in by_parent
            if (not n.required_permission_key) and had_children and not children:
                continue
            out.append(MenuRead(
                id=n.id, key=n.key, label_th=n.label_th, label_en=n.label_en,
                icon=n.icon, path=n.path, parent_id=n.parent_id,
                order_index=n.order_index,
                required_permission_key=n.required_permission_key,
                is_system=n.is_system, children=children,
            ))
        return out

    return walk(None)


@router.get("/menus", response_model=list[MenuRead])
async def get_my_menus(
    user: CurrentUser, db: AsyncSession = Depends(get_db)
) -> list[MenuRead]:
    perms: set[str] = getattr(user, "_effective_permissions", set())
    result = await db.execute(select(MenuItem))
    items = list(result.scalars().all())
    return _build_tree(items, perms)

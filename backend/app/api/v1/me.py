"""/me — profile, effective permissions, filtered menu tree."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.db.models.menu_item import MenuItem
from app.db.session import DbDep
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

    Round 29 — that last rule had never worked. It judged "had children" by
    the children that SURVIVED the filter, so a group whose every child was
    filtered out looked like a childless leaf and stayed: roles without
    settings access saw an empty "การตั้งค่า", and once "รายงาน" lost its own
    permission (each report carries one now) every role without a report key
    would have seen an empty "รายงาน" too. A node is a group if the CATALOGUE
    gives it children; that is what decides whether an empty one hides.
    """
    # Every node that is a parent of anything, before filtering.
    groups = {item.parent_id for item in items if item.parent_id is not None}
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
            # Drop empty-perm groups with nothing visible inside (they'd render
            # as a dead-end chevron otherwise). A permission-less LEAF such as
            # Dashboard is not a group and is always kept.
            if (not n.required_permission_key) and n.id in groups and not children:
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
    user: CurrentUser, db: AsyncSession = DbDep
) -> list[MenuRead]:
    perms: set[str] = getattr(user, "_effective_permissions", set())
    result = await db.execute(select(MenuItem))
    items = list(result.scalars().all())
    return _build_tree(items, perms)

"""Menu CRUD — tree editor."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_permission
from app.auth.permissions import PermissionKey
from app.db.models.menu_item import MenuItem
from app.db.session import get_db
from app.schemas.auth import MenuCreate, MenuRead, MenuUpdate
from app.services.loggers.activity_logger import ActivityLogger

router = APIRouter(tags=["menus"])


def _to_node(item: MenuItem) -> MenuRead:
    return MenuRead(
        id=item.id, key=item.key, label_th=item.label_th, label_en=item.label_en,
        icon=item.icon, path=item.path, parent_id=item.parent_id,
        order_index=item.order_index,
        required_permission_key=item.required_permission_key,
        is_system=item.is_system, children=[],
    )


@router.get("", response_model=list[MenuRead], dependencies=[
    Depends(require_permission(PermissionKey.MENUS_READ))
])
async def list_menus(db: AsyncSession = Depends(get_db)) -> list[MenuRead]:
    result = await db.execute(select(MenuItem).order_by(MenuItem.order_index))
    items = list(result.scalars().all())
    by_parent: dict = {}
    for item in items:
        by_parent.setdefault(item.parent_id, []).append(item)

    def walk(parent_id) -> list[MenuRead]:
        nodes = sorted(by_parent.get(parent_id, []), key=lambda i: i.order_index)
        out: list[MenuRead] = []
        for n in nodes:
            node = _to_node(n)
            node.children = walk(n.id)
            out.append(node)
        return out
    return walk(None)


@router.post("", response_model=MenuRead, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission(PermissionKey.MENUS_CREATE))])
async def create_menu(
    payload: MenuCreate,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> MenuRead:
    existing = await db.execute(select(MenuItem).where(MenuItem.key == payload.key))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Menu key already exists")
    item = MenuItem(**payload.model_dump())
    db.add(item)
    await db.flush()
    audit = ActivityLogger(db)
    await audit.log(
        action="menu.created", action_type="create", resource_type="menu",
        resource_id=str(item.id), user=user, request=request,
    )
    return _to_node(item)


@router.patch("/{menu_id}", response_model=MenuRead, dependencies=[
    Depends(require_permission(PermissionKey.MENUS_UPDATE))
])
async def patch_menu(
    menu_id: UUID,
    payload: MenuUpdate,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> MenuRead:
    item = (await db.execute(select(MenuItem).where(MenuItem.id == menu_id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Menu not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    audit = ActivityLogger(db)
    await audit.log(
        action="menu.updated", action_type="update", resource_type="menu",
        resource_id=str(item.id), user=user, request=request,
    )
    return _to_node(item)


@router.delete("/{menu_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[
    Depends(require_permission(PermissionKey.MENUS_DELETE))
])
async def delete_menu(
    menu_id: UUID,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    item = (await db.execute(select(MenuItem).where(MenuItem.id == menu_id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Menu not found")
    children = (await db.execute(
        select(func.count()).select_from(MenuItem).where(MenuItem.parent_id == menu_id)
    )).scalar_one()
    if children:
        raise HTTPException(status_code=400, detail="Menu has children — delete those first")
    await db.delete(item)
    audit = ActivityLogger(db)
    await audit.log(
        action="menu.deleted", action_type="delete", resource_type="menu",
        resource_id=str(menu_id), user=user, request=request,
    )

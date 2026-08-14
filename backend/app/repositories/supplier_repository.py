"""Supplier CRUD repository."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.supplier import Supplier
from app.schemas.supplier import SupplierCreate, SupplierUpdate


async def get_supplier(db: AsyncSession, supplier_id: UUID) -> Supplier | None:
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    return result.scalar_one_or_none()


async def get_supplier_scoped(
    db: AsyncSession, supplier_id: UUID, scope_conditions: list[Any]
) -> Supplier | None:
    """Scope-aware lookup — returns None (→ 404) if outside the caller's scope."""
    stmt = select(Supplier).where(Supplier.id == supplier_id)
    for cond in scope_conditions:
        stmt = stmt.where(cond)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_supplier_by_code(db: AsyncSession, code: str) -> Supplier | None:
    result = await db.execute(
        select(Supplier).where(func.lower(Supplier.code) == code.strip().lower())
    )
    return result.scalar_one_or_none()


async def list_suppliers(
    db: AsyncSession,
    scope_conditions: list[Any],
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    active_only: bool = False,
) -> list[Supplier]:
    stmt = select(Supplier).order_by(Supplier.name.asc())
    for cond in scope_conditions:
        stmt = stmt.where(cond)
    if active_only:
        stmt = stmt.where(Supplier.is_active.is_(True))
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            Supplier.name.ilike(pattern) | Supplier.code.ilike(pattern)
        )
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _apply_supplier_status_filter(stmt, *, status: str):
    """Round 8-20D — the single place that turns the three-way status filter
    into an is_active WHERE clause, so the search endpoint and anything that
    reuses it can't drift (same one-helper convention as plot_repository's
    _apply_plot_status_filter)."""
    if status == "active":
        return stmt.where(Supplier.is_active.is_(True))
    if status == "inactive":
        return stmt.where(Supplier.is_active.is_(False))
    return stmt  # "all"


async def search_suppliers(
    db: AsyncSession,
    scope_conditions: list[Any],
    *,
    q: str | None = None,
    contact_name: str | None = None,
    contact_phone_digits: str | None = None,
    status: str = "active",
    limit: int = 50,
    offset: int = 0,
) -> list[Supplier]:
    """Round 8-20D — the filter row behind POST /suppliers/search.

    Every supplied filter is ANDed. All three text filters are
    case-insensitive substring matches; `q` keeps the EXACT semantics
    GET /suppliers?q= has always had (code OR name), reused rather than
    redefined, so the two endpoints can never disagree about what `q` means.

    `contact_phone_digits` matches Supplier.contact_phone as a substring. The
    caller MUST pass an already-validated ASCII-digits-only fragment (the
    endpoint checks this by hand) — that guarantee is what makes the LIKE
    pattern safe: with no '%' or '_' able to reach it, the fragment cannot
    widen its own match. The value itself is parameterized by SQLAlchemy
    either way; this is about LIKE wildcard semantics, not SQL injection.

    `scope_conditions` are the SAME app-layer conditions
    get_supplier_scope_filter builds for the list/get endpoints (suppliers has
    no RLS policy, so these are the only isolation layer) — reused here, never
    re-derived or widened.

    No JOIN anywhere: every filter is a column on `suppliers` itself, so a
    supplier can never appear twice and no DISTINCT is needed. If a future
    filter ever needs another table, use an EXISTS correlated subquery (the
    pattern plot_repository.search_plots_by_phone follows) rather than a JOIN.

    Ordering is Supplier.name — the same display order list_suppliers uses —
    so paging through a filtered result is stable and never repeats a row.
    """
    stmt = select(Supplier).order_by(Supplier.name.asc(), Supplier.id.asc())
    for cond in scope_conditions:
        stmt = stmt.where(cond)

    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(Supplier.name.ilike(pattern) | Supplier.code.ilike(pattern))
    if contact_name and contact_name.strip():
        stmt = stmt.where(Supplier.contact_name.ilike(f"%{contact_name.strip()}%"))
    if contact_phone_digits:
        stmt = stmt.where(Supplier.contact_phone.like(f"%{contact_phone_digits}%"))

    stmt = _apply_supplier_status_filter(stmt, status=status)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_suppliers_by_codes(
    db: AsyncSession, codes: list[str]
) -> dict[str, Supplier]:
    """Round 8-20A — batch form of get_supplier_by_code, keyed by the LOWERED
    code. ONE query for a whole import file, never one per row.

    Deliberately NOT scope-filtered: the Supplier import needs to know a code
    is taken even when the caller can't see its row, or it would try to create
    a duplicate and hit the unique index as a confusing 409. The caller
    (services/supplier_import.py) applies scope separately via
    filter_supplier_ids_in_scope and turns an out-of-scope hit into a generic
    per-row error that describes nothing about the Supplier.
    """
    if not codes:
        return {}
    lowered = [c.strip().lower() for c in codes]
    result = await db.execute(
        select(Supplier).where(func.lower(Supplier.code).in_(lowered))
    )
    return {s.code.strip().lower(): s for s in result.scalars().all()}


async def filter_supplier_ids_in_scope(
    db: AsyncSession, supplier_ids: list[UUID], scope_conditions: list[Any]
) -> set[UUID]:
    """Round 8-20A — which of `supplier_ids` the caller may actually act on,
    in ONE query. `scope_conditions` are the same app-layer conditions
    get_supplier_scope_filter builds for the list/get endpoints (suppliers has
    no RLS policy, so these are the only isolation layer) — reused here, never
    re-derived."""
    if not supplier_ids:
        return set()
    stmt = select(Supplier.id).where(Supplier.id.in_(supplier_ids))
    for cond in scope_conditions:
        stmt = stmt.where(cond)
    result = await db.execute(stmt)
    return set(result.scalars().all())


async def list_suppliers_for_template(
    db: AsyncSession, *, scope_conditions: list[Any]
) -> list[Supplier]:
    """Round 8-20A — every in-scope Supplier, ACTIVE AND INACTIVE, for the
    import template's pre-filled rows.

    Separate from list_suppliers because that one is a paginated UI list (its
    50-row default limit would silently truncate a template) and because a
    template must include inactive Suppliers so they can be reactivated
    through the file. Ordered by code — the identity column the file is keyed
    on — rather than list_suppliers' display-oriented name order.
    """
    stmt = select(Supplier).order_by(Supplier.code.asc())
    for cond in scope_conditions:
        stmt = stmt.where(cond)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def create_supplier(db: AsyncSession, payload: SupplierCreate) -> Supplier:
    supplier = Supplier(
        code=payload.code.strip().upper(),
        name=payload.name.strip(),
        tax_id=payload.tax_id,
        contact_name=payload.contact_name,
        contact_email=payload.contact_email,
        contact_phone=payload.contact_phone,
        address=payload.address,
    )
    db.add(supplier)
    await db.flush()
    await db.refresh(supplier)
    return supplier


async def update_supplier(
    db: AsyncSession, supplier: Supplier, payload: SupplierUpdate
) -> Supplier:
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(supplier, field, value)
    await db.flush()
    await db.refresh(supplier)
    return supplier

"""Dashboard — scope-aware KPI summary endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.scope import RLSContext
from app.auth.dependencies import CurrentUser
from app.db.session import get_db
from app.repositories import dashboard_repository as repo
from app.schemas.dashboard import DashboardSummary

router = APIRouter(tags=["dashboard"])

_SUPPLIER_VISIBLE_ROLES = {"internal:super_admin", "internal:admin", "farmlog:supervisor"}


@router.get("/summary", response_model=DashboardSummary)
async def get_summary(
    _rls: RLSContext,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> DashboardSummary:
    """Returns KPIs scoped to the current user via Postgres RLS.

    No explicit permission required — any authenticated user gets their
    scoped view.  Suppliers count is included only for internal/supervisor
    roles (they can meaningfully see the total across all suppliers).
    """
    role_names = {r.name for r in current_user.roles}
    include_suppliers = bool(role_names & _SUPPLIER_VISIBLE_ROLES)
    return await repo.get_summary(db, include_suppliers=include_suppliers)

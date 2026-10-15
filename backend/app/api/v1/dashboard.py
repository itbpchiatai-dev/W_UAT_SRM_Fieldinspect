"""Dashboard — scope-aware KPI summary endpoint."""
from __future__ import annotations

import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.scope import RLSContext
from app.auth.dependencies import CurrentUser, require_permission
from app.auth.permissions import PermissionKey
from app.db.session import DbDep
from app.api.v1.reports import xlsx_response
from app.repositories import dashboard_repository as repo
from app.services.excel_workbook import CellValue, build_xlsx
from app.schemas.dashboard import (
    DashboardSummary,
    ExecutiveBreakdown,
    ExecutiveFollowUp,
    ExecutiveSummary,
    ExecutiveTrend,
)

router = APIRouter(tags=["dashboard"])

_SUPPLIER_VISIBLE_ROLES = {"internal:super_admin", "internal:admin", "farmlog:supervisor"}

# Round 47 — the three filters every executive endpoint shares. Declared
# once so a parameter cannot be spelled one way on the trend and another on
# the table; the names match the report endpoints (supplier_id / date_from /
# date_to) so the whole app asks for a filtered period the same way.
_RANGE_Q = Query(repo.DEFAULT_RANGE, description="30d | 3m | ytd (ignored when date_from/date_to given)")
_FROM_Q = Query(None, description="Start of a custom window; overrides `range`")
_TO_Q = Query(None, description="End of a custom window; overrides `range`")
_SUPPLIER_Q = Query(None, description="Narrow the whole page to one supplier")
# Round 49 — P.Code is how production strains are analysed here, so it
# narrows the page the same way a supplier does.
_PCODE_Q = Query(None, description="Narrow the whole page to one P.Code")



@router.get("/summary", response_model=DashboardSummary)
async def get_summary(
    _rls: RLSContext,
    current_user: CurrentUser,
    db: AsyncSession = DbDep,
) -> DashboardSummary:
    """Returns KPIs scoped to the current user via Postgres RLS.

    No explicit permission required — any authenticated user gets their
    scoped view.  Suppliers count is included only for internal/supervisor
    roles (they can meaningfully see the total across all suppliers).
    """
    role_names = {r.name for r in current_user.roles}
    include_suppliers = bool(role_names & _SUPPLIER_VISIBLE_ROLES)
    return await repo.get_summary(db, include_suppliers=include_suppliers)


@router.get(
    "/executive",
    response_model=ExecutiveSummary,
    dependencies=[
        Depends(require_permission(PermissionKey.EXECUTIVE_DASHBOARD_READ)),
    ],
)
async def get_executive(
    _rls: RLSContext,
    range: str = _RANGE_Q,
    date_from: datetime.date | None = _FROM_Q,
    date_to: datetime.date | None = _TO_Q,
    supplier_id: UUID | None = _SUPPLIER_Q,
    p_code: str | None = _PCODE_Q,
    db: AsyncSession = DbDep,
) -> ExecutiveSummary:
    """Company-wide overview — adoption, overdue plots, and yield against plan.

    Unlike /summary this one IS gated, because it presents a single set of
    totals for the whole company rather than a scoped view of the caller's own
    work. The permission is held by super_admin / admin / supervisor, the same
    roles scope.py already grants full read access to — so this exposes no row
    they could not already open one at a time, only a faster way to read them.

    RLSContext still runs first and is not redundant: it is what keeps the
    aggregates honest if the key is ever granted to a scoped user, who would
    then see their own narrower totals instead of the company's.
    """
    return await repo.get_executive_summary(
        db, range_key=range, date_from=date_from, date_to=date_to,
        supplier_id=supplier_id, p_code=p_code,
    )


# Round 44 — the three blocks the overview is built from. Separate endpoints
# rather than one fat payload: the range tabs refetch the trend and the
# comparison table, but the follow-up list never changes with the range, and
# a single endpoint would re-run its queries on every tab click.
_EXECUTIVE_GATE = [Depends(require_permission(PermissionKey.EXECUTIVE_DASHBOARD_READ))]


@router.get("/executive/trend", response_model=ExecutiveTrend,
            dependencies=_EXECUTIVE_GATE)
async def get_executive_trend(
    _rls: RLSContext,
    range: str = _RANGE_Q,
    date_from: datetime.date | None = _FROM_Q,
    date_to: datetime.date | None = _TO_Q,
    supplier_id: UUID | None = _SUPPLIER_Q,
    p_code: str | None = _PCODE_Q,
    db: AsyncSession = DbDep,
) -> ExecutiveTrend:
    """Inspections per week/month with the average score riding on top.

    An unrecognised `range` falls back to the default instead of 422: this is
    a read-only dashboard, and a stale bookmark should render the page.
    """
    return await repo.get_executive_trend(
        db, range_key=range, date_from=date_from, date_to=date_to,
        supplier_id=supplier_id, p_code=p_code,
    )


@router.get("/executive/breakdown", response_model=ExecutiveBreakdown,
            dependencies=_EXECUTIVE_GATE)
async def get_executive_breakdown(
    _rls: RLSContext,
    range: str = _RANGE_Q,
    by: str = Query(repo.GROUP_SUPPLIER, description="supplier | crop | variety | p_code"),
    date_from: datetime.date | None = _FROM_Q,
    date_to: datetime.date | None = _TO_Q,
    supplier_id: UUID | None = _SUPPLIER_Q,
    p_code: str | None = _PCODE_Q,
    db: AsyncSession = DbDep,
) -> ExecutiveBreakdown:
    """The comparison table. One shape, three groupings — see the repository
    for why grouping on the PLOT is what makes that possible."""
    return await repo.get_executive_breakdown(
        db, range_key=range, group_by=by, date_from=date_from, date_to=date_to,
        supplier_id=supplier_id, p_code=p_code,
    )


@router.get("/executive/follow-up", response_model=ExecutiveFollowUp,
            dependencies=_EXECUTIVE_GATE)
async def get_executive_follow_up(
    _rls: RLSContext,
    limit: int = Query(10, ge=1, le=200),
    supplier_id: UUID | None = _SUPPLIER_Q,
    p_code: str | None = _PCODE_Q,
    db: AsyncSession = DbDep,
) -> ExecutiveFollowUp:
    """Plots worth a visit, worst reason first. Deliberately NOT range-scoped
    — a window would hide the plot nobody has touched in four months, which
    is the one most worth finding."""
    return await repo.get_executive_follow_up(
        db, limit=limit, supplier_id=supplier_id, p_code=p_code,
    )


# Round 47 — the comparison table, downloadable.
#
# The columns mirror the on-screen table exactly and in the same order: a
# workbook that quietly carries MORE than the page is how a figure nobody
# has looked at ends up in a meeting, and one that carries less sends the
# reader back to the screen.
_BREAKDOWN_HEADERS: dict[str, str] = {
    "supplier": "Supplier",
    "crop": "พืช",
    "variety": "พันธุ์",
    "p_code": "P.Code",
}


def _breakdown_workbook(result: ExecutiveBreakdown) -> bytes:
    head = _BREAKDOWN_HEADERS.get(result.group_by, "Supplier")
    rows: list[list[CellValue]] = [[
        head, "แปลง", "ตรวจแล้ว", "ค้างตรวจ", "คะแนนเฉลี่ย",
        "เป้าผลิต (kg)", "เก็บได้ (kg)", "บรรลุเป้า (%)",
    ]]
    for r in result.rows:
        rows.append([
            r.label,
            r.plots,
            r.plots_inspected,
            r.plots_overdue,
            # None rather than 0 for every absent figure: a spreadsheet cell
            # holding 0 is a measurement, and these are "not measured". A
            # blank cell is also what AVERAGE() skips, which is the right
            # behaviour once someone adds a total row of their own.
            float(r.avg_score) if r.avg_score is not None else None,
            float(r.target_kg) if r.target_kg is not None else None,
            float(r.harvest_kg) if r.harvest_kg is not None else None,
            float(r.achievement_pct) if r.achievement_pct is not None else None,
        ])
    return build_xlsx([("executive-breakdown", rows)])


@router.get("/executive/breakdown/export", dependencies=_EXECUTIVE_GATE)
async def export_executive_breakdown(
    _rls: RLSContext,
    range: str = _RANGE_Q,
    by: str = Query(repo.GROUP_SUPPLIER, description="supplier | crop | variety | p_code"),
    date_from: datetime.date | None = _FROM_Q,
    date_to: datetime.date | None = _TO_Q,
    supplier_id: UUID | None = _SUPPLIER_Q,
    p_code: str | None = _PCODE_Q,
    db: AsyncSession = DbDep,
) -> Response:
    """The same rows the table shows, as .xlsx.

    Takes the identical filters and calls the identical repository function
    as the table endpoint, so the download can never disagree with what the
    person was looking at when they clicked it.
    """
    result = await repo.get_executive_breakdown(
        db, range_key=range, group_by=by, date_from=date_from, date_to=date_to,
        supplier_id=supplier_id, p_code=p_code,
    )
    return xlsx_response(
        _breakdown_workbook(result), f"executive-breakdown-{result.group_by}.xlsx",
    )

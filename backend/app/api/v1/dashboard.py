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
    ExecutiveConcerns,
    ExecutiveBreakdown,
    ExecutiveFollowUp,
    ExecutiveSummary,
    ExecutiveTrend,
    ProductionStatus,
)

router = APIRouter(tags=["dashboard"])

_SUPPLIER_VISIBLE_ROLES = {"internal:super_admin", "internal:admin", "farmlog:supervisor"}

# Round 47 — the three filters every executive endpoint shares. Declared
# once so a parameter cannot be spelled one way on the trend and another on
# the table; the names match the report endpoints (supplier_id / date_from /
# date_to) so the whole app asks for a filtered period the same way.
_RANGE_Q = Query(
    repo.DEFAULT_RANGE,
    description="30d | 3m | 6m | ytd (ignored when date_from/date_to given)",
)
_FROM_Q = Query(None, description="Start of a custom window; overrides `range`")
_TO_Q = Query(None, description="End of a custom window; overrides `range`")
_SUPPLIER_Q = Query(None, description="Narrow the whole page to one supplier")
# Round 49 — P.Code is how production strains are analysed here, so it
# narrows the page the same way a supplier does.
_PCODE_Q = Query(None, description="Narrow the whole page to one P.Code")
# Round 50 — a VIEW, never the thing that decides which production lens
# a plot falls in. No default: closing a cycle is a staff action that may
# never happen, so the page must not quietly depend on it.
_CYCLE_STATUS_Q = Query(
    None, description="active | harvested | cancelled (default: all)",
)



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
    cycle_status: str | None = _CYCLE_STATUS_Q,
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
        supplier_id=supplier_id, p_code=p_code, cycle_status=cycle_status,
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
    cycle_status: str | None = _CYCLE_STATUS_Q,
    db: AsyncSession = DbDep,
) -> ExecutiveTrend:
    """Inspections per week/month with the average score riding on top.

    An unrecognised `range` falls back to the default instead of 422: this is
    a read-only dashboard, and a stale bookmark should render the page.
    """
    return await repo.get_executive_trend(
        db, range_key=range, date_from=date_from, date_to=date_to,
        supplier_id=supplier_id, p_code=p_code, cycle_status=cycle_status,
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
    cycle_status: str | None = _CYCLE_STATUS_Q,
    db: AsyncSession = DbDep,
) -> ExecutiveBreakdown:
    """The comparison table. One shape, three groupings — see the repository
    for why grouping on the PLOT is what makes that possible."""
    return await repo.get_executive_breakdown(
        db, range_key=range, group_by=by, date_from=date_from, date_to=date_to,
        supplier_id=supplier_id, p_code=p_code, cycle_status=cycle_status,
    )


@router.get("/executive/follow-up", response_model=ExecutiveFollowUp,
            dependencies=_EXECUTIVE_GATE)
async def get_executive_follow_up(
    _rls: RLSContext,
    limit: int = Query(10, ge=1, le=200),
    offset: int = Query(0, ge=0),
    supplier_id: UUID | None = _SUPPLIER_Q,
    p_code: str | None = _PCODE_Q,
    cycle_status: str | None = _CYCLE_STATUS_Q,
    db: AsyncSession = DbDep,
) -> ExecutiveFollowUp:
    """Plots worth a visit, worst reason first. Deliberately NOT range-scoped
    — a window would hide the plot nobody has touched in four months, which
    is the one most worth finding."""
    return await repo.get_executive_follow_up(
        db, limit=limit, offset=offset, supplier_id=supplier_id, p_code=p_code,
        cycle_status=cycle_status,
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
        "กำลังผลิต (แปลง)", "เป้า (kg)", "คาดว่าจะได้ (kg)", "คาดการณ์ (%)",
        "ผลิตจบ (แปลง)", "เป้า (kg)", "ผลผลิตหลังคัด (kg)", "ผลิตได้จริง (%)",
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
            r.estimate_plots,
            float(r.estimate_target_kg) if r.estimate_target_kg is not None else None,
            float(r.estimate_kg) if r.estimate_kg is not None else None,
            float(r.estimate_pct) if r.estimate_pct is not None else None,
            r.actual_plots,
            float(r.actual_target_kg) if r.actual_target_kg is not None else None,
            float(r.actual_kg) if r.actual_kg is not None else None,
            float(r.actual_pct) if r.actual_pct is not None else None,
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
    cycle_status: str | None = _CYCLE_STATUS_Q,
    db: AsyncSession = DbDep,
) -> Response:
    """The same rows the table shows, as .xlsx.

    Takes the identical filters and calls the identical repository function
    as the table endpoint, so the download can never disagree with what the
    person was looking at when they clicked it.
    """
    result = await repo.get_executive_breakdown(
        db, range_key=range, group_by=by, date_from=date_from, date_to=date_to,
        supplier_id=supplier_id, p_code=p_code, cycle_status=cycle_status,
    )
    return xlsx_response(
        _breakdown_workbook(result), f"executive-breakdown-{result.group_by}.xlsx",
    )


@router.get("/executive/production", response_model=ProductionStatus,
            dependencies=_EXECUTIVE_GATE)
async def get_production_status(
    _rls: RLSContext,
    range: str = _RANGE_Q,
    date_from: datetime.date | None = _FROM_Q,
    date_to: datetime.date | None = _TO_Q,
    supplier_id: UUID | None = _SUPPLIER_Q,
    p_code: str | None = _PCODE_Q,
    cycle_status: str | None = _CYCLE_STATUS_Q,
    db: AsyncSession = DbDep,
) -> ProductionStatus:
    """The same plots through two lenses, split by their LAST record.

    Still growing -> judged on the yield ESTIMATE. Finished (that last
    record carries ผลผลิตหลังทำความสะอาด) -> judged on the cleaned weight,
    which is what was actually produced. Cycle status is not the test: it
    is a staff action that may never happen, and a mis-tapped "เก็บเกี่ยว"
    mid-season must not move a plot out of production.
    """
    return await repo.get_production_status(
        db, range_key=range, date_from=date_from, date_to=date_to,
        supplier_id=supplier_id, p_code=p_code, cycle_status=cycle_status,
    )


@router.get("/executive/concerns", response_model=ExecutiveConcerns,
            dependencies=_EXECUTIVE_GATE)
async def get_concerns(
    _rls: RLSContext,
    range: str = _RANGE_Q,
    # Clamped, not validated: a number typed into a filter box must never
    # produce an error page on a read-only dashboard.
    threshold_pct: float | None = Query(
        None, description="Below this share of plan is a concern (default 70)",
    ),
    # Unset = every row, which is what callers before round 56 received.
    limit: int | None = Query(None, ge=1, le=200),
    offset: int = Query(0, ge=0),
    date_from: datetime.date | None = _FROM_Q,
    date_to: datetime.date | None = _TO_Q,
    supplier_id: UUID | None = _SUPPLIER_Q,
    p_code: str | None = _PCODE_Q,
    cycle_status: str | None = _CYCLE_STATUS_Q,
    db: AsyncSession = DbDep,
) -> ExecutiveConcerns:
    """What to act on, summarised per P.Code.

    Returns structured rows, not sentences: the wording belongs to the page,
    and a row carrying its own prose cannot be re-sorted or re-grouped
    without rewriting it.
    """
    return await repo.get_concerns(
        db, range_key=range, date_from=date_from, date_to=date_to,
        supplier_id=supplier_id, p_code=p_code, cycle_status=cycle_status,
        threshold_pct=threshold_pct, limit=limit, offset=offset,
    )


# Round 56 — the two lists, downloadable.
#
# EVERY row, not the page on screen: the pager exists to keep the page
# light, and an export that stopped at ten would be a worse screenshot.
# Same filters, same repository call, so the file can never disagree with
# the list it came from. The wording mirrors the page's (concernText /
# REASON_TEXT in ExecutiveDashboard.tsx); the figures go in their own
# columns instead of inside a sentence, so they can be sorted and summed.
_SEVERITY_TEXT: dict[str, str] = {"high": "รุนแรง", "medium": "เฝ้าระวัง"}

_CONCERN_TEXT: dict[str, str] = {
    repo.CONCERN_IN_PRODUCTION_LOW: "กำลังผลิต — ต่ำกว่าเกณฑ์ของเป้า",
    repo.CONCERN_FINISHED_LOW: "ผลิตจบแล้ว — ได้ไม่ถึงเกณฑ์ของเป้า",
    repo.CONCERN_MISSING_CLEANED: "ปิดรอบแล้ว แต่ยังไม่กรอกผลผลิตหลังทำความสะอาด",
    repo.CONCERN_LOW_RETENTION: "อัตราคงเหลือหลังคัดต่ำกว่าค่าเฉลี่ยมาก",
}

# Only these two are measured against the typed threshold; a threshold
# printed beside the other two would claim a test that was never applied.
_THRESHOLD_KINDS = {repo.CONCERN_IN_PRODUCTION_LOW, repo.CONCERN_FINISHED_LOW}

_REASON_TEXT: dict[str, str] = {
    repo.FOLLOW_UP_NEVER: "ไม่เคยตรวจ",
    repo.FOLLOW_UP_LOW_SCORE: "คะแนนต่ำ",
    repo.FOLLOW_UP_OVERDUE: "ค้างตรวจ",
    repo.FOLLOW_UP_LOW_YIELD: "ผลผลิตต่ำกว่าเป้า",
}

# The value column means a different thing per reason; on screen the unit
# is glued to the number, in a spreadsheet it gets a column of its own.
_REASON_UNIT: dict[str, str] = {
    repo.FOLLOW_UP_NEVER: "วัน นับจากลงทะเบียนแปลง",
    repo.FOLLOW_UP_LOW_SCORE: "คะแนน (เต็ม 10)",
    repo.FOLLOW_UP_OVERDUE: "วัน นับจากตรวจล่าสุด",
    repo.FOLLOW_UP_LOW_YIELD: "% ของเป้า",
}


def _concerns_workbook(result: ExecutiveConcerns) -> bytes:
    rows: list[list[CellValue]] = [[
        "ระดับ", "P.Code", "ประเด็น", "แปลง", "ค่า (%)", "เกณฑ์ (% ของเป้า)",
    ]]
    for r in result.rows:
        rows.append([
            _SEVERITY_TEXT.get(r.severity, r.severity),
            r.key,
            _CONCERN_TEXT.get(r.kind, r.kind),
            r.plots,
            float(r.value) if r.value is not None else None,
            result.threshold_pct if r.kind in _THRESHOLD_KINDS else None,
        ])
    return build_xlsx([("executive-concerns", rows)])


def _follow_up_workbook(result: ExecutiveFollowUp) -> bytes:
    rows: list[list[CellValue]] = [[
        "รหัสแปลง", "ชื่อแปลง", "Supplier", "เหตุผล", "ค่า", "หน่วย", "ตรวจล่าสุด",
    ]]
    for r in result.rows:
        rows.append([
            r.plot_code,
            r.plot_name,
            r.supplier_code,
            _REASON_TEXT.get(r.reason, r.reason),
            float(r.value),
            _REASON_UNIT.get(r.reason, ""),
            r.last_inspected.isoformat() if r.last_inspected else "ยังไม่เคยตรวจ",
        ])
    return build_xlsx([("executive-follow-up", rows)])


@router.get("/executive/concerns/export", dependencies=_EXECUTIVE_GATE)
async def export_concerns(
    _rls: RLSContext,
    range: str = _RANGE_Q,
    threshold_pct: float | None = Query(
        None, description="Below this share of plan is a concern (default 70)",
    ),
    date_from: datetime.date | None = _FROM_Q,
    date_to: datetime.date | None = _TO_Q,
    supplier_id: UUID | None = _SUPPLIER_Q,
    p_code: str | None = _PCODE_Q,
    cycle_status: str | None = _CYCLE_STATUS_Q,
    db: AsyncSession = DbDep,
) -> Response:
    """Every concern for these filters and this threshold, as .xlsx."""
    result = await repo.get_concerns(
        db, range_key=range, date_from=date_from, date_to=date_to,
        supplier_id=supplier_id, p_code=p_code, cycle_status=cycle_status,
        threshold_pct=threshold_pct, limit=None,
    )
    return xlsx_response(_concerns_workbook(result), "executive-concerns.xlsx")


@router.get("/executive/follow-up/export", dependencies=_EXECUTIVE_GATE)
async def export_follow_up(
    _rls: RLSContext,
    supplier_id: UUID | None = _SUPPLIER_Q,
    p_code: str | None = _PCODE_Q,
    cycle_status: str | None = _CYCLE_STATUS_Q,
    db: AsyncSession = DbDep,
) -> Response:
    """Every plot on the worklist, worst first, as .xlsx. No period, like
    the list itself — it is about today."""
    result = await repo.get_executive_follow_up(
        db, limit=None, supplier_id=supplier_id, p_code=p_code,
        cycle_status=cycle_status,
    )
    return xlsx_response(_follow_up_workbook(result), "executive-follow-up.xlsx")

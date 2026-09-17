"""Reports — read-only aggregate views for the FarmLog "รายงาน" menu.

Report #1 "สถานะแปลง" (Plot Status): every active plot with its latest
inspection-derived status + yield, filterable by supplier / province / crop /
inspected-state / last-inspection date range, plus an Excel export of the
exact same filtered rows. Gated by plots.read (no new permission) and scoped
by RLS via get_rls_context, same as the Plots list.
"""
from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.scope import get_rls_context
from app.auth.dependencies import require_permission
from app.auth.permissions import PermissionKey
from app.db.session import DbDep
from app.repositories import report_repository as repo
from app.schemas.report import ReportCycleYieldRow, ReportPlotStatusRow
from app.services.excel_workbook import CellValue, build_xlsx

router = APIRouter(tags=["reports"])


@dataclass(frozen=True)
class ReportColumn:
    """One column of a report, in the ONE place both surfaces read.

    Round 29 — the header row and the cell values used to be two parallel
    lists in two functions, matched by position. Hiding a column from the
    Supplier copy would have meant editing both and hoping they stayed lined
    up; forgetting the second is how a column disappears from the screen but
    stays in the downloaded file.

    `key` is the ReportRow field the column shows, so the same name hides it
    from the JSON response (FastAPI's response_model_exclude) and from the
    workbook. A derived column (a label, a computed percentage) uses a name
    of its own, prefixed "calc:", and is workbook-only by nature.
    """

    key: str
    header: str
    value: Callable[[Any], CellValue]


def _visible(columns: list[ReportColumn], hidden: frozenset[str]) -> list[ReportColumn]:
    return [c for c in columns if c.key not in hidden]


def _workbook(
    sheet: str, columns: list[ReportColumn], rows: list[Any], hidden: frozenset[str],
) -> bytes:
    shown = _visible(columns, hidden)
    data: list[list[CellValue]] = [[c.header for c in shown]]
    for r in rows:
        data.append([c.value(r) for c in shown])
    return build_xlsx([(sheet, data)])


def _xlsx(content: bytes, filename: str) -> Response:
    """One workbook response shape for every report export (round 29): the
    spreadsheet media type, an attachment name, and no-store — a report may
    carry commercial figures and must not sit in a shared cache."""
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


def _validate_date_range(
    date_from: datetime.date | None, date_to: datetime.date | None
) -> None:
    """422 when the range is inverted (dateFrom > dateTo) — shared by both
    reports' JSON + export endpoints."""
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=422, detail="dateFrom ต้องไม่มากกว่า dateTo"
        )


@router.get("/plot-status", response_model=list[ReportPlotStatusRow], dependencies=[
    Depends(require_permission(PermissionKey.REPORTS_PLOT_STATUS)),
    Depends(get_rls_context),
])
async def plot_status_report(
    db: AsyncSession = DbDep,
    supplier_id: UUID | None = None,
    province: str | None = None,
    crop: str | None = None,
    inspected: str | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    # Round X — "เลขที่ Invoice", matched against the plot's open cycle.
    invoice: str | None = None,
    # Round 8-25D — the on-screen table used to have no ceiling at all (every
    # matching plot came back in one response); the export endpoint below
    # still does, on purpose, since a downloaded workbook must always contain
    # every filtered row regardless of what's paged on screen.
    limit: int = 100,
    offset: int = 0,
) -> list[ReportPlotStatusRow]:
    return await repo.plot_status_rows(
        db,
        supplier_id=supplier_id,
        province=province,
        crop=crop,
        inspected=inspected,
        date_from=date_from,
        date_to=date_to,
        invoice=invoice,
        limit=limit,
        offset=offset,
    )


# Round 29 — one spec per report, read by BOTH the JSON response and the
# workbook. `key` names the ReportPlotStatusRow field, so hiding a column is
# one entry in the hidden set below and it leaves the screen and the file
# together. "calc:" columns are derived and exist only in the workbook.
PLOT_STATUS_COLUMNS: list[ReportColumn] = [
    ReportColumn("supplier_code", "Supplier", lambda r: r.supplier_code),
    ReportColumn("supplier_name", "ชื่อ Supplier", lambda r: r.supplier_name),
    ReportColumn("plot_code", "รหัสแปลง", lambda r: r.plot_code),
    ReportColumn("plot_name", "ชื่อแปลง", lambda r: r.plot_name),
    ReportColumn("province", "จังหวัด", lambda r: r.province),
    ReportColumn(
        "calc:cycle_status", "สถานะรอบปลูก",
        lambda r: "รอเริ่มรอบปลูก" if r.active_cycle_id is None else "กำลังปลูก",
    ),
    # Round X — the open cycle's identity and references.
    ReportColumn("cycle_label", "ชื่อรอบปลูก", lambda r: r.cycle_label),
    ReportColumn("po_number", "PO Number", lambda r: r.po_number),
    ReportColumn("p_code", "P.Code", lambda r: r.p_code),
    ReportColumn("lot_no", "Lot No ระบบ", lambda r: r.lot_no),
    ReportColumn("supplier_lot_no", "Supplier Lot No", lambda r: r.supplier_lot_no),
    ReportColumn("oracle_supplier_code", "Oracle Supplier Code",
                 lambda r: r.oracle_supplier_code),
    ReportColumn("oracle_invoice", "Oracle Invoice", lambda r: r.oracle_invoice),
    ReportColumn("ref_account", "Ref Account", lambda r: r.ref_account),
    ReportColumn("planting_date", "วันที่ปลูก",
                 lambda r: r.planting_date.isoformat() if r.planting_date else None),
    ReportColumn("current_crop", "ชนิดพืช", lambda r: r.current_crop),
    ReportColumn("current_variety", "พันธุ์", lambda r: r.current_variety),
    ReportColumn("current_stage", "ระยะ", lambda r: r.current_stage),
    ReportColumn("current_yield_pct", "เปอร์เซ็นต์เทียบเป้าผลิต",
                 lambda r: _num(r.current_yield_pct)),
    # Round X — before the target, so "เป้าผลิต" keeps its "หน่วย" beside it.
    ReportColumn("plant_count", "จำนวนต้น", lambda r: r.plant_count),
    ReportColumn("expected_yield_full", "เป้าผลิต", lambda r: _num(r.expected_yield_full)),
    ReportColumn("expected_yield_unit", "หน่วย", lambda r: r.expected_yield_unit),
    ReportColumn(
        "calc:current_expected_yield", "ผลผลิตที่คาดว่าจะได้",
        lambda r: _num(_current_expected_yield(r.expected_yield_full, r.current_yield_pct)),
    ),
    # Round X — what the field team has REPORTED, not yet confirmed: the
    # confirmed figures appear in the cycle-yield report once the cycle is
    # closed. The label says so, so the two are never mistaken for each other.
    ReportColumn("reported_harvest_yield", "ผลผลิตตอนเก็บเกี่ยว (kg) — ภาคสนามรายงาน",
                 lambda r: _num(r.reported_harvest_yield)),
    ReportColumn("reported_final_yield_after_clean",
                 "ผลผลิตหลังทำความสะอาด (kg) — ภาคสนามรายงาน",
                 lambda r: _num(r.reported_final_yield_after_clean)),
    ReportColumn("reported_harvest_date", "วันที่รายงานผลผลิต",
                 lambda r: r.reported_harvest_date.isoformat()
                 if r.reported_harvest_date else None),
    ReportColumn("current_field_prep_score", "เตรียมแปลง",
                 lambda r: r.current_field_prep_score),
    ReportColumn("current_weather_score", "สภาพอากาศ", lambda r: r.current_weather_score),
    ReportColumn("current_care_score", "ดูแลรักษา", lambda r: r.current_care_score),
    ReportColumn("current_variety_resistance_score", "ต้านทานโรค",
                 lambda r: r.current_variety_resistance_score),
    ReportColumn("last_inspected_at", "ตรวจล่าสุด",
                 lambda r: r.last_inspected_at.date().isoformat()
                 if r.last_inspected_at else None),
    ReportColumn("last_inspected_by_code", "โดย", lambda r: r.last_inspected_by_code),
    ReportColumn("calc:is_inspected", "สถานะตรวจ",
                 lambda r: "ตรวจแล้ว" if r.is_inspected else "ยังไม่ตรวจ"),
]


# The header row, derived from the spec above — never a second list to keep
# in step with it.
_PLOT_STATUS_HEADERS: list[str] = [c.header for c in PLOT_STATUS_COLUMNS]


def _current_expected_yield(
    expected_yield_full: Decimal | None, current_yield_pct: Decimal | None
) -> Decimal | None:
    """expected_yield_full * current_yield_pct / 100 — mirrors the frontend's
    lib/yield-planning.ts::computeCurrentExpectedYield so the Excel column
    matches what the on-screen table shows."""
    if expected_yield_full is None or current_yield_pct is None:
        return None
    return (expected_yield_full * current_yield_pct) / 100


def _num(value: Decimal | None) -> float | None:
    """A Decimal as a numeric Excel cell (not text); None stays a blank cell,
    never 0."""
    return float(value) if value is not None else None


def _plot_status_workbook(
    rows: list[ReportPlotStatusRow], hidden: frozenset[str] = frozenset(),
) -> bytes:
    return _workbook("plot-status", PLOT_STATUS_COLUMNS, rows, hidden)


@router.get("/plot-status/export", dependencies=[
    Depends(require_permission(PermissionKey.REPORTS_PLOT_STATUS)),
    Depends(get_rls_context),
])
async def export_plot_status_report(
    db: AsyncSession = DbDep,
    supplier_id: UUID | None = None,
    province: str | None = None,
    crop: str | None = None,
    inspected: str | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    invoice: str | None = None,
) -> Response:
    rows = await repo.plot_status_rows(
        db,
        supplier_id=supplier_id,
        province=province,
        crop=crop,
        inspected=inspected,
        date_from=date_from,
        date_to=date_to,
        invoice=invoice,
    )
    content = _plot_status_workbook(rows)
    return _xlsx(content, "plot-status-report.xlsx")


# --- Report #2 "ผลผลิตตามรอบปลูก" (Cycle Yield, round 8-2.8B) ---------------
# One row per PlotCycle with its frozen final ESTIMATED-yield snapshot (round
# 8-2.8A). Same plots.read + RLS gating as plot-status; final_* read verbatim
# from the cycle, never recomputed. status default = "closed".

def _validate_cycle_yield_status(status: str) -> None:
    if status not in repo.CYCLE_YIELD_STATUS_FILTERS:
        raise HTTPException(
            status_code=422,
            detail="status ต้องเป็น closed / harvested / cancelled / active / all",
        )


@router.get("/cycle-yield", response_model=list[ReportCycleYieldRow], dependencies=[
    Depends(require_permission(PermissionKey.REPORTS_CYCLE_YIELD)),
    Depends(get_rls_context),
])
async def cycle_yield_report(
    db: AsyncSession = DbDep,
    supplier_id: UUID | None = None,
    crop: str | None = None,
    status: str = "closed",
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    # Round X — "เลขที่ Invoice", matched against the row's own cycle.
    invoice: str | None = None,
    # Round 8-25D — see plot_status_report's comment: the export endpoint
    # below deliberately never passes a limit.
    limit: int = 100,
    offset: int = 0,
) -> list[ReportCycleYieldRow]:
    _validate_cycle_yield_status(status)
    _validate_date_range(date_from, date_to)
    return await repo.cycle_yield_rows(
        db,
        supplier_id=supplier_id,
        crop=crop,
        status=status,
        date_from=date_from,
        date_to=date_to,
        invoice=invoice,
        limit=limit,
        offset=offset,
    )


# Round 29 — the cycle-yield spec, same shape as PLOT_STATUS_COLUMNS above.
CYCLE_YIELD_COLUMNS: list[ReportColumn] = [
    ReportColumn("supplier_code", "รหัส Supplier", lambda r: r.supplier_code),
    ReportColumn("supplier_name", "ชื่อ Supplier", lambda r: r.supplier_name),
    ReportColumn("plot_code", "รหัสแปลง", lambda r: r.plot_code),
    ReportColumn("plot_name", "ชื่อแปลง", lambda r: r.plot_name),
    ReportColumn("province", "จังหวัด", lambda r: r.province),
    ReportColumn("cycle_label", "ชื่อรอบปลูก", lambda r: r.cycle_label),
    ReportColumn("cycle_no", "รอบที่", lambda r: r.cycle_no),
    ReportColumn("cycle_status", "สถานะรอบ",
                 lambda r: _CYCLE_STATUS_LABELS.get(r.cycle_status, r.cycle_status)),
    ReportColumn("crop", "ชนิดพืช", lambda r: r.crop),
    ReportColumn("variety", "พันธุ์", lambda r: r.variety),
    ReportColumn("po_number", "PO Number", lambda r: r.po_number),
    ReportColumn("p_code", "P.Code", lambda r: r.p_code),
    ReportColumn("lot_no", "Lot No ระบบ", lambda r: r.lot_no),
    ReportColumn("supplier_lot_no", "Supplier Lot No", lambda r: r.supplier_lot_no),
    ReportColumn("lot_no_source", "ที่มา Lot",
                 lambda r: _LOT_SOURCE_LABELS.get(r.lot_no_source)
                 if r.lot_no_source else None),
    # Round X — the cycle's Oracle references, for reconciliation. After the
    # lot source, so the three lot columns stay together (round 8-12C.1).
    ReportColumn("oracle_supplier_code", "Oracle Supplier Code",
                 lambda r: r.oracle_supplier_code),
    ReportColumn("oracle_invoice", "Oracle Invoice", lambda r: r.oracle_invoice),
    ReportColumn("ref_account", "Ref Account", lambda r: r.ref_account),
    ReportColumn("planting_date", "วันที่ปลูก",
                 lambda r: r.planting_date.isoformat() if r.planting_date else None),
    ReportColumn("plant_count", "จำนวนต้น/จำนวนปลูก", lambda r: r.plant_count),
    # Decimals as numeric cells (not text).
    ReportColumn("expected_yield_full", "เป้าผลิต", lambda r: _num(r.expected_yield_full)),
    ReportColumn("expected_yield_unit", "หน่วย", lambda r: r.expected_yield_unit),
    ReportColumn("final_yield_pct", "Yield สุดท้าย (%)", lambda r: _num(r.final_yield_pct)),
    ReportColumn("final_estimated_yield", "ผลผลิตประมาณการสุดท้าย",
                 lambda r: _num(r.final_estimated_yield)),
    # Round 8-7C.1 — ACTUAL harvest figures (round 8-7A's final_plot Excel
    # action), right after the ESTIMATE column above so the two families sit
    # next to each other but are never confused: these are real measured
    # values, never called "ประมาณการ".
    ReportColumn("harvest_yield", "ผลผลิตตอนเก็บเกี่ยว", lambda r: _num(r.harvest_yield)),
    ReportColumn("final_yield_after_clean", "ผลผลิตจริงหลังทำความสะอาด",
                 lambda r: _num(r.final_yield_after_clean)),
    ReportColumn("final_yield_unit", "หน่วยผลผลิตจริง", lambda r: r.final_yield_unit),
    # Round X — actual after-clean yield vs the target, same-unit only
    # (yield_calculation.actual_vs_target_pct); blank across units.
    ReportColumn("actual_yield_pct", "ผลผลิตจริงเทียบเป้า (%)",
                 lambda r: _actual_vs_target_cell(r)),
    ReportColumn("harvest_date", "วันที่เก็บเกี่ยว",
                 lambda r: r.harvest_date.isoformat() if r.harvest_date else None),
    ReportColumn("final_note", "หมายเหตุผลผลิตสุดท้าย", lambda r: r.final_note),
    ReportColumn("started_at", "วันที่เริ่มรอบ",
                 lambda r: r.started_at.date().isoformat() if r.started_at else None),
    ReportColumn("closed_at", "วันที่ปิดรอบ",
                 lambda r: r.closed_at.date().isoformat() if r.closed_at else None),
    ReportColumn("close_reason", "เหตุผลการปิด", lambda r: r.close_reason),
    ReportColumn("final_inspection_record_id", "รหัสบันทึกที่ใช้สรุป",
                 lambda r: str(r.final_inspection_record_id)
                 if r.final_inspection_record_id else None),
]

_CYCLE_YIELD_HEADERS: list[str] = [c.header for c in CYCLE_YIELD_COLUMNS]

# Thai status labels — "ผลผลิตประมาณการสุดท้าย" is the frozen ESTIMATE at
# close time; "ผลผลิตตอนเก็บเกี่ยว"/"ผลผลิตจริงหลังทำความสะอาด" (round 8-7C.1)
# are the REAL measured harvest figures — the two are distinct columns,
# never conflated or relabeled into each other.
_CYCLE_STATUS_LABELS: dict[str, str] = {
    "active": "กำลังปลูก",
    "harvested": "เก็บเกี่ยวแล้ว",
    "cancelled": "ยกเลิก",
}

# Round 8-5B — Thai label for a cycle's lot_no_source in the export.
_LOT_SOURCE_LABELS: dict[str, str] = {
    "auto": "อัตโนมัติ",
    "manual": "กรอกเอง",
    "legacy": "ข้อมูลเดิม",
}


# Round X — shown instead of a percentage when both figures exist but in
# different units; the Plots list says the same thing (round R).
_DIFFERENT_UNITS = "คนละหน่วย"


def _actual_vs_target_cell(r: ReportCycleYieldRow) -> CellValue:
    """The percentage when it can be said; the reason when it cannot because
    the units differ; blank when a figure is missing (nothing to compare)."""
    if r.actual_yield_pct is not None:
        return float(r.actual_yield_pct)
    if (
        r.final_yield_after_clean is not None and r.expected_yield_full is not None
        and r.final_yield_unit != r.expected_yield_unit
    ):
        return _DIFFERENT_UNITS
    return None


def _cycle_yield_workbook(
    rows: list[ReportCycleYieldRow], hidden: frozenset[str] = frozenset(),
) -> bytes:
    return _workbook("cycle-yield", CYCLE_YIELD_COLUMNS, rows, hidden)


@router.get("/cycle-yield/export", dependencies=[
    Depends(require_permission(PermissionKey.REPORTS_CYCLE_YIELD)),
    Depends(get_rls_context),
])
async def export_cycle_yield_report(
    db: AsyncSession = DbDep,
    supplier_id: UUID | None = None,
    crop: str | None = None,
    status: str = "closed",
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    invoice: str | None = None,
) -> Response:
    _validate_cycle_yield_status(status)
    _validate_date_range(date_from, date_to)
    rows = await repo.cycle_yield_rows(
        db,
        supplier_id=supplier_id,
        crop=crop,
        status=status,
        date_from=date_from,
        date_to=date_to,
        invoice=invoice,
    )
    content = _cycle_yield_workbook(rows)
    return _xlsx(content, "cycle-yield-report.xlsx")

# --- The Supplier's own two reports (round 29) ----------------------------
#
# Separate routes with separate permission keys, not one route that decides by
# role: "who may open what" is then readable straight off the route table and
# off the Roles page, instead of living inside a condition.
#
# The ROWS are identical — RLS already scopes every report to the caller's own
# supplier, which is what makes a Supplier-facing report safe at all (verified
# on UAT: a supplier-scoped session sees only its own plots). What each variant
# decides for itself is its COLUMNS.
#
# Nothing is hidden yet, by decision: Oracle Invoice in particular is shared
# deliberately, so a Supplier can reconcile against it. When a column should go,
# its field name goes in the set below and it leaves the JSON response AND the
# workbook together — one edit, both surfaces, no way to hide it from the screen
# while it stays in the downloaded file.
SUPPLIER_HIDDEN_PLOT_STATUS: frozenset[str] = frozenset()
SUPPLIER_HIDDEN_CYCLE_YIELD: frozenset[str] = frozenset()


@router.get(
    "/supplier/plot-status",
    response_model=list[ReportPlotStatusRow],
    response_model_exclude=SUPPLIER_HIDDEN_PLOT_STATUS,
    dependencies=[
        Depends(require_permission(PermissionKey.REPORTS_PLOT_STATUS_SUPPLIER)),
        Depends(get_rls_context),
    ],
)
async def supplier_plot_status_report(
    # Depends on the internal handler rather than restating eight query
    # parameters: one implementation, one filter contract, and FastAPI still
    # documents every parameter on this route.
    rows: list[ReportPlotStatusRow] = Depends(plot_status_report),
) -> list[ReportPlotStatusRow]:
    return rows


@router.get(
    "/supplier/plot-status/export",
    dependencies=[
        Depends(require_permission(PermissionKey.REPORTS_PLOT_STATUS_SUPPLIER)),
        Depends(get_rls_context),
    ],
)
async def export_supplier_plot_status_report(
    db: AsyncSession = DbDep,
    supplier_id: UUID | None = None,
    province: str | None = None,
    crop: str | None = None,
    inspected: str | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    invoice: str | None = None,
) -> Response:
    # Every filtered row, never the on-screen page — same rule as the internal
    # export (repo default limit=None).
    rows = await repo.plot_status_rows(
        db,
        supplier_id=supplier_id,
        province=province,
        crop=crop,
        inspected=inspected,
        date_from=date_from,
        date_to=date_to,
        invoice=invoice,
    )
    return _xlsx(
        _plot_status_workbook(rows, SUPPLIER_HIDDEN_PLOT_STATUS),
        "plot-status-report.xlsx",
    )


@router.get(
    "/supplier/cycle-yield",
    response_model=list[ReportCycleYieldRow],
    response_model_exclude=SUPPLIER_HIDDEN_CYCLE_YIELD,
    dependencies=[
        Depends(require_permission(PermissionKey.REPORTS_CYCLE_YIELD_SUPPLIER)),
        Depends(get_rls_context),
    ],
)
async def supplier_cycle_yield_report(
    rows: list[ReportCycleYieldRow] = Depends(cycle_yield_report),
) -> list[ReportCycleYieldRow]:
    return rows


@router.get(
    "/supplier/cycle-yield/export",
    dependencies=[
        Depends(require_permission(PermissionKey.REPORTS_CYCLE_YIELD_SUPPLIER)),
        Depends(get_rls_context),
    ],
)
async def export_supplier_cycle_yield_report(
    db: AsyncSession = DbDep,
    supplier_id: UUID | None = None,
    crop: str | None = None,
    status: str = "closed",
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    invoice: str | None = None,
) -> Response:
    _validate_cycle_yield_status(status)
    _validate_date_range(date_from, date_to)
    rows = await repo.cycle_yield_rows(
        db,
        supplier_id=supplier_id,
        crop=crop,
        status=status,
        date_from=date_from,
        date_to=date_to,
        invoice=invoice,
    )
    return _xlsx(
        _cycle_yield_workbook(rows, SUPPLIER_HIDDEN_CYCLE_YIELD),
        "cycle-yield-report.xlsx",
    )

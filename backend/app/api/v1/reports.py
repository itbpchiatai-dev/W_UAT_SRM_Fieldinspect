"""Reports — read-only aggregate views for the FarmLog "รายงาน" menu.

Report #1 "สถานะแปลง" (Plot Status): every active plot with its latest
inspection-derived status + yield, filterable by supplier / province / crop /
inspected-state / last-inspection date range, plus an Excel export of the
exact same filtered rows. Gated by plots.read (no new permission) and scoped
by RLS via get_rls_context, same as the Plots list.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
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
    Depends(require_permission(PermissionKey.PLOTS_READ)),
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


_PLOT_STATUS_HEADERS: list[str] = [
    "Supplier",
    "ชื่อ Supplier",
    "รหัสแปลง",
    "ชื่อแปลง",
    "จังหวัด",
    "สถานะรอบปลูก",
    # Round X — the open cycle's identity and references.
    "ชื่อรอบปลูก",
    "PO Number",
    "P.Code",
    "Lot No ระบบ",
    "Supplier Lot No",
    "Oracle Supplier Code",
    "Oracle Invoice",
    "Ref Account",
    "วันที่ปลูก",
    "ชนิดพืช",
    "พันธุ์",
    "ระยะ",
    "เปอร์เซ็นต์เทียบเป้าผลิต",
    # Round X — before the target, so "เป้าผลิต" keeps its "หน่วย" beside it.
    "จำนวนต้น",
    "เป้าผลิต",
    "หน่วย",
    "ผลผลิตที่คาดว่าจะได้",
    # Round X — what the field team has REPORTED, not yet confirmed: the
    # confirmed figures appear in the cycle-yield report once the cycle is
    # closed. The label says so, so the two are never mistaken for each other.
    "ผลผลิตตอนเก็บเกี่ยว (kg) — ภาคสนามรายงาน",
    "ผลผลิตหลังทำความสะอาด (kg) — ภาคสนามรายงาน",
    "วันที่รายงานผลผลิต",
    "เตรียมแปลง",
    "สภาพอากาศ",
    "ดูแลรักษา",
    "ต้านทานโรค",
    "ตรวจล่าสุด",
    "โดย",
    "สถานะตรวจ",
]


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


def _plot_status_workbook(rows: list[ReportPlotStatusRow]) -> bytes:
    data: list[list[CellValue]] = [_PLOT_STATUS_HEADERS]
    for r in rows:
        data.append(
            [
                r.supplier_code,
                r.supplier_name,
                r.plot_code,
                r.plot_name,
                r.province,
                "รอเริ่มรอบปลูก" if r.active_cycle_id is None else "กำลังปลูก",
                r.cycle_label,
                r.po_number,
                r.p_code,
                r.lot_no,
                r.supplier_lot_no,
                r.oracle_supplier_code,
                r.oracle_invoice,
                r.ref_account,
                r.planting_date.isoformat() if r.planting_date else None,
                r.current_crop,
                r.current_variety,
                r.current_stage,
                _num(r.current_yield_pct),
                r.plant_count,
                _num(r.expected_yield_full),
                r.expected_yield_unit,
                _num(_current_expected_yield(r.expected_yield_full, r.current_yield_pct)),
                _num(r.reported_harvest_yield),
                _num(r.reported_final_yield_after_clean),
                r.reported_harvest_date.isoformat() if r.reported_harvest_date else None,
                r.current_field_prep_score,
                r.current_weather_score,
                r.current_care_score,
                r.current_variety_resistance_score,
                r.last_inspected_at.date().isoformat() if r.last_inspected_at else None,
                r.last_inspected_by_code,
                "ตรวจแล้ว" if r.is_inspected else "ยังไม่ตรวจ",
            ]
        )
    return build_xlsx([("plot-status", data)])


@router.get("/plot-status/export", dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_READ)),
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
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="plot-status-report.xlsx"',
            "Cache-Control": "no-store",
        },
    )


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
    Depends(require_permission(PermissionKey.PLOTS_READ)),
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


_CYCLE_YIELD_HEADERS: list[str] = [
    "รหัส Supplier",
    "ชื่อ Supplier",
    "รหัสแปลง",
    "ชื่อแปลง",
    "จังหวัด",
    "ชื่อรอบปลูก",
    "รอบที่",
    "สถานะรอบ",
    "ชนิดพืช",
    "พันธุ์",
    "PO Number",
    "P.Code",
    "Lot No ระบบ",
    "Supplier Lot No",
    "ที่มา Lot",
    # Round X — the cycle's Oracle references, for reconciliation. After the
    # lot source, so the three lot columns stay together (round 8-12C.1).
    "Oracle Supplier Code",
    "Oracle Invoice",
    "Ref Account",
    "วันที่ปลูก",
    "จำนวนต้น/จำนวนปลูก",
    "เป้าผลิต",
    "หน่วย",
    "Yield สุดท้าย (%)",
    "ผลผลิตประมาณการสุดท้าย",
    # Round 8-7C.1 — ACTUAL harvest figures (round 8-7A's final_plot Excel
    # action), inserted right after the ESTIMATE column above so the two
    # families sit next to each other but are never confused: these five are
    # real measured values, never called "ประมาณการ".
    "ผลผลิตตอนเก็บเกี่ยว",
    "ผลผลิตจริงหลังทำความสะอาด",
    "หน่วยผลผลิตจริง",
    # Round X — actual after-clean yield vs the target, same-unit only
    # (yield_calculation.actual_vs_target_pct); blank across units.
    "ผลผลิตจริงเทียบเป้า (%)",
    "วันที่เก็บเกี่ยว",
    "หมายเหตุผลผลิตสุดท้าย",
    "วันที่เริ่มรอบ",
    "วันที่ปิดรอบ",
    "เหตุผลการปิด",
    "รหัสบันทึกที่ใช้สรุป",
]

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


def _cycle_yield_workbook(rows: list[ReportCycleYieldRow]) -> bytes:
    data: list[list[CellValue]] = [_CYCLE_YIELD_HEADERS]
    for r in rows:
        data.append(
            [
                r.supplier_code,
                r.supplier_name,
                r.plot_code,
                r.plot_name,
                r.province,
                r.cycle_label,
                r.cycle_no,
                _CYCLE_STATUS_LABELS.get(r.cycle_status, r.cycle_status),
                r.crop,
                r.variety,
                r.po_number,
                r.p_code,
                r.lot_no,
                r.supplier_lot_no,
                _LOT_SOURCE_LABELS.get(r.lot_no_source) if r.lot_no_source else None,
                r.oracle_supplier_code,
                r.oracle_invoice,
                r.ref_account,
                r.planting_date.isoformat() if r.planting_date else None,
                r.plant_count,
                # Decimals as numeric cells (not text).
                _num(r.expected_yield_full),
                r.expected_yield_unit,
                _num(r.final_yield_pct),
                _num(r.final_estimated_yield),
                # Round 8-7C.1 — ACTUAL harvest figures, verbatim; None → blank cell.
                _num(r.harvest_yield),
                _num(r.final_yield_after_clean),
                r.final_yield_unit,
                _actual_vs_target_cell(r),
                r.harvest_date.isoformat() if r.harvest_date else None,
                r.final_note,
                r.started_at.date().isoformat() if r.started_at else None,
                r.closed_at.date().isoformat() if r.closed_at else None,
                r.close_reason,
                str(r.final_inspection_record_id) if r.final_inspection_record_id else None,
            ]
        )
    return build_xlsx([("cycle-yield", data)])


@router.get("/cycle-yield/export", dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_READ)),
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
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="cycle-yield-report.xlsx"',
            "Cache-Control": "no-store",
        },
    )

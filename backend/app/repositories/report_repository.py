"""Report queries — read-only, RLS-scoped like every other list endpoint.

All queries here run under the caller's RLS context (set by the endpoint's
get_rls_context dependency), so a supplier-scoped user automatically sees
only their own plots without any explicit WHERE on supplier_id.
"""
from __future__ import annotations

import datetime
from uuid import UUID

from sqlalchemy import cast, func, select
from sqlalchemy import Date as SADate
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.plot import Plot
from app.db.models.plot_cycle import (
    CYCLE_CLOSED_STATUSES,
    CYCLE_STATUS_ACTIVE,
    CYCLE_STATUS_CANCELLED,
    CYCLE_STATUS_HARVESTED,
    PlotCycle,
)
from app.db.models.supplier import Supplier
from app.schemas.report import ReportCycleYieldRow, ReportPlotStatusRow

# Cycle-yield status filter values (Report #2). "closed" = the two terminal
# states; "all" = no status filter. Anything else is rejected by the endpoint.
CYCLE_YIELD_STATUS_FILTERS: tuple[str, ...] = (
    "closed", CYCLE_STATUS_HARVESTED, CYCLE_STATUS_CANCELLED, CYCLE_STATUS_ACTIVE, "all",
)


async def plot_status_rows(
    db: AsyncSession,
    *,
    supplier_id: UUID | None = None,
    province: str | None = None,
    crop: str | None = None,
    inspected: str | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[ReportPlotStatusRow]:
    """Rows for Report #1 "สถานะแปลง".

    Identity + yield PLAN (crop/variety/expected yield/plant count) come from
    the plot's ACTIVE cycle (round 7.4) — eager-loaded via Plot.active_cycle in
    one IN-query, no N+1 — so a plot between cycles reports null there and the
    frontend shows "รอเริ่มรอบปลูก" instead of the last cycle's stale plan. The
    inspection-derived snapshot (stage/yield%/scores/last-inspection) stays from
    the plot's denormalized columns, kept in sync from the latest record
    (sync_current_status_from_record). Only active plots are reported.

    limit=None (round 8-25D's own default) is UNBOUNDED — the on-screen report
    endpoint always passes a real limit (see the router); the export endpoint
    deliberately never does, since a downloaded workbook must contain every
    filtered row regardless of what's paged on screen.
    """
    stmt = (
        select(Plot, Supplier.code, Supplier.name)
        .join(Supplier, Plot.supplier_id == Supplier.id)
        .options(selectinload(Plot.active_cycle))
        .where(Plot.is_active.is_(True))
        .order_by(Supplier.code.asc(), Plot.plot_code.asc())
    )

    if supplier_id is not None:
        stmt = stmt.where(Plot.supplier_id == supplier_id)
    if province:
        stmt = stmt.where(func.lower(Plot.province) == province.strip().lower())
    if crop:
        stmt = stmt.where(Plot.current_crop == crop)
    if inspected == "inspected":
        stmt = stmt.where(Plot.last_inspection_record_id.isnot(None))
    elif inspected == "not_inspected":
        stmt = stmt.where(Plot.last_inspection_record_id.is_(None))
    # A date range filters on last_inspected_at — never-inspected plots
    # (NULL) drop out naturally, matching "ช่วงวันที่ตรวจล่าสุด".
    if date_from is not None:
        stmt = stmt.where(cast(Plot.last_inspected_at, SADate) >= date_from)
    if date_to is not None:
        stmt = stmt.where(cast(Plot.last_inspected_at, SADate) <= date_to)
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)

    result = await db.execute(stmt)
    rows: list[ReportPlotStatusRow] = []
    for plot, supplier_code, supplier_name in result.all():
        cycle = plot.active_cycle
        rows.append(
            ReportPlotStatusRow(
                plot_id=plot.id,
                supplier_code=supplier_code,
                supplier_name=supplier_name,
                plot_code=plot.plot_code,
                plot_name=plot.name,
                province=plot.province,
                active_cycle_id=cycle.id if cycle is not None else None,
                active_cycle_no=cycle.cycle_no if cycle is not None else None,
                active_cycle_status=cycle.status if cycle is not None else None,
                # Identity + plan from the ACTIVE cycle (authoritative), null
                # when the plot is between cycles.
                current_crop=cycle.crop if cycle is not None else None,
                current_variety=cycle.variety if cycle is not None else None,
                expected_yield_full=cycle.expected_yield_full if cycle is not None else None,
                expected_yield_unit=cycle.expected_yield_unit if cycle is not None else None,
                plant_count=cycle.plant_count if cycle is not None else None,
                # Inspection-derived snapshot — stays from the plot mirror
                # (synced from the latest record of the active cycle).
                current_stage=plot.current_stage,
                current_yield_pct=plot.current_yield_pct,
                current_field_prep_score=plot.current_field_prep_score,
                current_weather_score=plot.current_weather_score,
                current_care_score=plot.current_care_score,
                current_variety_resistance_score=plot.current_variety_resistance_score,
                last_inspected_at=plot.last_inspected_at,
                last_inspected_by_code=plot.last_inspected_by_code,
                is_inspected=plot.last_inspection_record_id is not None,
            )
        )
    return rows


async def cycle_yield_rows(
    db: AsyncSession,
    *,
    supplier_id: UUID | None = None,
    crop: str | None = None,
    status: str = "closed",
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[ReportCycleYieldRow]:
    """Rows for Report #2 "ผลผลิตตามรอบปลูก" (round 8-2.8B).

    One row per PlotCycle, read from the cycle's OWN columns plus its plot +
    supplier identity — a single PlotCycle⋈Plot⋈Supplier query (no N+1), never
    the plots mirror, and NEVER a records-table query: the final_* fields are
    the frozen close-time snapshot (round 8-2.8A), read verbatim, never
    recomputed here.

    Unlike the plot-status report this does NOT filter Plot.is_active — a
    deactivated plot keeps its cycle history so the record is preserved. RLS
    (the plots policy applies through the join, same as plot_status_rows)
    scopes a supplier-only caller to their own plots automatically.

    status: 'closed' (default) = harvested + cancelled; 'harvested' /
    'cancelled' / 'active' pick one; 'all' = no status filter. date_from/
    date_to filter on closed_at (so active cycles, closed_at NULL, drop out of
    any date-bounded query). Ordered supplierCode ASC, plotCode ASC, cycleNo
    DESC (newest cycle first within a plot).

    limit=None (round 8-25D's own default) is UNBOUNDED — same contract as
    plot_status_rows above: the on-screen endpoint always passes a real limit,
    the export endpoint never does.
    """
    stmt = (
        select(PlotCycle, Plot, Supplier.code, Supplier.name)
        .join(Plot, PlotCycle.plot_id == Plot.id)
        .join(Supplier, Plot.supplier_id == Supplier.id)
        .order_by(Supplier.code.asc(), Plot.plot_code.asc(), PlotCycle.cycle_no.desc())
    )

    if supplier_id is not None:
        stmt = stmt.where(Plot.supplier_id == supplier_id)
    if crop:
        stmt = stmt.where(PlotCycle.crop == crop)

    if status == "closed":
        stmt = stmt.where(PlotCycle.status.in_(CYCLE_CLOSED_STATUSES))
    elif status == "all":
        pass  # no status filter
    else:  # a single explicit status (harvested / cancelled / active)
        stmt = stmt.where(PlotCycle.status == status)

    # closed_at date range — active cycles (closed_at NULL) never satisfy a
    # bounded comparison, so they drop out of any date-filtered query.
    if date_from is not None:
        stmt = stmt.where(cast(PlotCycle.closed_at, SADate) >= date_from)
    if date_to is not None:
        stmt = stmt.where(cast(PlotCycle.closed_at, SADate) <= date_to)
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)

    result = await db.execute(stmt)
    rows: list[ReportCycleYieldRow] = []
    for cycle, plot, supplier_code, supplier_name in result.all():
        rows.append(
            ReportCycleYieldRow(
                supplier_id=plot.supplier_id,
                supplier_code=supplier_code,
                supplier_name=supplier_name,
                plot_id=plot.id,
                plot_code=plot.plot_code,
                plot_name=plot.name,
                province=plot.province,
                plot_is_active=plot.is_active,
                cycle_id=cycle.id,
                cycle_no=cycle.cycle_no,
                cycle_label=cycle.cycle_label,
                cycle_status=cycle.status,
                crop=cycle.crop,
                variety=cycle.variety,
                po_number=cycle.po_number,
                p_code=cycle.p_code,
                lot_no=cycle.lot_no,
                lot_no_source=cycle.lot_no_source,
                supplier_lot_no=cycle.supplier_lot_no,
                planting_date=cycle.planting_date,
                plant_count=cycle.plant_count,
                expected_yield_full=cycle.expected_yield_full,
                expected_yield_unit=cycle.expected_yield_unit,
                started_at=cycle.started_at,
                closed_at=cycle.closed_at,
                close_reason=cycle.close_reason,
                # Verbatim snapshot — NEVER recomputed from records/mirror.
                final_yield_pct=cycle.final_yield_pct,
                final_estimated_yield=cycle.final_estimated_yield,
                final_inspection_record_id=cycle.final_inspection_record_id,
                # Round 8-7C.1 — ACTUAL harvest figures (round 8-7A), read
                # verbatim from THIS cycle's own columns — never the plot
                # mirror, never recomputed, never a records query.
                harvest_yield=cycle.harvest_yield,
                final_yield_after_clean=cycle.final_yield_after_clean,
                final_yield_unit=cycle.final_yield_unit,
                harvest_date=cycle.harvest_date,
                final_note=cycle.final_note,
            )
        )
    return rows

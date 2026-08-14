"""Report response schemas — read-only, aggregate/denormalized views.

Report #1 "สถานะแปลง" (Plot Status): identity + yield plan come from the plot's
ACTIVE planting cycle (round 7.4; null when between cycles), while the
inspection-derived snapshot (stage/yield%/scores/last-inspection) comes from
the plots table's denormalized columns (kept in sync from the latest inspection
record by plot_repository.sync_current_status_from_record). No records-table
query needed. See report_repository.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from uuid import UUID

from app.schemas.base import CamelBaseModel


class ReportPlotStatusRow(CamelBaseModel):
    plot_id: UUID
    supplier_code: str
    supplier_name: str
    plot_code: str
    plot_name: str
    province: str | None

    # Active planting cycle (round 7.4) — the authoritative source for this
    # plot's current identity/plan. active_cycle_id is null iff the plot has NO
    # active cycle (between cycles / never started): the frontend shows
    # "รอเริ่มรอบปลูก" and suppresses the stale yield plan on that signal alone.
    active_cycle_id: UUID | None = None
    active_cycle_no: int | None = None
    active_cycle_status: str | None = None

    # Plot identity — sourced from the ACTIVE cycle (round 7.4), not the plot
    # mirror, so a plot between cycles reports null here rather than the last
    # cycle's crop. Equal to the mirror while a cycle is active (kept in sync).
    current_crop: str | None
    current_variety: str | None

    # True inspection-derived snapshot (synced from the latest record).
    current_stage: str | None
    current_yield_pct: Decimal | None
    current_field_prep_score: int | None
    current_weather_score: int | None
    current_care_score: int | None
    current_variety_resistance_score: int | None

    # Yield-planning base data — "current expected yield" is computed
    # (expected_yield_full * current_yield_pct / 100), never stored.
    expected_yield_full: Decimal | None
    expected_yield_unit: str | None
    plant_count: int | None

    last_inspected_at: datetime.datetime | None
    last_inspected_by_code: str | None

    # True iff the plot has at least one inspection record synced onto it
    # (last_inspection_record_id IS NOT NULL) — drives the "ตรวจแล้ว /
    # ยังไม่ตรวจ" badge and the inspected/not_inspected filter.
    is_inspected: bool


class ReportCycleYieldRow(CamelBaseModel):
    """Report #2 "ผลผลิตตามรอบปลูก" (Cycle Yield): one row per PlotCycle, read
    STRICTLY from the cycle's own columns + its plot/supplier identity — never
    from the plots mirror and never by re-querying inspection records. The
    final_yield_pct/final_estimated_yield fields are the frozen ESTIMATE
    snapshot from close (round 8-2.8A), read verbatim — NOT actual harvested
    yield. harvest_yield/final_yield_after_clean/final_yield_unit/
    harvest_date/final_note (round 8-7C.1) are the REAL measured figures
    (round 8-7A's final_plot Excel action), also read verbatim — the two
    families are never conflated. Includes closed cycles of INACTIVE plots so
    a deactivated plot never loses its cycle history. See
    report_repository.cycle_yield_rows."""

    supplier_id: UUID
    supplier_code: str
    supplier_name: str
    plot_id: UUID
    plot_code: str
    plot_name: str
    province: str | None
    plot_is_active: bool

    cycle_id: UUID
    cycle_no: int
    cycle_label: str | None
    cycle_status: str

    crop: str | None
    variety: str | None
    # PO / P.Code (round 8-5B) + lot source — read STRICTLY from THIS cycle's
    # own columns (historical report → the row's own cycle, never the active).
    po_number: str | None = None
    p_code: str | None = None
    lot_no: str | None
    lot_no_source: str | None = None
    # Round 8-12C — the SUPPLIER's own lot identifier for this cycle (round
    # 8-12A, migration 0048). Read verbatim from the cycle's own column, never
    # recomputed and never confused with lot_no (the system-generated Auto Lot
    # / manual value above).
    supplier_lot_no: str | None = None
    planting_date: datetime.date | None
    plant_count: int | None
    expected_yield_full: Decimal | None
    expected_yield_unit: str | None

    started_at: datetime.datetime
    closed_at: datetime.datetime | None
    close_reason: str | None

    # Frozen final estimated-yield snapshot (round 8-2.8A), read verbatim from
    # PlotCycle — NULL for cycles closed before 0038, cycles with no closing
    # inspection, and (finalEstimatedYield/finalYieldPct) any cycle still active.
    final_yield_pct: Decimal | None
    final_estimated_yield: Decimal | None
    final_inspection_record_id: UUID | None

    # Round 8-7C.1 — ACTUAL harvested yield (round 8-7A's final_plot Excel
    # action), read verbatim from PlotCycle. Distinct from the ESTIMATE
    # fields above: final_estimated_yield/final_yield_pct are a frozen guess
    # from the last inspection at close time; these are the REAL measured
    # figures recorded when the cycle is finalized. NULL for an active cycle,
    # a cycle closed by any path OTHER than final_plot, or a legacy cycle
    # closed before this field existed. Never recomputed here.
    harvest_yield: Decimal | None
    final_yield_after_clean: Decimal | None
    final_yield_unit: str | None
    harvest_date: datetime.date | None
    final_note: str | None

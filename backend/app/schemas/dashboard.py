"""Dashboard summary schema."""
from __future__ import annotations

import datetime
from decimal import Decimal
from uuid import UUID

from app.schemas.base import CamelBaseModel


class CropTypeStat(CamelBaseModel):
    crop_type: str | None = None
    count: int


class DashboardSummary(CamelBaseModel):
    total_records: int
    records_this_month: int
    avg_condition_score: float | None = None  # avg of the 4 condition scores (1-10)
    low_score_count: int  # records with any condition score <= 3 (needs attention)
    total_plots: int
    total_suppliers: int | None = None  # None when caller lacks suppliers.read
    by_crop_type: list[CropTypeStat]


class ExecutiveSummary(CamelBaseModel):
    """Round 42 — company-wide figures for the executive overview.

    Deliberately NOT an extension of DashboardSummary: that one answers "how
    is the work in MY scope going" and is served to every authenticated user,
    while this one answers "is the system being used, and did we hit plan"
    across every supplier and is gated on executive_dashboard.read. Merging
    them would mean one payload whose meaning changes with the caller.

    Every count here is derived live; nothing is denormalized for it.
    """

    # --- row 1: is the system being used -------------------------------
    plots_total: int
    plots_inspected: int           # plots with >= 1 active record, ever
    plots_never_inspected: int     # plots_total - plots_inspected, sent so the
                                   # UI never recomputes it and drifts
    plots_overdue: int             # inspected, but no visit within the window
    overdue_days: int              # the window itself — the card's label reads
                                   # from this instead of hardcoding "30"
    records_total: int
    records_last_7_days: int
    records_last_30_days: int
    avg_condition_score: float | None = None
    scored_record_count: int       # how many records that average is from
    watch_plot_count: int          # plots whose CURRENT snapshot has a score <= 3

    # --- round 44: scope + the actionable roll-up ----------------------
    supplier_total: int = 0
    # Which window the range-scoped fields below were computed over. Echoed
    # so a card's label can never claim a different period than the query.
    range: str = "3m"
    # DISTINCT plots needing attention. The three reasons below OVERLAP (one
    # plot can be overdue AND low-scoring), so they do NOT sum to this — the
    # card must show the total and list the reasons, never add them up.
    actionable_plots: int = 0
    actionable_overdue: int = 0
    actionable_low_score: int = 0
    actionable_low_yield: int = 0

    # --- row 2: did we hit plan ----------------------------------------
    target_kg_total: Decimal | None = None
    harvest_kg_total: Decimal | None = None
    achievement_pct: float | None = None
    cleaned_kg_total: Decimal | None = None
    cleaned_record_count: int      # records that report ผลผลิตหลังทำความสะอาด
    cleaned_missing_count: int     # the rest — the card shows this next to the
                                   # total, because a small cleaned figure means
                                   # "nobody filled it in", not "a bad harvest"
    # Round 44 — cleaned / harvested, NOT cleaned / target. A different base
    # from achievement_pct on purpose: this answers "how much survived
    # cleaning", which is a processing question, not a farming one.
    cleaned_retention_pct: float | None = None


class TrendPoint(CamelBaseModel):
    """One bar + one point on the line of the trend chart."""

    period: datetime.date   # first day of the week/month this bucket covers
    inspections: int
    avg_score: float | None = None


class ExecutiveTrend(CamelBaseModel):
    """Round 44 — inspections over time, with the average score riding on top.

    `granularity` travels with the points because the axis labels depend on
    it, and because a 30-day range bucketed by month would be one bar — the
    frontend needs to know which it got, not guess from the count.
    """

    granularity: str        # "week" | "month"
    range: str
    points: list[TrendPoint]


class BreakdownRow(CamelBaseModel):
    """One row of the comparison table — the same shape whichever attribute
    it is grouped by, which is what lets Supplier/พืช/พันธุ์ share a table."""

    key: str                # supplier code, crop, or variety
    label: str              # supplier name, or the key itself
    plots: int
    plots_inspected: int
    plots_overdue: int
    inspections: int
    avg_score: float | None = None
    target_kg: Decimal | None = None
    harvest_kg: Decimal | None = None
    achievement_pct: float | None = None


class ExecutiveBreakdown(CamelBaseModel):
    group_by: str           # "supplier" | "crop" | "variety"
    range: str
    rows: list[BreakdownRow]


class FollowUpRow(CamelBaseModel):
    """One plot worth visiting, and the single most serious reason why.

    One row per PLOT, not per reason: the list exists so someone can work
    down it, and a plot that is both overdue and low-scoring is still one
    trip. `reason` carries the worst of them.
    """

    plot_id: UUID
    plot_code: str
    plot_name: str
    supplier_code: str | None = None
    reason: str             # "low_score" | "overdue" | "low_yield"
    value: float            # days overdue / lowest score / achievement %
    last_inspected: datetime.date | None = None


class ExecutiveFollowUp(CamelBaseModel):
    total: int              # distinct plots flagged, before the limit
    rows: list[FollowUpRow]

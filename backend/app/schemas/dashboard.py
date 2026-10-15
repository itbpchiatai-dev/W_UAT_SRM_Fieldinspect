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
    # Round 53 — one number per lens, not one blended one. The old
    # achievement_pct summed every record, so a plot's target was counted
    # once per visit AND an in-progress estimate was averaged together with
    # a finished result. Both halves divide by the same target, so the two
    # percentages are comparable and the dumbbell chart can draw the gap.
    estimate_plots: int = 0
    estimate_target_kg: Decimal | None = None
    estimate_kg: Decimal | None = None
    estimate_pct: float | None = None
    actual_plots: int = 0
    actual_target_kg: Decimal | None = None
    actual_kg: Decimal | None = None
    actual_pct: float | None = None


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

# --- Round 50: the two lenses ------------------------------------------
#
# A plot is read through exactly one of them, decided by its LAST record:
# carries ผลผลิตหลังทำความสะอาด -> finished, otherwise still in production.
# Cycle status is NOT the test — closing a cycle is a staff action that may
# never happen, and someone can mis-tap "เก็บเกี่ยว" mid-season and keep
# inspecting afterwards. The last record is the only thing that always
# reflects what actually happened.


class LensFigures(CamelBaseModel):
    """One lens: how many plots, the plan, what came of it.

    `value_kg` is the yield ESTIMATE for plots still in production and the
    CLEANED weight for finished ones — two different measurements, which is
    the whole reason the lenses are separate. The field is shared because
    both answer "what will this produce", and `pct` divides it by the same
    target either way, so the two percentages are directly comparable.
    """

    plots: int
    target_kg: Decimal | None = None
    value_kg: Decimal | None = None
    pct: float | None = None


class StageCount(CamelBaseModel):
    stage: str
    plots: int


class ProductionStatus(CamelBaseModel):
    """Round 50 — the block that sits above the comparison table."""

    in_production: LensFigures
    finished: LensFigures
    # The harvest weight of the SAME plots the finished lens counted — the
    # matching base for retention. Dividing cleaned by anything else turns
    # "most records are unfilled" into "most of the crop was lost".
    finished_harvest_kg: Decimal | None = None
    finished_retention_pct: float | None = None
    by_stage: list[StageCount] = []
    range: str = "3m"


CONCERN_IN_PRODUCTION_LOW = "in_production_low"
CONCERN_FINISHED_LOW = "finished_low"
CONCERN_MISSING_CLEANED = "missing_cleaned"
CONCERN_LOW_RETENTION = "low_retention"


class ConcernRow(CamelBaseModel):
    """One thing worth acting on, summarised at P.Code level.

    Carries data, not a sentence: the wording belongs to the page, and a
    row that already contains its own Thai prose cannot be re-grouped or
    re-sorted without rewriting it.
    """

    severity: str          # "high" | "medium"
    kind: str
    key: str               # the P.Code, or UNSPECIFIED_LABEL
    plots: int
    # Reading depends on `kind`: the worst (or average) percentage for the
    # yield kinds, the retention percentage for low_retention, unset for
    # missing_cleaned where the count IS the message.
    value: float | None = None


class ExecutiveConcerns(CamelBaseModel):
    threshold_pct: float   # echoed so the page cannot caption a different number
    rows: list[ConcernRow]


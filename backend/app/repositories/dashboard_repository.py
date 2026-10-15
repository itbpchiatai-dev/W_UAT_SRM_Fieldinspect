"""Dashboard summary queries — all filtered by Postgres RLS automatically."""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, and_, cast, func, select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.plot import Plot
from app.db.models.plot_cycle import (
    CYCLE_STATUS_ACTIVE,
    CYCLE_STATUS_HARVESTED,
    PlotCycle,
)
from app.db.models.record import Record
from app.db.models.supplier import Supplier
from app.schemas.dashboard import (
    BreakdownRow,
    CropTypeStat,
    DashboardSummary,
    ExecutiveBreakdown,
    ExecutiveFollowUp,
    ExecutiveSummary,
    ExecutiveTrend,
    FollowUpRow,
    TrendPoint,
)


async def get_summary(db: AsyncSession, *, include_suppliers: bool) -> DashboardSummary:
    """Return scope-filtered KPIs.

    RLS set_config is called by the endpoint's RLSContext dependency before
    this function runs, so plain SELECT queries are automatically restricted
    to the caller's scope without extra WHERE conditions.
    """
    today = datetime.date.today()
    month_start = today.replace(day=1)

    total_records = (
        await db.scalar(
            select(func.count(Record.id)).where(Record.is_active.is_(True))
        )
    ) or 0

    records_this_month = (
        await db.scalar(
            select(func.count(Record.id)).where(
                Record.is_active.is_(True),
                Record.record_date >= month_start,
            )
        )
    ) or 0

    # Condition scores (Step 12.6) — overall average + records flagged for attention.
    score_avgs = (
        await db.execute(
            select(
                func.avg(Record.field_prep_score),
                func.avg(Record.weather_score),
                func.avg(Record.care_score),
                func.avg(Record.variety_resistance_score),
            ).where(Record.is_active.is_(True))
        )
    ).one()
    _present = [float(v) for v in score_avgs if v is not None]
    avg_condition_score = round(sum(_present) / len(_present), 1) if _present else None

    low_score_count = (
        await db.scalar(
            select(func.count(Record.id)).where(
                Record.is_active.is_(True),
                (Record.field_prep_score <= 3)
                | (Record.weather_score <= 3)
                | (Record.care_score <= 3)
                | (Record.variety_resistance_score <= 3),
            )
        )
    ) or 0

    total_plots = (
        await db.scalar(
            select(func.count(Plot.id)).where(Plot.is_active.is_(True))
        )
    ) or 0

    total_suppliers: int | None = None
    if include_suppliers:
        total_suppliers = (
            await db.scalar(
                select(func.count(Supplier.id)).where(Supplier.is_active.is_(True))
            )
        ) or 0

    # Top-6 crops by record count (None = unspecified)
    rows = (
        await db.execute(
            select(Record.crop, func.count(Record.id).label("cnt"))
            .where(Record.is_active.is_(True))
            .group_by(Record.crop)
            .order_by(func.count(Record.id).desc())
            .limit(6)
        )
    ).all()

    by_crop_type = [CropTypeStat(crop_type=r.crop, count=r.cnt) for r in rows]

    return DashboardSummary(
        total_records=total_records,
        records_this_month=records_this_month,
        avg_condition_score=avg_condition_score,
        low_score_count=low_score_count,
        total_plots=total_plots,
        total_suppliers=total_suppliers,
        by_crop_type=by_crop_type,
    )


# Round 42 — "ค้างตรวจ" starts here. A plot that has been inspected but not
# within this window is overdue; a plot never inspected at all is counted
# separately, because the two need different actions (chase the visit vs
# start the plot) and merging them produces one large number nobody can act
# on.
OVERDUE_DAYS = 30

# A condition score at or below this is what the watch list is made of —
# the same boundary DashboardSummary.low_score_count already uses, kept as
# one constant so the two pages can never disagree about what "low" means.
LOW_SCORE = 3


def achievement_pct(
    target_kg: Decimal | None, harvest_kg: Decimal | None
) -> float | None:
    """Harvest as a percentage of plan, or None when there is nothing to
    compare against.

    None rather than 0 for a missing or zero target: "0% of plan" reads as a
    failed season, while the truth is that no plan was recorded. The card
    shows a dash for None and a number for 0.0, and those must stay different
    things. A harvest ABOVE plan is a real result and is returned as-is.
    """
    if target_kg is None or harvest_kg is None or target_kg <= 0:
        return None
    return round(float(harvest_kg) / float(target_kg) * 100, 1)


# --- Round 44: the time range -------------------------------------------
#
# The filter applies to what HAPPENED (inspections, scores, harvest) and
# never to what EXISTS (how many plots and suppliers there are, which plots
# are overdue right now). Mixing the two under one control is how a
# dashboard starts lying the moment somebody changes the range, so every
# range-scoped figure is grouped together in the schema and the chosen range
# is echoed back in the payload.
RANGE_30D = "30d"
RANGE_3M = "3m"
RANGE_YTD = "ytd"
RANGE_CUSTOM = "custom"
# Round 47 — "ทั้งหมด" is gone, replaced by an explicit date_from/date_to.
# A preset answers "lately"; a named pair of dates answers "that season",
# and an unbounded "everything" answered neither while quietly growing
# slower as the table grew.
EXECUTIVE_RANGES: tuple[str, ...] = (RANGE_30D, RANGE_3M, RANGE_YTD)
DEFAULT_RANGE = RANGE_3M

# Where a custom window stops being readable as weeks. 17 weeks already
# crowds the axis; past that the chart buckets by month.
_WEEKLY_SPAN_DAYS = 120

# A harvested cycle that came in under this share of plan is worth a visit.
# Only CLOSED cycles are judged: an inspection at ระยะงอก legitimately
# reports a low percentage, and flagging those would bury the real ones.
LOW_YIELD_PCT = Decimal("80")

# Severity order for the follow-up list. A low score is a CONFIRMED problem
# somebody already saw; overdue is an unknown; a missed yield target is
# history. One row per plot carries the worst of them.
FOLLOW_UP_LOW_SCORE = "low_score"
FOLLOW_UP_OVERDUE = "overdue"
FOLLOW_UP_LOW_YIELD = "low_yield"
_FOLLOW_UP_SEVERITY = {
    FOLLOW_UP_LOW_SCORE: 0,
    FOLLOW_UP_OVERDUE: 1,
    FOLLOW_UP_LOW_YIELD: 2,
}
# Which way "worse" runs for each reason's value: more days overdue is
# worse, a lower score is worse, a lower % of plan is worse.
_FOLLOW_UP_DIRECTION = {
    FOLLOW_UP_LOW_SCORE: 1,
    FOLLOW_UP_OVERDUE: -1,
    FOLLOW_UP_LOW_YIELD: 1,
}


@dataclass(frozen=True)
class Window:
    """The slice of time every range-scoped figure on the page is cut from.

    One object rather than a bare pair of dates because three things have to
    travel together and stay consistent: the bounds, the label the payload
    echoes back, and the bucket size the trend chart draws. Computing the
    granularity at the point of use is how a custom six-month window ends up
    with 26 bars on one chart and 6 on another.
    """

    start: datetime.date | None
    end: datetime.date | None
    key: str
    granularity: str

    def conditions(self) -> list:
        """WHERE fragments for records inside this window."""
        out = []
        if self.start is not None:
            out.append(Record.record_date >= self.start)
        if self.end is not None:
            out.append(Record.record_date <= self.end)
        return out


def _plot_scope(supplier_id: UUID | None, p_code: str | None = None) -> list:
    """Conditions that pick the active plots this page is about.

    P.Code is not a column on plots — it lives on the planting cycle
    (plot_cycles.p_code, round 8-5A). For a PLOT, "is this P.Code" can only
    mean "is growing it now", so the filter asks about the plot's ACTIVE
    cycle. Written as an IN-subquery rather than a join so this stays a
    plain list of conditions every caller can splat into its own WHERE.
    """
    out = [Plot.is_active.is_(True)]
    if supplier_id is not None:
        out.append(Plot.supplier_id == supplier_id)
    if p_code:
        out.append(Plot.id.in_(
            select(PlotCycle.plot_id).where(
                PlotCycle.status == CYCLE_STATUS_ACTIVE,
                PlotCycle.p_code == p_code,
            )
        ))
    return out


def _record_scope(supplier_id: UUID | None, p_code: str | None = None) -> list:
    """The same for records.

    records.supplier_id is forced equal to the plot's on create (see the
    Record model), so the supplier filter needs no join and cannot disagree
    with _plot_scope.

    P.Code deliberately reads differently here: a record belongs to ONE
    cycle, so it is matched against that record's own cycle rather than the
    plot's current one. An inspection from last season still counts toward
    the P.Code it was actually grown under — which is the whole point of
    filtering a history by P.Code.
    """
    out = [Record.is_active.is_(True)]
    if supplier_id is not None:
        out.append(Record.supplier_id == supplier_id)
    if p_code:
        out.append(Record.plot_cycle_id.in_(
            select(PlotCycle.id).where(PlotCycle.p_code == p_code)
        ))
    return out


def normalize_range(range_key: str | None) -> str:
    """An unknown value falls back to the default rather than 400.

    This is a read-only dashboard: a stale bookmark carrying ?range=all —
    which round 47 removed — should show the default page, not an error.
    """
    return range_key if range_key in EXECUTIVE_RANGES else DEFAULT_RANGE


def _granularity_for_span(days: int) -> str:
    return "week" if days <= _WEEKLY_SPAN_DAYS else "month"


def resolve_window(
    range_key: str | None,
    date_from: datetime.date | None,
    date_to: datetime.date | None,
    today: datetime.date,
) -> Window:
    """Explicit dates win over the preset; one date alone is still explicit.

    A half-open pair is honoured rather than rejected: "from 1 June" with no
    end is a sentence a person means, and falling back to the preset there
    would silently show them a different window than the one they typed.
    """
    if date_from is not None or date_to is not None:
        start, end = date_from, date_to
        if start is not None and end is not None and end < start:
            start, end = end, start   # typed backwards; honour the intent
        span = ((end or today) - (start or today)).days
        return Window(start, end, RANGE_CUSTOM, _granularity_for_span(abs(span)))

    key = normalize_range(range_key)
    if key == RANGE_30D:
        return Window(today - datetime.timedelta(days=30), None, key, "week")
    if key == RANGE_3M:
        return Window(today - datetime.timedelta(days=90), None, key, "week")
    return Window(datetime.date(today.year, 1, 1), None, key, "month")


def retention_pct(
    cleaned_kg: Decimal | None, harvest_kg_of_cleaned: Decimal | None
) -> float | None:
    """How much weight survived cleaning.

    The second argument is deliberately NOT the window's whole harvest: it
    is the harvest of the records that also reported a cleaned weight. The
    first version of this divided by every record's harvest and reported
    9%, turning "12 of 305 records filled this in" into "91% was lost in
    cleaning" — a believable, alarming, and completely wrong claim.
    """
    if not harvest_kg_of_cleaned or harvest_kg_of_cleaned <= 0 or cleaned_kg is None:
        return None
    return round(float(cleaned_kg) / float(harvest_kg_of_cleaned) * 100, 1)


def follow_up_sort_key(reason: str, value: float) -> tuple[int, float]:
    """Order inside the follow-up list: reason first, then worst value.

    "Worst" points in different directions per reason — more days overdue is
    worse, but a LOWER score and a LOWER percentage of plan are worse. The
    first version negated all three and so listed a score of 3 above a score
    of 2.
    """
    return (_FOLLOW_UP_SEVERITY[reason], _FOLLOW_UP_DIRECTION[reason] * value)


async def get_executive_summary(
    db: AsyncSession,
    *,
    range_key: str = DEFAULT_RANGE,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    supplier_id: UUID | None = None,
    p_code: str | None = None,
) -> ExecutiveSummary:
    """Company-wide figures for the executive overview.

    RLS still applies: the endpoint's RLSContext runs first, and every holder
    of executive_dashboard.read is in scope.py's _FULL_ACCESS_ROLES, so these
    aggregates cover every supplier. Were the key ever granted to a
    supplier-scoped user, the numbers would silently narrow to that supplier
    rather than leak — RLS is the backstop, the permission is the gate.

    No query here can raise on empty data: every SUM is NULL-guarded and the
    one division is guarded on a positive denominator, so a database with no
    records yet renders the page with zeros instead of a 500.
    """
    today = datetime.date.today()
    window = resolve_window(range_key, date_from, date_to, today)
    # The supplier filter narrows EVERYTHING on the page, including the
    # counts that are otherwise "as of now" — picking one supplier changes
    # what "this" is, not just which period is in view.
    plot_scope = _plot_scope(supplier_id, p_code)
    record_scope = _record_scope(supplier_id, p_code)

    plots_total = (await db.scalar(
        select(func.count(Plot.id)).where(*plot_scope)
    )) or 0

    # "How big is what I am looking at" — the first thing a manager needs and
    # the thing v1 of this page left out entirely. Not range-scoped: a
    # supplier does not stop existing because the window is 30 days.
    supplier_total = (await db.scalar(
        select(func.count(Supplier.id)).where(*[
            c for c in (Supplier.is_active.is_(True),
                        Supplier.id == supplier_id if supplier_id else None)
            if c is not None
        ])
    )) or 0

    # Both of these count PLOTS, and both only count active ones — otherwise
    # plots_never_inspected (a subtraction) could go negative the moment a
    # plot with records is deactivated.
    plots_inspected = (await db.scalar(
        select(func.count(func.distinct(Record.plot_id)))
        .select_from(Record).join(Plot, Plot.id == Record.plot_id)
        .where(Record.is_active.is_(True), *plot_scope)
    )) or 0

    overdue_cutoff = today - datetime.timedelta(days=OVERDUE_DAYS)
    last_visit = _last_visit_sq(supplier_id, p_code)
    plots_overdue = (await db.scalar(
        select(func.count()).select_from(last_visit)
        .where(last_visit.c.last_visit < overdue_cutoff)
    )) or 0

    # record_date, not created_at: these cards are about field visits, so a
    # record typed in late belongs to the day the inspector stood in the plot.
    counts = (await db.execute(
        select(
            func.count(Record.id),
            func.count(Record.id).filter(
                Record.record_date > today - datetime.timedelta(days=7)),
            func.count(Record.id).filter(
                Record.record_date > today - datetime.timedelta(days=30)),
        ).where(*record_scope)
    )).one()
    records_total, records_7, records_30 = (counts[0] or 0, counts[1] or 0, counts[2] or 0)

    scores = (await db.execute(
        select(
            func.avg(Record.field_prep_score),
            func.avg(Record.weather_score),
            func.avg(Record.care_score),
            func.avg(Record.variety_resistance_score),
            func.count(Record.id).filter(Record.field_prep_score.isnot(None)),
        ).where(*record_scope, *window.conditions())
    )).one()
    present = [float(v) for v in scores[:4] if v is not None]
    avg_condition_score = round(sum(present) / len(present), 1) if present else None
    scored_record_count = scores[4] or 0

    # Plots, not records: "which plots need attention right now" reads off the
    # plot's CURRENT snapshot (its latest inspection), so a plot that scored
    # badly once and has since recovered is not still on the list.
    # Postgres LEAST ignores NULLs and returns NULL only when all four are,
    # so a plot never inspected is excluded without a special case.
    watch_plot_count = (await db.scalar(
        select(func.count(Plot.id)).where(*plot_scope, _low_score_expr() <= LOW_SCORE)
    )) or 0

    yields = (await db.execute(
        select(
            func.sum(Record.yield_target_kg_snapshot),
            func.sum(Record.yield_quantity_kg),
            func.sum(Record.final_yield_after_clean),
            func.count(Record.id).filter(Record.final_yield_after_clean.isnot(None)),
            func.count(Record.id),
            # The harvest of ONLY the records that also reported a cleaned
            # weight — the matching base for cleaned_retention_pct below.
            func.sum(Record.yield_quantity_kg).filter(
                Record.final_yield_after_clean.isnot(None)
            ),
        ).where(*record_scope, *window.conditions())
    )).one()
    (target_kg, harvest_kg, cleaned_kg, cleaned_count, records_in_range,
     harvest_kg_of_cleaned) = yields
    cleaned_count = cleaned_count or 0
    records_in_range = records_in_range or 0

    # cleaned / harvested, over the SAME records — not over every record in
    # the window. Dividing by the whole window's harvest turned "12 of 305
    # records reported a cleaned weight" into "9% survived cleaning", which
    # is a different and alarming claim about a real harvest.
    cleaned_retention_pct = retention_pct(cleaned_kg, harvest_kg_of_cleaned)

    actionable = await _actionable_counts(db, today, supplier_id, p_code)

    return ExecutiveSummary(
        plots_total=plots_total,
        plots_inspected=plots_inspected,
        plots_never_inspected=max(0, plots_total - plots_inspected),
        plots_overdue=plots_overdue,
        overdue_days=OVERDUE_DAYS,
        records_total=records_total,
        records_last_7_days=records_7,
        records_last_30_days=records_30,
        avg_condition_score=avg_condition_score,
        scored_record_count=scored_record_count,
        watch_plot_count=watch_plot_count,
        target_kg_total=target_kg,
        harvest_kg_total=harvest_kg,
        achievement_pct=achievement_pct(target_kg, harvest_kg),
        cleaned_kg_total=cleaned_kg,
        cleaned_record_count=cleaned_count,
        cleaned_missing_count=max(0, records_in_range - cleaned_count),
        cleaned_retention_pct=cleaned_retention_pct,
        supplier_total=supplier_total,
        range=window.key,
        actionable_plots=actionable["total"],
        actionable_overdue=actionable[FOLLOW_UP_OVERDUE],
        actionable_low_score=actionable[FOLLOW_UP_LOW_SCORE],
        actionable_low_yield=actionable[FOLLOW_UP_LOW_YIELD],
    )


# --- Round 44: shared definitions of "overdue" and "low score" -----------
#
# Both the summary counts and the follow-up list are built from these, so
# the number on the card and the length of the list below it can never
# disagree about what they are counting.


def _last_visit_sq(supplier_id: UUID | None = None, p_code: str | None = None):
    """plot_id -> the most recent inspection date, over ALL time.

    Never range-scoped: a plot is overdue because nobody has been there
    lately, which is a fact about today and not about the window the user
    happens to be looking at.
    """
    return (
        select(
            Record.plot_id.label("plot_id"),
            func.max(Record.record_date).label("last_visit"),
        )
        .select_from(Record)
        .join(Plot, Plot.id == Record.plot_id)
        .where(Record.is_active.is_(True), *_plot_scope(supplier_id, p_code))
        .group_by(Record.plot_id)
        .subquery()
    )


def _low_score_expr():
    """Postgres LEAST ignores NULLs and is NULL only when all four are, so a
    plot that was never inspected is excluded without a special case."""
    return func.least(
        Plot.current_field_prep_score,
        Plot.current_weather_score,
        Plot.current_care_score,
        Plot.current_variety_resistance_score,
    )


async def _flag_sets(
    db: AsyncSession,
    today: datetime.date,
    supplier_id: UUID | None = None,
    p_code: str | None = None,
) -> dict[str, dict]:
    """{reason: {plot_id: value}} for the three things worth a visit.

    Returned as three separate sets rather than one merged list because the
    card shows each reason's own count (which overlap) AND the distinct
    total, and computing those from one merged list would lose the overlap.
    """
    cutoff = today - datetime.timedelta(days=OVERDUE_DAYS)
    last_visit = _last_visit_sq(supplier_id, p_code)
    overdue_rows = (await db.execute(
        select(last_visit.c.plot_id, last_visit.c.last_visit)
        .where(last_visit.c.last_visit < cutoff)
    )).all()
    overdue = {r.plot_id: float((today - r.last_visit).days) for r in overdue_rows}

    low_score_rows = (await db.execute(
        select(Plot.id, _low_score_expr().label("worst"))
        .where(*_plot_scope(supplier_id, p_code), _low_score_expr() <= LOW_SCORE)
    )).all()
    low_score = {r.id: float(r.worst) for r in low_score_rows}

    # Only CLOSED cycles are judged. An inspection at ระยะงอก legitimately
    # reports a low percentage of plan, and flagging those would bury the
    # harvests that genuinely came up short.
    low_yield_rows = (await db.execute(
        select(PlotCycle.plot_id, func.min(PlotCycle.final_yield_pct).label("pct"))
        .select_from(PlotCycle)
        .join(Plot, Plot.id == PlotCycle.plot_id)
        .where(
            *_plot_scope(supplier_id, p_code),
            PlotCycle.status == CYCLE_STATUS_HARVESTED,
            PlotCycle.final_yield_pct.isnot(None),
            PlotCycle.final_yield_pct < LOW_YIELD_PCT,
        )
        .group_by(PlotCycle.plot_id)
    )).all()
    low_yield = {r.plot_id: float(r.pct) for r in low_yield_rows}

    return {
        FOLLOW_UP_OVERDUE: overdue,
        FOLLOW_UP_LOW_SCORE: low_score,
        FOLLOW_UP_LOW_YIELD: low_yield,
    }


async def _actionable_counts(
    db: AsyncSession,
    today: datetime.date,
    supplier_id: UUID | None = None,
    p_code: str | None = None,
) -> dict:
    sets = await _flag_sets(db, today, supplier_id, p_code)
    union: set = set()
    for ids in sets.values():
        union |= set(ids)
    out = {reason: len(ids) for reason, ids in sets.items()}
    out["total"] = len(union)
    return out


async def get_executive_trend(
    db: AsyncSession,
    *,
    range_key: str = DEFAULT_RANGE,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    supplier_id: UUID | None = None,
    p_code: str | None = None,
) -> ExecutiveTrend:
    """Inspections per bucket, with the average condition score on top.

    Buckets are weeks for the short ranges and months for the long ones: 30
    days grouped by month is a single bar, which is not a trend.

    The score is averaged the SAME way get_summary and get_executive_summary
    do it — each of the four columns averaged, then those averaged — so the
    line and the card agree. Averaging per record instead would be defensible
    on its own but would make the two disagree whenever a score is missing.
    """
    today = datetime.date.today()
    window = resolve_window(range_key, date_from, date_to, today)

    bucket = cast(
        func.date_trunc(window.granularity, Record.record_date), Date
    ).label("period")
    rows = (await db.execute(
        select(
            bucket,
            func.count(Record.id).label("inspections"),
            func.avg(Record.field_prep_score),
            func.avg(Record.weather_score),
            func.avg(Record.care_score),
            func.avg(Record.variety_resistance_score),
        )
        .where(*_record_scope(supplier_id, p_code), *window.conditions())
        .group_by(bucket)
        .order_by(bucket)
    )).all()

    points: list[TrendPoint] = []
    for r in rows:
        present = [float(v) for v in (r[2], r[3], r[4], r[5]) if v is not None]
        points.append(TrendPoint(
            period=r.period,
            inspections=r.inspections or 0,
            avg_score=round(sum(present) / len(present), 1) if present else None,
        ))
    return ExecutiveTrend(
        granularity=window.granularity, range=window.key, points=points,
    )


GROUP_SUPPLIER = "supplier"
GROUP_CROP = "crop"
GROUP_VARIETY = "variety"
# Round 49 — P.Code is what the people reading this page analyse production
# strains by, so it is both a filter and a way to group the table.
GROUP_P_CODE = "p_code"
BREAKDOWN_GROUPS: tuple[str, ...] = (
    GROUP_SUPPLIER, GROUP_CROP, GROUP_VARIETY, GROUP_P_CODE,
)
UNSPECIFIED_LABEL = "(ไม่ระบุ)"


def normalize_group_by(group_by: str | None) -> str:
    return group_by if group_by in BREAKDOWN_GROUPS else GROUP_SUPPLIER


async def get_executive_breakdown(
    db: AsyncSession,
    *,
    range_key: str = DEFAULT_RANGE,
    group_by: str = GROUP_SUPPLIER,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    supplier_id: UUID | None = None,
    p_code: str | None = None,
) -> ExecutiveBreakdown:
    """The comparison table, grouped by one attribute OF THE PLOT.

    Grouping on the plot (its supplier, its current crop, its current
    variety) rather than on the record is what lets all three modes share
    one table: "แปลง" and "ตรวจแล้ว" mean the same thing in every mode, and
    a plot with no inspections still appears instead of vanishing from the
    comparison that is supposed to show it is behind.

    The range filter lives in the JOIN condition, not the WHERE clause —
    in the WHERE it would drop every plot with no inspections in the window,
    which are exactly the rows this table exists to surface.
    """
    today = datetime.date.today()
    window = resolve_window(range_key, date_from, date_to, today)
    group_by = normalize_group_by(group_by)

    # P.Code groups on the plot's ACTIVE cycle, which is the only place it
    # is stored. An aliased join keeps it out of the way of the harvested-
    # cycle subquery the follow-up list uses.
    active_cycle = aliased(PlotCycle)
    if group_by == GROUP_SUPPLIER:
        # Code AND name, the way supplierLabel() renders one everywhere else
        # in the app — a table of bare company names is hard to match back
        # to the codes people actually quote.
        key_col = Supplier.code
        label_col = func.concat(Supplier.code, " — ", Supplier.name)
    elif group_by == GROUP_CROP:
        key_col = label_col = Plot.current_crop
    elif group_by == GROUP_P_CODE:
        key_col = label_col = active_cycle.p_code
    else:
        key_col = label_col = Plot.current_variety

    join_on = [Record.plot_id == Plot.id, Record.is_active.is_(True)]
    join_on.extend(window.conditions())

    stmt = (
        select(
            key_col.label("key"),
            label_col.label("label"),
            func.count(func.distinct(Plot.id)).label("plots"),
            func.count(func.distinct(Record.plot_id)).label("plots_inspected"),
            func.count(Record.id).label("inspections"),
            func.avg(Record.field_prep_score),
            func.avg(Record.weather_score),
            func.avg(Record.care_score),
            func.avg(Record.variety_resistance_score),
            func.sum(Record.yield_target_kg_snapshot).label("target_kg"),
            func.sum(Record.yield_quantity_kg).label("harvest_kg"),
        )
        .select_from(Plot)
        .outerjoin(Record, and_(*join_on))
        .where(*_plot_scope(supplier_id, p_code))
        .group_by(key_col, label_col)
    )
    if group_by == GROUP_SUPPLIER:
        stmt = stmt.join(Supplier, Supplier.id == Plot.supplier_id)
    elif group_by == GROUP_P_CODE:
        stmt = stmt.outerjoin(active_cycle, and_(
            active_cycle.plot_id == Plot.id,
            active_cycle.status == CYCLE_STATUS_ACTIVE,
        ))
    rows = (await db.execute(stmt)).all()

    # Overdue is a fact about today, so it is counted over all time and
    # merged in rather than folded into the range-scoped query above.
    last_visit = _last_visit_sq(supplier_id, p_code)
    cutoff = today - datetime.timedelta(days=OVERDUE_DAYS)
    overdue_stmt = (
        select(key_col.label("key"), func.count(func.distinct(Plot.id)).label("n"))
        .select_from(Plot)
        .join(last_visit, last_visit.c.plot_id == Plot.id)
        .where(*_plot_scope(supplier_id, p_code), last_visit.c.last_visit < cutoff)
        .group_by(key_col)
    )
    if group_by == GROUP_SUPPLIER:
        overdue_stmt = overdue_stmt.join(Supplier, Supplier.id == Plot.supplier_id)
    elif group_by == GROUP_P_CODE:
        overdue_stmt = overdue_stmt.outerjoin(active_cycle, and_(
            active_cycle.plot_id == Plot.id,
            active_cycle.status == CYCLE_STATUS_ACTIVE,
        ))
    overdue_by_key = {r.key: r.n for r in (await db.execute(overdue_stmt)).all()}

    out: list[BreakdownRow] = []
    for r in rows:
        present = [float(v) for v in (r[5], r[6], r[7], r[8]) if v is not None]
        key = r.key if r.key not in (None, "") else UNSPECIFIED_LABEL
        out.append(BreakdownRow(
            key=key,
            label=(r.label if r.label not in (None, "") else UNSPECIFIED_LABEL),
            plots=r.plots or 0,
            plots_inspected=r.plots_inspected or 0,
            plots_overdue=overdue_by_key.get(r.key, 0),
            inspections=r.inspections or 0,
            avg_score=round(sum(present) / len(present), 1) if present else None,
            target_kg=r.target_kg,
            harvest_kg=r.harvest_kg,
            achievement_pct=achievement_pct(r.target_kg, r.harvest_kg),
        ))

    # Worst first: this table exists to find where to go, not to rank the
    # winners. Rows with nothing to compare sink to the bottom rather than
    # masquerading as the worst performers.
    out.sort(key=lambda row: (
        row.achievement_pct is None,
        row.achievement_pct if row.achievement_pct is not None else 0.0,
        -row.plots,
    ))
    return ExecutiveBreakdown(group_by=group_by, range=window.key, rows=out)


async def get_executive_follow_up(
    db: AsyncSession,
    *,
    limit: int = 10,
    supplier_id: UUID | None = None,
    p_code: str | None = None,
) -> ExecutiveFollowUp:
    """Plots worth a visit, worst reason first, one row per plot.

    Never range-scoped — "who should I send someone to" is a question about
    today, and a 30-day window would hide the plot nobody has touched in
    four months, which is the one that most needs finding.
    """
    today = datetime.date.today()
    sets = await _flag_sets(db, today, supplier_id, p_code)

    # One row per plot carrying its most serious reason: the list is a
    # worklist, and a plot that is both overdue and low-scoring is still one
    # trip.
    worst: dict = {}
    for reason in (FOLLOW_UP_LOW_SCORE, FOLLOW_UP_OVERDUE, FOLLOW_UP_LOW_YIELD):
        for plot_id, value in sets[reason].items():
            current = worst.get(plot_id)
            if current is None or _FOLLOW_UP_SEVERITY[reason] < _FOLLOW_UP_SEVERITY[current[0]]:
                worst[plot_id] = (reason, value)

    total = len(worst)
    if total == 0:
        return ExecutiveFollowUp(total=0, rows=[])

    # Worse-first INSIDE each reason, and "worse" points the other way for
    # two of the three: more days overdue is worse, but a LOWER score and a
    # LOWER percentage of plan are worse. One sign per reason.
    ordered = sorted(
        worst.items(),
        key=lambda item: follow_up_sort_key(item[1][0], item[1][1]),
    )[:limit]
    wanted = [plot_id for plot_id, _ in ordered]

    last_visit = _last_visit_sq(supplier_id, p_code)
    detail_rows = (await db.execute(
        select(
            Plot.id, Plot.plot_code, Plot.name,
            Supplier.code.label("supplier_code"),
            last_visit.c.last_visit,
        )
        .select_from(Plot)
        .join(Supplier, Supplier.id == Plot.supplier_id)
        .outerjoin(last_visit, last_visit.c.plot_id == Plot.id)
        .where(Plot.id.in_(wanted))
    )).all()
    detail = {r.id: r for r in detail_rows}

    rows: list[FollowUpRow] = []
    for plot_id, (reason, value) in ordered:
        d = detail.get(plot_id)
        if d is None:            # deactivated between the two queries
            continue
        rows.append(FollowUpRow(
            plot_id=plot_id,
            plot_code=d.plot_code,
            plot_name=d.name,
            supplier_code=d.supplier_code,
            reason=reason,
            value=round(value, 1),
            last_inspected=d.last_visit,
        ))
    return ExecutiveFollowUp(total=total, rows=rows)

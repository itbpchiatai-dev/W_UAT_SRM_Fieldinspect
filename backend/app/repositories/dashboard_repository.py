"""Dashboard summary queries — all filtered by Postgres RLS automatically."""
from __future__ import annotations

import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.plot import Plot
from app.db.models.record import Record
from app.db.models.supplier import Supplier
from app.schemas.dashboard import CropTypeStat, DashboardSummary


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
            select(func.count(Record.id)).where(Record.is_active == True)
        )
    ) or 0

    records_this_month = (
        await db.scalar(
            select(func.count(Record.id)).where(
                Record.is_active == True,
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
            ).where(Record.is_active == True)
        )
    ).one()
    _present = [float(v) for v in score_avgs if v is not None]
    avg_condition_score = round(sum(_present) / len(_present), 1) if _present else None

    low_score_count = (
        await db.scalar(
            select(func.count(Record.id)).where(
                Record.is_active == True,
                (Record.field_prep_score <= 3)
                | (Record.weather_score <= 3)
                | (Record.care_score <= 3)
                | (Record.variety_resistance_score <= 3),
            )
        )
    ) or 0

    total_plots = (
        await db.scalar(
            select(func.count(Plot.id)).where(Plot.is_active == True)
        )
    ) or 0

    total_suppliers: int | None = None
    if include_suppliers:
        total_suppliers = (
            await db.scalar(
                select(func.count(Supplier.id)).where(Supplier.is_active == True)
            )
        ) or 0

    # Top-6 crops by record count (None = unspecified)
    rows = (
        await db.execute(
            select(Record.crop, func.count(Record.id).label("cnt"))
            .where(Record.is_active == True)
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

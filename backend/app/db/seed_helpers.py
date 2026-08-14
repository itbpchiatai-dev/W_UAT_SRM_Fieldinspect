"""Seed helpers for the Plot → PlotCycle → Record hierarchy (round 7.1.1).

After migration 0034, records.plot_cycle_id is NOT NULL — every seeded Record
must reference its plot's active cycle. These helpers let the mock/reset seeds
(which build Plot/Record rows directly rather than via the API repositories)
stay cycle-aware without duplicating the get-or-create logic in each script.
"""
from __future__ import annotations

import datetime
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.plot import Plot
from app.db.models.plot_cycle import CYCLE_STATUS_ACTIVE, PlotCycle


def build_active_cycle(
    plot: Plot, *, cycle_no: int = 1, started_at: datetime.datetime | None = None
) -> PlotCycle:
    """Construct (does NOT add/flush) an ACTIVE PlotCycle mirroring a plot's
    master/planting fields — the same fields the migration-0034 backfill
    copies from plots.current_*. The plot must already have an id."""
    if started_at is None:
        if plot.current_planting_date is not None:
            started_at = datetime.datetime.combine(
                plot.current_planting_date, datetime.time.min,
                tzinfo=datetime.timezone.utc,
            )
        else:
            started_at = datetime.datetime.now(datetime.timezone.utc)
    return PlotCycle(
        plot_id=plot.id,
        cycle_no=cycle_no,
        status=CYCLE_STATUS_ACTIVE,
        crop=plot.current_crop,
        variety=plot.current_variety,
        lot_no=plot.current_lot_no,
        planting_date=plot.current_planting_date,
        plant_count=plot.plant_count,
        expected_yield_full=plot.expected_yield_full,
        expected_yield_unit=plot.expected_yield_unit,
        started_at=started_at,
    )


async def active_cycle_map(
    session: AsyncSession, plots: Iterable[Plot]
) -> dict[UUID, PlotCycle]:
    """Return {plot_id -> active PlotCycle} for the given plots, creating (and
    flushing) an active cycle for any plot that doesn't already have one.

    Idempotent get-or-create in bulk: safe for seeds that create fresh plots
    (no cycle yet) AND for seeds that append records to existing plots (which
    already carry a backfilled active cycle). The plots must already be
    flushed (have ids)."""
    plots = list(plots)
    ids = [p.id for p in plots]
    by_plot: dict[UUID, PlotCycle] = {}
    if ids:
        existing = (await session.execute(
            select(PlotCycle).where(
                PlotCycle.status == CYCLE_STATUS_ACTIVE,
                PlotCycle.plot_id.in_(ids),
            )
        )).scalars().all()
        by_plot = {c.plot_id: c for c in existing}

    created = False
    for plot in plots:
        if plot.id not in by_plot:
            cycle = build_active_cycle(plot)
            session.add(cycle)
            by_plot[plot.id] = cycle
            created = True
    if created:
        await session.flush()
    return by_plot

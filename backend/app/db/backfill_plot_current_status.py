"""Dev-safe backfill: plots.current_* snapshot from existing record history
(round 17, problem #1/#4) — `python -m app.db.backfill_plot_current_status`.

Only touches plots where last_inspection_record_id IS NULL (current status
truly empty) that already have at least one record — every other plot is
left alone. Reuses plot_repository.sync_current_status_from_record verbatim
(the exact same field mapping the live create-record sync path uses, round
12) sourced from each such plot's latest record (record_date desc,
created_at desc — same ordering as record_repository.list_records). Never
modifies `records` in any way — append-only history is untouched.

Round 17.1: sync_current_status_from_record no longer touches
current_crop/current_variety/current_lot_no/current_planting_date (those
are plot MASTER data, set only via Plot Create/Edit) — since this script
calls that function verbatim, it inherits the same behavior automatically.
It only ever backfills the true inspection-derived snapshot fields
(stage/yield%/scores/GPS/last-inspected-*).

Idempotent: plots that already have a last_inspection_record_id are
skipped, so re-running finds nothing left to do.

Dry-run by default — pass --apply to actually commit; otherwise this only
prints what it would change and makes no writes.

Refuses to run unless DB_HOST resolves to localhost/127.0.0.1, matching
app.db.seed_mock_farmlog's guard against ever pointing this at a
non-local database.
"""
from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.scope import get_public_plot_rls_context
from app.core.config import get_settings
from app.db.models.plot import Plot
from app.db.models.record import Record
from app.db.session import close_db, get_db_session, init_db
from app.repositories import plot_repository as repo

_ALLOWED_HOSTS = {"localhost", "127.0.0.1"}


async def _plots_needing_backfill(session: AsyncSession) -> list[Plot]:
    """Plots with an empty current-status snapshot that DO have >=1 record."""
    stmt = (
        select(Plot)
        .where(Plot.last_inspection_record_id.is_(None))
        .where(Plot.id.in_(select(Record.plot_id).distinct()))
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _latest_record_for_plot(session: AsyncSession, plot_id: UUID) -> Record | None:
    stmt = (
        select(Record)
        .where(Record.plot_id == plot_id)
        .order_by(Record.record_date.desc(), Record.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def run(apply: bool) -> None:
    settings = get_settings()
    print("=== DB target ===")
    print(f"host={settings.DB_HOST} port={settings.DB_PORT} db={settings.DB_NAME}")
    if settings.DB_HOST not in _ALLOWED_HOSTS:
        raise SystemExit(
            f"Refusing to run: DB_HOST={settings.DB_HOST!r} is not localhost/127.0.0.1."
        )

    async with get_db_session() as session:
        await get_public_plot_rls_context(db=session)

        plots = await _plots_needing_backfill(session)
        print(f"before: plots_missing_current_status_with_history={len(plots)}")

        if not plots:
            print("nothing to backfill")
            return

        updated = 0
        for plot in plots:
            latest = await _latest_record_for_plot(session, plot.id)
            if latest is None:
                continue
            print(f"  plot {plot.plot_code} ({plot.id}) <- record {latest.id} ({latest.record_date})")
            if apply:
                await repo.sync_current_status_from_record(session, latest)
            updated += 1

        if apply:
            await session.commit()
            after_plots = await _plots_needing_backfill(session)
            print(f"after: plots_missing_current_status_with_history={len(after_plots)}")
            print(f"backfilled {updated} plot(s)")
        else:
            print(f"[dry-run] would backfill {updated} plot(s) — re-run with --apply to commit")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually commit changes (default: dry-run report only, no writes)",
    )
    args = parser.parse_args()

    await init_db()
    try:
        await run(apply=args.apply)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

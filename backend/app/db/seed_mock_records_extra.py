"""APPEND-ONLY dev mock: add N extra inspection records spread evenly across
every active plot, then re-sync each touched plot's current-status snapshot
from its latest record. `python -m app.db.seed_mock_records_extra`

Unlike app.db.seed_mock_farmlog (which DELETES everything first), this only
INSERTS new records — existing suppliers/plots/records are left untouched.
Safe to run more than once (each run adds another batch).

Refuses to run unless DB_HOST resolves to localhost/127.0.0.1, same guard as
the other mock scripts.

What it does:
  - Adds RECORD_COUNT (default 200) records, round-robin over all active
    plots so each plot gets ~RECORD_COUNT/len(plots) new inspections.
  - record.crop/variety are copied from the plot's own master data
    (plots.current_crop/current_variety) so an inspection reflects what's
    actually planted; stage/weather/yield/scores/date are varied per record.
  - recorded_by_id = the internal:super_admin user (looked up by role, not
    hardcoded) — matches seed_mock_farmlog's convention; keeps
    external-field-helper@system.local reserved for the public flow.
  - After inserting, re-runs sync_current_status_from_record for every
    touched plot using that plot's newest record (by created_at), so the
    denormalized plots.current_* / last_inspection_record_id the map and the
    "สถานะแปลง" report read stay correct.
  - record.latitude/longitude (GPS-at-visit) are set near the plot's
    registered point with small jitter. This is a DISTINCT column from
    plots.latitude/longitude, which is what the dashboard map plots — so the
    map markers do NOT move; only inspection history / current status grow.
"""
from __future__ import annotations

import asyncio
import datetime
from decimal import Decimal

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models.plot import Plot
from app.db.models.record import Record
from app.db.models.role import Role
from app.db.models.user import User
from app.db.session import close_db, get_db_session, init_db
from app.db.seed_helpers import active_cycle_map
from app.repositories.plot_repository import sync_current_status_from_record

_ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
_NO_USER_ID = "00000000-0000-0000-0000-000000000000"

RECORD_COUNT = 200
_SUBMITTED_BY_CODE_COUNT = 20

# Reference "today" for spreading record_date — kept as a constant rather than
# datetime.date.today() so re-runs are reproducible within a dev DB.
_TODAY = datetime.date(2026, 7, 3)
_DATE_WINDOW_DAYS = 90

_MOCK_GROWTH_STAGES = ["ระยะงอก", "เจริญเติบโต", "ออกดอก", "ติดผล", "เก็บเกี่ยว"]
_MOCK_WEATHER = ["แจ่มใส", "มีเมฆ", "ฝนตก", "ร้อนจัด", "ลมแรง"]
_FALLBACK_CROPS = ["พริก", "เมล่อน", "ฟักทอง", "แตงโม", "แตงกวา"]


async def _require_localhost() -> None:
    settings = get_settings()
    print("=== DB target ===")
    print(f"host={settings.DB_HOST} port={settings.DB_PORT} db={settings.DB_NAME}")
    if settings.DB_HOST not in _ALLOWED_HOSTS:
        raise SystemExit(
            f"Refusing to run: DB_HOST={settings.DB_HOST!r} is not localhost/127.0.0.1."
        )


async def _set_scope_all(session) -> None:
    # plots/records are FORCE ROW LEVEL SECURITY; without a scope GUC set,
    # RLS's CASE defaults to false and every query silently sees 0 rows. Use a
    # session-level (transaction-local=false) GUC so it survives commit() when
    # we re-query for the post-insert sync pass. user_id/supplier_id must be a
    # castable uuid even under scope='all' (the RLS policy still casts them),
    # so use the all-zero sentinel — never an empty string, which errors with
    # "invalid input syntax for type uuid".
    from sqlalchemy import text

    await session.execute(
        text("SELECT set_config('app.scope', 'all', false)")
    )
    await session.execute(
        text("SELECT set_config('app.user_id', :uid, false)"), {"uid": _NO_USER_ID}
    )
    await session.execute(
        text("SELECT set_config('app.supplier_id', :sid, false)"), {"sid": _NO_USER_ID}
    )


def _jitter(value, seed: int):
    """Small deterministic offset (~±0.02°) around a registered coordinate so
    per-visit GPS isn't identical to the plot's point. None-safe."""
    if value is None:
        return None
    delta = Decimal((seed % 41) - 20) / Decimal(1000)  # -0.020 .. +0.020
    return value + delta


async def main() -> None:
    await init_db()
    try:
        await _require_localhost()

        async with get_db_session() as session:
            await _set_scope_all(session)

            admin = (await session.execute(
                select(User).join(User.roles)
                .where(Role.name == "internal:super_admin").limit(1)
            )).scalars().first()
            if admin is None:
                raise SystemExit("No internal:super_admin user found — run app.seed first.")

            plots = list((await session.execute(
                select(Plot).where(Plot.is_active.is_(True)).order_by(Plot.plot_code.asc())
            )).scalars().all())
            if not plots:
                raise SystemExit("No active plots found — run the plot seed first.")

            before = (await session.execute(select(func.count()).select_from(Record))).scalar_one()
            print(f"before: records={before}, active_plots={len(plots)}")

            # active planting cycle per plot (round 7.1.1). These plots already
            # exist, so they carry a backfilled active cycle — active_cycle_map
            # returns it (get-or-create; nothing new is created here in practice).
            cycle_by_plot = await active_cycle_map(session, plots)

            new_records: list[Record] = []
            for index in range(RECORD_COUNT):
                plot = plots[index % len(plots)]
                crop = plot.current_crop or _FALLBACK_CROPS[index % len(_FALLBACK_CROPS)]
                record = Record(
                    plot_id=plot.id,
                    supplier_id=plot.supplier_id,  # derived from the plot, never mismatched
                    plot_cycle_id=cycle_by_plot[plot.id].id,
                    recorded_by_id=admin.id,
                    record_date=_TODAY - datetime.timedelta(days=(index * 7) % _DATE_WINDOW_DAYS),
                    submitted_by_code=f"FIELD{(index % _SUBMITTED_BY_CODE_COUNT) + 1:03d}",
                    crop=crop,
                    variety=plot.current_variety,
                    growth_stage=_MOCK_GROWTH_STAGES[index % len(_MOCK_GROWTH_STAGES)],
                    yield_pct=Decimal("60") + Decimal((index * 13) % 61),  # 60..120
                    weather_condition=_MOCK_WEATHER[index % len(_MOCK_WEATHER)],
                    field_prep_score=1 + (index % 10),
                    weather_score=1 + ((index + 3) % 10),
                    care_score=1 + ((index + 6) % 10),
                    variety_resistance_score=1 + ((index + 9) % 10),
                    recommendation="ข้อมูลทดสอบ (batch +200) — ติดตามผลรอบถัดไป",
                    notes=f"mock extra record {index + 1:03d}/{RECORD_COUNT}",
                    latitude=_jitter(plot.latitude, index),
                    longitude=_jitter(plot.longitude, index * 7 + 3),
                    photo_urls=[],
                    custom_fields={"mockSeed": "farmlog-extra-200"},
                    is_active=True,
                )
                session.add(record)
                new_records.append(record)

            await session.commit()
            print(f"inserted: {len(new_records)} records")

            # Re-sync every touched plot from its NEWEST record (by created_at),
            # so plots.current_* / last_inspection_record_id reflect the latest
            # inspection — not necessarily one we just inserted (respects any
            # pre-existing newer record too).
            touched_plot_ids = {r.plot_id for r in new_records}
            synced = 0
            for plot_id in touched_plot_ids:
                latest = (await session.execute(
                    select(Record)
                    .where(Record.plot_id == plot_id, Record.is_active.is_(True))
                    .order_by(Record.created_at.desc())
                    .limit(1)
                )).scalars().first()
                if latest is not None:
                    await sync_current_status_from_record(session, latest)
                    synced += 1
            await session.commit()
            print(f"re-synced current status for {synced} plots")

            after = (await session.execute(select(func.count()).select_from(Record))).scalar_one()
            per_plot_counts = select(func.count().label("c")).select_from(Record).group_by(
                Record.plot_id
            ).subquery()
            stats = (await session.execute(
                select(
                    func.min(per_plot_counts.c.c),
                    func.max(per_plot_counts.c.c),
                    func.avg(per_plot_counts.c.c),
                )
            )).one()
            print(f"after: records={after} (added {after - before})")
            print(f"records/plot: min={stats[0]} max={stats[1]} avg={float(stats[2]):.2f}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

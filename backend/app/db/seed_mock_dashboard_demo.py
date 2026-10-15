"""Round 41 — DEV-ONLY demo data shaped so the executive dashboard is legible.
`python -m app.db.seed_mock_dashboard_demo [--reset | --clean]`

Why a second mock seeder instead of reusing seed_mock_records_extra:
that one spreads records round-robin over EVERY active plot inside a 90-day
window and writes notes on every row. Run it and the dashboard reads
"100% of plots inspected, 100% have notes, no plot is overdue" — every KPI
pegged, none of them telling you anything. A dashboard built against that
data looks finished right up until it meets UAT, where the same KPIs read
16% inspected and 3.7% cleaned-yield, and half the page turns out to be
cards nobody designed for the empty case.

So this seeder deliberately produces an UNEVEN dataset — bad in the same
places UAT is bad, just not as empty (see _SHAPE below). The numbers it
prints at the end are the same KPIs the dashboard will compute, so round 42
can be checked against them instead of against a hunch.

Safety:
  - Refuses to run unless DB_HOST is localhost/127.0.0.1 — the same guard
    every other mock script uses. UAT is protected by structure, not by
    anyone remembering. UAT gets its test data from real users typing it.
  - APPEND-ONLY, and every row it writes carries
    custom_fields->>'mockSeed' = 'dashboard-demo', so --clean removes
    exactly what this script created and nothing else.
  - Refuses to double-seed: a second plain run would stack two datasets and
    destroy the shape. Use --reset to regenerate.

Deterministic: one fixed RNG seed, so the same DB yields the same dataset
on every run and a number in a screenshot can be reproduced.

What it writes per record: stage/scores/weather/yield that vary with the
visit's position in the plot's own sequence (a plot ripens over its visits),
yields derived through services.yield_calculation.derive_yield — the SAME
function both real create flows use, so the stored
yield_pct/quantity/target triple is consistent with production rows rather
than invented here.

photo_urls stays empty on purpose: a URL with no file behind it renders as
a broken image in the record view, which looks like a bug in the app rather
than a gap in the demo data.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import random
from decimal import Decimal

from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.db.models.plot import Plot
from app.db.models.plot_cycle import CYCLE_STATUS_ACTIVE, PlotCycle
from app.db.models.record import Record
from app.db.models.role import Role
from app.db.models.user import User
from app.db.seed_helpers import active_cycle_map
from app.db.session import close_db, get_db_session, init_db
from app.repositories.plot_repository import resync_current_status_from_latest
from app.services.yield_calculation import YieldValidationError, derive_yield

_ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
_NO_USER_ID = "00000000-0000-0000-0000-000000000000"

SEED_TAG = "dashboard-demo"
_RANDOM_SEED = 20261015

# Reference "today". A constant, not date.today(), so a dataset stays
# reproducible and "7 days ago" means the same thing on every run.
_TODAY = datetime.date(2026, 10, 15)

# --- the shape (spec §6) --------------------------------------------------
# Left column is what this seeder aims for; right column is what UAT actually
# reads today. The point is to sit between "realistic" and "empty".
_SHAPE = {
    #                               demo      UAT 2026-10-15
    "months_back": 9,              # 9        0.9
    "plot_coverage": 0.55,         # 55%      16%
    "stale_share": 0.20,           # 20%      not measured
    # Share of HARVEST records, not of all records: a plot harvests once,
    # so "35% of every record" would be a number no real season can
    # produce. Lands around 8-9% of all rows, close to UAT's 3.7%.
    "cleaned_share_of_harvest": 0.35,
    "problem_plot_share": 0.12,    # 12%      exists
    "notes_share": 0.10,           # 10%      3%
    "recommendation_share": 0.20,  # 20%      6%
}
_STALE_DAYS = 30  # spec §0 decision 3 — "ค้างตรวจ" starts at 30 days

# Supplier achievement spread, so the supplier comparison table (spec §5.1
# row 3) has something to rank. Without this every supplier lands on ~100%
# and the sort order is noise.
_ACHIEVEMENT_MIN = Decimal("0.60")
_ACHIEVEMENT_MAX = Decimal("1.10")

# Fallback yield plan for a cycle that has none. Only ever written when the
# cycle's expected_yield_full IS NULL — an existing plan is never touched.
_FALLBACK_PLAN_KG = [Decimal("500"), Decimal("800"), Decimal("1200"),
                     Decimal("2000"), Decimal("3000")]

_STAGES_EARLY = ["ระยะงอก", "เจริญเติบโต"]
_STAGES_MID = ["ออกดอก", "ติดผล"]
_STAGE_HARVEST = "เก็บเกี่ยว"
_WEATHER = ["แจ่มใส", "มีเมฆ", "ฝนตก", "ร้อนจัด", "ลมแรง"]
_INSPECTOR_TYPES = ["farmer", "supplier", "chiatai"]
_NOTE_TEXTS = [
    "พบเพลี้ยไฟบริเวณขอบแปลง แนะนำให้เฝ้าระวัง",
    "ระบบน้ำหยดอุดตันบางจุด เกษตรกรรับไปแก้ไขแล้ว",
    "ดินแน่นผิดปกติ ควรพรวนก่อนรอบถัดไป",
    "ต้นกล้าตายบางส่วนจากฝนตกหนักสัปดาห์ที่ผ่านมา",
]
_RECOMMENDATION_TEXTS = [
    "ควรเพิ่มความถี่การให้น้ำในช่วงอากาศร้อน",
    "แนะนำใส่ปุ๋ยสูตรเสมอก่อนเข้าระยะออกดอก",
    "ติดตามผลอีกครั้งใน 2 สัปดาห์",
    "ควรเก็บตัวอย่างดินไปตรวจค่า pH",
]


async def _require_localhost() -> None:
    settings = get_settings()
    print("=== DB target ===")
    print(f"host={settings.DB_HOST} port={settings.DB_PORT} db={settings.DB_NAME}")
    if settings.DB_HOST not in _ALLOWED_HOSTS:
        raise SystemExit(
            f"Refusing to run: DB_HOST={settings.DB_HOST!r} is not localhost/127.0.0.1. "
            "This script never runs against UAT — real users supply UAT's test data."
        )


async def _set_scope_all(session) -> None:
    """plots/records are FORCE ROW LEVEL SECURITY: with no scope GUC the
    policies' CASE falls through to false and every query silently returns 0
    rows. Session-level (transaction-local=false) so it survives the commits
    below. user_id/supplier_id must still be castable to uuid even under
    scope='all' — hence the all-zero sentinel rather than ''."""
    await session.execute(text("SELECT set_config('app.scope', 'all', false)"))
    await session.execute(
        text("SELECT set_config('app.user_id', :uid, false)"), {"uid": _NO_USER_ID}
    )
    await session.execute(
        text("SELECT set_config('app.supplier_id', :sid, false)"), {"sid": _NO_USER_ID}
    )


def _tag_filter():
    """The one condition that identifies this script's rows."""
    return Record.custom_fields["mockSeed"].astext == SEED_TAG


def _any_mock_filter():
    """Rows written by ANY mock seeder — they all stamp custom_fields.mockSeed.

    --fresh uses this because the shape only holds if this script's dataset is
    the only mock dataset present: a local DB carrying
    seed_mock_records_extra's 200 round-robin rows reads "100% of plots
    inspected" no matter what this script writes. Deliberately keyed on the
    stamp rather than on a date range or a user, so a hand-entered test record
    is never in range."""
    return Record.custom_fields.has_key("mockSeed")  # noqa: W601 — JSONB ? operator


def _jitter(value, rng: random.Random):
    """GPS-at-visit, a few hundred metres off the plot's registered point.

    This is records.latitude/longitude, a DISTINCT column from
    plots.latitude/longitude that the dashboard map reads — so markers do not
    move. Returning the plot's own None rather than inventing a coordinate
    matters: sync_current_status_from_record copies this straight into
    plots.current_gps_*, and a made-up point there would look like real
    captured GPS."""
    if value is None:
        return None
    return value + Decimal(rng.randint(-20, 20)) / Decimal(1000)


async def _tagged_count(session) -> int:
    return (await session.execute(
        select(func.count()).select_from(Record).where(_tag_filter())
    )).scalar_one()


async def _clean(session, condition=None) -> int:
    """Delete the records matching `condition` (this script's rows by
    default), then put each affected plot's current-status snapshot back
    where it belongs.

    plots.last_inspection_record_id is ON DELETE SET NULL, so the FK survives
    on its own — but last_inspected_at / current_stage / the four score
    columns would keep advertising values from rows that no longer exist.
    resync_current_status_from_latest recomputes from whatever record is left
    and clears the snapshot outright when none is."""
    if condition is None:
        condition = _tag_filter()

    plot_ids = [r[0] for r in (await session.execute(
        select(Record.plot_id).where(condition).distinct()
    )).all()]
    if not plot_ids:
        print("nothing matched — nothing to clean")
        return 0

    deleted = (await session.execute(
        Record.__table__.delete().where(condition)
    )).rowcount
    await session.flush()

    for plot_id in plot_ids:
        await resync_current_status_from_latest(session, plot_id)
    await session.commit()
    print(f"deleted {deleted} mock records · re-synced {len(plot_ids)} plots")
    return deleted


def _supplier_factors(supplier_ids: list, rng: random.Random) -> dict:
    """One achievement multiplier per supplier, spread across the range so the
    comparison table ranks on something real. Sorted first: dict iteration
    order must not decide who looks good."""
    ordered = sorted(supplier_ids, key=str)
    if not ordered:
        return {}
    if len(ordered) == 1:
        return {ordered[0]: Decimal("0.95")}
    step = (_ACHIEVEMENT_MAX - _ACHIEVEMENT_MIN) / Decimal(len(ordered) - 1)
    factors = {}
    for i, sid in enumerate(ordered):
        base = _ACHIEVEMENT_MIN + step * Decimal(i)
        jitter = Decimal(rng.randint(-4, 4)) / Decimal(100)
        factors[sid] = max(Decimal("0.40"), base + jitter)
    return factors


def _stage_for(visit_index: int, visit_count: int, harvested: bool, rng) -> str:
    """A plot ripens across its own visit sequence rather than at random."""
    if harvested and visit_index == visit_count - 1:
        return _STAGE_HARVEST
    progress = (visit_index + 1) / visit_count
    if progress <= 0.4:
        return rng.choice(_STAGES_EARLY)
    return rng.choice(_STAGES_MID)


def _scores(rng: random.Random, problem: bool) -> tuple[int, int, int, int]:
    """Four 1-10 condition scores. A problem plot gets ONE score dragged into
    the 1-3 band — that's what the dashboard's "แปลงที่ควรเฝ้าระวัง" card
    counts, and it has to be a minority or the card means nothing."""
    values = [rng.randint(5, 9) for _ in range(4)]
    if problem:
        values[rng.randrange(4)] = rng.randint(1, 3)
    return tuple(values)  # type: ignore[return-value]


def _visit_dates(rng: random.Random, stale: bool, count: int) -> list[datetime.date]:
    """Newest visit first decides whether the plot is overdue; the rest walk
    backwards at realistic intervals, clamped to the demo window."""
    days_since_last = (
        rng.randint(_STALE_DAYS + 1, 120) if stale else rng.randint(0, _STALE_DAYS - 1)
    )
    oldest_allowed = _TODAY - datetime.timedelta(days=_SHAPE["months_back"] * 30)
    dates = []
    cursor = _TODAY - datetime.timedelta(days=days_since_last)
    for _ in range(count):
        if cursor < oldest_allowed:
            break
        dates.append(cursor)
        cursor = cursor - datetime.timedelta(days=rng.randint(20, 45))
    return sorted(dates)  # oldest first


async def _generate(session, rng: random.Random) -> None:
    admin = (await session.execute(
        select(User).join(User.roles).where(Role.name == "internal:super_admin").limit(1)
    )).scalars().first()
    if admin is None:
        raise SystemExit("No internal:super_admin user found — run app.seed first.")

    plots = list((await session.execute(
        select(Plot).where(Plot.is_active.is_(True)).order_by(Plot.plot_code.asc())
    )).scalars().all())
    if not plots:
        raise SystemExit("No active plots found — run app.db.seed_mock_farmlog first.")
    if len(plots) < 50:
        print(f"WARNING: only {len(plots)} active plots — the demo will look thin. "
              "Consider running app.db.seed_mock_farmlog first.")

    # Only plots that ALREADY have an open planting cycle take part.
    # active_cycle_map is get-or-CREATE: handed a plot whose only cycle is
    # closed, it mints a fresh cycle_no=1 that collides with the closed one on
    # uq_plot_cycles_plot_id_cycle_no (this is not theoretical — it is what the
    # first run against the local DB hit). Opening a planting cycle is the
    # cycle repository's job and never a demo seeder's, so the list is filtered
    # first and the shared helper is left with nothing to create.
    eligible_ids = set((await session.execute(
        select(PlotCycle.plot_id).where(
            PlotCycle.status == CYCLE_STATUS_ACTIVE,
            PlotCycle.plot_id.in_([p.id for p in plots]),
        )
    )).scalars().all())
    eligible = [p for p in plots if p.id in eligible_ids]
    if not eligible:
        raise SystemExit(
            "No active plot has an open planting cycle — nothing to attach "
            "inspections to. Open a cycle first (or run app.db.seed_mock_farmlog)."
        )
    if len(eligible) < len(plots):
        print(f"note: {len(plots) - len(eligible)} active plots have no open cycle "
              "and are left out")

    # Coverage is measured against ALL active plots, because that is the
    # denominator the dashboard's own KPI uses.
    covered_count = min(len(eligible), max(1, round(len(plots) * _SHAPE["plot_coverage"])))
    covered = rng.sample(eligible, covered_count)
    covered.sort(key=lambda p: p.plot_code or "")  # deterministic insert order

    factors = _supplier_factors([p.supplier_id for p in plots], rng)
    cycle_by_plot = await active_cycle_map(session, covered)

    # A cycle with no yield plan makes derive_yield return a NULL target, and
    # "อัตราบรรลุเป้า" has nothing to divide by. Fill ONLY the NULLs.
    plans_filled = 0
    for plot in covered:
        cycle = cycle_by_plot[plot.id]
        if cycle.expected_yield_full is None:
            cycle.expected_yield_full = rng.choice(_FALLBACK_PLAN_KG)
            cycle.expected_yield_unit = "kg"
            plans_filled += 1
    if plans_filled:
        await session.flush()

    rows: list[Record] = []
    skipped_no_target = 0
    for plot in covered:
        cycle = cycle_by_plot[plot.id]
        stale = rng.random() < _SHAPE["stale_share"]
        problem_plot = rng.random() < _SHAPE["problem_plot_share"]
        visit_count = rng.choices([1, 2, 3, 4, 5, 6], weights=[20, 25, 22, 15, 10, 8])[0]
        dates = _visit_dates(rng, stale, visit_count)
        if not dates:
            continue
        harvested = dates[-1] <= _TODAY - datetime.timedelta(days=7) and rng.random() < 0.6
        factor = factors.get(plot.supplier_id, Decimal("1.0"))

        # The cycle's plan converted to kg — asked of derive_yield itself with a
        # 1 kg probe rather than re-implementing the unit conversion here, so
        # this can never drift from what the real create flow would store.
        probe = derive_yield(
            yield_quantity_kg=Decimal("1"),
            client_yield_pct=None,
            expected_yield_full=cycle.expected_yield_full,
            expected_yield_unit=cycle.expected_yield_unit,
        )
        target_kg = probe.yield_target_kg_snapshot
        if target_kg is None:
            skipped_no_target += 1
            continue

        for i, visit_date in enumerate(dates):
            last = i == len(dates) - 1
            stage = _stage_for(i, len(dates), harvested, rng)
            # Yield builds toward harvest instead of being flat across visits.
            ripeness = Decimal("0.45") + Decimal("0.55") * Decimal(i + 1) / Decimal(len(dates))
            jitter = Decimal(rng.randint(92, 108)) / Decimal(100)
            quantity = (target_kg * factor * ripeness * jitter).quantize(Decimal("0.01"))
            try:
                yd = derive_yield(
                    yield_quantity_kg=quantity,
                    client_yield_pct=None,
                    expected_yield_full=cycle.expected_yield_full,
                    expected_yield_unit=cycle.expected_yield_unit,
                )
            except YieldValidationError:
                continue

            cleaned = None
            if last and stage == _STAGE_HARVEST and rng.random() < _SHAPE["cleaned_share_of_harvest"]:
                # Always a loss against ผลผลิตที่เก็บได้, never a gain.
                cleaned = (quantity * Decimal(rng.randint(80, 95)) / Decimal(100)).quantize(
                    Decimal("0.01")
                )

            prep, weather_s, care, resistance = _scores(rng, problem_plot)
            # created_at drives plots.last_inspected_at (see
            # sync_current_status_from_record) and the "latest record" ordering,
            # so it is written to match the visit instead of defaulting to now()
            # — otherwise every demo plot reads "inspected today" and the
            # overdue card is always zero.
            visit_dt = datetime.datetime.combine(
                visit_date, datetime.time(9, 30), tzinfo=datetime.timezone.utc
            )
            rows.append(Record(
                plot_id=plot.id,
                supplier_id=plot.supplier_id,  # always the plot's, never another
                plot_cycle_id=cycle_by_plot[plot.id].id,
                recorded_by_id=admin.id,
                record_date=visit_date,
                created_at=visit_dt,
                updated_at=visit_dt,
                captured_at=visit_dt,
                submitted_by_code=f"FIELD{(rng.randint(1, 20)):03d}",
                inspector_type=rng.choice(_INSPECTOR_TYPES),
                crop=plot.current_crop,
                variety=plot.current_variety,
                growth_stage=stage,
                weather_condition=rng.choice(_WEATHER),
                yield_pct=yd.yield_pct,
                yield_quantity_kg=yd.yield_quantity_kg,
                yield_target_kg_snapshot=yd.yield_target_kg_snapshot,
                final_yield_after_clean=cleaned,
                field_prep_score=prep,
                weather_score=weather_s,
                care_score=care,
                variety_resistance_score=resistance,
                notes=(rng.choice(_NOTE_TEXTS)
                       if rng.random() < _SHAPE["notes_share"] else None),
                recommendation=(rng.choice(_RECOMMENDATION_TEXTS)
                                if rng.random() < _SHAPE["recommendation_share"] else None),
                latitude=_jitter(plot.latitude, rng),
                longitude=_jitter(plot.longitude, rng),
                photo_urls=[],
                custom_fields={"mockSeed": SEED_TAG},
                is_active=True,
            ))

    for row in rows:
        session.add(row)
    await session.commit()
    print(f"inserted {len(rows)} records across {len(covered)} plots "
          f"(of {len(plots)} active)")
    if plans_filled:
        print(f"filled {plans_filled} cycle yield plans that were NULL")
    if skipped_no_target:
        print(f"skipped {skipped_no_target} plots whose cycle plan has no kg-convertible unit")

    touched = {r.plot_id for r in rows}
    for plot_id in touched:
        await resync_current_status_from_latest(session, plot_id)
    await session.commit()
    print(f"re-synced current status for {len(touched)} plots")


async def _report(session) -> None:
    """Print the same KPIs the executive dashboard will compute, so round 42
    has something to check itself against."""
    async def one(sql: str):
        return (await session.execute(text(sql), {"today": _TODAY})).first()

    # Where the rows came from. Printed first because every KPI below is read
    # off the WHOLE table: one leftover round-robin mock dataset is enough to
    # peg "plots inspected" at 100% and make the shape below meaningless.
    print()
    print("=== records in this DB, by origin ===")
    for row in (await session.execute(text("""
        SELECT coalesce(custom_fields->>'mockSeed', '(no stamp — hand-entered)') AS src,
               count(*) AS n
        FROM records WHERE is_active GROUP BY 1 ORDER BY 2 DESC"""))).all():
        print(f"   {row.src:34} {row.n}")

    print()
    print("=== what the dashboard will read ===")

    r = await one("""SELECT
        (SELECT count(*) FROM plots WHERE is_active) AS plots,
        (SELECT count(DISTINCT plot_id) FROM records WHERE is_active) AS inspected,
        (SELECT count(*) FROM records WHERE is_active) AS records""")
    pct = (r.inspected / r.plots * 100) if r.plots else 0
    print(f"   แปลงที่เคยถูกตรวจ        {r.inspected} / {r.plots} ({pct:.0f}%)")
    print(f"   บันทึกทั้งหมด            {r.records}")

    r = await one("""SELECT
        count(*) FILTER (WHERE record_date > CAST(:today AS date) - 7)  AS d7,
        count(*) FILTER (WHERE record_date > CAST(:today AS date) - 30) AS d30,
        min(record_date) AS first_day, max(record_date) AS last_day
        FROM records WHERE is_active""")
    print(f"   บันทึก 7 วัน / 30 วัน     {r.d7} / {r.d30}")
    print(f"   ช่วงวันที่               {r.first_day} ถึง {r.last_day}")

    r = await one(f"""SELECT count(*) AS stale FROM (
        SELECT plot_id, max(record_date) AS last_visit
        FROM records WHERE is_active GROUP BY plot_id) t
        WHERE t.last_visit < CAST(:today AS date) - {_STALE_DAYS}""")
    print(f"   แปลงค้างตรวจเกิน {_STALE_DAYS} วัน   {r.stale}")

    r = await one("""SELECT
        round(avg((field_prep_score + weather_score + care_score
                   + variety_resistance_score) / 4.0), 2) AS avg_score,
        count(*) FILTER (WHERE least(field_prep_score, weather_score,
                                     care_score, variety_resistance_score) <= 3) AS low
        FROM records WHERE is_active AND field_prep_score IS NOT NULL""")
    print(f"   คะแนนเฉลี่ย              {r.avg_score}")
    print(f"   บันทึกที่มีคะแนน <= 3     {r.low}")

    r = await one("""SELECT
        round(sum(yield_target_kg_snapshot), 0) AS target,
        round(sum(yield_quantity_kg), 0)        AS harvest,
        count(*) FILTER (WHERE final_yield_after_clean IS NOT NULL) AS cleaned,
        count(*) AS total
        FROM records WHERE is_active""")
    ach = (r.harvest / r.target * 100) if r.target else 0
    cleaned_pct = (r.cleaned / r.total * 100) if r.total else 0
    print(f"   เป้า / เก็บได้ (kg)       {r.target} / {r.harvest}  = {ach:.0f}%")
    print(f"   กรอกผลผลิตหลังคัด        {r.cleaned} / {r.total} ({cleaned_pct:.0f}%)")

    r = await one("""SELECT
        round(100.0 * count(*) FILTER (WHERE notes IS NOT NULL AND notes <> '')
              / nullif(count(*), 0), 0) AS notes_pct,
        round(100.0 * count(*) FILTER (WHERE recommendation IS NOT NULL
              AND recommendation <> '') / nullif(count(*), 0), 0) AS rec_pct
        FROM records WHERE is_active""")
    print(f"   notes / recommendation   {r.notes_pct}% / {r.rec_pct}%")

    rows = (await session.execute(text("""
        SELECT s.code,
               round(100.0 * sum(r.yield_quantity_kg)
                     / nullif(sum(r.yield_target_kg_snapshot), 0), 0) AS ach
        FROM records r JOIN suppliers s ON s.id = r.supplier_id
        WHERE r.is_active GROUP BY s.code ORDER BY ach ASC NULLS LAST"""))).all()
    if rows:
        spread = [f"{x.code}={x.ach}%" for x in rows[:3]]
        tail = [f"{x.code}={x.ach}%" for x in rows[-2:]]
        print(f"   บรรลุเป้าต่ำสุด          {' · '.join(spread)}")
        print(f"   บรรลุเป้าสูงสุด          {' · '.join(tail)}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true",
                        help="delete this script's previous rows, then regenerate")
    parser.add_argument("--clean", action="store_true",
                        help="delete this script's rows and stop")
    parser.add_argument("--fresh", action="store_true",
                        help="delete EVERY mock seeder's rows (any custom_fields."
                             "mockSeed stamp) before generating — needed when an "
                             "older mock dataset is flattening the KPIs. Records "
                             "without a mockSeed stamp are never touched.")
    args = parser.parse_args()

    await init_db()
    try:
        await _require_localhost()
        async with get_db_session() as session:
            await _set_scope_all(session)

            existing = await _tagged_count(session)
            if args.clean:
                await _clean(session)
                await _report(session)
                return
            if args.fresh:
                await _clean(session, _any_mock_filter())
                existing = 0
            if existing and not args.reset:
                raise SystemExit(
                    f"{existing} rows from a previous run are already here. "
                    "Stacking a second dataset would destroy the shape this "
                    "script exists to produce — re-run with --reset."
                )
            if existing:
                await _clean(session)

            rng = random.Random(_RANDOM_SEED)
            await _generate(session, rng)
            await _report(session)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

"""DEV-ONLY: normalize every plot's yield plan to a single unit (kg) with an
amount that is CONSISTENT with its plant count. `python -m app.db.normalize_plot_yield_kg`

Why: the mock seed handed out three different units (กก./ตัน/ลูก) round-robin
and an expected_yield_full (1000–2400) picked independently of plant_count, so
"จำนวนต้น 500 → 2,400 ลูก" pairs made no physical sense and the map/report
mixed units in one total.

How: for every plot,
  - expected_yield_unit := "kg"
  - expected_yield_full := round(plant_count * kg-per-plant for its crop),
    so a 500-plant chili plot at 2 kg/plant reads 1,000 kg — the number now
    tracks the plant count. Falls back to a default factor for an unknown/
    blank crop. Rounded to whole kg.
Then re-syncs each plot's current_* snapshot (expected_yield_* is admin-owned
and never synced, but current_yield_pct feeds the computed "current expected
yield" the UI shows, so nothing else needs touching).

Idempotent: re-running yields the same numbers. localhost guard.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.models.plot import Plot
from app.db.session import close_db, get_db_session, init_db

_ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
_NO_USER_ID = "00000000-0000-0000-0000-000000000000"

# Realistic-ish harvest weight per plant (kg) so expected_yield_full tracks
# plant_count. Values are illustrative demo figures, not agronomic truth.
_KG_PER_PLANT: dict[str, float] = {
    "พริก": 2.0,      # chili
    "เมล่อน": 3.0,    # melon
    "ฟักทอง": 4.0,    # pumpkin
    "แตงโม": 5.0,     # watermelon
    "แตงกวา": 3.5,    # cucumber
}
_DEFAULT_KG_PER_PLANT = 2.5


async def _require_localhost() -> None:
    settings = get_settings()
    print("=== DB target ===")
    print(f"host={settings.DB_HOST} port={settings.DB_PORT} db={settings.DB_NAME}")
    if settings.DB_HOST not in _ALLOWED_HOSTS:
        raise SystemExit(
            f"Refusing to run: DB_HOST={settings.DB_HOST!r} is not localhost/127.0.0.1."
        )


async def _set_scope_all(session) -> None:
    await session.execute(text("SELECT set_config('app.scope', 'all', false)"))
    await session.execute(
        text("SELECT set_config('app.user_id', :uid, false)"), {"uid": _NO_USER_ID}
    )
    await session.execute(
        text("SELECT set_config('app.supplier_id', :sid, false)"), {"sid": _NO_USER_ID}
    )


async def main() -> None:
    await init_db()
    try:
        await _require_localhost()
        async with get_db_session() as session:
            await _set_scope_all(session)

            plots = list((await session.execute(select(Plot))).scalars().all())
            print(f"plots: {len(plots)}")

            updated = 0
            for plot in plots:
                factor = _KG_PER_PLANT.get((plot.current_crop or "").strip(), _DEFAULT_KG_PER_PLANT)
                # Fall back to a sensible plant count if one somehow isn't set,
                # so no plot ends up with a null/zero yield plan.
                plant = plot.plant_count if plot.plant_count and plot.plant_count > 0 else 500
                plot.plant_count = plant
                plot.expected_yield_full = Decimal(str(round(plant * factor)))
                plot.expected_yield_unit = "kg"
                updated += 1

            await session.commit()
            print(f"normalized to kg + consistent amounts: {updated} plots")

            # Show a few samples so the numbers are eyeballable.
            for plot in plots[:5]:
                print(
                    f"  {plot.plot_code}: {plot.current_crop} "
                    f"{plot.plant_count} plants -> {plot.expected_yield_full} kg"
                )
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

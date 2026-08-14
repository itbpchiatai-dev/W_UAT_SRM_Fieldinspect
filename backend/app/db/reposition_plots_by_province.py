"""DEV-ONLY: give every plot a distinct lat/lng that actually falls inside
its own province.  `python -m app.db.reposition_plots_by_province`

Why: the mock seed placed every plot in a province on ONE identical reference
point (see seed_reset_farmlog_full._LOCATIONS), so on the dashboard map all
10 plots of a province stack into a single dot. This spreads them out to
realistic, distinct coordinates that lie within the real provincial boundary
(e.g. a เชียงใหม่ plot lands somewhere inside Chiang Mai, not on top of the
next plot).

How: reads the same public GeoJSON the offline map geometry was baked from
(apisit/thailand.json), and for each plot samples a deterministic random point
inside its province polygon by rejection sampling within the polygon's
bounding box (point-in-polygon ray casting). The RNG is seeded from the plot
id, so re-running produces the SAME coordinates — idempotent, no drift.

Also moves each plot's inspection records' captured GPS to near the plot's new
point and re-syncs the denormalized current_gps_lat/lng, so PlotDetail's
"GPS ล่าสุด" and the record history stay coherent with the new location.

Only touches plots whose province maps to a known polygon; anything else is
left untouched and reported. Refuses to run unless DB_HOST is localhost.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from decimal import Decimal

from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.models.plot import Plot
from app.db.models.record import Record
from app.db.session import close_db, get_db_session, init_db
from app.repositories.plot_repository import sync_current_status_from_record

_ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
_NO_USER_ID = "00000000-0000-0000-0000-000000000000"

# GeoJSON source (same file gen_thailand_geo.py baked the map outlines from).
# Overridable via env for other machines.
_GEOJSON = os.environ.get(
    "THAILAND_GEOJSON",
    r"C:\Users\tunyawut.wo\AppData\Local\Temp\th.json",
)

# Thai (DB) → English (GeoJSON `properties.name`). Only the provinces the
# current mock data actually uses need to be here; unknown provinces are
# skipped + reported rather than mis-placed.
_TH_TO_EN = {
    "เชียงใหม่": "Chiang Mai",
    "เชียงราย": "Chiang Rai",
    "นครราชสีมา": "Nakhon Ratchasima",
    "ขอนแก่น": "Khon Kaen",
    "กาญจนบุรี": "Kanchanaburi",
    "ราชบุรี": "Ratchaburi",
    "จันทบุรี": "Chanthaburi",
    "เพชรบูรณ์": "Phetchabun",
    "สุพรรณบุรี": "Suphan Buri",
    "ประจวบคีรีขันธ์": "Prachuap Khiri Khan",
}


async def _require_localhost() -> None:
    settings = get_settings()
    print("=== DB target ===")
    print(f"host={settings.DB_HOST} port={settings.DB_PORT} db={settings.DB_NAME}")
    if settings.DB_HOST not in _ALLOWED_HOSTS:
        raise SystemExit(
            f"Refusing to run: DB_HOST={settings.DB_HOST!r} is not localhost/127.0.0.1."
        )


async def _set_scope_all(session) -> None:
    # plots/records are FORCE ROW LEVEL SECURITY — without a scope GUC every
    # query sees 0 rows. Session-level so it survives commit(). user_id/
    # supplier_id must be a castable uuid, so use the all-zero sentinel.
    await session.execute(text("SELECT set_config('app.scope', 'all', false)"))
    await session.execute(
        text("SELECT set_config('app.user_id', :uid, false)"), {"uid": _NO_USER_ID}
    )
    await session.execute(
        text("SELECT set_config('app.supplier_id', :sid, false)"), {"sid": _NO_USER_ID}
    )


def _outer_rings(feature: dict) -> list[list[list[float]]]:
    """Return the outer ring(s) of a Polygon/MultiPolygon feature."""
    g = feature["geometry"]
    if g["type"] == "Polygon":
        return [g["coordinates"][0]]
    if g["type"] == "MultiPolygon":
        return [poly[0] for poly in g["coordinates"]]
    return []


def _bbox(rings: list[list[list[float]]]) -> tuple[float, float, float, float]:
    xs = [pt[0] for ring in rings for pt in ring]
    ys = [pt[1] for ring in rings for pt in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _point_in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon for a single ring."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _point_in_any_ring(x: float, y: float, rings: list[list[list[float]]]) -> bool:
    return any(_point_in_ring(x, y, ring) for ring in rings)


def _sample_point(rings, bbox, rng: random.Random) -> tuple[float, float]:
    """Rejection-sample a point inside the province polygon."""
    minx, miny, maxx, maxy = bbox
    for _ in range(2000):
        x = rng.uniform(minx, maxx)
        y = rng.uniform(miny, maxy)
        if _point_in_any_ring(x, y, rings):
            return x, y
    # Fallback (extremely unlikely): bbox center.
    return (minx + maxx) / 2, (miny + maxy) / 2


def _load_province_geo() -> dict[str, dict]:
    """English name → {rings, bbox} for every feature in the GeoJSON."""
    if not os.path.exists(_GEOJSON):
        raise SystemExit(
            f"GeoJSON not found at {_GEOJSON!r}. Set THAILAND_GEOJSON to its path."
        )
    data = json.load(open(_GEOJSON, encoding="utf-8"))
    out: dict[str, dict] = {}
    for f in data["features"]:
        name = f["properties"].get("name", "")
        rings = _outer_rings(f)
        if rings:
            out[name] = {"rings": rings, "bbox": _bbox(rings)}
    return out


async def main() -> None:
    await init_db()
    try:
        await _require_localhost()
        geo = _load_province_geo()

        async with get_db_session() as session:
            await _set_scope_all(session)

            plots = list((await session.execute(
                select(Plot).order_by(Plot.plot_code.asc())
            )).scalars().all())
            print(f"plots: {len(plots)}")

            moved = 0
            skipped_provinces: dict[str, int] = {}
            touched_plot_ids: list = []
            for plot in plots:
                province_th = (plot.province or "").strip()
                en = _TH_TO_EN.get(province_th)
                shape = geo.get(en) if en else None
                if shape is None:
                    skipped_provinces[province_th or "(blank)"] = (
                        skipped_provinces.get(province_th or "(blank)", 0) + 1
                    )
                    continue

                # Deterministic per-plot RNG → reproducible placement.
                rng = random.Random(f"plot-loc:{plot.id}")
                lng, lat = _sample_point(shape["rings"], shape["bbox"], rng)
                plot.latitude = Decimal(f"{lat:.7f}")
                plot.longitude = Decimal(f"{lng:.7f}")
                touched_plot_ids.append(plot.id)
                moved += 1

            await session.flush()

            # Keep records' captured GPS + the denormalized current_gps_* near
            # the new plot point so history/detail stay coherent.
            resynced = 0
            for plot_id in touched_plot_ids:
                recs = list((await session.execute(
                    select(Record).where(Record.plot_id == plot_id)
                )).scalars().all())
                plot = next(p for p in plots if p.id == plot_id)
                for rec in recs:
                    rng = random.Random(f"rec-loc:{rec.id}")
                    # ±~0.03° jitter around the plot's new point.
                    dlat = Decimal(f"{rng.uniform(-0.03, 0.03):.7f}")
                    dlng = Decimal(f"{rng.uniform(-0.03, 0.03):.7f}")
                    rec.latitude = plot.latitude + dlat
                    rec.longitude = plot.longitude + dlng
                await session.flush()

                latest = (await session.execute(
                    select(Record)
                    .where(Record.plot_id == plot_id, Record.is_active.is_(True))
                    .order_by(Record.created_at.desc())
                    .limit(1)
                )).scalars().first()
                if latest is not None:
                    await sync_current_status_from_record(session, latest)
                    resynced += 1

            await session.commit()
            print(f"repositioned: {moved} plots")
            print(f"re-synced current GPS for {resynced} plots")
            if skipped_provinces:
                print("skipped (no polygon mapping):")
                for prov, n in sorted(skipped_provinces.items()):
                    print(f"  {prov}: {n}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

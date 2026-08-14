"""Dev-safe backfill: plots.qr_key for plots created before round 20's QR
hardening (migration 0026) — `python -m app.db.backfill_plot_qr_key`.

Generates a fresh random opaque key (app.services.plot_qr_key.generate_qr_key)
for every plot where qr_key IS NULL. Idempotent: plots that already have a
key are skipped, so re-running finds nothing left to do.

Never logs/prints the generated key values themselves — only plot_code and
a count. The key isn't a hash, but the same "don't put a value someone
could use to forge a plot's QR sign into a log" principle applies.

Dry-run by default — pass --apply to actually commit; otherwise this only
prints what it would change (plot codes + a count) and makes no writes.

Refuses to run unless DB_HOST resolves to localhost/127.0.0.1, matching
app.db.seed_mock_farmlog / backfill_plot_current_status's guard against
ever pointing this at a non-local database.
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models.plot import Plot
from app.db.session import close_db, get_db_session, init_db
from app.services.plot_qr_key import generate_qr_key

_ALLOWED_HOSTS = {"localhost", "127.0.0.1"}


async def run(apply: bool) -> None:
    settings = get_settings()
    print("=== DB target ===")
    print(f"host={settings.DB_HOST} port={settings.DB_PORT} db={settings.DB_NAME}")
    if settings.DB_HOST not in _ALLOWED_HOSTS:
        raise SystemExit(
            f"Refusing to run: DB_HOST={settings.DB_HOST!r} is not localhost/127.0.0.1."
        )

    async with get_db_session() as session:
        result = await session.execute(select(Plot).where(Plot.qr_key.is_(None)))
        plots = list(result.scalars().all())
        print(f"before: plots_missing_qr_key={len(plots)}")

        if not plots:
            print("nothing to backfill")
            return

        for plot in plots:
            print(f"  plot {plot.plot_code} ({plot.id}) <- new qr_key generated")
            if apply:
                plot.qr_key = generate_qr_key()

        if apply:
            await session.commit()
            after = await session.execute(select(Plot).where(Plot.qr_key.is_(None)))
            print(f"after: plots_missing_qr_key={len(list(after.scalars().all()))}")
            print(f"backfilled {len(plots)} plot(s)")
        else:
            print(f"[dry-run] would backfill {len(plots)} plot(s) — re-run with --apply to commit")


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

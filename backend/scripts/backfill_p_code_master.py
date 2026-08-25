"""Seed master_data(type='p_code') from the P.Codes already typed into
plot_cycles (round 8-26A). Dry-run by default; pass --apply to write.

WHY THIS IS A SCRIPT AND NOT AN ALEMBIC MIGRATION
-------------------------------------------------
plot_cycles has FORCE ROW LEVEL SECURITY and its only policy is scoped
`TO srm_app` (migration 0035). Alembic connects as the OWNER role
(settings.database_url — e.g. uat_srm_app), which is NOT srm_app and has
neither BYPASSRLS nor superuser, and FORCE RLS applies to the owner too. A
migration reading plot_cycles therefore gets **zero rows with no error at
all** — verified against live UAT before writing this:

    current_user: uat_srm_app
    bypassrls,super: [(False, False)]
    cycles visible: 0          <- the table really has 10

A back-fill migration would have "succeeded", inserted nothing, and left
every existing cycle unable to resolve its P.Code. So the back-fill runs
through the APP's own runtime engine instead (settings.database_runtime_url
= srm_app) with `app.scope = 'all'`, exactly how the API reads plot_cycles.
The alternative — temporarily granting the migration role a permissive RLS
policy — was rejected: loosening RLS to move data is not worth it when a
plain script does the same job with no security surface at all.

WHAT IT WRITES
--------------
One master_data row per distinct P.Code found on a cycle, filed under that
cycle's VARIETY (crop -> variety -> p_code; see services/p_code_master.py).
Nothing else: no crop, no variety, no plot_cycles column is ever touched.
Idempotent — a P.Code already present in master_data is reported and skipped,
so re-running is safe.

WHAT IT REFUSES TO GUESS
------------------------
Every row below is REPORTED and SKIPPED rather than resolved automatically,
because each needs a human decision, not a default:

  - a P.Code on a cycle with no variety      -> nothing to file it under
  - one P.Code used under two varieties      -> master_data's own
                                                UNIQUE(type, value) allows
                                                only one parent
  - one variety carrying two P.Codes         -> violates the confirmed
                                                "1 variety = 1 P.Code" rule;
                                                a human picks the survivor
  - a variety missing from master_data       -> the parent must exist first

Usage (inside the backend container):

    python scripts/backfill_p_code_master.py            # dry run
    python scripts/backfill_p_code_master.py --apply    # write
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# `python scripts/<name>.py` puts scripts/ on sys.path, not the backend root
# — add the parent so `import app` resolves the same way it does for uvicorn.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db.session import close_db, get_db_session, init_db  # noqa: E402
from app.repositories import master_data_repository as repo  # noqa: E402
from app.schemas.master_data import MasterDataCreate  # noqa: E402
from app.services.p_code_master import P_CODE_TYPE, VARIETY_TYPE  # noqa: E402


async def _collect(db) -> tuple[list[tuple[str, str]], list[str]]:
    """Returns (rows_to_create, problems). Read-only."""
    # scope='all' is what makes plot_cycles readable at all under RLS — the
    # same GUC the API sets per request (app/api/deps/scope.py).
    await db.execute(text("SET app.scope = 'all'"))
    result = await db.execute(text(
        "SELECT DISTINCT btrim(p_code) AS p_code, btrim(coalesce(variety, '')) AS variety "
        "FROM plot_cycles WHERE p_code IS NOT NULL AND btrim(p_code) <> ''"
    ))
    pairs = [(r.p_code, r.variety) for r in result]

    problems: list[str] = []
    if not pairs:
        problems.append(
            "อ่าน plot_cycles ได้ 0 แถวที่มี P.Code — ถ้าคาดว่าควรมีข้อมูล "
            "ให้ตรวจว่าเชื่อมต่อด้วย role srm_app (RLS) จริงหรือไม่"
        )
        return [], problems

    by_p_code: dict[str, set[str]] = {}
    by_variety: dict[str, set[str]] = {}
    for p_code, variety in pairs:
        by_p_code.setdefault(p_code, set()).add(variety)
        if variety:
            by_variety.setdefault(variety, set()).add(p_code)

    existing_p_codes = {
        m.value: m for m in await repo.list_by_type_values(db, P_CODE_TYPE, set(by_p_code))
    }
    existing_varieties = {
        m.value for m in await repo.list_by_type_values(db, VARIETY_TYPE, set(by_variety))
    }

    to_create: list[tuple[str, str]] = []
    for p_code in sorted(by_p_code):
        varieties = by_p_code[p_code]
        if len(varieties) > 1:
            problems.append(f"SKIP {p_code}: ใช้อยู่ภายใต้หลายพันธุ์ {sorted(varieties)}")
            continue
        variety = next(iter(varieties))
        if not variety:
            problems.append(f"SKIP {p_code}: รอบปลูกที่ใช้ค่านี้ไม่ได้ระบุพันธุ์")
            continue
        if len(by_variety.get(variety, set())) > 1:
            problems.append(
                f"SKIP {p_code}: พันธุ์ '{variety}' มีหลาย P.Code {sorted(by_variety[variety])}"
            )
            continue
        if variety not in existing_varieties:
            problems.append(f"SKIP {p_code}: ไม่พบพันธุ์ '{variety}' ใน master_data")
            continue
        already = existing_p_codes.get(p_code)
        if already is not None:
            note = "" if already.parent == variety else f" (parent ต่างกัน: '{already.parent}')"
            problems.append(f"SKIP {p_code}: มีใน master_data อยู่แล้ว{note}")
            continue
        to_create.append((p_code, variety))
    return to_create, problems


async def main(apply: bool) -> int:
    await init_db()
    try:
        async with get_db_session() as db:
            to_create, problems = await _collect(db)

            for line in problems:
                print(line)
            for p_code, variety in to_create:
                print(f"{'CREATE' if apply else 'WOULD CREATE'} {p_code} -> พันธุ์ '{variety}'")

            if apply and to_create:
                for p_code, variety in to_create:
                    await repo.create(
                        db, MasterDataCreate(type=P_CODE_TYPE, value=p_code, parent=variety),
                    )
                await db.commit()

            print(
                f"\n{'wrote' if apply else 'would write'} {len(to_create)} row(s); "
                f"skipped {len(problems)}"
            )
            if not apply and to_create:
                print("dry run — re-run with --apply to write")
            # A skipped row always needs a human decision, so it is a
            # non-zero exit: a CI/ops caller must not read "0 problems" out
            # of a run that quietly left P.Codes unmigrated.
            return 1 if problems else 0
    finally:
        await close_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main("--apply" in sys.argv)))

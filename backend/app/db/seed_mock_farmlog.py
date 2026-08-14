"""One-off DEV-ONLY reset + mock-data seed for FarmLog testing (round 10.0
of the FarmLog public-inspect-flow work). `python -m app.db.seed_mock_farmlog`

DESTRUCTIVE — unlike app.seed / app.db.seed (both idempotent upserts safe
to re-run in onboarding), this unconditionally DELETES all existing
records / plot_assignments / plots / suppliers before creating fresh mock
data. Never wire this into onboarding or CI.

Refuses to run unless DB_HOST resolves to localhost/127.0.0.1, as a guard
against ever pointing this at anything but a local dev database.

Scope (confirmed with the user before writing this script):
  - Deletes ALL existing suppliers/plots/plot_assignments/records, then
    creates 10 suppliers / 100 plots (10 per supplier) / 100 records.
  - Does NOT touch users/roles/permissions/sessions/auth/app_settings/
    alembic_version. Deleting a supplier that a user's `supplier_id`
    still points at triggers Postgres's existing `ON DELETE SET NULL` on
    `users.supplier_id` — a pre-existing FK behavior, not something this
    script executes directly against the users table.
  - Does NOT delete or modify existing master_data rows — those are real
    dropdown defaults other parts of the app depend on, not disposable
    demo data. Only adds new rows, reusing the existing `type` categories
    (never inventing a new category name).
  - Does NOT touch external-field-helper@system.local.
  - recorded_by_id on every mock record is the existing internal:super_admin
    user (looked up by role, not hardcoded by email) — keeps
    external-field-helper@system.local's meaning reserved for records that
    actually came through the public unauthenticated flow.
"""
from __future__ import annotations

import asyncio
import datetime
from decimal import Decimal

from sqlalchemy import delete, func, select

from app.api.deps.scope import get_public_plot_rls_context
from app.core.config import get_settings
from app.db.models.master_data import MasterData
from app.db.models.plot import Plot
from app.db.models.plot_assignment import PlotAssignment
from app.db.models.record import Record
from app.db.models.role import Role
from app.db.models.supplier import Supplier
from app.db.models.user import User
from app.db.seed_helpers import active_cycle_map
from app.db.session import close_db, get_db_session, init_db
from app.services.plot_qr_key import generate_qr_key

_ALLOWED_HOSTS = {"localhost", "127.0.0.1"}

_SUPPLIER_COUNT = 10
_PLOTS_PER_SUPPLIER = 10
_SUBMITTED_BY_CODE_COUNT = 20

# New master_data rows — reuses existing `type` categories only (see
# app/db/seed.py's _MASTER_DATA / _MASTER_DATA_SUPPLEMENT for what already
# exists); values here are checked distinct from existing ones before insert.
# level / severity / irrigation / fertilizer removed round 8-14F — no
# production consumer; admin UI has hidden them since round 8-14E/8-14E.1.
_MASTER_DATA_NEW: dict[str, list[str]] = {
    "crop": ["มะละกอ", "สับปะรด", "กล้วย", "มันสำปะหลัง"],
    "growth_stage": ["ระยะกล้า", "แตกกอ", "ยืดปล้อง", "สุกแก่"],
    "weather": ["อากาศแปรปรวน", "น้ำท่วมขัง", "ภัยแล้ง", "ลูกเห็บตก"],
}
_MASTER_DATA_NEW_VARIETY: list[tuple[str, str]] = [
    ("มะละกอฮอลแลนด์", "มะละกอ"),
    ("สับปะรดภูแล", "สับปะรด"),
    ("กล้วยหอมทอง", "กล้วย"),
    ("มันสำปะหลังระยอง 90", "มันสำปะหลัง"),
]

_MOCK_CROPS = ["พริก", "เมล่อน", "ฟักทอง", "แตงโม", "แตงกวา"]
_MOCK_VARIETIES = ["พริกขี้หนู", "พริกจินดา", "เมล่อนญี่ปุ่น", "แตงโมกินรี", "แตงกวาญี่ปุ่น"]
_MOCK_GROWTH_STAGES = ["ระยะงอก", "เจริญเติบโต", "ออกดอก", "ติดผล", "เก็บเกี่ยว"]
_MOCK_WEATHER = ["แจ่มใส", "มีเมฆ", "ฝนตก", "ร้อนจัด", "ลมแรง"]


async def _confirm_target_and_counts() -> None:
    settings = get_settings()
    print("=== DB target ===")
    print(f"host={settings.DB_HOST} port={settings.DB_PORT} db={settings.DB_NAME}")
    if settings.DB_HOST not in _ALLOWED_HOSTS:
        raise SystemExit(
            f"Refusing to run: DB_HOST={settings.DB_HOST!r} is not localhost/127.0.0.1."
        )

    async with get_db_session() as session:
        # plots/records are FORCE ROW LEVEL SECURITY; without a scope GUC set,
        # RLS's CASE defaults to false and every query silently sees 0 rows.
        await get_public_plot_rls_context(db=session)
        for label, model in (
            ("suppliers", Supplier), ("plots", Plot),
            ("plot_assignments", PlotAssignment), ("records", Record),
            ("master_data", MasterData),
        ):
            count = (await session.execute(select(func.count()).select_from(model))).scalar_one()
            print(f"before: {label}={count}")


async def _delete_farmlog_domain_data() -> None:
    async with get_db_session() as session:
        await get_public_plot_rls_context(db=session)
        await session.execute(delete(Record))
        await session.execute(delete(PlotAssignment))
        await session.execute(delete(Plot))
        await session.execute(delete(Supplier))
        await session.commit()
    print("deleted: records, plot_assignments, plots, suppliers")


async def _add_master_data() -> None:
    async with get_db_session() as session:
        existing_pairs = set(
            (await session.execute(select(MasterData.type, MasterData.value))).all()
        )
        max_order: dict[str, int] = {}
        for type_, values in _MASTER_DATA_NEW.items():
            current_max = (await session.execute(
                select(func.max(MasterData.order_index)).where(MasterData.type == type_)
            )).scalar_one()
            max_order[type_] = (current_max or 0) + 1

        added = 0
        for type_, values in _MASTER_DATA_NEW.items():
            for value in values:
                if (type_, value) in existing_pairs:
                    continue
                session.add(MasterData(type=type_, value=value, order_index=max_order[type_]))
                max_order[type_] += 1
                added += 1
        for value, parent in _MASTER_DATA_NEW_VARIETY:
            if ("variety", value) in existing_pairs:
                continue
            session.add(MasterData(
                type="variety", value=value, parent=parent,
                order_index=max_order.get("variety", 0),
            ))
            max_order["variety"] = max_order.get("variety", 0) + 1
            added += 1
        await session.commit()
    print(f"master_data: added {added} new rows (existing rows untouched)")


async def _seed_mock_data() -> tuple[list[str], list[str]]:
    async with get_db_session() as session:
        await get_public_plot_rls_context(db=session)
        admin = (await session.execute(
            select(User)
            .join(User.roles)
            .where(Role.name == "internal:super_admin")
            .limit(1)
        )).scalars().first()
        if admin is None:
            raise SystemExit("No internal:super_admin user found — run app.seed first.")

        suppliers: list[Supplier] = []
        for i in range(1, _SUPPLIER_COUNT + 1):
            code = f"SUP{i:03d}"
            supplier = Supplier(
                code=code,
                name=f"ซัพพลายเออร์ทดสอบ {i:02d}",
                is_active=True,
            )
            session.add(supplier)
            suppliers.append(supplier)
        await session.flush()

        plots: list[Plot] = []
        for supplier in suppliers:
            for n in range(1, _PLOTS_PER_SUPPLIER + 1):
                plot_code = f"{supplier.code}-P{n:03d}"
                plot = Plot(
                    supplier_id=supplier.id,
                    plot_code=plot_code,
                    name=f"แปลงทดสอบ {plot_code}",
                    is_active=True,
                    # This script builds Plot() directly rather than going
                    # through plot_repository.create_plot() (which would
                    # generate this automatically) — round 20.1 fix: every
                    # mock plot needs a qr_key too, same as every real plot
                    # created via the API does.
                    qr_key=generate_qr_key(),
                )
                session.add(plot)
                plots.append(plot)
        await session.flush()

        # active planting cycle per plot (round 7.1.1 — record binds to it)
        cycle_by_plot = await active_cycle_map(session, plots)

        base_date = datetime.date(2026, 6, 1)
        for index, plot in enumerate(plots):
            record = Record(
                plot_id=plot.id,
                supplier_id=plot.supplier_id,
                plot_cycle_id=cycle_by_plot[plot.id].id,
                recorded_by_id=admin.id,
                record_date=base_date + datetime.timedelta(days=index % 60),
                submitted_by_code=f"FIELD{(index % _SUBMITTED_BY_CODE_COUNT) + 1:03d}",
                crop=_MOCK_CROPS[index % len(_MOCK_CROPS)],
                variety=_MOCK_VARIETIES[index % len(_MOCK_VARIETIES)],
                growth_stage=_MOCK_GROWTH_STAGES[index % len(_MOCK_GROWTH_STAGES)],
                yield_pct=Decimal("90") + Decimal(index % 20),
                weather_condition=_MOCK_WEATHER[index % len(_MOCK_WEATHER)],
                field_prep_score=5 + (index % 6),
                weather_score=5 + (index % 6),
                care_score=5 + (index % 6),
                variety_resistance_score=5 + (index % 6),
                recommendation="ข้อมูลทดสอบสำหรับ round 10.0 — ติดตามรอบถัดไป",
                notes=f"mock record {index + 1:03d}/100 สำหรับทดสอบระบบ",
                photo_urls=[],
                custom_fields={"mockSeed": "farmlog-round10"},
                is_active=True,
            )
            session.add(record)
        await session.commit()

        return (
            [s.code for s in suppliers],
            [p.plot_code for p in plots],
        )


async def _after_counts() -> None:
    async with get_db_session() as session:
        await get_public_plot_rls_context(db=session)
        for label, model in (
            ("suppliers", Supplier), ("plots", Plot),
            ("plot_assignments", PlotAssignment), ("records", Record),
            ("master_data", MasterData),
        ):
            count = (await session.execute(select(func.count()).select_from(model))).scalar_one()
            print(f"after: {label}={count}")

        no_qr_key = (await session.execute(
            select(func.count()).select_from(Plot).where(Plot.qr_key.is_(None))
        )).scalar_one()
        print(f"after: plots_missing_qr_key={no_qr_key}")


async def main() -> None:
    await init_db()
    try:
        await _confirm_target_and_counts()
        await _delete_farmlog_domain_data()
        await _add_master_data()
        supplier_codes, plot_codes = await _seed_mock_data()
        await _after_counts()

        print("=== sample codes ===")
        for code in supplier_codes[:3]:
            matching_plots = [p for p in plot_codes if p.startswith(code + "-")][:1]
            for plot_code in matching_plots:
                print(f"supplierCode={code} plotCode={plot_code} qr_payload={code}|{plot_code}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

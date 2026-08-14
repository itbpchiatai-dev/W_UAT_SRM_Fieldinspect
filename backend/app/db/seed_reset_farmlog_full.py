"""One-off DEV-ONLY full reset + rich seed for FarmLog (interconnected data).

    python -m app.db.seed_reset_farmlog_full

DESTRUCTIVE — unconditionally DELETES all suppliers / plots / plot_assignments
/ records AND all master_data, then rebuilds a clean, fully-interconnected
dataset:

  - master_data : wiped, reseeded with a clean canonical set (crop / variety /
                  growth_stage / weather / province). variety rows carry
                  their parent crop. level / severity / irrigation /
                  fertilizer removed round 8-14F — no production consumer;
                  admin UI has hidden them since round 8-14E/8-14E.1.
  - suppliers   : 10
  - plots       : 100 (10 per supplier), every column populated
  - records     : 100 (1 per plot), every column populated, scores 1–10,
                  matched crop→variety→plot, yield 0–150
  - plot_assignments : a slice of plots assigned to real non-system users so
                  the plot × user graph is exercised too
  - each plot's current_* snapshot is synced from its record via the same
    plot_repository.sync_current_status_from_record the real API uses.

Matches the LIVE schema (scores model; submitted_by_code is nullable and
retired — round 8-3G — this seed still writes a legacy-style value on its
mock records purely to exercise historical-record display, not because the
column requires it), not the older list/status model. Does NOT touch
users / roles / permissions / auth / app_settings / field_definitions /
alembic_version.

Refuses to run unless DB_HOST is localhost/127.0.0.1.
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
from app.db.session import close_db, get_db_session, init_db
from app.db.seed_helpers import active_cycle_map
from app.repositories.plot_repository import sync_current_status_from_record
from app.services.plot_qr_key import generate_qr_key

_ALLOWED_HOSTS = {"localhost", "127.0.0.1"}

_SUPPLIER_COUNT = 10
_PLOTS_PER_SUPPLIER = 10

# System accounts never eligible for plot assignment / attribution.
_SYSTEM_EMAILS = {"external-field-helper@system.local"}

# ── Clean master-data catalog (type → values). variety handled separately. ──
_MASTER_DATA: dict[str, list[str]] = {
    "crop": ["พริก", "เมล่อน", "ฟักทอง", "แตงโม", "แตงกวา"],
    "growth_stage": ["ระยะงอก", "เจริญเติบโต", "ออกดอก", "ติดผล", "เก็บเกี่ยว"],
    "weather": ["แจ่มใส", "มีเมฆ", "ฝนตก", "ร้อนจัด", "ลมแรง"],
    # level / severity / irrigation / fertilizer removed round 8-14F — see
    # the module docstring above.
    # Thailand's 77 provinces — controlled dropdown for the Plots form's
    # "จังหวัด" field. Kept in sync with migration 0028_seed_provinces and
    # seed.py's _MASTER_DATA.
    "province": [
        "เชียงใหม่", "เชียงราย", "ลำปาง", "ลำพูน", "แม่ฮ่องสอน",
        "น่าน", "พะเยา", "แพร่", "อุตรดิตถ์",
        "ตาก", "พิษณุโลก", "สุโขทัย", "เพชรบูรณ์", "พิจิตร", "กำแพงเพชร", "นครสวรรค์", "อุทัยธานี",
        "ขอนแก่น", "นครราชสีมา", "อุดรธานี", "อุบลราชธานี", "บุรีรัมย์", "สุรินทร์", "ศรีสะเกษ",
        "ร้อยเอ็ด", "มหาสารคาม", "กาฬสินธุ์", "ชัยภูมิ", "เลย", "หนองคาย", "หนองบัวลำภู",
        "สกลนคร", "นครพนม", "มุกดาหาร", "ยโสธร", "อำนาจเจริญ", "บึงกาฬ",
        "กรุงเทพมหานคร", "นนทบุรี", "ปทุมธานี", "พระนครศรีอยุธยา", "อ่างทอง", "ลพบุรี",
        "สิงห์บุรี", "ชัยนาท", "สระบุรี", "นครนายก", "นครปฐม", "สมุทรปราการ",
        "สมุทรสาคร", "สมุทรสงคราม", "สุพรรณบุรี",
        "ชลบุรี", "ระยอง", "จันทบุรี", "ตราด", "ฉะเชิงเทรา", "ปราจีนบุรี", "สระแก้ว",
        "กาญจนบุรี", "ราชบุรี", "เพชรบุรี", "ประจวบคีรีขันธ์",
        "นครศรีธรรมราช", "สุราษฎร์ธานี", "กระบี่", "พังงา", "ภูเก็ต", "ระนอง", "ชุมพร",
        "สงขลา", "สตูล", "ตรัง", "พัทลุง", "ปัตตานี", "ยะลา", "นราธิวาส",
    ],
}
# (variety, parent crop)
_VARIETIES: list[tuple[str, str]] = [
    ("พริกขี้หนู", "พริก"), ("พริกจินดา", "พริก"),
    ("เมล่อนญี่ปุ่น", "เมล่อน"), ("เมล่อนออร์เร้นจ์", "เมล่อน"),
    ("ฟักทองศรีเมือง", "ฟักทอง"),
    ("แตงโมกินรี", "แตงโม"), ("แตงโมตอร์ปิโด", "แตงโม"),
    ("แตงกวาญี่ปุ่น", "แตงกวา"),
]

# variety options per crop, for matching a plot's crop to a valid variety.
_VARIETY_BY_CROP: dict[str, list[str]] = {}
for _v, _c in _VARIETIES:
    _VARIETY_BY_CROP.setdefault(_c, []).append(_v)

_CROPS = _MASTER_DATA["crop"]
_STAGES = _MASTER_DATA["growth_stage"]
_WEATHER = _MASTER_DATA["weather"]

# Geographic spread for plots (province, district, village, lat, lng).
_LOCATIONS: list[tuple[str, str, str, str, str]] = [
    ("เชียงใหม่", "แม่ริม", "บ้านสันโป่ง", "18.9100", "98.9400"),
    ("เชียงราย", "เมือง", "บ้านป่าอ้อ", "19.9100", "99.8300"),
    ("นครราชสีมา", "ปากช่อง", "บ้านหนองสาหร่าย", "14.7000", "101.4160"),
    ("ขอนแก่น", "เมือง", "บ้านโนนม่วง", "16.4320", "102.8360"),
    ("กาญจนบุรี", "ท่าม่วง", "บ้านหนองตากยา", "13.9970", "99.6100"),
    ("ราชบุรี", "ปากท่อ", "บ้านห้วยยางโทน", "13.3830", "99.6700"),
    ("จันทบุรี", "ท่าใหม่", "บ้านเขาบายศรี", "12.6100", "102.1000"),
    ("เพชรบูรณ์", "หล่มสัก", "บ้านน้ำก้อ", "16.7700", "101.2400"),
    ("สุพรรณบุรี", "อู่ทอง", "บ้านดอนคา", "14.3700", "99.8900"),
    ("ประจวบคีรีขันธ์", "หัวหิน", "บ้านหนองพลับ", "12.5700", "99.7900"),
]

# Yield plan uses ONE unit (kg) with an amount that tracks each plot's plant
# count: expected_yield_full = plant_count * kg-per-plant for the crop. Keeps
# "จำนวนต้น → ผลผลิตที่คาดหวัง" physically sensible and every total in a single
# comparable unit. Illustrative demo weights, not agronomic truth.
_YIELD_UNIT = "kg"
_KG_PER_PLANT: dict[str, float] = {
    "พริก": 2.0,
    "เมล่อน": 3.0,
    "ฟักทอง": 4.0,
    "แตงโม": 5.0,
    "แตงกวา": 3.5,
}
_DEFAULT_KG_PER_PLANT = 2.5


async def _confirm_target() -> None:
    settings = get_settings()
    print("=== DB target ===")
    print(f"host={settings.DB_HOST} port={settings.DB_PORT} db={settings.DB_NAME}")
    if settings.DB_HOST not in _ALLOWED_HOSTS:
        raise SystemExit(
            f"Refusing to run: DB_HOST={settings.DB_HOST!r} is not localhost/127.0.0.1."
        )
    async with get_db_session() as s:
        await get_public_plot_rls_context(db=s)
        for label, model in (
            ("suppliers", Supplier), ("plots", Plot),
            ("plot_assignments", PlotAssignment), ("records", Record),
            ("master_data", MasterData),
        ):
            n = (await s.execute(select(func.count()).select_from(model))).scalar_one()
            print(f"before: {label}={n}")


async def _wipe() -> None:
    async with get_db_session() as s:
        await get_public_plot_rls_context(db=s)
        # FK order: records → plot_assignments → plots → suppliers.
        # plots.last_inspection_record_id → records is ON DELETE SET NULL, so
        # deleting records first is safe. master_data has no inbound FKs.
        await s.execute(delete(Record))
        await s.execute(delete(PlotAssignment))
        await s.execute(delete(Plot))
        await s.execute(delete(Supplier))
        await s.execute(delete(MasterData))
        await s.commit()
    print("wiped: records, plot_assignments, plots, suppliers, master_data")


async def _seed_master_data() -> None:
    async with get_db_session() as s:
        added = 0
        for type_, values in _MASTER_DATA.items():
            for i, value in enumerate(values):
                s.add(MasterData(type=type_, value=value, order_index=i))
                added += 1
        for i, (value, parent) in enumerate(_VARIETIES):
            s.add(MasterData(type="variety", value=value, parent=parent, order_index=i))
            added += 1
        await s.commit()
    print(f"master_data: seeded {added} rows")


async def _seed_domain() -> dict[str, int]:
    base_date = datetime.date(2026, 5, 1)

    async with get_db_session() as s:
        await get_public_plot_rls_context(db=s)

        admin = (await s.execute(
            select(User).join(User.roles)
            .where(Role.name == "internal:super_admin").limit(1)
        )).scalars().first()
        if admin is None:
            raise SystemExit("No internal:super_admin user found — run app.seed first.")

        # Real, non-system users to spread plot assignments across.
        assignable = (await s.execute(
            select(User).where(User.email.notin_(_SYSTEM_EMAILS)).order_by(User.email)
        )).scalars().all()

        # ── suppliers ──
        suppliers: list[Supplier] = []
        for i in range(1, _SUPPLIER_COUNT + 1):
            code = f"SUP{i:03d}"
            suppliers.append(Supplier(
                code=code,
                name=f"ซัพพลายเออร์ {i:02d} จำกัด",
                tax_id=f"010{i:010d}"[:13],
                contact_name=f"ผู้ประสานงาน {i:02d}",
                contact_email=f"contact{i:02d}@supplier-demo.local",
                contact_phone=f"08{i:08d}"[:10],
                address=f"{i} หมู่ {i % 12 + 1} ต.ทดสอบ อ.เมือง",
                is_active=True,
            ))
            s.add(suppliers[-1])
        await s.flush()

        # ── plots (every column populated) ──
        plots: list[Plot] = []
        gidx = 0
        for si, supplier in enumerate(suppliers):
            for n in range(1, _PLOTS_PER_SUPPLIER + 1):
                loc = _LOCATIONS[gidx % len(_LOCATIONS)]
                province, district, village, lat, lng = loc
                crop = _CROPS[gidx % len(_CROPS)]
                variety = _VARIETY_BY_CROP[crop][gidx % len(_VARIETY_BY_CROP[crop])]
                plot_code = f"{supplier.code}-P{n:03d}"
                # Yield plan in a single unit (kg), amount consistent with the
                # plant count: full = plant_count * kg-per-plant for the crop.
                plant_count = 500 + (gidx % 20) * 50
                kg_per_plant = _KG_PER_PLANT.get(crop, _DEFAULT_KG_PER_PLANT)
                plot = Plot(
                    supplier_id=supplier.id,
                    plot_code=plot_code,
                    name=f"แปลง{crop} {plot_code}",
                    village=village,
                    district=district,
                    province=province,
                    latitude=Decimal(lat),
                    longitude=Decimal(lng),
                    rai=Decimal(str(2 + (gidx % 18))) + Decimal("0.25"),
                    is_active=True,
                    plant_count=plant_count,
                    expected_yield_full=Decimal(str(round(plant_count * kg_per_plant))),
                    expected_yield_unit=_YIELD_UNIT,
                    qr_key=generate_qr_key(),
                    # Plot MASTER planting-cycle data (admin-owned; sync never touches).
                    current_crop=crop,
                    current_variety=variety,
                    current_lot_no=f"LOT-{supplier.code}-{n:02d}",
                    current_planting_date=base_date - datetime.timedelta(days=30 + gidx % 40),
                )
                s.add(plot)
                plots.append(plot)
                gidx += 1
        await s.flush()

        # ── plot_assignments (interconnect plots ↔ users) ──
        assignments = 0
        if assignable:
            # Assign every 3rd plot to a rotating real user.
            for i, plot in enumerate(plots):
                if i % 3 == 0:
                    user = assignable[i % len(assignable)]
                    s.add(PlotAssignment(plot_id=plot.id, user_id=user.id))
                    assignments += 1
        await s.flush()

        # ── active planting cycle per plot (round 7.1.1 — record binds to it) ──
        cycle_by_plot = await active_cycle_map(s, plots)

        # ── records (1 per plot, every column populated) ──
        records: list[Record] = []
        for i, plot in enumerate(plots):
            crop = plot.current_crop or _CROPS[i % len(_CROPS)]
            variety = plot.current_variety or _VARIETY_BY_CROP[crop][0]
            rec = Record(
                plot_id=plot.id,
                supplier_id=plot.supplier_id,
                plot_cycle_id=cycle_by_plot[plot.id].id,
                recorded_by_id=admin.id,
                submitted_by_code=f"FIELD{(i % 20) + 1:03d}",
                submitted_by_name=f"ผู้ตรวจภาคสนาม {(i % 20) + 1:02d}",
                record_date=base_date + datetime.timedelta(days=i % 60),
                crop=crop,
                variety=variety,
                growth_stage=_STAGES[i % len(_STAGES)],
                planting_date=plot.current_planting_date,
                yield_pct=Decimal(str(60 + (i % 91))),          # 60–150
                weather_condition=_WEATHER[i % len(_WEATHER)],
                field_prep_score=1 + (i % 10),                  # 1–10
                weather_score=1 + ((i + 2) % 10),
                care_score=1 + ((i + 4) % 10),
                variety_resistance_score=1 + ((i + 6) % 10),
                recommendation=(
                    f"แปลง {plot.plot_code}: {crop}พันธุ์{variety} "
                    f"อยู่ระยะ{_STAGES[i % len(_STAGES)]} — แนะนำติดตามการให้น้ำและใส่ปุ๋ยตามรอบ"
                ),
                notes=f"บันทึกตรวจแปลง {i + 1:03d}/100 (ชุดข้อมูลตัวอย่าง)",
                latitude=plot.latitude,
                longitude=plot.longitude,
                photo_urls=[],          # image upload infra not wired (Step 15)
                custom_fields={},       # no custom FieldDefinition rows exist
                is_active=True,
            )
            s.add(rec)
            records.append(rec)
        await s.flush()

        # ── sync each plot's current_* snapshot from its record (as API does) ──
        for rec in records:
            await sync_current_status_from_record(s, rec)

        await s.commit()

        return {
            "suppliers": len(suppliers),
            "plots": len(plots),
            "assignments": assignments,
            "records": len(records),
            "assignable_users": len(assignable),
        }


async def _after() -> None:
    async with get_db_session() as s:
        await get_public_plot_rls_context(db=s)
        for label, model in (
            ("suppliers", Supplier), ("plots", Plot),
            ("plot_assignments", PlotAssignment), ("records", Record),
            ("master_data", MasterData),
        ):
            n = (await s.execute(select(func.count()).select_from(model))).scalar_one()
            print(f"after: {label}={n}")

        synced = (await s.execute(
            select(func.count()).select_from(Plot)
            .where(Plot.last_inspection_record_id.isnot(None))
        )).scalar_one()
        print(f"after: plots_with_synced_status={synced}")

        # sample joined row
        row = (await s.execute(
            select(Supplier.code, Plot.plot_code, Plot.current_crop,
                   Plot.current_yield_pct, Record.record_date, Record.submitted_by_code)
            .join(Plot, Plot.supplier_id == Supplier.id)
            .join(Record, Record.plot_id == Plot.id)
            .limit(1)
        )).first()
        if row:
            print(f"sample: supplier={row.code} plot={row.plot_code} crop={row.current_crop} "
                  f"yield={row.current_yield_pct}% date={row.record_date} by={row.submitted_by_code}")


async def main() -> None:
    await init_db()
    try:
        await _confirm_target()
        await _wipe()
        await _seed_master_data()
        stats = await _seed_domain()
        await _after()
        print("=== done ===")
        print(stats)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

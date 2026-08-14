"""Database seed entrypoint — `python -m app.db.seed`.

Wired into the onboarding "first run" flow (docs/human/onboarding.md §2.4).
Seeds reference / lookup data. Every insert MUST be idempotent (upsert by a
unique key) — onboarding re-runs this after a `docker compose down -v` reset.

See docs/database.md §7 for the recommended pattern.
"""
from __future__ import annotations

import asyncio
import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models.field_definition import FieldDefinition
from app.db.models.master_data import MasterData
from app.db.models.plot import Plot
from app.db.models.plot_assignment import PlotAssignment
from app.db.models.record import Record
from app.db.models.role import Role
from app.db.models.supplier import Supplier
from app.db.models.user import User
from app.db.seed_helpers import active_cycle_map
from app.db.session import close_db, get_db_session, init_db

# --- Master Data (dropdown options, Spec §3.5) -----------------------------
# (type, [values...])  · variety carries a parent crop.  Admin edits via UI.
_MASTER_DATA: dict[str, list[str]] = {
    "crop": ["พริก", "เมล่อน", "ฟักทอง", "แตงโม", "แตงกวา"],
    "growth_stage": ["ระยะงอก", "เจริญเติบโต", "ออกดอก", "ติดผล", "เก็บเกี่ยว"],
    "weather": ["แจ่มใส", "มีเมฆ", "ฝนตก", "ร้อนจัด", "ลมแรง"],
    # level / severity / irrigation / fertilizer removed round 8-14F — no
    # production consumer (Record Form only ever read growth_stage/weather;
    # confirmed via rg audit) and the admin UI has hidden them since round
    # 8-14E/8-14E.1. Existing rows of these types were deleted from
    # local/dev DB in the same round; do not reintroduce them here.
    # Thailand's 77 provinces — makes the Plots form's "จังหวัด" a controlled
    # dropdown (MasterDataSelect type="province") instead of free text. Kept
    # in sync with migration 0028_seed_provinces.
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
# variety → parent crop (subset for demo; admin extends)
_VARIETIES: list[tuple[str, str]] = [
    ("พริกขี้หนู", "พริก"), ("พริกจินดา", "พริก"),
    ("เมล่อนญี่ปุ่น", "เมล่อน"), ("แตงโมกินรี", "แตงโม"), ("แตงกวาญี่ปุ่น", "แตงกวา"),
]

_MASTER_DATA_SUPPLEMENT: dict[str, list[str]] = {
    "crop": ["มะเขือเทศ", "ข้าวโพดหวาน", "ผักกาดหอม", "คะน้า", "ถั่วฝักยาว", "บวบ", "มะเขือยาว"],
    "growth_stage": ["ตั้งตัว", "แตกใบ", "ขยายผล", "ก่อนเก็บเกี่ยว", "พักแปลง"],
    "weather": ["ฝนปรอย", "อากาศเย็น", "ความชื้นสูง", "หลังฝน", "หมอกเช้า"],
    # level / severity / irrigation / fertilizer removed round 8-14F — see
    # _MASTER_DATA's comment above.
}

_VARIETY_SUPPLEMENT: list[tuple[str, str]] = [
    ("พริกซุปเปอร์ฮอท", "พริก"),
    ("เมล่อนกรีนเน็ต", "เมล่อน"),
    ("เมล่อนออเรนจ์", "เมล่อน"),
    ("แตงโมจินตหรา", "แตงโม"),
    ("แตงกวาลูกผสม", "แตงกวา"),
    ("ฟักทองทองอำไพ", "ฟักทอง"),
    ("ฟักทองญี่ปุ่น", "ฟักทอง"),
]

# --- Core field catalog (Step 12.5) — mirrors the reshaped `records` columns.
# (key, label, field_type, required, order_index, list_default, options_source)
_CORE_FIELDS: list[tuple[str, str, str, bool, int, bool, str | None]] = [
    ("plot",              "แปลง",                       "plot_picker", True,  5,   True,  None),
    ("record_date",       "วันที่ตรวจ",                 "date",        True,  10,  True,  None),
    ("crop",              "ชนิดพืช",                    "list",        False, 20,  True,  "masterdata:crop"),
    ("variety",           "พันธุ์/สายพันธุ์",           "list",        False, 25,  False, "masterdata:variety"),
    ("growth_stage",      "ระยะการเจริญเติบโต",         "list",        False, 30,  True,  "masterdata:growth_stage"),
    ("planting_date",     "วันที่ปลูก",                 "date",        False, 35,  False, None),
    ("yield_pct",         "% คาดว่าจะได้ผลผลิต (Yield)", "percent",    False, 40,  True,  None),
    ("weather_condition", "สภาพอากาศ",                  "list",        False, 50,  False, "masterdata:weather"),
    ("field_prep_score",       "คะแนนการเตรียมแปลง",         "score", False, 60,  False, None),
    ("weather_score",          "คะแนนสภาพอากาศ",             "score", False, 70,  False, None),
    ("care_score",             "คะแนนการดูแลรักษา",          "score", False, 80,  False, None),
    ("variety_resistance_score", "คะแนนความต้านทานของสายพันธุ์", "score", False, 90,  False, None),
    ("recommendation",    "คำแนะนำ",                    "multiline",   False, 170, False, None),
    ("notes",             "หมายเหตุ",                   "multiline",   False, 180, False, None),
    ("gps",               "พิกัด GPS",                  "geo",         False, 190, False, None),
    ("photos",            "ภาพถ่าย",                    "photo",       False, 200, False, None),
]


_SAMPLE_SUPPLIERS: list[tuple[str, str, str, str, str, str, str]] = [
    ("SUP001", "สวนตัวอย่างลำปาง", "0105559000011", "อนันต์ ใจดี", "anan.sup001@example.invalid", "080-000-0001", "อ.เมือง จ.ลำปาง"),
    ("SUP002", "ไร่พริกแม่ริม", "0105559000012", "เบญจา แม่ริม", "benja.sup002@example.invalid", "080-000-0002", "อ.แม่ริม จ.เชียงใหม่"),
    ("SUP003", "กลุ่มเกษตรกรบ้านน้ำใส", "0105559000013", "ชาญชัย น้ำใส", "chanchai.sup003@example.invalid", "080-000-0003", "อ.ปากช่อง จ.นครราชสีมา"),
    ("SUP004", "ฟาร์มแตงโมวังทอง", "0105559000014", "ดาริน วังทอง", "darin.sup004@example.invalid", "080-000-0004", "อ.วังทอง จ.พิษณุโลก"),
    ("SUP005", "สวนเมล่อนห้วยทราย", "0105559000015", "เอกชัย ห้วยทราย", "ekkachai.sup005@example.invalid", "080-000-0005", "อ.ชะอำ จ.เพชรบุรี"),
    ("SUP006", "วิสาหกิจชุมชนเขียวสด", "0105559000016", "ฟ้าใส เขียวสด", "fasai.sup006@example.invalid", "080-000-0006", "อ.เมือง จ.ขอนแก่น"),
    ("SUP007", "ไร่แตงกวานครชัย", "0105559000017", "กิตติ นครชัย", "kitti.sup007@example.invalid", "080-000-0007", "อ.เมือง จ.นครปฐม"),
    ("SUP008", "ฟาร์มผักปลอดภัยบูรพา", "0105559000018", "ลลิตา บูรพา", "lalita.sup008@example.invalid", "080-000-0008", "อ.ศรีราชา จ.ชลบุรี"),
    ("SUP009", "แปลงทดลองอีสานใต้", "0105559000019", "มานพ อีสาน", "manop.sup009@example.invalid", "080-000-0009", "อ.เมือง จ.บุรีรัมย์"),
    ("SUP010", "สวนพริกริมโขง", "0105559000020", "นฤมล ริมโขง", "narumon.sup010@example.invalid", "080-000-0010", "อ.เมือง จ.หนองคาย"),
    ("SUP011", "เครือข่ายเกษตรกรบางระกำ", "0105559000021", "ปกรณ์ บางระกำ", "pakorn.sup011@example.invalid", "080-000-0011", "อ.บางระกำ จ.พิษณุโลก"),
    ("SUP012", "ฟาร์มเมล็ดพันธุ์ต้นน้ำ", "0105559000022", "รุ่งทิวา ต้นน้ำ", "rungtiwa.sup012@example.invalid", "080-000-0012", "อ.แม่แตง จ.เชียงใหม่"),
]

_SAMPLE_USERS: list[tuple[str, str, str, str | None, bool, list[str]]] = [
    ("supervisor.demo@example.invalid", "สมชาย หัวหน้าทีม", "azure_ad", None, False, ["farmlog:supervisor"]),
    ("officer01.demo@example.invalid", "วิภา เจ้าหน้าที่ 01", "azure_ad", None, False, ["farmlog:field_officer"]),
    ("officer02.demo@example.invalid", "นิรันดร์ เจ้าหน้าที่ 02", "azure_ad", None, False, ["farmlog:field_officer"]),
    ("officer03.demo@example.invalid", "กมล เจ้าหน้าที่ 03", "azure_ad", None, False, ["farmlog:field_officer"]),
    ("owner01.demo@example.invalid", "อนันต์ เจ้าของสวน", "azure_ad", "SUP001", True, ["supplier:owner"]),
    ("owner02.demo@example.invalid", "เบญจา เจ้าของไร่", "azure_ad", "SUP002", True, ["supplier:owner"]),
    ("owner03.demo@example.invalid", "ชาญชัย เจ้าของกลุ่ม", "azure_ad", "SUP003", True, ["supplier:owner"]),
    ("staff01.demo@example.invalid", "จิราพร ผู้ช่วยสวน", "azure_ad", "SUP001", False, ["supplier:staff"]),
    ("staff02.demo@example.invalid", "ธนกร ผู้ช่วยไร่", "azure_ad", "SUP002", False, ["supplier:staff"]),
    ("staff03.demo@example.invalid", "พิมพ์ชนก ผู้ช่วยกลุ่ม", "azure_ad", "SUP003", False, ["supplier:staff"]),
    ("admin.demo@example.invalid", "ผู้ดูแลระบบตัวอย่าง", "azure_ad", None, False, ["internal:admin"]),
    ("viewer.demo@example.invalid", "ผู้ใช้งานตัวอย่าง", "azure_ad", None, False, ["internal:user"]),
]

_SAMPLE_PLOTS: list[tuple[str, str, str, str, str, str, Decimal, Decimal, Decimal]] = [
    ("SUP001", "P001", "แปลงพริกเหนือ", "บ้านแม่เมาะ", "เมืองลำปาง", "ลำปาง", Decimal("18.2891000"), Decimal("99.4928000"), Decimal("12.50")),
    ("SUP002", "P002", "แปลงพริกโรงเรือน", "บ้านริมใต้", "แม่ริม", "เชียงใหม่", Decimal("18.9162000"), Decimal("98.9407000"), Decimal("8.75")),
    ("SUP003", "P003", "แปลงเมล่อน A", "บ้านน้ำใส", "ปากช่อง", "นครราชสีมา", Decimal("14.7083000"), Decimal("101.4161000"), Decimal("6.25")),
    ("SUP004", "P004", "แปลงแตงโมวังทอง", "บ้านวังนกแอ่น", "วังทอง", "พิษณุโลก", Decimal("16.8246000"), Decimal("100.4299000"), Decimal("15.00")),
    ("SUP005", "P005", "แปลงเมล่อนห้วยทราย", "ห้วยทรายเหนือ", "ชะอำ", "เพชรบุรี", Decimal("12.8005000"), Decimal("99.9663000"), Decimal("5.50")),
    ("SUP006", "P006", "แปลงทดลองผักรวม", "บ้านทุ่ม", "เมืองขอนแก่น", "ขอนแก่น", Decimal("16.4459000"), Decimal("102.8359000"), Decimal("9.00")),
    ("SUP007", "P007", "แปลงแตงกวา 1", "ธรรมศาลา", "เมืองนครปฐม", "นครปฐม", Decimal("13.8199000"), Decimal("100.0621000"), Decimal("7.25")),
    ("SUP008", "P008", "แปลงผักปลอดภัย", "หนองขาม", "ศรีราชา", "ชลบุรี", Decimal("13.1737000"), Decimal("100.9308000"), Decimal("10.00")),
    ("SUP009", "P009", "แปลงอีสานใต้ 1", "บ้านบัว", "เมืองบุรีรัมย์", "บุรีรัมย์", Decimal("14.9930000"), Decimal("103.1029000"), Decimal("11.25")),
    ("SUP010", "P010", "แปลงพริกริมโขง", "มีชัย", "เมืองหนองคาย", "หนองคาย", Decimal("17.8783000"), Decimal("102.7413000"), Decimal("13.75")),
    ("SUP011", "P011", "แปลงบางระกำ A", "บางระกำ", "บางระกำ", "พิษณุโลก", Decimal("16.7587000"), Decimal("100.1175000"), Decimal("14.00")),
    ("SUP012", "P012", "แปลงต้นน้ำ 1", "สันมหาพน", "แม่แตง", "เชียงใหม่", Decimal("19.1173000"), Decimal("98.9447000"), Decimal("6.80")),
    ("SUP001", "P013", "แปลงพริกใต้", "บ้านแม่เมาะ", "เมืองลำปาง", "ลำปาง", Decimal("18.2805000"), Decimal("99.5002000"), Decimal("9.40")),
    ("SUP002", "P014", "แปลงพริกกลางแจ้ง", "บ้านแม่สา", "แม่ริม", "เชียงใหม่", Decimal("18.9007000"), Decimal("98.9298000"), Decimal("10.60")),
    ("SUP003", "P015", "แปลงเมล่อน B", "บ้านหนองสาหร่าย", "ปากช่อง", "นครราชสีมา", Decimal("14.6555000"), Decimal("101.3982000"), Decimal("5.90")),
]

_SAMPLE_CROPS = ["พริก", "เมล่อน", "ฟักทอง", "แตงโม", "แตงกวา"]
_SAMPLE_VARIETIES = ["พริกขี้หนู", "พริกจินดา", "เมล่อนญี่ปุ่น", "แตงโมกินรี", "แตงกวาญี่ปุ่น"]
_SAMPLE_GROWTH_STAGES = ["ระยะงอก", "เจริญเติบโต", "ออกดอก", "ติดผล", "เก็บเกี่ยว"]
_SAMPLE_WEATHER = ["แจ่มใส", "มีเมฆ", "ฝนตก", "ร้อนจัด", "ลมแรง"]
# Demo field-attribution codes — NOT real staff codes, just sample data.
_SAMPLE_SUBMITTED_BY_CODES = ["FIELD01", "FIELD02", "FIELD03", "FIELD04", "FIELD05"]


async def _seed_master_data() -> None:
    """Upsert dropdown options by (type, value). Idempotent."""
    async with get_db_session() as session:
        async def upsert(type_: str, value: str, parent: str | None, order: int) -> None:
            existing = (await session.execute(
                select(MasterData).where(MasterData.type == type_, MasterData.value == value)
            )).scalar_one_or_none()
            if existing is None:
                session.add(MasterData(type=type_, value=value, parent=parent, order_index=order))

        merged_master_data = {
            type_: [*values, *_MASTER_DATA_SUPPLEMENT.get(type_, [])]
            for type_, values in _MASTER_DATA.items()
        }
        for type_, values in _MASTER_DATA_SUPPLEMENT.items():
            merged_master_data.setdefault(type_, values)

        for type_, values in merged_master_data.items():
            seen: set[str] = set()
            for i, value in enumerate(v for v in values if not (v in seen or seen.add(v))):
                await upsert(type_, value, None, i)
        for i, (value, parent) in enumerate([*_VARIETIES, *_VARIETY_SUPPLEMENT]):
            await upsert("variety", value, parent, i)
        await session.commit()


async def _seed_core_field_definitions() -> None:
    """Upsert core FieldDefinition rows by key, then prune stale core rows.
    Label/order are only set on insert so Field Master tweaks survive a re-seed.
    Custom fields (is_core=False) are never touched."""
    canonical = {f[0] for f in _CORE_FIELDS}
    async with get_db_session() as session:
        for key, label, field_type, required, order_index, list_default, options_source in _CORE_FIELDS:
            existing = (await session.execute(
                select(FieldDefinition).where(FieldDefinition.key == key)
            )).scalar_one_or_none()
            if existing is None:
                session.add(FieldDefinition(
                    key=key, label=label, field_type=field_type,
                    required=required, order_index=order_index,
                    list_default=list_default, options_source=options_source,
                    is_core=True, active=True,
                ))
            else:
                # Sync system-controlled attrs (type/source/is_core) to the code;
                # leave admin-editable label/required/order/active as-is.
                existing.field_type = field_type
                existing.options_source = options_source
                existing.is_core = True
        # Prune core rows that no longer map to a records column (schema reshape).
        stale = (await session.execute(
            select(FieldDefinition).where(
                FieldDefinition.is_core.is_(True),
                FieldDefinition.key.notin_(canonical),
            )
        )).scalars().all()
        for row in stale:
            await session.delete(row)
        await session.commit()


async def _seed_sample_data() -> None:
    """Seed demo business data for local/dev review.

    Uses stable codes/emails and sample dates so repeated runs update/skip
    instead of duplicating rows. All people/emails are fictitious.
    """
    async with get_db_session() as session:
        suppliers: dict[str, Supplier] = {}
        for code, name, tax_id, contact_name, contact_email, contact_phone, address in _SAMPLE_SUPPLIERS:
            supplier = (await session.execute(
                select(Supplier).where(Supplier.code == code)
            )).scalar_one_or_none()
            if supplier is None:
                supplier = Supplier(
                    code=code,
                    name=name,
                    tax_id=tax_id,
                    contact_name=contact_name,
                    contact_email=contact_email,
                    contact_phone=contact_phone,
                    address=address,
                    is_active=True,
                )
                session.add(supplier)
            else:
                supplier.name = name
                supplier.tax_id = tax_id
                supplier.contact_name = contact_name
                supplier.contact_email = contact_email
                supplier.contact_phone = contact_phone
                supplier.address = address
                supplier.is_active = True
            suppliers[code] = supplier
        await session.flush()

        role_rows = (await session.execute(select(Role))).scalars().all()
        roles = {role.name: role for role in role_rows}

        users: dict[str, User] = {}
        for email, full_name, auth_provider, supplier_code, is_supplier_admin, role_names in _SAMPLE_USERS:
            user = (await session.execute(
                select(User)
                .where(User.email == email)
                .options(selectinload(User.roles))
            )).scalar_one_or_none()
            if user is None:
                user = User(
                    email=email,
                    full_name=full_name,
                    auth_provider=auth_provider,
                    password_hash=None,
                    is_active=True,
                    is_approved=True,
                    is_rejected=False,
                    email_verified=True,
                    supplier_id=suppliers[supplier_code].id if supplier_code else None,
                    is_supplier_admin=is_supplier_admin,
                )
                session.add(user)
                await session.flush()
                await session.refresh(user, attribute_names=["roles"])
            else:
                user.full_name = full_name
                user.auth_provider = auth_provider
                user.password_hash = None
                user.is_active = True
                user.is_approved = True
                user.is_rejected = False
                user.email_verified = True
                user.supplier_id = suppliers[supplier_code].id if supplier_code else None
                user.is_supplier_admin = is_supplier_admin
            user.roles = [roles[name] for name in role_names if name in roles]
            users[email] = user

        plots: dict[str, Plot] = {}
        for supplier_code, plot_code, name, village, district, province, lat, lng, rai in _SAMPLE_PLOTS:
            supplier = suppliers[supplier_code]
            plot = (await session.execute(
                select(Plot)
                .where(Plot.supplier_id == supplier.id, Plot.plot_code == plot_code)
                .options(selectinload(Plot.assignments))
            )).scalar_one_or_none()
            if plot is None:
                plot = Plot(
                    supplier_id=supplier.id,
                    plot_code=plot_code,
                    name=name,
                    village=village,
                    district=district,
                    province=province,
                    latitude=lat,
                    longitude=lng,
                    rai=rai,
                    is_active=True,
                )
                session.add(plot)
                await session.flush()
                await session.refresh(plot, attribute_names=["assignments"])
            else:
                plot.name = name
                plot.village = village
                plot.district = district
                plot.province = province
                plot.latitude = lat
                plot.longitude = lng
                plot.rai = rai
                plot.is_active = True
            plots[plot_code] = plot

        responsible_cycle = [
            "officer01.demo@example.invalid",
            "officer02.demo@example.invalid",
            "officer03.demo@example.invalid",
            "staff01.demo@example.invalid",
            "staff02.demo@example.invalid",
            "staff03.demo@example.invalid",
        ]
        for index, plot in enumerate(plots.values()):
            assigned = [
                users[responsible_cycle[index % len(responsible_cycle)]],
                users["supervisor.demo@example.invalid"],
            ]
            if plot.supplier_id == suppliers["SUP001"].id:
                assigned.append(users["owner01.demo@example.invalid"])
            elif plot.supplier_id == suppliers["SUP002"].id:
                assigned.append(users["owner02.demo@example.invalid"])
            elif plot.supplier_id == suppliers["SUP003"].id:
                assigned.append(users["owner03.demo@example.invalid"])

            existing_user_ids = {assignment.user_id for assignment in plot.assignments}
            for assigned_user in assigned:
                if assigned_user.id not in existing_user_ids:
                    session.add(PlotAssignment(plot_id=plot.id, user_id=assigned_user.id))

        recorded_by = users["supervisor.demo@example.invalid"]
        # active planting cycle per plot (round 7.1.1 — record binds to it)
        cycle_by_plot = await active_cycle_map(session, plots.values())
        base_date = datetime.date(2026, 6, 1)
        for index, plot in enumerate(plots.values()):
            sample_key = f"sample-record-{index + 1:02d}"
            record_date = base_date + datetime.timedelta(days=index)
            existing_records = (await session.execute(
                select(Record).where(
                    Record.plot_id == plot.id,
                    Record.record_date == record_date,
                )
            )).scalars().all()
            record = next(
                (
                    row for row in existing_records
                    if row.custom_fields.get("sampleKey") == sample_key
                ),
                None,
            )
            if record is None:
                record = Record(
                    plot_id=plot.id,
                    supplier_id=plot.supplier_id,
                    plot_cycle_id=cycle_by_plot[plot.id].id,
                    recorded_by_id=recorded_by.id,
                    record_date=record_date,
                )
                session.add(record)
            record.submitted_by_code = _SAMPLE_SUBMITTED_BY_CODES[index % len(_SAMPLE_SUBMITTED_BY_CODES)]
            record.crop = _SAMPLE_CROPS[index % len(_SAMPLE_CROPS)]
            record.variety = _SAMPLE_VARIETIES[index % len(_SAMPLE_VARIETIES)]
            record.growth_stage = _SAMPLE_GROWTH_STAGES[index % len(_SAMPLE_GROWTH_STAGES)]
            record.planting_date = record_date - datetime.timedelta(days=25 + index)
            record.yield_pct = Decimal("92.5") + Decimal(index % 8)
            record.weather_condition = _SAMPLE_WEATHER[index % len(_SAMPLE_WEATHER)]
            record.field_prep_score = 6 + (index % 5)
            record.weather_score = 5 + (index % 6)
            record.care_score = 6 + (index % 5)
            record.variety_resistance_score = 5 + (index % 5)
            record.recommendation = "ติดตามความชื้นและตรวจโรคพืชตามรอบ"
            record.notes = f"ข้อมูลตัวอย่างสำหรับทดสอบระบบ ลำดับ {index + 1}"
            record.latitude = plot.latitude
            record.longitude = plot.longitude
            record.photo_urls = []
            record.custom_fields = {
                "sampleKey": sample_key,
                "soilMoisture": ["ต่ำ", "ปานกลาง", "ดี"][index % 3],
                "inspectionRound": index + 1,
            }
            record.is_active = True

        await session.commit()


async def seed_lookup_data() -> None:
    """Insert lookup / reference data. Idempotent — safe to re-run."""
    await _seed_master_data()
    await _seed_core_field_definitions()
    await _seed_sample_data()


async def main() -> None:
    await init_db()
    try:
        await seed_lookup_data()
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

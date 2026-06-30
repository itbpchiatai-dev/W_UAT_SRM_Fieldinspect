"""Database seed entrypoint — `python -m app.db.seed`.

Wired into the onboarding "first run" flow (docs/human/onboarding.md §2.4).
Seeds reference / lookup data. Every insert MUST be idempotent (upsert by a
unique key) — onboarding re-runs this after a `docker compose down -v` reset.

See docs/database.md §7 for the recommended pattern.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.models.field_definition import FieldDefinition
from app.db.models.master_data import MasterData
from app.db.session import close_db, get_db_session, init_db

# --- Master Data (dropdown options, Spec §3.5) -----------------------------
# (type, [values...])  · variety carries a parent crop.  Admin edits via UI.
_MASTER_DATA: dict[str, list[str]] = {
    "crop": ["พริก", "เมล่อน", "ฟักทอง", "แตงโม", "แตงกวา"],
    "growth_stage": ["ระยะงอก", "เจริญเติบโต", "ออกดอก", "ติดผล", "เก็บเกี่ยว"],
    "weather": ["แจ่มใส", "มีเมฆ", "ฝนตก", "ร้อนจัด", "ลมแรง"],
    "level": ["ต่ำ", "ปานกลาง", "ดี", "ดีมาก"],
    "severity": ["ไม่พบ", "เล็กน้อย", "ปานกลาง", "รุนแรง"],
    "irrigation": ["น้ำหยด", "สปริงเกลอร์", "ร่องน้ำ", "ฝนธรรมชาติ"],
    "fertilizer": ["เคมี", "อินทรีย์", "ผสม", "ไม่ใส่"],
}
# variety → parent crop (subset for demo; admin extends)
_VARIETIES: list[tuple[str, str]] = [
    ("พริกขี้หนู", "พริก"), ("พริกจินดา", "พริก"),
    ("เมล่อนญี่ปุ่น", "เมล่อน"), ("แตงโมกินรี", "แตงโม"), ("แตงกวาญี่ปุ่น", "แตงกวา"),
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
    ("field_prep_level",  "การเตรียมแปลง",              "list",        False, 60,  False, "masterdata:level"),
    ("care_level",        "การดูแลรักษา",               "list",        False, 70,  False, "masterdata:level"),
    ("pest_status",       "แมลงศัตรูพืช",               "list",        False, 80,  True,  "masterdata:severity"),
    ("disease_status",    "โรคพืช",                     "list",        False, 90,  True,  "masterdata:severity"),
    ("weed_status",       "วัชพืช",                     "list",        False, 100, False, "masterdata:severity"),
    ("irrigation_method", "วิธีการให้น้ำ",              "list",        False, 110, False, "masterdata:irrigation"),
    ("fertilizer",        "ปุ๋ยที่ใช้",                 "list",        False, 120, False, "masterdata:fertilizer"),
    ("recommendation",    "คำแนะนำ",                    "multiline",   False, 170, False, None),
    ("notes",             "หมายเหตุ",                   "multiline",   False, 180, False, None),
    ("gps",               "พิกัด GPS",                  "geo",         False, 190, False, None),
    ("photos",            "ภาพถ่าย",                    "photo",       False, 200, False, None),
]


async def _seed_master_data() -> None:
    """Upsert dropdown options by (type, value). Idempotent."""
    async with get_db_session() as session:
        async def upsert(type_: str, value: str, parent: str | None, order: int) -> None:
            existing = (await session.execute(
                select(MasterData).where(MasterData.type == type_, MasterData.value == value)
            )).scalar_one_or_none()
            if existing is None:
                session.add(MasterData(type=type_, value=value, parent=parent, order_index=order))

        for type_, values in _MASTER_DATA.items():
            for i, value in enumerate(values):
                await upsert(type_, value, None, i)
        for i, (value, parent) in enumerate(_VARIETIES):
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


async def seed_lookup_data() -> None:
    """Insert lookup / reference data. Idempotent — safe to re-run."""
    await _seed_master_data()
    await _seed_core_field_definitions()


async def main() -> None:
    await init_db()
    try:
        await seed_lookup_data()
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

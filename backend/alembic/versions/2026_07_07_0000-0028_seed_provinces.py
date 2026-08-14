"""seed provinces — populate master_data(type='province') with Thailand's 77
provinces so the Plots form's "จังหวัด" field can become a controlled
dropdown (MasterDataSelect type="province") instead of free text.

Data-only migration: no schema change. master_data already exists (0019)
and its `province` type is a first-class supported category (see
app/db/models/master_data.py docstring). Idempotent via ON CONFLICT on the
(type, value) unique index (uq_master_data_type_value) — re-running or
running after a seed that already added provinces is a no-op. Existing
plots keep their free-text province values (the column is unchanged); a
value not in this list still renders (MasterDataSelect preserves an
unknown current value as a visible option).

Revision ID: 0028_seed_provinces
Revises: 0027_supplier_inspection_code
Create Date: 2026-07-07 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_seed_provinces"
down_revision = "0027_supplier_inspection_code"
branch_labels = None
depends_on = None

# Thailand's 77 provinces (76 + Bangkok), ordered roughly by region then
# alphabetically within region — the same ordering carried in the seed
# scripts so admin-facing lists match regardless of how the DB was built.
_PROVINCES: list[str] = [
    # ภาคเหนือ
    "เชียงใหม่", "เชียงราย", "ลำปาง", "ลำพูน", "แม่ฮ่องสอน",
    "น่าน", "พะเยา", "แพร่", "อุตรดิตถ์",
    "ตาก", "พิษณุโลก", "สุโขทัย", "เพชรบูรณ์", "พิจิตร", "กำแพงเพชร", "นครสวรรค์", "อุทัยธานี",
    # ภาคตะวันออกเฉียงเหนือ
    "ขอนแก่น", "นครราชสีมา", "อุดรธานี", "อุบลราชธานี", "บุรีรัมย์", "สุรินทร์", "ศรีสะเกษ",
    "ร้อยเอ็ด", "มหาสารคาม", "กาฬสินธุ์", "ชัยภูมิ", "เลย", "หนองคาย", "หนองบัวลำภู",
    "สกลนคร", "นครพนม", "มุกดาหาร", "ยโสธร", "อำนาจเจริญ", "บึงกาฬ",
    # ภาคกลาง
    "กรุงเทพมหานคร", "นนทบุรี", "ปทุมธานี", "พระนครศรีอยุธยา", "อ่างทอง", "ลพบุรี",
    "สิงห์บุรี", "ชัยนาท", "สระบุรี", "นครนายก", "นครปฐม", "สมุทรปราการ",
    "สมุทรสาคร", "สมุทรสงคราม", "สุพรรณบุรี",
    # ภาคตะวันออก
    "ชลบุรี", "ระยอง", "จันทบุรี", "ตราด", "ฉะเชิงเทรา", "ปราจีนบุรี", "สระแก้ว",
    # ภาคตะวันตก
    "กาญจนบุรี", "ราชบุรี", "เพชรบุรี", "ประจวบคีรีขันธ์",
    # ภาคใต้
    "นครศรีธรรมราช", "สุราษฎร์ธานี", "กระบี่", "พังงา", "ภูเก็ต", "ระนอง", "ชุมพร",
    "สงขลา", "สตูล", "ตรัง", "พัทลุง", "ปัตตานี", "ยะลา", "นราธิวาส",
]


def upgrade() -> None:
    # id/created_at/updated_at have DB-side defaults (0019); active defaults
    # TRUE. ON CONFLICT (type, value) DO NOTHING makes this idempotent and
    # safe alongside any seed run that already inserted some provinces.
    stmt = sa.text(
        "INSERT INTO master_data (type, value, order_index, active) "
        "VALUES ('province', :value, :order_index, TRUE) "
        "ON CONFLICT (type, value) DO NOTHING"
    )
    for order_index, value in enumerate(_PROVINCES):
        op.execute(stmt.bindparams(value=value, order_index=order_index))


def downgrade() -> None:
    op.execute("DELETE FROM master_data WHERE type = 'province'")

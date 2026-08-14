"""inspection_protocol_criteria — admin-editable growth-stage protocol config
(round 5.5).

Creates the table, seeds the 5 stages x 4 slots from the built-in registry
(app/services/inspection_protocols.py's DEFAULT_PROTOCOLS as of this round),
and adds the "Inspection Protocol" admin menu under FarmLog admin (gated by
the existing masterdata.read — no new permission).

The service falls back to the built-in registry when this table is empty, so
this seed is a convenience (lets admins edit) rather than a correctness
requirement. Labels carry no apostrophes, so the plain-literal INSERT below
is safe; kept in one statement per the sync-psycopg migration engine.

Revision ID: 0032_protocol_criteria
Revises: 0031_records_submitted_ip
Create Date: 2026-07-11 00:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0032_protocol_criteria"
down_revision = "0031_records_submitted_ip"
branch_labels = None
depends_on = None

# camelCase slot names, in canonical order 0..3.
_SLOTS = ["fieldPrepScore", "weatherScore", "careScore", "varietyResistanceScore"]

# stage -> [label per slot, in _SLOTS order]. Mirrors DEFAULT_PROTOCOLS.
_PROTOCOLS = {
    "ระยะงอก": ["การเตรียมแปลง", "สภาพอากาศ", "การดูแลรักษา", "ความต้านทานของสายพันธุ์"],
    "เจริญเติบโต": ["สภาพอากาศ", "การดูแลรักษา", "ความเสี่ยง", "สภาพแปลง"],
    "ออกดอก": ["ความสมบูรณ์ของดอก", "สภาพอากาศ", "การดูแลรักษา", "ความเสี่ยงโรคและแมลง"],
    "ติดผล": ["การติดผล", "ความสมบูรณ์ของผล", "การดูแลรักษา", "ความเสี่ยงโรคและแมลง"],
    "เก็บเกี่ยว": ["ความพร้อมเก็บเกี่ยว", "คุณภาพผลผลิต", "ปริมาณผลผลิตคาดการณ์", "สภาพแปลงก่อนเก็บเกี่ยว"],
}


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE inspection_protocol_criteria (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            growth_stage VARCHAR(100) NOT NULL,
            slot VARCHAR(50) NOT NULL,
            label VARCHAR(255) NOT NULL,
            order_index INTEGER NOT NULL DEFAULT 0,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_protocol_stage_slot UNIQUE (growth_stage, slot)
        );
        CREATE INDEX ix_inspection_protocol_criteria_growth_stage
            ON inspection_protocol_criteria (growth_stage);
        """
    )

    rows = [
        f"('{stage}', '{slot}', '{label}', {i})"
        for stage, labels in _PROTOCOLS.items()
        for i, (slot, label) in enumerate(zip(_SLOTS, labels))
    ]
    op.execute(
        "INSERT INTO inspection_protocol_criteria (growth_stage, slot, label, order_index) "
        "VALUES " + ", ".join(rows) + ";"
    )

    # Admin menu under FarmLog admin (mirrors the 0029 pattern) — gated by the
    # existing masterdata.read so master-data admins already see it.
    op.execute(
        """
        INSERT INTO menu_items
            (key, label_th, label_en, icon, path, parent_id, order_index,
             required_permission_key, is_system)
        SELECT 'farmlog.admin.inspectionprotocols', 'เกณฑ์การตรวจ', 'Inspection Protocol',
               'ClipboardCheck', '/farmlog/admin/inspection-protocols', p.id, 50,
               'masterdata.read', TRUE
        FROM menu_items p
        WHERE p.key = 'farmlog.admin'
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM menu_items WHERE key = 'farmlog.admin.inspectionprotocols';")
    op.execute("DROP TABLE IF EXISTS inspection_protocol_criteria;")

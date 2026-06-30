"""master_data — editable dropdown option source (Step 12.5, Spec §3.5).

GLOBAL config (not supplier-scoped) — no RLS.

Revision ID: 0019_master_data
Revises: 0018_field_definitions
Create Date: 2026-06-30 01:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0019_master_data"
down_revision = "0018_field_definitions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE master_data (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            type         VARCHAR(64) NOT NULL,
            value        VARCHAR(255) NOT NULL,
            parent       VARCHAR(255),
            order_index  INTEGER NOT NULL DEFAULT 0,
            active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX ix_master_data_type ON master_data (type, active, order_index);
        CREATE UNIQUE INDEX uq_master_data_type_value ON master_data (type, value);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS master_data;")

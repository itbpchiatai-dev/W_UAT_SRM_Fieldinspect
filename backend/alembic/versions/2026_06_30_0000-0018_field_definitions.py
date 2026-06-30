"""field_definitions — schema-driven form fields (Step 12).

Catalogs every form field. `is_core=true` rows mirror the typed `records`
columns (label/order/required editable, key/type immutable). `is_core=false`
rows are admin-created custom fields stored in `records.custom_fields` JSONB.

This is GLOBAL config (not supplier-scoped) — no RLS.

Revision ID: 0018_field_definitions
Revises: 0017_record_gps_photos
Create Date: 2026-06-30 00:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0018_field_definitions"
down_revision = "0017_record_gps_photos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE field_definitions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            key             VARCHAR(64) NOT NULL UNIQUE,
            label           VARCHAR(255) NOT NULL,
            field_type      VARCHAR(32) NOT NULL,
            required        BOOLEAN NOT NULL DEFAULT FALSE,
            options_source  VARCHAR(128),
            options         JSONB NOT NULL DEFAULT '[]',
            is_core         BOOLEAN NOT NULL DEFAULT FALSE,
            list_default    BOOLEAN NOT NULL DEFAULT FALSE,
            order_index     INTEGER NOT NULL DEFAULT 0,
            active          BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX ix_field_definitions_active ON field_definitions (active, order_index);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS field_definitions;")

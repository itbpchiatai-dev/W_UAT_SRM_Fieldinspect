"""suppliers — baseline supplier master table

Revision ID: 0012_suppliers
Revises: 0011_db_connections
Create Date: 2026-06-29 00:00:00.000000
"""
from __future__ import annotations

from alembic import op

revision = "0012_suppliers"
down_revision = "0011_db_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE suppliers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(50) NOT NULL,
            name VARCHAR(255) NOT NULL,
            tax_id VARCHAR(20),
            contact_name VARCHAR(255),
            contact_email VARCHAR(255),
            contact_phone VARCHAR(50),
            address TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE UNIQUE INDEX uq_suppliers_code ON suppliers (code);
        CREATE INDEX ix_suppliers_code ON suppliers (code);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS suppliers;")

"""user_supplier_link — add supplier_id FK + is_supplier_admin to users

Revision ID: 0013_user_supplier_link
Revises: 0012_suppliers
Create Date: 2026-06-29 01:00:00.000000
"""
from __future__ import annotations

from alembic import op

revision = "0013_user_supplier_link"
down_revision = "0012_suppliers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE users
            ADD COLUMN supplier_id UUID REFERENCES suppliers(id) ON DELETE SET NULL,
            ADD COLUMN is_supplier_admin BOOLEAN NOT NULL DEFAULT FALSE;
        CREATE INDEX ix_users_supplier_id ON users (supplier_id);
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS ix_users_supplier_id;
        ALTER TABLE users
            DROP COLUMN IF EXISTS is_supplier_admin,
            DROP COLUMN IF EXISTS supplier_id;
    """)

"""user.is_approved + auth.auto_approve_new_users setting

Revision ID: 0007_user_is_approved
Revises: 0006_app_settings
Create Date: 2026-01-02 03:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_user_is_approved"
down_revision = "0006_app_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Column. Existing rows backfill to True so the bootstrap super-admin
    #    (created in earlier seed runs) stays able to sign in — only NEW
    #    accounts default to the un-approved state.
    op.add_column(
        "users",
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.execute("UPDATE users SET is_approved = true")
    # 2. Flip the column default so freshly-inserted rows are gated.
    op.alter_column("users", "is_approved", server_default=sa.text("false"))

    # 3. Seed the auth.auto_approve_new_users toggle (off by default).
    op.execute(
        """
        INSERT INTO app_settings (id, key, value, value_type, category, requires_role, updated_at)
        VALUES (gen_random_uuid(), 'auth.auto_approve_new_users', 'false'::jsonb,
                'boolean', 'auth', 'internal:super_admin', NOW())
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_settings WHERE key = 'auth.auto_approve_new_users'")
    op.drop_column("users", "is_approved")

"""app_settings — Pattern C admin config

Revision ID: 0006_app_settings
Revises: 0005_menus_permissions
Create Date: 2026-01-02 02:00:00.000000
"""
from __future__ import annotations

from alembic import op

revision = "0006_app_settings"
down_revision = "0005_menus_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE app_settings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            key VARCHAR(150) NOT NULL UNIQUE,
            value JSONB NOT NULL,
            value_type VARCHAR(20) NOT NULL DEFAULT 'string',
            category VARCHAR(50) NOT NULL DEFAULT 'general',
            description TEXT,
            requires_role VARCHAR(100) NOT NULL DEFAULT 'internal:super_admin',
            updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX ix_app_settings_key ON app_settings (key);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_settings CASCADE")

"""permissions catalog + menu_items + role_permissions + user_permission_overrides

Revision ID: 0005_menus_permissions
Revises: 0004_auth_core
Create Date: 2026-01-02 01:00:00.000000
"""
from __future__ import annotations

from alembic import op

revision = "0005_menus_permissions"
down_revision = "0004_auth_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE permissions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            key VARCHAR(100) NOT NULL UNIQUE,
            display_name VARCHAR(150) NOT NULL,
            category VARCHAR(50) NOT NULL DEFAULT 'other',
            is_menu BOOLEAN NOT NULL DEFAULT FALSE
        );
        CREATE INDEX ix_permissions_key ON permissions (key);

        CREATE TABLE role_permissions (
            role_id UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
            permission_id UUID NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
            PRIMARY KEY (role_id, permission_id)
        );

        CREATE TABLE user_permission_overrides (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            permission_id UUID NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
            granted BOOLEAN NOT NULL DEFAULT TRUE,
            granted_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reason TEXT
        );
        CREATE INDEX ix_upo_user_id ON user_permission_overrides (user_id);
        CREATE INDEX ix_upo_permission_id ON user_permission_overrides (permission_id);

        CREATE TABLE menu_items (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            key VARCHAR(100) NOT NULL UNIQUE,
            label_th VARCHAR(150) NOT NULL,
            label_en VARCHAR(150) NOT NULL,
            icon VARCHAR(50),
            path VARCHAR(200) NOT NULL,
            parent_id UUID REFERENCES menu_items(id) ON DELETE CASCADE,
            order_index INTEGER NOT NULL DEFAULT 0,
            required_permission_key VARCHAR(100) NOT NULL,
            is_system BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX ix_menu_items_key ON menu_items (key);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS menu_items CASCADE")
    op.execute("DROP TABLE IF EXISTS user_permission_overrides CASCADE")
    op.execute("DROP TABLE IF EXISTS role_permissions CASCADE")
    op.execute("DROP TABLE IF EXISTS permissions CASCADE")

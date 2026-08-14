"""auth core — users + roles + role_permissions + user_roles + user_permission_overrides

Revision ID: 0004_auth_core
Revises: 0003_ai_call_logs
Create Date: 2026-01-02 00:00:00.000000
"""
from __future__ import annotations

from alembic import op

revision = "0004_auth_core"
down_revision = "0003_ai_call_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email VARCHAR(255) NOT NULL UNIQUE,
            full_name VARCHAR(255) NOT NULL DEFAULT '',
            auth_provider VARCHAR(20) NOT NULL,
            password_hash VARCHAR(255),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            email_verified BOOLEAN NOT NULL DEFAULT FALSE,
            totp_secret VARCHAR(255),
            last_login_at TIMESTAMPTZ,
            business_unit_ids VARCHAR[] NOT NULL DEFAULT '{}'::varchar[],
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX ix_users_email ON users (email);

        CREATE TABLE roles (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(100) NOT NULL UNIQUE,
            display_name VARCHAR(150) NOT NULL,
            provider_scope VARCHAR(20) NOT NULL DEFAULT 'any',
            is_system BOOLEAN NOT NULL DEFAULT FALSE,
            description VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX ix_roles_name ON roles (name);

        CREATE TABLE user_roles (
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role_id UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, role_id)
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_roles CASCADE")
    op.execute("DROP TABLE IF EXISTS roles CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")

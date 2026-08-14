"""db_connections — admin-managed external PostgreSQL connection targets

Revision ID: 0011_db_connections
Revises: 0010_normalize_user_emails
Create Date: 2026-01-02 07:00:00.000000
"""
from __future__ import annotations

from alembic import op


revision = "0011_db_connections"
down_revision = "0010_normalize_user_emails"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE db_connections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(100) NOT NULL UNIQUE,
            description TEXT,
            host VARCHAR(255) NOT NULL,
            port INTEGER NOT NULL DEFAULT 5432,
            database VARCHAR(128) NOT NULL,
            username VARCHAR(128) NOT NULL,
            password_encrypted TEXT NOT NULL,
            ssl_mode VARCHAR(20) NOT NULL DEFAULT 'prefer',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            allow_write BOOLEAN NOT NULL DEFAULT FALSE,
            last_tested_at TIMESTAMPTZ,
            last_test_status VARCHAR(20),
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX ix_db_connections_name ON db_connections (name);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS db_connections CASCADE")

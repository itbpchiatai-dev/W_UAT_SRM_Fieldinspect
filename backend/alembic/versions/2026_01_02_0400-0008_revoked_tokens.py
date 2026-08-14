"""revoked_tokens — refresh-token blocklist

Revision ID: 0008_revoked_tokens
Revises: 0007_user_is_approved
Create Date: 2026-01-02 04:00:00.000000

Closes Deep-Audit HIGH-3 — stolen refresh tokens can now be invalidated
server-side at logout / rotation / password-change time instead of
waiting for their natural 7-day exp.
"""
from __future__ import annotations

from alembic import op


revision = "0008_revoked_tokens"
down_revision = "0007_user_is_approved"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE revoked_tokens (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            jti VARCHAR(64) NOT NULL UNIQUE,
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            expires_at TIMESTAMPTZ NOT NULL,
            reason VARCHAR(40) NOT NULL DEFAULT 'logout',
            revoked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX ix_revoked_tokens_jti ON revoked_tokens (jti);
        CREATE INDEX ix_revoked_tokens_user_id ON revoked_tokens (user_id);
        CREATE INDEX ix_revoked_tokens_expires_at ON revoked_tokens (expires_at);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS revoked_tokens CASCADE")

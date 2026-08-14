"""user moderation fields — is_rejected / rejection_reason / approval token

Revision ID: 0009_user_approval_fields
Revises: 0008_revoked_tokens
Create Date: 2026-01-02 05:00:00.000000

The User model + /users approve/reject flow reference is_rejected,
rejection_reason, approval_token_hash and approval_token_expires_at, but no
migration added them. Without this, `alembic upgrade head` produces a users
table missing these columns and `python -m app.seed` fails with
UndefinedColumnError before the bootstrap super-admin is created.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0009_user_approval_fields"
down_revision = "0008_revoked_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_rejected", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
    )
    op.add_column(
        "users",
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("approval_token_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("approval_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_approval_token_hash", "users", ["approval_token_hash"])


def downgrade() -> None:
    op.drop_index("ix_users_approval_token_hash", table_name="users")
    op.drop_column("users", "approval_token_expires_at")
    op.drop_column("users", "approval_token_hash")
    op.drop_column("users", "rejection_reason")
    op.drop_column("users", "is_rejected")

"""normalize user emails to lowercase

Revision ID: 0010_normalize_user_emails
Revises: 0009_user_approval_fields
Create Date: 2026-01-02 06:00:00.000000
"""
from __future__ import annotations

from alembic import op


revision = "0010_normalize_user_emails"
down_revision = "0009_user_approval_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM users
                GROUP BY lower(email)
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'users.email contains case-insensitive duplicates; merge them before normalizing';
            END IF;
        END $$;
    """)
    op.execute("UPDATE users SET email = lower(trim(email)) WHERE email <> lower(trim(email))")


def downgrade() -> None:
    # Email canonicalization is intentionally irreversible.
    pass

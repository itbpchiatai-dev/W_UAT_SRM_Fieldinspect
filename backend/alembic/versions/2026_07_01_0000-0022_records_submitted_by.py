"""records submitted_by — field-attribution info, separate from recorded_by_id.

recorded_by_id stays the logged-in user (audit/security). submitted_by_code /
submitted_by_name are free-text attribution for whoever actually filled the
form on-site (may not have a login account) — never used for authorization.

Phased-safe: add nullable, backfill existing rows with a placeholder, then
enforce NOT NULL — 'UNKNOWN' is a placeholder, not a real staff code.

Revision ID: 0022_records_submitted_by
Revises: 0021_records_scores
Create Date: 2026-07-01 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_records_submitted_by"
down_revision = "0021_records_scores"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("records", sa.Column("submitted_by_code", sa.String(length=50), nullable=True))
    op.add_column("records", sa.Column("submitted_by_name", sa.String(length=255), nullable=True))
    op.execute("UPDATE records SET submitted_by_code = 'UNKNOWN' WHERE submitted_by_code IS NULL")
    op.alter_column("records", "submitted_by_code", nullable=False)


def downgrade() -> None:
    op.drop_column("records", "submitted_by_name")
    op.drop_column("records", "submitted_by_code")

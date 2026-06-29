"""Add GPS coordinates and photo_urls to records.

Revision ID: 0017_record_gps_photos
Revises: 0016_rls
Create Date: 2026-06-29 05:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0017_record_gps_photos"
down_revision = "0016_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("records", sa.Column("latitude", sa.Numeric(10, 7), nullable=True))
    op.add_column("records", sa.Column("longitude", sa.Numeric(10, 7), nullable=True))
    op.add_column(
        "records",
        sa.Column("photo_urls", JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("records", "photo_urls")
    op.drop_column("records", "longitude")
    op.drop_column("records", "latitude")

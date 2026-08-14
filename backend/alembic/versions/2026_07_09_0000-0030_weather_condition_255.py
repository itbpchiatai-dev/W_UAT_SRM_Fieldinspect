"""Widen records.weather_condition varchar(100) -> varchar(255).

สภาพอากาศ became multi-select on the inspection forms: the UI now stores a
", "-joined list of master-data weather values in this same column (kept a
plain string deliberately — every reader/report/Excel export displays it
as text, and a JSONB array would have forced all of them to change for no
reader benefit). All ~14 seeded weather options joined together can exceed
100 chars, so the old cap could reject a legitimate selection; 255 fits
every possible combination of the seeded options with ample headroom.

Widening a varchar in Postgres is a metadata-only change — instant, no
table rewrite, no data touched. Downgrade truncates nothing by itself but
would fail if any row exceeds 100 chars by then; acceptable for a
dev-stage downgrade path (same stance as other length changes here).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_weather_condition_255"
down_revision = "0029_seed_report_menus"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "records",
        "weather_condition",
        existing_type=sa.String(length=100),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "records",
        "weather_condition",
        existing_type=sa.String(length=255),
        type_=sa.String(length=100),
        existing_nullable=True,
    )

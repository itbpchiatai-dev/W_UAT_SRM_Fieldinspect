"""records reshape — yield/list-driven inspection model (Step 12.5).

Pivots the record from a pest/disease-detail model to a fast, list-driven
field model (options from master_data) with a Yield % (0–150, default 100).
crop_type is renamed to crop (data preserved); growth_stage / weather_condition
/ irrigation_method / recommendation / notes / lat-lng / photo_urls / custom_fields
are kept. Detail/number columns are dropped (dev demo data in them is lost).

Revision ID: 0020_records_reshape
Revises: 0019_master_data
Create Date: 2026-06-30 02:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_records_reshape"
down_revision = "0019_master_data"
branch_labels = None
depends_on = None

_DROP = [
    "area_rai", "plant_height_cm",
    "pest_found", "pest_detail", "pest_severity",
    "disease_found", "disease_detail", "disease_severity",
    "weed_severity", "fertilizer_used", "fertilizer_amount_kg",
]


def upgrade() -> None:
    # Preserve crop data under the new key.
    op.alter_column("records", "crop_type", new_column_name="crop")

    for col in _DROP:
        op.drop_column("records", col)

    op.add_column("records", sa.Column("variety", sa.String(100), nullable=True))
    op.add_column("records", sa.Column("planting_date", sa.Date(), nullable=True))
    op.add_column("records", sa.Column(
        "yield_pct", sa.Numeric(5, 1), nullable=True, server_default="100"))
    op.add_column("records", sa.Column("field_prep_level", sa.String(50), nullable=True))
    op.add_column("records", sa.Column("care_level", sa.String(50), nullable=True))
    op.add_column("records", sa.Column("pest_status", sa.String(50), nullable=True))
    op.add_column("records", sa.Column("disease_status", sa.String(50), nullable=True))
    op.add_column("records", sa.Column("weed_status", sa.String(50), nullable=True))
    op.add_column("records", sa.Column("fertilizer", sa.String(100), nullable=True))


def downgrade() -> None:
    for col in ("variety", "planting_date", "yield_pct", "field_prep_level",
                "care_level", "pest_status", "disease_status", "weed_status", "fertilizer"):
        op.drop_column("records", col)

    op.add_column("records", sa.Column("area_rai", sa.Numeric(10, 2), nullable=True))
    op.add_column("records", sa.Column("plant_height_cm", sa.Numeric(8, 2), nullable=True))
    op.add_column("records", sa.Column("pest_found", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("records", sa.Column("pest_detail", sa.Text(), nullable=True))
    op.add_column("records", sa.Column("pest_severity", sa.SmallInteger(), nullable=True))
    op.add_column("records", sa.Column("disease_found", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("records", sa.Column("disease_detail", sa.Text(), nullable=True))
    op.add_column("records", sa.Column("disease_severity", sa.SmallInteger(), nullable=True))
    op.add_column("records", sa.Column("weed_severity", sa.SmallInteger(), nullable=True))
    op.add_column("records", sa.Column("fertilizer_used", sa.String(255), nullable=True))
    op.add_column("records", sa.Column("fertilizer_amount_kg", sa.Numeric(10, 2), nullable=True))

    op.alter_column("records", "crop", new_column_name="crop_type")

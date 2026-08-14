"""records scores — replace status/list assessment fields with 1-10 scores.

Per the authoritative field spec, the on-site condition assessment moves from
7 list/status fields (field_prep_level, care_level, pest_status, disease_status,
weed_status, irrigation_method, fertilizer) to 4 numeric 1-10 score fields
(field_prep_score, weather_score, care_score, variety_resistance_score).
weather_condition (separate basic-info field) is unchanged.

Revision ID: 0021_records_scores
Revises: 0020_records_reshape
Create Date: 2026-06-30 03:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_records_scores"
down_revision = "0020_records_reshape"
branch_labels = None
depends_on = None

_DROP = [
    "field_prep_level", "care_level",
    "pest_status", "disease_status", "weed_status",
    "irrigation_method", "fertilizer",
]

_SCORES = [
    "field_prep_score", "weather_score", "care_score", "variety_resistance_score",
]


def upgrade() -> None:
    for col in _DROP:
        op.drop_column("records", col)

    for col in _SCORES:
        op.add_column("records", sa.Column(col, sa.SmallInteger(), nullable=True))

    op.create_check_constraint(
        "ck_records_scores_range",
        "records",
        " AND ".join(f"({col} IS NULL OR ({col} BETWEEN 1 AND 10))" for col in _SCORES),
    )


def downgrade() -> None:
    op.drop_constraint("ck_records_scores_range", "records", type_="check")

    for col in _SCORES:
        op.drop_column("records", col)

    op.add_column("records", sa.Column("field_prep_level", sa.String(50), nullable=True))
    op.add_column("records", sa.Column("care_level", sa.String(50), nullable=True))
    op.add_column("records", sa.Column("pest_status", sa.String(50), nullable=True))
    op.add_column("records", sa.Column("disease_status", sa.String(50), nullable=True))
    op.add_column("records", sa.Column("weed_status", sa.String(50), nullable=True))
    op.add_column("records", sa.Column("irrigation_method", sa.String(100), nullable=True))
    op.add_column("records", sa.Column("fertilizer", sa.String(100), nullable=True))

"""plots current status — Plot Master + Current Status (round 11).

Adds a denormalized "current status" snapshot to `plots`, mirroring the
per-visit fields already on `records` so a plot's latest known state is
readable without joining/aggregating records:

  - current_crop / current_variety / current_lot_no / current_planting_date
    — current planting-cycle identity. lot_no is new (no records column
      backs it yet); the other two mirror records.crop/variety/planting_date.
  - current_stage — mirrors records.growth_stage.
  - current_yield_pct — mirrors records.yield_pct.
  - current_field_prep_score / current_weather_score / current_care_score /
    current_variety_resistance_score — mirrors records' 4 separate 1-10
    scores one-for-one (not collapsed into a single aggregate), same
    ck_..._range check-constraint pattern as records (see migration 0021).
  - current_gps_lat / current_gps_lng — GPS captured at the last visit
    (distinct from plots.latitude/longitude, the plot's registered/reference
    location, which is unaffected).
  - last_inspected_at / last_inspected_by_code / last_inspection_record_id —
    when, by whom (field-attribution code, not a login identity — mirrors
    records.submitted_by_code), and which record produced this snapshot.
    last_inspection_record_id is FK'd to records.id (ON DELETE SET NULL, not
    RESTRICT — the plot must be able to outlive a since-deleted record) so
    the full record can still be looked up rather than duplicating every
    field records already has (deliberately no photo columns — photo_urls
    stays record-only; read it via this FK instead of denormalizing a URL
    that could go stale if the record's own photos change).

Schema/model only this round — every column stays NULL until a later round
wires up the sync-on-record-create logic (both the logged-in and public
record-creation paths). All columns nullable: existing plots and any plot
with zero records yet have nothing to show here.

Revision ID: 0024_plots_current_status
Revises: 0023_plots_inspection_code
Create Date: 2026-07-03 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0024_plots_current_status"
down_revision = "0023_plots_inspection_code"
branch_labels = None
depends_on = None

_SCORES = [
    "current_field_prep_score",
    "current_weather_score",
    "current_care_score",
    "current_variety_resistance_score",
]


def upgrade() -> None:
    op.add_column("plots", sa.Column("current_crop", sa.String(100), nullable=True))
    op.add_column("plots", sa.Column("current_variety", sa.String(100), nullable=True))
    op.add_column("plots", sa.Column("current_lot_no", sa.String(100), nullable=True))
    op.add_column("plots", sa.Column("current_planting_date", sa.Date(), nullable=True))
    op.add_column("plots", sa.Column("current_stage", sa.String(100), nullable=True))
    op.add_column("plots", sa.Column("current_yield_pct", sa.Numeric(5, 1), nullable=True))

    for col in _SCORES:
        op.add_column("plots", sa.Column(col, sa.SmallInteger(), nullable=True))
    # Bare name here — Base.metadata's naming_convention (app/db/base.py)
    # auto-prepends "ck_plots_", producing "ck_plots_current_scores_range".
    # (records' equivalent constraint in migration 0021 passed the already-
    # prefixed name, so it got double-prefixed to
    # ck_records_ck_records_scores_range — a pre-existing wart, left as-is
    # there since it's already applied; not repeated here.)
    op.create_check_constraint(
        "current_scores_range",
        "plots",
        " AND ".join(f"({col} IS NULL OR ({col} BETWEEN 1 AND 10))" for col in _SCORES),
    )

    op.add_column("plots", sa.Column("current_gps_lat", sa.Numeric(10, 7), nullable=True))
    op.add_column("plots", sa.Column("current_gps_lng", sa.Numeric(10, 7), nullable=True))

    op.add_column("plots", sa.Column("last_inspected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("plots", sa.Column("last_inspected_by_code", sa.String(50), nullable=True))
    op.add_column(
        "plots",
        sa.Column("last_inspection_record_id", PgUUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_plots_last_inspection_record_id_records",
        "plots", "records",
        ["last_inspection_record_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_plots_last_inspection_record_id", "plots", ["last_inspection_record_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_plots_last_inspection_record_id", table_name="plots")
    op.drop_constraint("fk_plots_last_inspection_record_id_records", "plots", type_="foreignkey")
    op.drop_column("plots", "last_inspection_record_id")
    op.drop_column("plots", "last_inspected_by_code")
    op.drop_column("plots", "last_inspected_at")

    op.drop_column("plots", "current_gps_lng")
    op.drop_column("plots", "current_gps_lat")

    # Bare name again — op.drop_constraint re-applies the same naming
    # convention templating op.create_check_constraint did (see upgrade()).
    op.drop_constraint("current_scores_range", "plots", type_="check")
    for col in _SCORES:
        op.drop_column("plots", col)

    op.drop_column("plots", "current_yield_pct")
    op.drop_column("plots", "current_stage")
    op.drop_column("plots", "current_planting_date")
    op.drop_column("plots", "current_lot_no")
    op.drop_column("plots", "current_variety")
    op.drop_column("plots", "current_crop")

"""plots yield planning — plant_count / expected_yield_full / expected_yield_unit (round 17).

Adds the "base/master" yield-planning fields to `plots`, distinct from the
current-status snapshot added in migration 0024:

  - plant_count — number of plants/units planted this cycle.
  - expected_yield_full — the expected yield when current_yield_pct is 100%.
  - expected_yield_unit — free-text unit label (e.g. kg/g/ตัน/ผล/ลัง), not an
    enum — different crops use different units and the list isn't fixed.

These three are set directly by an admin on Plot Create/Edit and are never
touched by `sync_current_status_from_record` (same treatment as
`current_lot_no` since migration 0024/round 12) — there is no records column
to source them from, unlike current_yield_pct which mirrors the latest
record's yield_pct.

"Current expected yield" (expected_yield_full * current_yield_pct / 100) is
deliberately NOT a stored column — it's derived from two values that already
exist (this migration's expected_yield_full, and current_yield_pct from
migration 0024), computed on demand in the API/frontend instead of kept in
sync as a fourth column.

All three columns are nullable — existing plots (and the mock-seeded ones)
have no source for this data until an admin fills it in; see
app/db/backfill_plot_current_status.py for the separate, unrelated backfill
of the *current-status* snapshot from existing record history (round 17
problem #1/#4), which this migration does not attempt for these fields.

Revision ID: 0025_plots_yield_planning
Revises: 0024_plots_current_status
Create Date: 2026-07-04 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_plots_yield_planning"
down_revision = "0024_plots_current_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plots", sa.Column("plant_count", sa.Integer(), nullable=True))
    op.add_column("plots", sa.Column("expected_yield_full", sa.Numeric(12, 2), nullable=True))
    op.add_column("plots", sa.Column("expected_yield_unit", sa.String(20), nullable=True))
    op.create_check_constraint(
        "plant_count_non_negative", "plots", "plant_count IS NULL OR plant_count >= 0",
    )
    op.create_check_constraint(
        "expected_yield_full_non_negative", "plots",
        "expected_yield_full IS NULL OR expected_yield_full >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("expected_yield_full_non_negative", "plots", type_="check")
    op.drop_constraint("plant_count_non_negative", "plots", type_="check")
    op.drop_column("plots", "expected_yield_unit")
    op.drop_column("plots", "expected_yield_full")
    op.drop_column("plots", "plant_count")

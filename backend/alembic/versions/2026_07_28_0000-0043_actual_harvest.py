"""plot_cycles actual harvest fields (round 8-7A) — captures the REAL
harvested yield when a cycle is finalized/closed, distinct from
final_estimated_yield (an ESTIMATE frozen from the last inspection,
migration 0038 — unchanged, still populated the same way by
plot_cycle_repository.close_cycle for every close path, this one included).

Five nullable columns on plot_cycles:

  harvest_yield             NUMERIC(14, 2) — total yield at harvest, BEFORE
                             cleaning/sorting.
  final_yield_after_clean   NUMERIC(14, 2) — net actual yield AFTER cleaning —
                             the true final production figure.
  final_yield_unit          VARCHAR(20)    — unit for the two actual-yield
                             fields above (e.g. kg, g, ตัน, ผล, ลัง). Stored
                             verbatim — no unit conversion is ever applied.
  harvest_date              DATE           — the date harvest actually
                             happened (distinct from planting_date).
  final_note                TEXT           — optional free-text note.

Reuses the EXISTING final_inspection_record_id (migration 0038) as the
pointer to the inspection record a finalize snapshot came from — no new
record-reference column added this round.

Deliberately NULLABLE with NO backfill: every existing cycle (open, or
already closed by any prior action) keeps all five NULL and must read
without error. A cycle closed by any non-final_plot path (single-cycle
close endpoint, rollover, Excel start_next_cycle/close_and_start_new_cycle)
is UNAFFECTED — those paths never write these columns.

Constraints (ORM `ck_` naming convention, mirrored in
app/db/models/plot_cycle.py so model metadata and this migration agree):

  - ck_plot_cycles_harvest_yield_non_negative
        harvest_yield IS NULL OR harvest_yield >= 0
  - ck_plot_cycles_final_yield_after_clean_non_negative
        final_yield_after_clean IS NULL OR final_yield_after_clean >= 0
  - ck_plot_cycles_actual_harvest_all_or_none
        harvest_yield, final_yield_after_clean, final_yield_unit, and
        harvest_date must be ALL NULL or ALL NOT NULL together — a cycle
        is never left half-recorded. final_note is intentionally excluded
        from this rule (always independently optional, per spec).

Additive only: five nullable columns + three CHECKs, NO backfill, NO other
table touched, NO RLS policy/grant/role/ownership change. Transactional DDL
— any failure rolls the whole migration back.

Revision ID: 0043_actual_harvest
Revises: 0042_plot_cycle_po_lot
Create Date: 2026-07-28 00:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0043_actual_harvest"
down_revision = "0042_plot_cycle_po_lot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Five nullable columns + three CHECKs. Nullable so every existing row
    # (open or already closed) reads without backfill.
    op.execute(
        """
        ALTER TABLE plot_cycles ADD COLUMN harvest_yield NUMERIC(14, 2);
        ALTER TABLE plot_cycles ADD COLUMN final_yield_after_clean NUMERIC(14, 2);
        ALTER TABLE plot_cycles ADD COLUMN final_yield_unit VARCHAR(20);
        ALTER TABLE plot_cycles ADD COLUMN harvest_date DATE;
        ALTER TABLE plot_cycles ADD COLUMN final_note TEXT;

        ALTER TABLE plot_cycles ADD CONSTRAINT ck_plot_cycles_harvest_yield_non_negative
            CHECK (harvest_yield IS NULL OR harvest_yield >= 0);
        ALTER TABLE plot_cycles ADD CONSTRAINT ck_plot_cycles_final_yield_after_clean_non_negative
            CHECK (final_yield_after_clean IS NULL OR final_yield_after_clean >= 0);
        ALTER TABLE plot_cycles ADD CONSTRAINT ck_plot_cycles_actual_harvest_all_or_none
            CHECK (
                (harvest_yield IS NULL AND final_yield_after_clean IS NULL
                 AND final_yield_unit IS NULL AND harvest_date IS NULL)
                OR
                (harvest_yield IS NOT NULL AND final_yield_after_clean IS NOT NULL
                 AND final_yield_unit IS NOT NULL AND harvest_date IS NOT NULL)
            );
        """
    )


def downgrade() -> None:
    # Reverse dependency order: CHECKs, then the five columns.
    op.execute(
        """
        ALTER TABLE plot_cycles DROP CONSTRAINT IF EXISTS ck_plot_cycles_actual_harvest_all_or_none;
        ALTER TABLE plot_cycles DROP CONSTRAINT IF EXISTS ck_plot_cycles_final_yield_after_clean_non_negative;
        ALTER TABLE plot_cycles DROP CONSTRAINT IF EXISTS ck_plot_cycles_harvest_yield_non_negative;
        ALTER TABLE plot_cycles DROP COLUMN IF EXISTS final_note;
        ALTER TABLE plot_cycles DROP COLUMN IF EXISTS harvest_date;
        ALTER TABLE plot_cycles DROP COLUMN IF EXISTS final_yield_unit;
        ALTER TABLE plot_cycles DROP COLUMN IF EXISTS final_yield_after_clean;
        ALTER TABLE plot_cycles DROP COLUMN IF EXISTS harvest_yield;
        """
    )

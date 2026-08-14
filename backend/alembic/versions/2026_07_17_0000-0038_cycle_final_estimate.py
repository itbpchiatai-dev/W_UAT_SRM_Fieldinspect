"""plot_cycles final estimated-yield snapshot (round 8-2.8A).

Freezes a cycle's "ผลผลิตประมาณการสุดท้าย" (final ESTIMATED yield — NOT actual
harvested yield) at close time so the number survives after the plot's live
snapshot is cleared and a new cycle starts. Three nullable columns:

  final_yield_pct           — the closing inspection's yield %, 0–150
  final_estimated_yield     — expected_yield_full × final_yield_pct / 100, >= 0
  final_inspection_record_id — the record the snapshot was taken from
                               (FK records.id, ON DELETE SET NULL)

Deliberately NULLABLE with NO backfill: cycles closed before this migration
keep all three NULL and must read without error. Adds only columns + a FK +
two CHECK constraints — no data mutation, no RLS/policy/grant change.

Revision ID: 0038_cycle_final_estimate
Revises: 0037_rls_uuid_guard
Create Date: 2026-07-17 00:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0038_cycle_final_estimate"
down_revision = "0037_rls_uuid_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable column adds — no table rewrite, no backfill, safe on a populated
    # table. RLS + grants on plot_cycles (migration 0035) are unaffected by a
    # column/constraint add.
    op.execute(
        """
        ALTER TABLE plot_cycles
            ADD COLUMN final_yield_pct NUMERIC(5, 1),
            ADD COLUMN final_estimated_yield NUMERIC(14, 2),
            ADD COLUMN final_inspection_record_id UUID;
        """
    )
    # FK + CHECKs use the ORM naming convention (ck_/fk_%table%…) so the model
    # metadata and the DB agree (test_cycle_final_estimate asserts both sides).
    op.execute(
        """
        ALTER TABLE plot_cycles
            ADD CONSTRAINT fk_plot_cycles_final_inspection_record_id_records
                FOREIGN KEY (final_inspection_record_id)
                REFERENCES records(id) ON DELETE SET NULL,
            ADD CONSTRAINT ck_plot_cycles_final_yield_pct_range
                CHECK (final_yield_pct IS NULL
                       OR (final_yield_pct >= 0 AND final_yield_pct <= 150)),
            ADD CONSTRAINT ck_plot_cycles_final_estimated_yield_non_negative
                CHECK (final_estimated_yield IS NULL OR final_estimated_yield >= 0);
        """
    )


def downgrade() -> None:
    # Drop constraints (FK + both CHECKs) before the columns they reference.
    op.execute(
        """
        ALTER TABLE plot_cycles
            DROP CONSTRAINT IF EXISTS ck_plot_cycles_final_estimated_yield_non_negative,
            DROP CONSTRAINT IF EXISTS ck_plot_cycles_final_yield_pct_range,
            DROP CONSTRAINT IF EXISTS fk_plot_cycles_final_inspection_record_id_records,
            DROP COLUMN IF EXISTS final_inspection_record_id,
            DROP COLUMN IF EXISTS final_estimated_yield,
            DROP COLUMN IF EXISTS final_yield_pct;
        """
    )

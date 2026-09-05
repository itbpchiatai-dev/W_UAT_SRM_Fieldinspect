"""records: ผลผลิตหลังทำความสะอาด captured in the field (round C).

Until now the two ACTUAL harvest figures a cycle ends with —
plot_cycles.harvest_yield and plot_cycles.final_yield_after_clean — could only
be entered by an admin, through the Excel `final_plot` action (migration 0043).
Round C moves the DATA CAPTURE to the people who actually weigh the crop: the
field inspection form. Round D will then let an admin confirm and close the
cycle from those figures instead of retyping them.

  final_yield_after_clean  NUMERIC(14,2) NULL

One column, not two, and that is the point of the round-C design: the kg a
record already carries (yield_quantity_kg, migration 0044) IS "ผลผลิตที่เก็บได้"
— the user confirmed the harvest-stage figure and the pre-cleaning figure are
one and the same number, so reusing it avoids two nearly-identical kg boxes on
one screen. Only the AFTER-cleaning figure is genuinely new.

NUMERIC(14,2) matches plot_cycles.final_yield_after_clean (migration 0043)
exactly, because that is where this value is ultimately copied to when the
cycle is closed. yield_quantity_kg is NUMERIC(12,2) — deliberately left alone;
this column is not a mirror of it.

Deliberately NOT added:
  - a harvest_yield column. That is yield_quantity_kg, already here.
  - a harvest_date column. records.record_date already says when the
    inspection happened, and the harvest date a closed cycle stores comes from
    the เก็บเกี่ยว record's own date.
  - a unit column. The figure is always kilograms
    (plot_import.FINAL_PLOT_FIXED_YIELD_UNIT), server-stamped, never chosen.

Additive only: nullable, NO default, NO backfill (every existing record reads
NULL), NO data mutation, NO change to any other table, NO index, NO RLS
policy/grant/role/ownership change. The CHECK mirrors
ck_plot_cycles_final_yield_after_clean_non_negative so the value cannot become
negative on its way from a record to the cycle it is copied into.
Transactional DDL — any failure rolls the whole migration back.

Revision ID: 0054_records_final_yield_clean
Revises: 0053_plots_auto_plot_code
Create Date: 2026-09-04 01:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0054_records_final_yield_clean"
down_revision = "0053_plots_auto_plot_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE records ADD COLUMN final_yield_after_clean NUMERIC(14, 2);

        ALTER TABLE records ADD CONSTRAINT ck_records_final_yield_after_clean_non_negative
            CHECK (final_yield_after_clean IS NULL OR final_yield_after_clean >= 0);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE records DROP CONSTRAINT IF EXISTS ck_records_final_yield_after_clean_non_negative;
        ALTER TABLE records DROP COLUMN IF EXISTS final_yield_after_clean;
        """
    )

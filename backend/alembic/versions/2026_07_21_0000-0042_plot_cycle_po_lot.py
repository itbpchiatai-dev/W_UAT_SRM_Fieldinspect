"""plot_cycles PO / P.Code + Auto Lot metadata (round 8-5A) — cycle-level
purchase-order number, product code, and the bookkeeping for a
server-generated Auto Lot number.

Adds four nullable columns + three CHECK constraints + one partial unique
index to `plot_cycles`:

  1. po_number      VARCHAR(100) NULL — purchase-order number for THIS cycle,
                    stored normalized (trimmed, upper-cased) by the app layer.
                    Independent of lot_no: a cycle may carry a PO whether its
                    lot_no is manual, auto, or absent.
  2. p_code         VARCHAR(100) NULL — product code for this cycle, trimmed
                    (case preserved). Never part of the Auto Lot formula.
  3. lot_no_source  VARCHAR(20)  NULL — how lot_no was derived:
                    'auto'   → server-generated {PO}-{plotCode}-{running}
                    'manual' → a value the user/import supplied verbatim
                    'legacy' → reserved for pre-8-5A/backfilled data (this
                               migration writes NO 'legacy' rows — see below)
                    NULL     → no lot_no / legacy cycles predating this round
  4. lot_running_no INTEGER      NULL — the running number component of an Auto
                    Lot, unique per (plot_id, po_number) among 'auto' cycles.
                    NULL for manual/absent lots.

Constraints (names via the ORM ck_/uq_plot_cycles_* convention in
app/db/base.py so model metadata and this migration agree — mirrored in
app/db/models/plot_cycle.py):

  - ck_plot_cycles_lot_no_source_allowed
        lot_no_source IS NULL OR lot_no_source IN ('auto','manual','legacy')
  - ck_plot_cycles_lot_running_no_positive
        lot_running_no IS NULL OR lot_running_no >= 1
  - ck_plot_cycles_auto_lot_requires_fields
        an 'auto' cycle must carry po_number, lot_no AND lot_running_no (the
        Auto Lot bookkeeping is complete or the row isn't 'auto'). NULL-safe:
        non-'auto'/NULL source rows are unconstrained.
  - uq_plot_cycles_auto_lot_running (partial UNIQUE, WHERE lot_no_source =
        'auto') on (plot_id, po_number, lot_running_no) — the final
        concurrency backstop so two concurrent auto-lot creates for the same
        plot+PO can't both claim the same running number. Only 'auto' rows
        participate; manual/legacy/NULL rows are never constrained.

Deliberately NO global unique on lot_no this round: plot_code is unique only
within a supplier (uq_plots_supplier_code on (supplier_id, plot_code)), so a
cross-plot lot_no uniqueness scope needs a business decision first (round
8-5B+).

Additive only: all four columns nullable, NO backfill (existing rows — of
which the audit found zero in dev — stay NULL on every new column), NO data
mutation/reseed, NO change to any other table, NO RLS policy/grant/role/
ownership change. Transactional DDL — any failure rolls the whole migration
back.

Revision ID: 0042_plot_cycle_po_lot
Revises: 0041_offline_submission
Create Date: 2026-07-21 00:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0042_plot_cycle_po_lot"
down_revision = "0041_offline_submission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Four nullable columns + three CHECKs + one partial unique index. Nullable
    # so existing rows read without backfill; the partial unique index and the
    # auto-lot CHECK only ever constrain 'auto' rows.
    op.execute(
        """
        ALTER TABLE plot_cycles ADD COLUMN po_number VARCHAR(100);
        ALTER TABLE plot_cycles ADD COLUMN p_code VARCHAR(100);
        ALTER TABLE plot_cycles ADD COLUMN lot_no_source VARCHAR(20);
        ALTER TABLE plot_cycles ADD COLUMN lot_running_no INTEGER;

        ALTER TABLE plot_cycles ADD CONSTRAINT ck_plot_cycles_lot_no_source_allowed
            CHECK (lot_no_source IS NULL OR lot_no_source IN ('auto', 'manual', 'legacy'));
        ALTER TABLE plot_cycles ADD CONSTRAINT ck_plot_cycles_lot_running_no_positive
            CHECK (lot_running_no IS NULL OR lot_running_no >= 1);
        ALTER TABLE plot_cycles ADD CONSTRAINT ck_plot_cycles_auto_lot_requires_fields
            CHECK (
                lot_no_source IS DISTINCT FROM 'auto'
                OR (po_number IS NOT NULL AND lot_no IS NOT NULL AND lot_running_no IS NOT NULL)
            );

        CREATE UNIQUE INDEX uq_plot_cycles_auto_lot_running
            ON plot_cycles (plot_id, po_number, lot_running_no)
            WHERE lot_no_source = 'auto';
        """
    )


def downgrade() -> None:
    # Reverse dependency order: index, then CHECKs, then the four columns.
    op.execute(
        """
        DROP INDEX IF EXISTS uq_plot_cycles_auto_lot_running;
        ALTER TABLE plot_cycles DROP CONSTRAINT IF EXISTS ck_plot_cycles_auto_lot_requires_fields;
        ALTER TABLE plot_cycles DROP CONSTRAINT IF EXISTS ck_plot_cycles_lot_running_no_positive;
        ALTER TABLE plot_cycles DROP CONSTRAINT IF EXISTS ck_plot_cycles_lot_no_source_allowed;
        ALTER TABLE plot_cycles DROP COLUMN IF EXISTS lot_running_no;
        ALTER TABLE plot_cycles DROP COLUMN IF EXISTS lot_no_source;
        ALTER TABLE plot_cycles DROP COLUMN IF EXISTS p_code;
        ALTER TABLE plot_cycles DROP COLUMN IF EXISTS po_number;
        """
    )

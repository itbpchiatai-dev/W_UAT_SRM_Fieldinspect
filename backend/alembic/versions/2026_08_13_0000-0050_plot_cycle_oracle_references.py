"""plot_cycles Oracle/Ref Account reference fields (round 8-21A) — three
independent, OPTIONAL, free-text back-office reference columns on
`plot_cycles`:

  1. oracle_supplier_code  VARCHAR(255) NULL
  2. oracle_invoice        VARCHAR(255) NULL
  3. ref_account           VARCHAR(255) NULL

These live on PlotCycle (not Plot) because they may legitimately change from
one planting cycle to the next — they are not permanent facts about the
physical field. Independent of every other cycle field: none of the three
feeds the Auto Lot formula (lot_no/lot_no_source/lot_running_no/
auto_lot_series_key), the running number, or any Manual/Auto decision —
same "no business logic" contract as supplier_lot_no (migration 0048).

Additive only: all three columns nullable, NO default, NO backfill (every
existing row — active or historical — reads NULL on all three, exactly like
every prior additive PlotCycle column added this way), NO data mutation/
reseed, NO change to any other table, NO CHECK constraint, NO index, NO RLS
policy/grant/role/ownership change. Transactional DDL — any failure rolls
the whole migration back.

Revision ID: 0050_plot_cycle_oracle_refs
Revises: 0049_auto_lot_v2_integrity
Create Date: 2026-08-13 00:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0050_plot_cycle_oracle_refs"
down_revision = "0049_auto_lot_v2_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE plot_cycles ADD COLUMN oracle_supplier_code VARCHAR(255);
        ALTER TABLE plot_cycles ADD COLUMN oracle_invoice VARCHAR(255);
        ALTER TABLE plot_cycles ADD COLUMN ref_account VARCHAR(255);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE plot_cycles DROP COLUMN IF EXISTS ref_account;
        ALTER TABLE plot_cycles DROP COLUMN IF EXISTS oracle_invoice;
        ALTER TABLE plot_cycles DROP COLUMN IF EXISTS oracle_supplier_code;
        """
    )

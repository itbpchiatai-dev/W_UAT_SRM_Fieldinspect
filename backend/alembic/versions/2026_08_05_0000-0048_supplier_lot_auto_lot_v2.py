"""plot_cycles: supplier_lot_no + Auto Lot V2 series bookkeeping (round 8-12A).

Two independent additions, both purely additive to EXISTING data:

  1. supplier_lot_no VARCHAR(100) NULL — the SUPPLIER's own lot identifier for
     the cycle, stored beside (never mixed into) the system's lot_no. Optional,
     not unique, takes no part in the Auto Lot formula or the running number.

  2. auto_lot_series_key VARCHAR(255) NULL — INTERNAL, server-derived
     bookkeeping for Auto Lot V2. V2's formula is
     {cycleLabel}-{supplierCode}-{pCode}-{running}, which contains no plot
     code, so its running number must be counted per
     (supplier, cycleLabel, pCode) ACROSS plots — otherwise two plots of one
     supplier sharing a series would generate identical lot numbers. This
     column stores that series identity so the DB itself can enforce
     "one running number per series". Never client-writable, never exposed
     through the API.

Why a new column rather than re-deriving the series from existing columns: the
V1 rows already in the table were numbered per (plot, PO) and legitimately
REUSE running numbers across plots (verified on dev: SUP010-P001 has 01,02 and
SUP010-P002 also has 01,02 under the same PO). Retrofitting a supplier-wide
uniqueness rule onto them would make existing, correct data violate a new
index. auto_lot_series_key IS NULL is therefore the durable, unambiguous marker
of "V1 row" — no lot string is ever re-parsed to classify a row.

Existing-data rules honoured here:
  - NOT ONE existing row is updated. There is no UPDATE/INSERT/DELETE in this
    migration at all: both columns land NULL on every existing row, which is
    exactly the "legacy / not V2" state.
  - lot_no, lot_no_source and lot_running_no are never touched, so no lot is
    regenerated, renumbered or reformatted.
  - No backfill of supplier_lot_no: nobody has told us what any supplier's own
    lot number was, and inventing one would be worse than leaving it empty.

Constraint/index changes:

  a. ck_plot_cycles_auto_lot_requires_fields is REPLACED. The V1 rule required
     every 'auto' row to carry a po_number (V1's leading component). V2 rows
     have no PO in the formula, so the rule becomes: an 'auto' row must have
     lot_no + lot_running_no, and must be identifiable as either a V1 row
     (po_number present) or a V2 row (auto_lot_series_key present). Every
     existing 'auto' row satisfies this unchanged, via the po_number branch.

  b. uq_plot_cycles_auto_lot_running (plot_id, po_number, lot_running_no) WHERE
     lot_no_source='auto' is REPLACED by the same index with an additional
     "AND auto_lot_series_key IS NULL" predicate. It is NOT dropped: it keeps
     protecting every V1 row exactly as before. The extra predicate stops it
     from also constraining V2 rows, which still store a po_number and would
     otherwise collide across different (cycleLabel, pCode) series on one plot.

  c. uq_plot_cycles_auto_lot_series_running (auto_lot_series_key,
     lot_running_no) WHERE auto_lot_series_key IS NOT NULL is NEW — the V2
     backstop. A concurrent double-allocate surfaces as a clean IntegrityError
     the endpoint turns into a 409, never a duplicate lot number.

Downgrade restores migration 0042's exact index and CHECK definitions and drops
both new columns. It performs no data change either, so up→down→up is
value-preserving for every pre-existing row.

Revision ID: 0048_supplier_lot_auto_lot_v2
Revises: 0047_inspector_type_chiatai
Create Date: 2026-08-05 00:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0048_supplier_lot_auto_lot_v2"
down_revision = "0047_inspector_type_chiatai"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE plot_cycles ADD COLUMN supplier_lot_no VARCHAR(100);
        ALTER TABLE plot_cycles ADD COLUMN auto_lot_series_key VARCHAR(255);

        -- (a) Auto rows: V1 identified by po_number, V2 by auto_lot_series_key.
        ALTER TABLE plot_cycles DROP CONSTRAINT IF EXISTS ck_plot_cycles_auto_lot_requires_fields;
        ALTER TABLE plot_cycles ADD CONSTRAINT ck_plot_cycles_auto_lot_requires_fields
            CHECK (
                lot_no_source IS DISTINCT FROM 'auto'
                OR (
                    lot_no IS NOT NULL
                    AND lot_running_no IS NOT NULL
                    AND (auto_lot_series_key IS NOT NULL OR po_number IS NOT NULL)
                )
            );

        -- (b) V1 backstop, now scoped to V1 rows only (series key NULL).
        DROP INDEX IF EXISTS uq_plot_cycles_auto_lot_running;
        CREATE UNIQUE INDEX uq_plot_cycles_auto_lot_running
            ON plot_cycles (plot_id, po_number, lot_running_no)
            WHERE lot_no_source = 'auto' AND auto_lot_series_key IS NULL;

        -- (c) V2 backstop: one running number per (supplier, cycleLabel, pCode).
        CREATE UNIQUE INDEX uq_plot_cycles_auto_lot_series_running
            ON plot_cycles (auto_lot_series_key, lot_running_no)
            WHERE auto_lot_series_key IS NOT NULL;
        """
    )


def downgrade() -> None:
    # Reverse order: new index, then the V1 index predicate, then the CHECK,
    # then the columns. Restores migration 0042's definitions verbatim.
    op.execute(
        """
        DROP INDEX IF EXISTS uq_plot_cycles_auto_lot_series_running;

        DROP INDEX IF EXISTS uq_plot_cycles_auto_lot_running;
        CREATE UNIQUE INDEX uq_plot_cycles_auto_lot_running
            ON plot_cycles (plot_id, po_number, lot_running_no)
            WHERE lot_no_source = 'auto';

        ALTER TABLE plot_cycles DROP CONSTRAINT IF EXISTS ck_plot_cycles_auto_lot_requires_fields;
        ALTER TABLE plot_cycles ADD CONSTRAINT ck_plot_cycles_auto_lot_requires_fields
            CHECK (
                lot_no_source IS DISTINCT FROM 'auto'
                OR (po_number IS NOT NULL AND lot_no IS NOT NULL AND lot_running_no IS NOT NULL)
            );

        ALTER TABLE plot_cycles DROP COLUMN IF EXISTS auto_lot_series_key;
        ALTER TABLE plot_cycles DROP COLUMN IF EXISTS supplier_lot_no;
        """
    )

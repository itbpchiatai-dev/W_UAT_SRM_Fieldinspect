"""plot_cycles: Auto Lot V2 database integrity (round 8-12A.1).

Round 8-12A added the V2 columns and a running-number backstop. This migration
closes the three integrity gaps that review found, all of them DDL-only:

  1. ck_plot_cycles_auto_lot_requires_fields is STRENGTHENED so a V2 auto row
     cannot exist without the data its own lot number is built from:

       V1 auto row (auto_lot_series_key IS NULL)
         → po_number + lot_no + lot_running_no      [unchanged legacy rule]
       V2 auto row (auto_lot_series_key IS NOT NULL)
         → lot_no + lot_running_no + NONBLANK cycle_label + NONBLANK p_code

     The V2 branch is new. Without it the DB would happily store a row whose
     lot_no was generated from a cycle_label that was later blanked, leaving a
     lot nobody can re-derive. btrim() is used (not just NOT NULL) because
     ''/'   ' are exactly the values normalize_cycle_label/normalize_p_code
     reject, and the DB should not accept what the app refuses to produce.

     Every existing row still satisfies this: all 32 auto rows on dev are V1
     (series key NULL) and carry a po_number, so they take the V1 branch
     untouched.

  2. uq_plot_cycles_auto_lot_series_running gains an explicit
     lot_no_source = 'auto' term. 0048 relied on "series key IS NOT NULL"
     implying auto; making it explicit means the index keeps meaning
     "one running number per series among AUTO rows" even if a future path
     ever set a series key on a non-auto row.

  3. NEW uq_plot_cycles_auto_lot_v2_lot_no — UNIQUE(lot_no) over V2 auto rows.
     The running number is unique per SERIES, but cycleLabel, supplierCode and
     pCode may all contain "-", so two DIFFERENT series can render the same
     lot_no string:

        ("26", "may-1") + running 1  ->  "26-SUP-may-1-001"
        ("26-may", "1") + running 1  ->  "26-may-SUP-1-001"   (same text)

     The series key (length-prefixed since 8-12A.1) keeps those series apart,
     which is correct for counting — but the printed lot number is what users
     and downstream systems key on, so the DB must also refuse a duplicate
     rendering. Scoped to V2 auto rows ONLY: a global UNIQUE(lot_no) is
     impossible here because dev already holds a legacy duplicate
     (one manual + one legacy row share a lot_no), and legacy data must not be
     invalidated by a new rule it was never written under.

No data is read, written, backfilled or deleted: this migration contains no
UPDATE/INSERT/DELETE at all. Existing lot_no / lot_no_source / lot_running_no /
supplier_lot_no values are untouched in both directions.

Downgrade restores 0048's exact CHECK and index definitions.

Revision ID: 0049_auto_lot_v2_integrity
Revises: 0048_supplier_lot_auto_lot_v2
Create Date: 2026-08-05 01:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0049_auto_lot_v2_integrity"
down_revision = "0048_supplier_lot_auto_lot_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- (1) V1 keeps its legacy rule; V2 must carry the components its lot
        --     number was rendered from.
        ALTER TABLE plot_cycles DROP CONSTRAINT IF EXISTS ck_plot_cycles_auto_lot_requires_fields;
        ALTER TABLE plot_cycles ADD CONSTRAINT ck_plot_cycles_auto_lot_requires_fields
            CHECK (
                lot_no_source IS DISTINCT FROM 'auto'
                OR (
                    lot_no IS NOT NULL
                    AND lot_running_no IS NOT NULL
                    AND (
                        (auto_lot_series_key IS NULL AND po_number IS NOT NULL)
                        OR (
                            auto_lot_series_key IS NOT NULL
                            AND cycle_label IS NOT NULL AND btrim(cycle_label) <> ''
                            AND p_code IS NOT NULL AND btrim(p_code) <> ''
                        )
                    )
                )
            );

        -- (2) Make "auto" explicit in the V2 running backstop.
        DROP INDEX IF EXISTS uq_plot_cycles_auto_lot_series_running;
        CREATE UNIQUE INDEX uq_plot_cycles_auto_lot_series_running
            ON plot_cycles (auto_lot_series_key, lot_running_no)
            WHERE lot_no_source = 'auto' AND auto_lot_series_key IS NOT NULL;

        -- (3) Two different series must never render the same lot_no text.
        CREATE UNIQUE INDEX uq_plot_cycles_auto_lot_v2_lot_no
            ON plot_cycles (lot_no)
            WHERE lot_no_source = 'auto' AND auto_lot_series_key IS NOT NULL;
        """
    )


def downgrade() -> None:
    # Reverse order, restoring migration 0048's definitions verbatim.
    op.execute(
        """
        DROP INDEX IF EXISTS uq_plot_cycles_auto_lot_v2_lot_no;

        DROP INDEX IF EXISTS uq_plot_cycles_auto_lot_series_running;
        CREATE UNIQUE INDEX uq_plot_cycles_auto_lot_series_running
            ON plot_cycles (auto_lot_series_key, lot_running_no)
            WHERE auto_lot_series_key IS NOT NULL;

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
        """
    )

"""relax plot_cycles.final_yield_pct's 150% business cap to 9999.9% storage
ceiling (round 8-8B.1).

Real growers reported genuine harvests over 150% of plan — 150% is now a
non-blocking WARNING threshold the frontend shows (lib/yield-planning.ts's
YIELD_WARNING_PCT), never a reason to reject a save. The only hard limit
left is the column's own NUMERIC(5,1) storage capacity: 4 integer digits +
1 decimal = a maximum of 9999.9.

This migration touches ONE constraint on ONE column:

  ck_plot_cycles_final_yield_pct_range (added migration 0038_cycle_
  final_estimate, historical text there is UNCHANGED and NOT edited by this
  migration — see that file's own docstring, which still correctly
  describes what was true when it was written):
    OLD: final_yield_pct IS NULL OR (final_yield_pct >= 0 AND final_yield_pct <= 150)
    NEW: final_yield_pct IS NULL OR (final_yield_pct >= 0 AND final_yield_pct <= 9999.9)

records.yield_pct and plots.current_yield_pct have NO equivalent DB-level
CHECK constraint (confirmed via pg_catalog, round 8-8B.1 Part A preflight)
— only Pydantic schema bounds (app/schemas/record.py's RecordCreate/
RecordUpdate/PublicRecordCreate, widened to le=9999.9 in this same round,
backend-code-only, no migration needed for those). This migration is the
only DB-side change round 8-8B.1 makes.

No column type change (NUMERIC(5,1) unchanged), no backfill, no data
mutation, no RLS/policy/grant/role change — additive constraint replacement
only, same DROP+ADD-with-the-same-name pattern already used throughout this
migration history for constraint edits.

Downgrade safety: reverting to the <=150 cap while a real row already holds
a value the OLD constraint would reject (final_yield_pct > 150 — expected
after this round's business rule ships) would either fail outright or,
worse, require silently deleting/rewriting that data to make the ALTER
succeed. Neither is acceptable, so downgrade() preflights with a read-only
COUNT and RAISE EXCEPTION + abort (whole statement is one transaction —
transactional DDL, see alembic/env.py) whenever any row would violate the
old cap. Never deletes or rewrites data to force a downgrade through.

Revision ID: 0045_relax_yield_pct_cap
Revises: 0044_yield_quantity_kg
Create Date: 2026-07-30 01:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0045_relax_yield_pct_cap"
down_revision = "0044_yield_quantity_kg"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE plot_cycles
            DROP CONSTRAINT IF EXISTS ck_plot_cycles_final_yield_pct_range;

        ALTER TABLE plot_cycles
            ADD CONSTRAINT ck_plot_cycles_final_yield_pct_range
                CHECK (final_yield_pct IS NULL
                       OR (final_yield_pct >= 0 AND final_yield_pct <= 9999.9));
        """
    )


def downgrade() -> None:
    # Read-only preflight, same transaction as the ALTER below: abort with a
    # clear message rather than let the old <=150 constraint silently reject
    # (or, far worse, have an operator delete/rewrite data to force it
    # through) any row this round's business rule legitimately produced.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM plot_cycles WHERE final_yield_pct > 150) THEN
                RAISE EXCEPTION
                    'Cannot downgrade 0045_relax_yield_pct_cap: plot_cycles has '
                    'final_yield_pct values > 150 (expected after round 8-8B.1 '
                    'shipped) that the old <=150 constraint would reject. '
                    'Resolve/migrate that data with an explicit, reviewed plan '
                    'before downgrading — this migration will never delete or '
                    'rewrite it for you.';
            END IF;
        END $$;

        ALTER TABLE plot_cycles
            DROP CONSTRAINT IF EXISTS ck_plot_cycles_final_yield_pct_range;

        ALTER TABLE plot_cycles
            ADD CONSTRAINT ck_plot_cycles_final_yield_pct_range
                CHECK (final_yield_pct IS NULL
                       OR (final_yield_pct >= 0 AND final_yield_pct <= 150));
        """
    )

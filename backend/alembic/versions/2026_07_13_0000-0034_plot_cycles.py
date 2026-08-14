"""plot_cycles — planting-cycle table + records.plot_cycle_id (round 7.1).

Introduces the Supplier → Plot → PlotCycle → Record hierarchy:

  1. CREATE plot_cycles (one row per planting cycle of a plot), with a partial
     unique index guaranteeing at most one 'active' cycle per plot.
  2. ADD records.plot_cycle_id, nullable at first.
  3. Preflight (DO/RAISE, non-mutating): abort if any record references a
     missing plot — the backfill can't link those and we won't guess.
  4. Backfill: one cycle_no=1 row per existing plot (status mirrors the plot's
     is_active: active plot → 'active', inactive plot → 'cancelled', since we
     can't know whether an inactive plot was harvested or abandoned — 'cancelled'
     avoids falsely claiming a real harvest). Master data (crop/variety/lot/
     planting_date/plant_count/expected_yield_*) is copied from the plot's
     current_* mirror columns. Then link every record to its plot's cycle_no=1.
  5. Postflight (DO/RAISE): abort if any record still has NULL plot_cycle_id,
     or any plot ended up with more than one active cycle — before we commit to
     NOT NULL.
  6. ALTER records.plot_cycle_id SET NOT NULL.

Phased-safe: additive only (new table + new nullable column made NOT NULL
after a verified backfill). Never drops or rewrites existing plots/records
data — the current_* mirror columns on plots are intentionally kept (no
destructive drop this round). Transactional DDL: any preflight/postflight
RAISE rolls the whole migration back, including the new table.

Revision ID: 0034_plot_cycles
Revises: 0033_protocol_constraints
Create Date: 2026-07-13 00:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0034_plot_cycles"
down_revision = "0033_protocol_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. plot_cycles table. Constraint/index names match the ORM naming
    #    convention (app/db/base.py) so model metadata and the DB agree —
    #    ck_plot_cycles_<name>, uq_plot_cycles_plot_id_cycle_no, etc. The PK
    #    is auto-named plot_cycles_pkey (same as migration 0032's table).
    op.execute(
        """
        CREATE TABLE plot_cycles (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            plot_id UUID NOT NULL,
            cycle_no INTEGER NOT NULL,
            status VARCHAR(20) NOT NULL,
            crop VARCHAR(100),
            variety VARCHAR(100),
            lot_no VARCHAR(100),
            planting_date DATE,
            plant_count INTEGER,
            expected_yield_full NUMERIC(12, 2),
            expected_yield_unit VARCHAR(20),
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            closed_at TIMESTAMPTZ,
            closed_by_id UUID,
            close_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_plot_cycles_plot_id_cycle_no UNIQUE (plot_id, cycle_no),
            CONSTRAINT ck_plot_cycles_cycle_no_positive CHECK (cycle_no >= 1),
            CONSTRAINT ck_plot_cycles_status_allowed
                CHECK (status IN ('active', 'harvested', 'cancelled')),
            CONSTRAINT ck_plot_cycles_plant_count_non_negative
                CHECK (plant_count IS NULL OR plant_count >= 0),
            CONSTRAINT ck_plot_cycles_expected_yield_full_non_negative
                CHECK (expected_yield_full IS NULL OR expected_yield_full >= 0),
            CONSTRAINT fk_plot_cycles_plot_id_plots
                FOREIGN KEY (plot_id) REFERENCES plots(id) ON DELETE RESTRICT,
            CONSTRAINT fk_plot_cycles_closed_by_id_users
                FOREIGN KEY (closed_by_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX ix_plot_cycles_plot_id ON plot_cycles (plot_id);
        CREATE INDEX ix_plot_cycles_closed_by_id ON plot_cycles (closed_by_id);
        CREATE UNIQUE INDEX uq_plot_cycles_active_per_plot
            ON plot_cycles (plot_id) WHERE status = 'active';
        """
    )

    # 2. records.plot_cycle_id — nullable first (backfill fills it below).
    op.execute(
        """
        ALTER TABLE records ADD COLUMN plot_cycle_id UUID;
        ALTER TABLE records ADD CONSTRAINT fk_records_plot_cycle_id_plot_cycles
            FOREIGN KEY (plot_cycle_id) REFERENCES plot_cycles(id) ON DELETE RESTRICT;
        CREATE INDEX ix_records_plot_cycle_id ON records (plot_cycle_id);
        """
    )

    # 3. Preflight — a record pointing at a non-existent plot can't be linked;
    #    abort with a clear count rather than guessing. Non-mutating.
    op.execute(
        """
        DO $$
        DECLARE orphan_count INTEGER;
        BEGIN
            SELECT count(*) INTO orphan_count
            FROM records r LEFT JOIN plots p ON p.id = r.plot_id
            WHERE p.id IS NULL;
            IF orphan_count > 0 THEN
                RAISE EXCEPTION
                    'Preflight (0034): % record(s) reference a missing plot; fix the data before migrating.',
                    orphan_count;
            END IF;
        END $$;
        """
    )

    # 4a. Backfill one cycle_no=1 per plot. status = active/cancelled by the
    #     plot's permanent is_active flag; started_at = planting date if known
    #     else the plot's creation time; a cancelled cycle's closed_at is the
    #     plot's last-modified time (≈ when it was deactivated) with an
    #     explicitly non-harvest close_reason.
    op.execute(
        """
        INSERT INTO plot_cycles (
            id, plot_id, cycle_no, status,
            crop, variety, lot_no, planting_date,
            plant_count, expected_yield_full, expected_yield_unit,
            started_at, closed_at, closed_by_id, close_reason,
            created_at, updated_at
        )
        SELECT
            gen_random_uuid(), p.id, 1,
            CASE WHEN p.is_active THEN 'active' ELSE 'cancelled' END,
            p.current_crop, p.current_variety, p.current_lot_no, p.current_planting_date,
            p.plant_count, p.expected_yield_full, p.expected_yield_unit,
            COALESCE(p.current_planting_date::timestamptz, p.created_at),
            CASE WHEN p.is_active THEN NULL ELSE p.updated_at END,
            NULL,
            CASE WHEN p.is_active THEN NULL
                 ELSE 'Backfilled from inactive plot (round 7.1); original close reason unknown' END,
            NOW(), NOW()
        FROM plots p;
        """
    )

    # 4b. Link every record to its plot's cycle_no=1 (exactly one per plot).
    op.execute(
        """
        UPDATE records r
        SET plot_cycle_id = c.id
        FROM plot_cycles c
        WHERE c.plot_id = r.plot_id AND c.cycle_no = 1;
        """
    )

    # 5. Postflight — verify the backfill is complete and the active-cycle
    #    invariant holds BEFORE committing to NOT NULL. Non-mutating.
    op.execute(
        """
        DO $$
        DECLARE unlinked_count INTEGER; multi_active_count INTEGER;
        BEGIN
            SELECT count(*) INTO unlinked_count
            FROM records WHERE plot_cycle_id IS NULL;
            IF unlinked_count > 0 THEN
                RAISE EXCEPTION
                    'Postflight (0034): % record(s) still have NULL plot_cycle_id after backfill; aborting before NOT NULL.',
                    unlinked_count;
            END IF;

            SELECT count(*) INTO multi_active_count FROM (
                SELECT plot_id FROM plot_cycles WHERE status = 'active'
                GROUP BY plot_id HAVING count(*) > 1
            ) dup;
            IF multi_active_count > 0 THEN
                RAISE EXCEPTION
                    'Postflight (0034): % plot(s) have more than one active cycle; aborting.',
                    multi_active_count;
            END IF;
        END $$;
        """
    )

    # 6. Now safe: every record is linked.
    op.execute("ALTER TABLE records ALTER COLUMN plot_cycle_id SET NOT NULL;")


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE records DROP CONSTRAINT IF EXISTS fk_records_plot_cycle_id_plot_cycles;
        DROP INDEX IF EXISTS ix_records_plot_cycle_id;
        ALTER TABLE records DROP COLUMN IF EXISTS plot_cycle_id;
        DROP TABLE IF EXISTS plot_cycles;
        """
    )

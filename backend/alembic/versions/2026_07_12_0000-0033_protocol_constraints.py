"""inspection_protocol_criteria — DB-level integrity constraints (round 5.7).

Hardens the round-5.5 table against bad data from manual SQL/scripts:
- CHECK slot IN the 4-slot allowlist,
- CHECK order_index BETWEEN 0 AND 3,
- UNIQUE (growth_stage, order_index) so a stage can't have two criteria at
  the same position (complements the existing UNIQUE (growth_stage, slot)).

Does NOT touch data or the round-5.5 behavior — purely additive constraints.
A preflight DO block reports (RAISE) a clear reason and aborts if existing
rows violate any rule, rather than auto-fixing or emitting a cryptic
constraint error. (Verified clean on the dev DB before writing this.)

Revision ID: 0033_protocol_constraints
Revises: 0032_protocol_criteria
Create Date: 2026-07-12 00:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0033_protocol_constraints"
down_revision = "0032_protocol_criteria"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preflight — abort with a clear message if existing data would violate a
    # constraint we're about to add. Never mutates.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM inspection_protocol_criteria
                WHERE slot NOT IN
                    ('fieldPrepScore', 'weatherScore', 'careScore', 'varietyResistanceScore')
            ) THEN
                RAISE EXCEPTION
                    'Preflight (0033): inspection_protocol_criteria has slot values outside the 4-slot allowlist; fix the data before migrating.';
            END IF;

            IF EXISTS (
                SELECT 1 FROM inspection_protocol_criteria
                WHERE order_index < 0 OR order_index > 3
            ) THEN
                RAISE EXCEPTION
                    'Preflight (0033): inspection_protocol_criteria has order_index outside 0-3; fix the data before migrating.';
            END IF;

            IF EXISTS (
                SELECT 1 FROM inspection_protocol_criteria
                GROUP BY growth_stage, order_index
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Preflight (0033): inspection_protocol_criteria has duplicate (growth_stage, order_index); fix the data before migrating.';
            END IF;
        END $$;
        """
    )

    # Constraint names match the ORM naming convention (app/db/base.py's `ck`
    # rule expands CheckConstraint("slot_allowlist") ->
    # ck_inspection_protocol_criteria_slot_allowlist) so the model metadata
    # and the DB agree.
    op.execute(
        """
        ALTER TABLE inspection_protocol_criteria
            ADD CONSTRAINT ck_inspection_protocol_criteria_slot_allowlist
            CHECK (slot IN
                ('fieldPrepScore', 'weatherScore', 'careScore', 'varietyResistanceScore'));
        ALTER TABLE inspection_protocol_criteria
            ADD CONSTRAINT ck_inspection_protocol_criteria_order_range
            CHECK (order_index BETWEEN 0 AND 3);
        ALTER TABLE inspection_protocol_criteria
            ADD CONSTRAINT uq_protocol_stage_order
            UNIQUE (growth_stage, order_index);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE inspection_protocol_criteria DROP CONSTRAINT IF EXISTS uq_protocol_stage_order;
        ALTER TABLE inspection_protocol_criteria DROP CONSTRAINT IF EXISTS ck_inspection_protocol_criteria_order_range;
        ALTER TABLE inspection_protocol_criteria DROP CONSTRAINT IF EXISTS ck_inspection_protocol_criteria_slot_allowlist;
        """
    )

"""records yield-in-kilograms foundation (round 8-8A) — a new kg-first input
for a record's Yield, with the Backend computing yield_pct by comparing it
against the active PlotCycle's expected_yield_full/expected_yield_unit target
at create time. yield_pct itself (migration 0004x pre-existing) is unchanged;
this migration only adds the two new columns the computation needs to keep
its inputs/outputs auditable:

Two nullable columns on records:

  yield_quantity_kg         NUMERIC(12, 2) — the yield amount the caller
                             entered/estimated, in kilograms. NULL for a
                             legacy client that still sends only yieldPct.
  yield_target_kg_snapshot  NUMERIC(12, 2) — the expected-yield target (in kg,
                             already unit-converted) the Backend compared
                             yield_quantity_kg against AT CREATE TIME. Frozen
                             per-record, like every other snapshot column in
                             this table — never recomputed later. NULL
                             whenever there was no comparable kg target (no
                             expected_yield_full, a non-weight unit such as
                               ผล/ลัง, or an unrecognised unit) — the backend
                             never fabricates a fake 100%. Server-derived
                             only; no client field ever sets this directly.

Deliberately NULLABLE with NO backfill: every existing record keeps both
columns NULL and must read without error — this round changes NOTHING about
records created before it, and yield_pct's existing meaning/precision (5,1)
is untouched.

Constraints (ORM `ck_` naming convention, mirrored in
app/db/models/record.py so model metadata and this migration agree):

  - ck_records_yield_quantity_kg_non_negative
        yield_quantity_kg IS NULL OR yield_quantity_kg >= 0
  - ck_records_yield_target_kg_snapshot_non_negative
        yield_target_kg_snapshot IS NULL OR yield_target_kg_snapshot >= 0

Additive only: two nullable columns + two CHECKs, NO backfill, NO other table
touched, NO RLS policy/grant/role/ownership change. Transactional DDL — any
failure rolls the whole migration back.

Revision ID: 0044_yield_quantity_kg
Revises: 0043_actual_harvest
Create Date: 2026-07-30 00:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0044_yield_quantity_kg"
down_revision = "0043_actual_harvest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE records ADD COLUMN yield_quantity_kg NUMERIC(12, 2);
        ALTER TABLE records ADD COLUMN yield_target_kg_snapshot NUMERIC(12, 2);

        ALTER TABLE records ADD CONSTRAINT ck_records_yield_quantity_kg_non_negative
            CHECK (yield_quantity_kg IS NULL OR yield_quantity_kg >= 0);
        ALTER TABLE records ADD CONSTRAINT ck_records_yield_target_kg_snapshot_non_negative
            CHECK (yield_target_kg_snapshot IS NULL OR yield_target_kg_snapshot >= 0);
        """
    )


def downgrade() -> None:
    # Reverse dependency order: CHECKs, then the two columns.
    op.execute(
        """
        ALTER TABLE records DROP CONSTRAINT IF EXISTS ck_records_yield_target_kg_snapshot_non_negative;
        ALTER TABLE records DROP CONSTRAINT IF EXISTS ck_records_yield_quantity_kg_non_negative;
        ALTER TABLE records DROP COLUMN IF EXISTS yield_target_kg_snapshot;
        ALTER TABLE records DROP COLUMN IF EXISTS yield_quantity_kg;
        """
    )

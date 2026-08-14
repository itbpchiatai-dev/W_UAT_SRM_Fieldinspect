"""inspector_type 'extension' → 'chiatai' (round 8-11A).

Renames one canonical inspector_type value across the records table so the
API/DB contract matches the UI's three choices:

    เกษตรกร        → farmer     (unchanged)
    บริษัทผู้ผลิต   → supplier   (unchanged)
    Chiatai        → chiatai    (was 'extension')

Only the ONE value changes. 'farmer', 'supplier' and NULL are never touched,
and no row is inserted or deleted — the records table's total count is
identical before and after, in both directions.

  1. DROP the CHECK constraint. Its real DB name is
     ck_records_inspector_type_allowed — migration 0039 created it with that
     literal name, which is also what app/db/base.py's NAMING_CONVENTION "ck"
     pattern (ck_%(table_name)s_%(constraint_name)s) resolves the model's
     `inspector_type_allowed` to. It must come off FIRST: the UPDATE below
     would otherwise violate the old allowlist, which has no 'chiatai'.
  2. UPDATE the affected rows in place.
  3. ADD the constraint back with the new allowlist.

Reversible: downgrade performs the exact inverse (drop → rewrite back to
'extension' → restore the original allowlist), so upgrade→downgrade→upgrade
returns to the same state. This is a DEV-data rename; there is no production
data to preserve, and no attempt is made to remember which rows were
originally 'extension' beyond the value itself.

Transactional DDL — any failure rolls the whole migration back.

Revision ID: 0047_inspector_type_chiatai
Revises: 0046_plot_access_credentials
Create Date: 2026-08-04 00:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0047_inspector_type_chiatai"
down_revision = "0046_plot_access_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE records DROP CONSTRAINT IF EXISTS ck_records_inspector_type_allowed;

        UPDATE records
            SET inspector_type = 'chiatai'
            WHERE inspector_type = 'extension';

        ALTER TABLE records ADD CONSTRAINT ck_records_inspector_type_allowed
            CHECK (inspector_type IS NULL
                   OR inspector_type IN ('farmer', 'supplier', 'chiatai'));
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE records DROP CONSTRAINT IF EXISTS ck_records_inspector_type_allowed;

        UPDATE records
            SET inspector_type = 'extension'
            WHERE inspector_type = 'chiatai';

        ALTER TABLE records ADD CONSTRAINT ck_records_inspector_type_allowed
            CHECK (inspector_type IS NULL
                   OR inspector_type IN ('farmer', 'supplier', 'extension'));
        """
    )

"""retire inspection codes — round 8-3G drops the two legacy gate-code
concepts the app no longer uses:

  1. records.submitted_by_code -> nullable. New records (logged-in and
     public-phone flows both) no longer collect a "submitted by" code at
     all (see RecordCreate/PublicRecordCreate, which drop the field
     entirely) — only submitted_by_name (optional) remains. Existing rows
     keep whatever value they already have; nothing is backfilled or
     rewritten.
  2. suppliers.inspection_code -> DROPPED. The supplier-level PIN gate
     (migration 0027) and its two verification endpoints
     (POST /plots/{id}/verify-inspection-code,
     POST /public/plots/verify-inspection-code) are retired this round —
     the public flow is phone-access-only (round 8-3B onward) and the
     logged-in flow never required a second gate beyond login+permission+
     RLS. See app/services/inspection_code.py, which is kept (not
     deleted) purely because migrations 0023/0027 import
     DEFAULT_INSPECTION_CODE/hash_inspection_code from it — a fresh
     `alembic upgrade head` on an empty DB must still be able to replay
     those two historical migrations.

Pure DDL, no application-row INSERT/UPDATE/DELETE, no RLS/policy/grant/
index change on either table, no touch to plot/plot_cycle/plot_access_phone
data or QR/qr_key.

Irreversible: suppliers.inspection_code is PLAINTEXT and admin-editable
(migration 0027) — an admin may have changed a supplier's code away from
the "1111" default at any point after creation, and once the column is
dropped that per-supplier value cannot be reconstructed from anything else
in this database. Unlike 0027's own downgrade (which could safely
re-derive the column because every row's value was still the hashed
default at that point), there is no equivalent fallback here. downgrade()
therefore refuses outright rather than attempting a lossy/partial
reconstruction — restoring requires a pre-migration database backup.

Revision ID: 0040_retire_inspection_codes
Revises: 0039_plot_access_phones
Create Date: 2026-07-19 00:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0040_retire_inspection_codes"
down_revision = "0039_plot_access_phones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. records.submitted_by_code -> nullable. No backfill: existing rows
    #    keep their value, new rows simply omit the field.
    op.alter_column("records", "submitted_by_code", nullable=True)

    # 2. suppliers.inspection_code -> dropped outright (see module docstring
    #    for why this is irreversible and never re-created by downgrade).
    op.drop_column("suppliers", "inspection_code")


def downgrade() -> None:
    # Refuse before touching the schema at all — see module docstring.
    # Re-adding suppliers.inspection_code with a fabricated default value
    # would silently overwrite every supplier's real, possibly-customized
    # code with a value that was never theirs — worse than refusing to
    # downgrade.
    message = (
        "Cannot restore retired supplier inspection codes; restore from a "
        "pre-migration backup"
    )
    raise RuntimeError(message)

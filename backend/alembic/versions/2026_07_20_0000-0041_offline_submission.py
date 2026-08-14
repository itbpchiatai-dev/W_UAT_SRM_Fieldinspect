"""offline submission foundation (round 8-4A) — lets the public inspection
flow accept a draft that was captured while offline and submitted later,
idempotently.

Adds two nullable columns + one partial unique index to `records`:

  1. client_submission_id UUID NULL — a client-generated id that stands for
     ONE offline draft across its whole life. The public create endpoint uses
     it as an idempotency key: a retry with the same key + same identity
     returns the already-created record instead of a duplicate. NULL for every
     online submission (the existing flow) and every pre-existing row.
  2. captured_at TIMESTAMPTZ NULL — when the inspector actually filled the
     form on-site (offline), as reported by the client after validation.
     created_at KEEPS its meaning untouched (server receive/commit time), so
     plot.current_* snapshot ordering — which uses created_at — is unaffected
     and a stale draft can never overwrite a newer inspection's snapshot.
  3. uq_records_client_submission_id — partial UNIQUE index over
     client_submission_id WHERE client_submission_id IS NOT NULL. This is the
     final race backstop for idempotency: two concurrent submits of the same
     key can't both insert. NULLs are excluded, so the millions of online/
     historical rows with a NULL key are never constrained against each other.

Additive only: no backfill (existing rows stay NULL), no data mutation, no
reseed, no change to created_at semantics, no RLS policy/grant/role change, no
touch to any other table. Transactional DDL — any failure rolls the whole
migration back. Constraint/index names follow the ORM naming convention
(app/db/base.py) so model metadata and this migration agree.

Revision ID: 0041_offline_submission
Revises: 0040_retire_inspection_codes
Create Date: 2026-07-20 00:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0041_offline_submission"
down_revision = "0040_retire_inspection_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Two nullable columns + a partial unique index. Nullable so existing rows
    # read without backfill; the partial index excludes NULLs so only real
    # offline keys are constrained to be unique.
    op.execute(
        """
        ALTER TABLE records ADD COLUMN client_submission_id UUID;
        ALTER TABLE records ADD COLUMN captured_at TIMESTAMPTZ;
        CREATE UNIQUE INDEX uq_records_client_submission_id
            ON records (client_submission_id)
            WHERE client_submission_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    # Reverse dependency order: index first, then the two columns.
    op.execute(
        """
        DROP INDEX IF EXISTS uq_records_client_submission_id;
        ALTER TABLE records DROP COLUMN IF EXISTS captured_at;
        ALTER TABLE records DROP COLUMN IF EXISTS client_submission_id;
        """
    )

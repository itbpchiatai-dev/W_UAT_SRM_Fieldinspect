"""plots inspection_code_hash — gate code checked before a field inspector
can start a record for that plot. NOT a login credential and NOT
submitted_by_code (records' field-attribution name) — a separate concern.

Stored bcrypt-hashed only, never plain text. Default for every plot is
"1111" — hashed once here (bcrypt is deliberately slow; the same hash is
reused for the whole backfill UPDATE since every row gets the same
plaintext) and bound as a query parameter rather than interpolated into
the migration source, so no literal hash value — let alone the plaintext —
ends up committed to git history, and each database that runs this
migration gets its own freshly-salted hash for the same default code.

Phased-safe: add nullable, backfill hash, then enforce NOT NULL.

Revision ID: 0023_plots_inspection_code
Revises: 0022_records_submitted_by
Create Date: 2026-07-02 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.services.inspection_code import DEFAULT_INSPECTION_CODE, hash_inspection_code

revision = "0023_plots_inspection_code"
down_revision = "0022_records_submitted_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plots", sa.Column("inspection_code_hash", sa.String(length=255), nullable=True))
    default_hash = hash_inspection_code(DEFAULT_INSPECTION_CODE)
    op.execute(
        sa.text(
            "UPDATE plots SET inspection_code_hash = :h WHERE inspection_code_hash IS NULL"
        ).bindparams(h=default_hash)
    )
    op.alter_column("plots", "inspection_code_hash", nullable=False)


def downgrade() -> None:
    op.drop_column("plots", "inspection_code_hash")

"""supplier inspection code — move the field inspection gate code from a
per-plot bcrypt hash (plots.inspection_code_hash, migrations 0023) to a
per-supplier PLAINTEXT code (suppliers.inspection_code).

Why the shape change (confirmed with the product owner):
  - The code is now shared by every plot of a supplier — a field worker
    who knows their supplier's code can inspect any of that supplier's
    plots, instead of a distinct code per plot.
  - Stored in PLAINTEXT (not hashed) by deliberate choice: unlike an
    account password, an admin must be able to READ the current code back
    to hand/print it to field workers, and the threat model is a short
    guessable PIN gating a low-value "start inspection" action (still
    rate-limited on the public endpoint), NOT authentication. Same
    plaintext-by-design rationale as plots.qr_key (migration 0026).

The per-plot hash column is dropped: keeping both would be two competing
sources of truth. Every existing hash was the default-code ("1111") hash
anyway — no real per-plot secret is lost. New suppliers default to "1111"
so existing rows and omitted-code creates keep the historical default.

Revision ID: 0027_supplier_inspection_code
Revises: 0026_plots_qr_key
Create Date: 2026-07-06 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.services.inspection_code import DEFAULT_INSPECTION_CODE, hash_inspection_code

revision = "0027_supplier_inspection_code"
down_revision = "0026_plots_qr_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default keeps existing supplier rows valid under NOT NULL and
    # gives omitted-code creates the historical "1111" default.
    op.add_column(
        "suppliers",
        sa.Column(
            "inspection_code",
            sa.String(length=20),
            nullable=False,
            server_default=DEFAULT_INSPECTION_CODE,
        ),
    )
    # Retire the per-plot hash — the supplier code is now the sole gate.
    op.drop_column("plots", "inspection_code_hash")


def downgrade() -> None:
    # Re-add the per-plot hash NOT NULL. No plaintext literal in SQL: hash
    # the default code at migration-run-time via the same helper the app
    # uses (mirrors migration 0023's approach), then backfill every row.
    op.add_column(
        "plots",
        sa.Column("inspection_code_hash", sa.String(length=255), nullable=True),
    )
    op.execute(
        sa.text("UPDATE plots SET inspection_code_hash = :h").bindparams(
            h=hash_inspection_code(DEFAULT_INSPECTION_CODE)
        )
    )
    op.alter_column("plots", "inspection_code_hash", nullable=False)

    op.drop_column("suppliers", "inspection_code")

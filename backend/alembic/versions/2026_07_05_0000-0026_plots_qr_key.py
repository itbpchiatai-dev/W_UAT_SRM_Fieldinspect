"""plots qr_key — opaque per-plot locator for the public QR deep link
(round 20 QR hardening), replacing supplierCode+plotCode (guessable/
enumerable from a plot's already-visible codes).

Stored in plaintext, NOT hashed, by deliberate choice: the QR key is
explicitly NOT a save credential — the real gate is the existing
inspection_code_hash (migration 0023). The actual security boundary this
round hardens is "you can no longer derive a valid QR URL just by knowing
a supplier/plot code"; knowing the QR key alone still only gets you to the
inspection-code prompt, same as before. Hashing here would also break a
real operational need: admins must be able to reprint the *same* QR image
for an already-affixed physical sticker (lost/damaged sign) without
invalidating it, which a hash-only/one-time-reveal design can't support
without a separate raw-value cache — i.e. hashing would add complexity
without reducing real risk, since the plaintext value has to live
somewhere retrievable regardless.

Phased-safe: add nullable + a unique index (Postgres unique indexes allow
multiple NULLs, so this is safe before backfill runs). Backfilling
existing plots is a separate script — app/db/backfill_plot_qr_key.py —
not inline SQL here, since each row needs its own randomly generated
value (unlike inspection_code_hash's migration 0023, where every row got
the same default-code hash). Left nullable even after backfill (unlike
0023, which tightens to NOT NULL): every plot created from this point
forward always gets a qr_key at create time (plot_repository.create_plot),
so NOT NULL isn't required for correctness, and staying nullable keeps
this migration a pure additive, zero-downtime change with no backfill
ordering dependency.

Revision ID: 0026_plots_qr_key
Revises: 0025_plots_yield_planning
Create Date: 2026-07-05 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_plots_qr_key"
down_revision = "0025_plots_yield_planning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plots", sa.Column("qr_key", sa.String(length=64), nullable=True))
    op.create_index("ix_plots_qr_key", "plots", ["qr_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_plots_qr_key", table_name="plots")
    op.drop_column("plots", "qr_key")

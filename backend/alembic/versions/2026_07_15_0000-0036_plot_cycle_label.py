"""plot_cycles.cycle_label — user-facing season/cycle name (round 8.0).

Adds an optional, free-text label for a planting cycle (e.g. "jun2026",
"may2026") so the UI can refer to a รอบปลูก by a human name instead of only
"รอบที่ N". Deliberately NULLABLE with no backfill: existing cycles keep
cycle_label = NULL and the frontend falls back to "รอบที่ <cycle_no>".

This is NOT lot_no — lot_no is a production/lot identifier; cycle_label is the
season name the admin chooses. They are independent columns.

Revision ID: 0036_plot_cycle_label
Revises: 0035_plot_cycles_rls
Create Date: 2026-07-15 00:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0036_plot_cycle_label"
down_revision = "0035_plot_cycles_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable add — no rewrite, no backfill, safe on a populated table. RLS
    # and grants on plot_cycles (migration 0035) are unaffected by a column add.
    op.execute("ALTER TABLE plot_cycles ADD COLUMN cycle_label VARCHAR(100);")


def downgrade() -> None:
    op.execute("ALTER TABLE plot_cycles DROP COLUMN IF EXISTS cycle_label;")

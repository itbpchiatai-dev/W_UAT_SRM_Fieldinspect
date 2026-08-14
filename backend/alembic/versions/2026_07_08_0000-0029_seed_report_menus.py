"""seed report menus — add the FarmLog "รายงาน" (Reports) menu and its first
child "สถานะแปลง" (Plot Status) so they appear in the sidebar of an
already-running DB without a full re-seed.

Data-only migration: no schema change. menu_items already exists (0005) and
these two rows mirror the DEFAULT_MENUS entries in app/seed.py. Idempotent via
ON CONFLICT on menu_items.key's UNIQUE constraint — re-running, or running
after a seed that already inserted them, is a no-op. Both hang off the
existing "farmlog" parent and are gated by plots.read (no new permission), so
every role that already sees the Plots menu also sees the report.

Revision ID: 0029_seed_report_menus
Revises: 0028_seed_provinces
Create Date: 2026-07-08 00:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0029_seed_report_menus"
down_revision = "0028_seed_provinces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Parent "รายงาน" — parent_id resolved from the existing "farmlog" node by
    # subselect. If "farmlog" somehow isn't present (a DB that never seeded the
    # FarmLog menus), the subselect yields NULL and the row is skipped by the
    # NOT EXISTS guard rather than creating an orphan top-level menu.
    op.execute(
        """
        INSERT INTO menu_items
            (key, label_th, label_en, icon, path, parent_id, order_index,
             required_permission_key, is_system)
        SELECT 'farmlog.reports', 'รายงาน', 'Reports', 'BarChart3',
               '/farmlog/reports', p.id, 40, 'plots.read', TRUE
        FROM menu_items p
        WHERE p.key = 'farmlog'
        ON CONFLICT (key) DO NOTHING
        """
    )
    # Child "สถานะแปลง" — hangs off the "farmlog.reports" node just inserted.
    op.execute(
        """
        INSERT INTO menu_items
            (key, label_th, label_en, icon, path, parent_id, order_index,
             required_permission_key, is_system)
        SELECT 'farmlog.reports.plotstatus', 'สถานะแปลง', 'Plot Status', 'Table2',
               '/farmlog/reports/plot-status', p.id, 10, 'plots.read', TRUE
        FROM menu_items p
        WHERE p.key = 'farmlog.reports'
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM menu_items WHERE key IN "
        "('farmlog.reports.plotstatus', 'farmlog.reports')"
    )

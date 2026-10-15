"""executive overview menu — land at the top level when there is no FarmLog node.

0059 hung the menu entry off the "farmlog" node and, copying 0029, inserted
nothing when that node was absent — on the theory that its absence meant a
database that had never seeded the FarmLog menus at all.

UAT proved the theory wrong. An admin had reshaped the sidebar through the
Menus page: the "farmlog" and "farmlog.admin" nodes were deleted and their
children moved to the top level. 0059 therefore granted the permission to
all three roles and then created no menu row — the page worked when its URL
was typed and could not be reached any other way. Nothing failed; the
INSERT ... SELECT simply selected nothing.

The rule this records: a missing parent is a layout someone chose, not a
broken install. This migration creates the row only if it does not exist
(ON CONFLICT DO NOTHING — wherever an admin has since moved it wins) and
puts it

  - under "farmlog" at order 5, when that node exists — 0059's intent; or
  - at the top level at order 15 otherwise, after the order-10 entries
    (Dashboard, Suppliers) and before แปลง (20).

The admin can drag it anywhere from the Menus page afterwards.

menu_items carries no row-level security, so it is safe to write from a
migration (see tests/unit/test_rls_migration_backfill_guard_round_n.py).

Revision ID: 0060_executive_menu_fallback
Revises: 0059_executive_dashboard
Create Date: 2026-10-15 01:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0060_executive_menu_fallback"
down_revision = "0059_executive_dashboard"
branch_labels = None
depends_on = None

PERMISSION_KEY = "executive_dashboard.read"

MENU_KEY = "farmlog.executive"
MENU_TH = "ภาพรวมผู้บริหาร"
MENU_EN = "Executive Overview"
MENU_ICON = "Gauge"
MENU_PATH = "/farmlog/executive"
PARENT_KEY = "farmlog"
ORDER_UNDER_PARENT = 5
ORDER_TOP_LEVEL = 15


def upgrade() -> None:
    # A scalar subquery yields NULL when the parent is absent, which is
    # exactly "top level" — unlike 0059's FROM-clause join, which yielded
    # no row at all.
    op.execute(
        f"""
        INSERT INTO menu_items
            (key, label_th, label_en, icon, path, parent_id, order_index,
             required_permission_key, is_system)
        SELECT '{MENU_KEY}', '{MENU_TH}', '{MENU_EN}', '{MENU_ICON}',
               '{MENU_PATH}',
               (SELECT id FROM menu_items WHERE key = '{PARENT_KEY}'),
               CASE WHEN EXISTS (SELECT 1 FROM menu_items WHERE key = '{PARENT_KEY}')
                    THEN {ORDER_UNDER_PARENT} ELSE {ORDER_TOP_LEVEL} END,
               '{PERMISSION_KEY}', TRUE
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    # Deliberately nothing. On a database where 0059 already created the
    # row, this migration did nothing — deleting it here would strip 0059's
    # menu while the schema still claims to be at 0059. 0059's own downgrade
    # removes the row either way.
    pass

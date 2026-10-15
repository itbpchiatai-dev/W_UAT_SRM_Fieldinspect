"""executive overview — permission, grants, and the menu entry (round 42).

A company-wide page: plots inspected vs registered, visits in the last
7/30 days, plots overdue past 30 days, average condition score, and yield
against plan across every supplier.

Its own key rather than plots.read, which is the mistake migration 0058 had
to undo for the reports: plots.read is also held by supplier:owner, so reusing
it would put a page of whole-company totals in a Supplier's sidebar. The key
goes to the three roles scope.py already lists in _FULL_ACCESS_ROLES
(super_admin / admin / supervisor) — so it grants a new VIEW of rows those
roles can already open one at a time, never access to new rows. RLS is
untouched and remains the backstop.

Menu: a first child of "FarmLog" at order 5, above "จัดการข้อมูล" (order 10) —
an overview reads as a starting point, not as something filed under
administration.

roles / permissions / role_permissions / menu_items carry no row-level
security, so unlike plots/records they are safe to write from a migration —
see tests/unit/test_rls_migration_backfill_guard_round_n.py for the rule.

Revision ID: 0059_executive_dashboard
Revises: 0058_report_permissions
Create Date: 2026-10-15 00:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0059_executive_dashboard"
down_revision = "0058_report_permissions"
branch_labels = None
depends_on = None

PERMISSION_KEY = "executive_dashboard.read"
PERMISSION_LABEL = "ดูภาพรวมผู้บริหาร"

MENU_KEY = "farmlog.executive"
MENU_TH = "ภาพรวมผู้บริหาร"
MENU_EN = "Executive Overview"
MENU_ICON = "Gauge"
MENU_PATH = "/farmlog/executive"
MENU_ORDER = 5


def upgrade() -> None:
    # `permissions` has no created_at/updated_at columns (migration 0005).
    op.execute(
        f"""
        INSERT INTO permissions (id, key, display_name, category, is_menu)
        VALUES (gen_random_uuid(), '{PERMISSION_KEY}', '{PERMISSION_LABEL}',
                'farmlog', TRUE)
        ON CONFLICT (key) DO NOTHING
        """
    )

    # internal:super_admin is listed EXPLICITLY and that is not redundant:
    # its keys=None in DEFAULT_ROLES means "every permission" only at SEED
    # time, so a permission a MIGRATION adds later reaches it only through a
    # grant like this (the lesson migrations 0057 and 0058 both record).
    #
    # supplier:owner is deliberately absent — this page is company-wide
    # totals, and a Supplier seeing its own numbers under that title would be
    # reading a different thing than the title claims.
    grants = ("internal:super_admin", "internal:admin", "farmlog:supervisor")
    names = ", ".join(f"'{r}'" for r in grants)
    # Yields nothing — and inserts nothing — if a role or the permission is
    # missing, so this never fails on a partially-seeded database.
    op.execute(
        f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r CROSS JOIN permissions p
        WHERE r.name IN ({names}) AND p.key = '{PERMISSION_KEY}'
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )

    # parent_id resolved by subselect from the existing "farmlog" node. If it
    # is somehow absent (a DB that never seeded the FarmLog menus) the SELECT
    # yields no row and nothing is inserted, rather than creating an orphan
    # top-level menu.
    op.execute(
        f"""
        INSERT INTO menu_items
            (key, label_th, label_en, icon, path, parent_id, order_index,
             required_permission_key, is_system)
        SELECT '{MENU_KEY}', '{MENU_TH}', '{MENU_EN}', '{MENU_ICON}',
               '{MENU_PATH}', p.id, {MENU_ORDER}, '{PERMISSION_KEY}', TRUE
        FROM menu_items p
        WHERE p.key = 'farmlog'
        ON CONFLICT (key) DO UPDATE
            SET label_th = EXCLUDED.label_th,
                label_en = EXCLUDED.label_en,
                icon = EXCLUDED.icon,
                path = EXCLUDED.path,
                order_index = EXCLUDED.order_index,
                required_permission_key = EXCLUDED.required_permission_key
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM menu_items WHERE key = '{MENU_KEY}'")
    # role_permissions first: it references permissions, and nothing here
    # cascades.
    op.execute(
        f"""
        DELETE FROM role_permissions rp USING permissions p
        WHERE rp.permission_id = p.id AND p.key = '{PERMISSION_KEY}'
        """
    )
    # Per-user overrides reference permissions too — a grant made by hand
    # would otherwise block the delete below with a foreign-key violation.
    op.execute(
        f"""
        DELETE FROM user_permission_overrides o USING permissions p
        WHERE o.permission_id = p.id AND p.key = '{PERMISSION_KEY}'
        """
    )
    op.execute(f"DELETE FROM permissions WHERE key = '{PERMISSION_KEY}'")

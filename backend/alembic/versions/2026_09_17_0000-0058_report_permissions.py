"""per-report permissions + the Supplier's own two reports (round 29).

All four report endpoints were gated by `plots.read` — "may see plots". So
anyone who could open the Plots page ran both reports and downloaded both
workbooks, and there was no way to grant one report without the other.
supplier:owner holds plots.read, so Suppliers were already running the
INTERNAL reports.

The rows were never the problem: RLS scopes every report to the caller's own
supplier, which is what makes a Supplier-facing report safe at all. What was
missing was control over WHICH report and WHICH columns.

    reports.plot_status              สถานะแปลงปัจจุบัน
    reports.cycle_yield              ผลผลิตตามรอบปลูก
    reports.plot_status_supplier     สถานะแปลงปัจจุบัน (Supplier)
    reports.cycle_yield_supplier     ผลผลิตตามรอบปลูก (Supplier)

Grants: the internal pair to super_admin / admin / supervisor, the Supplier
pair to supplier:owner. An admin who needs to see exactly what a Supplier sees
is given that key deliberately, not by default.

Menus: "รายงาน" loses its own permission and becomes a pure parent — the menu
builder (api/v1/me.py) keeps a permission-less parent only while it has a
visible child, so the group appears for whoever can run at least one report
and disappears for everyone else. Its four children carry a key each.

roles / permissions / role_permissions / menu_items carry no row-level
security, so unlike plots/records they are safe to write from a migration —
see tests/unit/test_rls_migration_backfill_guard_round_n.py for the rule.

Revision ID: 0058_report_permissions
Revises: 0057_plots_cancel_cycle
Create Date: 2026-09-17 00:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0058_report_permissions"
down_revision = "0057_plots_cancel_cycle"
branch_labels = None
depends_on = None

PERMISSIONS = [
    ("reports.plot_status", "รายงานสถานะแปลงปัจจุบัน"),
    ("reports.cycle_yield", "รายงานผลผลิตตามรอบปลูก"),
    ("reports.plot_status_supplier", "รายงานสถานะแปลงปัจจุบัน (Supplier)"),
    ("reports.cycle_yield_supplier", "รายงานผลผลิตตามรอบปลูก (Supplier)"),
]

MENUS = [
    # key, label_th, label_en, icon, path, order, permission
    ("farmlog.reports.plotstatus", "สถานะแปลงปัจจุบัน", "Plot Status", "Table2",
     "/farmlog/reports/plot-status", 10, "reports.plot_status"),
    ("farmlog.reports.cycleyield", "ผลผลิตตามรอบปลูก", "Cycle Yield", "Table2",
     "/farmlog/reports/cycle-yield", 20, "reports.cycle_yield"),
    ("farmlog.reports.plotstatus.supplier", "สถานะแปลงปัจจุบัน (Supplier)",
     "Plot Status (Supplier)", "Table2",
     "/farmlog/reports/supplier/plot-status", 30, "reports.plot_status_supplier"),
    ("farmlog.reports.cycleyield.supplier", "ผลผลิตตามรอบปลูก (Supplier)",
     "Cycle Yield (Supplier)", "Table2",
     "/farmlog/reports/supplier/cycle-yield", 40, "reports.cycle_yield_supplier"),
]


def upgrade() -> None:
    # `permissions` has no created_at/updated_at columns (migration 0005).
    for key, label in PERMISSIONS:
        op.execute(
            f"""
            INSERT INTO permissions (id, key, display_name, category, is_menu)
            VALUES (gen_random_uuid(), '{key}', '{label}', 'farmlog', TRUE)
            ON CONFLICT (key) DO NOTHING
            """
        )
    # Who gets what, spelled inside upgrade() so the grants are readable next
    # to the SQL that performs them — and so the scan in
    # tests/unit/test_cancel_cycle_round_s.py can see them, which is the point
    # of that guard.
    #
    # internal:super_admin is listed EXPLICITLY, and that is not redundant:
    # its keys=None in DEFAULT_ROLES means "every permission" only at SEED
    # time, so a permission a MIGRATION adds later reaches it only through a
    # grant like this (the lesson migration 0057 records).
    grants = {
        "reports.plot_status":
            ("internal:super_admin", "internal:admin", "farmlog:supervisor"),
        "reports.cycle_yield":
            ("internal:super_admin", "internal:admin", "farmlog:supervisor"),
        "reports.plot_status_supplier": ("internal:super_admin", "supplier:owner"),
        "reports.cycle_yield_supplier": ("internal:super_admin", "supplier:owner"),
    }
    for key, roles in grants.items():
        names = ", ".join(f"'{r}'" for r in roles)
        # Yields nothing — and inserts nothing — if a role or the permission is
        # missing, so this never fails on a partially-seeded database.
        op.execute(
            f"""
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT r.id, p.id
            FROM roles r CROSS JOIN permissions p
            WHERE r.name IN ({names}) AND p.key = '{key}'
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        )

    # The group becomes a pure parent; each report carries its own key.
    op.execute(
        "UPDATE menu_items SET required_permission_key = '' WHERE key = 'farmlog.reports'"
    )
    for key, th, en, icon, path, order, perm in MENUS:
        op.execute(
            f"""
            INSERT INTO menu_items
                (key, label_th, label_en, icon, path, parent_id, order_index,
                 required_permission_key, is_system)
            SELECT '{key}', '{th}', '{en}', '{icon}', '{path}', p.id, {order},
                   '{perm}', TRUE
            FROM menu_items p
            WHERE p.key = 'farmlog.reports'
            ON CONFLICT (key) DO UPDATE
                SET label_th = EXCLUDED.label_th,
                    label_en = EXCLUDED.label_en,
                    path = EXCLUDED.path,
                    order_index = EXCLUDED.order_index,
                    required_permission_key = EXCLUDED.required_permission_key
            """
        )


def downgrade() -> None:
    keys = ", ".join(f"'{k}'" for k, *_ in PERMISSIONS)
    menu_keys = ", ".join(
        f"'{k}'" for k, *_ in MENUS if k != "farmlog.reports.plotstatus"
    )
    op.execute(f"DELETE FROM menu_items WHERE key IN ({menu_keys})")
    # The one menu that predates this round goes back to plots.read, as does
    # its parent.
    op.execute(
        "UPDATE menu_items SET required_permission_key = 'plots.read', "
        "label_th = 'สถานะแปลง' WHERE key = 'farmlog.reports.plotstatus'"
    )
    op.execute(
        "UPDATE menu_items SET required_permission_key = 'plots.read' "
        "WHERE key = 'farmlog.reports'"
    )
    op.execute(
        f"""
        DELETE FROM role_permissions rp USING permissions p
        WHERE rp.permission_id = p.id AND p.key IN ({keys})
        """
    )
    op.execute(f"DELETE FROM permissions WHERE key IN ({keys})")

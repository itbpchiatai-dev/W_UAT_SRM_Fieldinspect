"""plots.cancel_cycle — a Supplier may END its own failed season (round S).

Round P made supplier:owner view-only, because plots.update was never merely
"edit a plot": it unlocked start cycle, edit cycle, close-as-harvested, and the
whole Excel importer as one bundle.

One of those is genuinely the Supplier's to do. When a planting fails, the
Supplier is who knows — and who should be able to end the season without
waiting on Chiatai. So that single action gets its own key rather than handing
plots.update back:

    plots.update        start / edit / close-as-HARVESTED / Excel import
                        -> Chiatai only. Closing as harvested is a claim about
                           a delivered crop.
    plots.cancel_cycle  end the season as CANCELLED, with a written reason
                        -> supplier:owner AND internal:admin.

A cancel also takes the plot out of service, whoever performs it — see
api/v1/plots.py cancel_plot_cycle. That is the one place a supplier:owner
causes a deactivation, and it is deliberate: under "one plot, one cycle"
(round E) a cancelled plot is finished, and leaving it in service would keep
it in the farmer's /public/inspect list forever.

Scope: the permission catalogue plus two grants. Nothing else about either role
changes, and user_permission_overrides is not touched — a per-user grant is an
admin's individual decision.

role_permissions / roles / permissions carry no row-level security, so unlike
plots/records they are safe to write from a migration — see
tests/unit/test_rls_migration_backfill_guard_round_n.py for the rule.

Revision ID: 0057_plots_cancel_cycle
Revises: 0056_supplier_owner_view_only
Create Date: 2026-09-11 00:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0057_plots_cancel_cycle"
down_revision = "0056_supplier_owner_view_only"
branch_labels = None
depends_on = None

KEY = "plots.cancel_cycle"
ROLES = ("internal:admin", "supplier:owner")


def upgrade() -> None:
    # `permissions` has no created_at/updated_at columns (migration 0005) —
    # do not name them here.
    op.execute(
        """
        INSERT INTO permissions (id, key, display_name, category, is_menu)
        VALUES (gen_random_uuid(), 'plots.cancel_cycle',
                'ยกเลิกรอบปลูก (จบด้วยการยกเลิก)', 'farmlog', FALSE)
        ON CONFLICT (key) DO NOTHING
        """
    )
    # internal:super_admin is listed EXPLICITLY, and that is not redundant.
    # Its keys=None in DEFAULT_ROLES means "every permission" only at SEED
    # time — the seeder expands it against the catalogue as it stands then.
    # A permission a MIGRATION adds later is new to an existing database, so
    # nothing grants it to super_admin unless this statement does. Leaving it
    # out (the first version of this migration did) left the highest-privilege
    # role missing exactly one key, silently. Migration 0051 got this right;
    # copy it, not the first draft of this one.
    #
    # The SELECT yields nothing — and inserts nothing — if a role or the
    # permission is missing, so this never fails on a partially-seeded
    # database.
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        CROSS JOIN permissions p
        WHERE r.name IN ('internal:super_admin', 'internal:admin', 'supplier:owner')
          AND p.key = 'plots.cancel_cycle'
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )


def downgrade() -> None:
    """role_permissions and user_permission_overrides both FK the permission
    with ON DELETE CASCADE (migration 0005), so deleting the catalogue row is
    enough; the explicit grant delete first is defensive and harmless."""
    op.execute(
        """
        DELETE FROM role_permissions rp
        USING permissions p
        WHERE rp.permission_id = p.id AND p.key = 'plots.cancel_cycle'
        """
    )
    op.execute("DELETE FROM permissions WHERE key = 'plots.cancel_cycle'")

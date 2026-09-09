"""supplier:owner becomes view-only — drop plots.create / plots.update (round P).

A Supplier Owner is, by decision, a farmer who can log in: the same inspection
they could already record through /public/inspect, plus the thing that flow
cannot give them — reading their own supplier's plots, history and reports.
Managing plots is Chiatai's job.

plots.update was much wider than "edit a plot". It unlocked start cycle, edit
cycle, CLOSE cycle, edit plot, and the entire Excel importer. Closing became
load-bearing in round P, where a close also deactivates the plot for anyone
holding plots.delete — so leaving plots.update on this role would have kept
suppliers one permission away from an action deliberately reserved for admins.

Scope of this migration: role_permissions rows for the 'supplier:owner' ROLE
only. It deliberately does NOT touch user_permission_overrides — a per-user
grant is an admin's explicit, individual decision and is not this role's to
revoke. Nothing else about the role changes: suppliers.read, plots.read,
records.read and records.create all stay.

Why a migration and not just app/seed.py: seeding only runs on a fresh
database. Every existing environment already has the two rows.

role_permissions / roles / permissions carry NO row-level security, so unlike
plots/records/plot_cycles they are safe to write from a migration — see
tests/unit/test_rls_migration_backfill_guard_round_n.py for the rule this
exemption is measured against.

Revision ID: 0056_supplier_owner_view_only
Revises: 0055_log_partition_maintenance
Create Date: 2026-09-09 00:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0056_supplier_owner_view_only"
down_revision = "0055_log_partition_maintenance"
branch_labels = None
depends_on = None

ROLE = "supplier:owner"
KEYS = ("plots.create", "plots.update")


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions rp
        USING roles r, permissions p
        WHERE rp.role_id = r.id
          AND rp.permission_id = p.id
          AND r.name = 'supplier:owner'
          AND p.key IN ('plots.create', 'plots.update')
        """
    )


def downgrade() -> None:
    """Put the two keys back. ON CONFLICT DO NOTHING so a re-run, or a
    database where a later seed already restored them, is a no-op rather than
    a unique-violation."""
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r, permissions p
        WHERE r.name = 'supplier:owner'
          AND p.key IN ('plots.create', 'plots.update')
        ON CONFLICT DO NOTHING
        """
    )

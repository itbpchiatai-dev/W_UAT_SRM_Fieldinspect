"""suppliers menu requires suppliers.update — not suppliers.read anymore.

Round 8-25Q: a supplier:owner user could see the "Suppliers" admin CRUD
menu/page because it was gated on suppliers.read — the same permission that
also has to stay broad (it populates the Supplier dropdown on Plots and
RecordForm, which supplier:owner and farmlog:field_officer legitimately
need). Revoking suppliers.read from those roles would break that dropdown
everywhere else; gating the MENU on suppliers.update instead leaves
suppliers.read untouched and only changes who sees the standalone admin
page. Confirmed with the user: internal:admin/internal:super_admin are the
only roles holding suppliers.update today, so this also (correctly, by
design — confirmed) hides the menu from farmlog:supervisor and
farmlog:field_officer, not just supplier:owner.

Data-only migration: no schema change, no role/permission table touched —
only the existing menu_items row's required_permission_key column. Matches
app/seed.py's DEFAULT_MENUS entry for farmlog.admin.suppliers, updated in
the same round (seed.py's own insert-if-missing upsert never touches an
already-existing row, so a fresh future deploy AND this migration both need
the new value — this migration is what fixes an already-seeded database
like UAT).

Revision ID: 0052_suppliers_menu_update
Revises: 0051_user_auth_version
Create Date: 2026-08-25 00:00:00
"""
from __future__ import annotations

from alembic import op

# NOTE: alembic_version.version_num is varchar(32) — this id is 26 chars,
# deliberately short (the original "0052_suppliers_menu_requires_update"
# was 35 and failed with StringDataRightTruncation on UAT; see 0016's own
# password-quoting bug for the same "never tested against a real database
# until deploy" lesson).
revision = "0052_suppliers_menu_update"
down_revision = "0051_user_auth_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE menu_items SET required_permission_key = 'suppliers.update' "
        "WHERE key = 'farmlog.admin.suppliers'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE menu_items SET required_permission_key = 'suppliers.read' "
        "WHERE key = 'farmlog.admin.suppliers'"
    )

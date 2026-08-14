"""RLS UUID-cast hardening — guard the 'assigned' branch (round 8-1).

migration 0016's records_scope and plots_scope policies cast app.user_id
with a bare `::uuid`:

    WHEN 'assigned' THEN
        plot_id IN (
            SELECT plot_id FROM plot_assignments
            WHERE user_id = current_setting('app.user_id', true)::uuid
        )

Postgres hoists this uncorrelated subquery into an InitPlan that runs once,
unconditionally — regardless of which CASE branch actually applies at
runtime. So an empty/missing app.user_id GUC throws "invalid input syntax
for type uuid" even under scope='all'/'supplier', where app.user_id is never
otherwise consulted. The app layer already works around this with the
`_NO_USER_ID` sentinel (`app/api/deps/scope.py`), and migration 0035 already
fixed this properly at the DB layer for plot_cycles_scope with
`NULLIF(current_setting('app.user_id', true), '')::uuid` — this migration
brings records_scope and plots_scope in line with that same fix, so the
policy is safe on its own without relying on the app-layer sentinel.

Pure hardening: does not change `all`/`supplier`/`assigned` visibility
semantics, write (`WITH CHECK`) semantics, table/role/policy names, or the
`FOR ALL` scope of either policy — only wraps the cast so an empty string
becomes NULL (no match, no crash) instead of a cast error. plot_cycles_scope
(migration 0035) is not touched — it already has the guard.

Revision ID: 0037_rls_uuid_guard
Revises: 0036_plot_cycle_label
Create Date: 2026-07-16 00:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0037_rls_uuid_guard"
down_revision = "0036_plot_cycle_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER POLICY records_scope ON records
        USING (
            CASE current_setting('app.scope', true)
                WHEN 'all'      THEN true
                WHEN 'supplier' THEN
                    supplier_id::text = current_setting('app.supplier_id', true)
                WHEN 'assigned' THEN
                    plot_id IN (
                        SELECT plot_id FROM plot_assignments
                        WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                    )
                ELSE false
            END
        )
        WITH CHECK (
            CASE current_setting('app.scope', true)
                WHEN 'all'      THEN true
                WHEN 'supplier' THEN
                    supplier_id::text = current_setting('app.supplier_id', true)
                WHEN 'assigned' THEN
                    plot_id IN (
                        SELECT plot_id FROM plot_assignments
                        WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                    )
                ELSE false
            END
        );
    """)

    op.execute("""
    ALTER POLICY plots_scope ON plots
        USING (
            CASE current_setting('app.scope', true)
                WHEN 'all'      THEN true
                WHEN 'supplier' THEN
                    supplier_id::text = current_setting('app.supplier_id', true)
                WHEN 'assigned' THEN
                    id IN (
                        SELECT plot_id FROM plot_assignments
                        WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                    )
                ELSE false
            END
        )
        WITH CHECK (
            CASE current_setting('app.scope', true)
                WHEN 'all'      THEN true
                WHEN 'supplier' THEN
                    supplier_id::text = current_setting('app.supplier_id', true)
                WHEN 'assigned' THEN
                    id IN (
                        SELECT plot_id FROM plot_assignments
                        WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                    )
                ELSE false
            END
        );
    """)


def downgrade() -> None:
    # Restore the exact bare-cast expressions from migration 0016.
    op.execute("""
    ALTER POLICY records_scope ON records
        USING (
            CASE current_setting('app.scope', true)
                WHEN 'all'      THEN true
                WHEN 'supplier' THEN
                    supplier_id::text = current_setting('app.supplier_id', true)
                WHEN 'assigned' THEN
                    plot_id IN (
                        SELECT plot_id FROM plot_assignments
                        WHERE user_id = current_setting('app.user_id', true)::uuid
                    )
                ELSE false
            END
        )
        WITH CHECK (
            CASE current_setting('app.scope', true)
                WHEN 'all'      THEN true
                WHEN 'supplier' THEN
                    supplier_id::text = current_setting('app.supplier_id', true)
                WHEN 'assigned' THEN
                    plot_id IN (
                        SELECT plot_id FROM plot_assignments
                        WHERE user_id = current_setting('app.user_id', true)::uuid
                    )
                ELSE false
            END
        );
    """)

    op.execute("""
    ALTER POLICY plots_scope ON plots
        USING (
            CASE current_setting('app.scope', true)
                WHEN 'all'      THEN true
                WHEN 'supplier' THEN
                    supplier_id::text = current_setting('app.supplier_id', true)
                WHEN 'assigned' THEN
                    id IN (
                        SELECT plot_id FROM plot_assignments
                        WHERE user_id = current_setting('app.user_id', true)::uuid
                    )
                ELSE false
            END
        )
        WITH CHECK (
            CASE current_setting('app.scope', true)
                WHEN 'all'      THEN true
                WHEN 'supplier' THEN
                    supplier_id::text = current_setting('app.supplier_id', true)
                WHEN 'assigned' THEN
                    id IN (
                        SELECT plot_id FROM plot_assignments
                        WHERE user_id = current_setting('app.user_id', true)::uuid
                    )
                ELSE false
            END
        );
    """)

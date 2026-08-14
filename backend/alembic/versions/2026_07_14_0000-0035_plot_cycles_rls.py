"""plot_cycles RLS — scope isolation via the parent plot (round 7.2A).

migration 0034 created plot_cycles but left it without Row Level Security.
The round-7.2B lifecycle endpoints will query plot_cycles directly, so it
needs the same scope isolation plots/records already have (migration 0016)
BEFORE those endpoints exist.

plot_cycles has no supplier_id of its own — a cycle's visibility is its
PARENT PLOT's visibility. This policy therefore resolves scope THROUGH plots
(supplier branch: the plot's supplier; assigned branch: plot_assignments),
reusing the exact same app.scope / app.supplier_id / app.user_id GUC
vocabulary as 0016 — no new RLS vocabulary invented.

Two deliberate differences from a straight copy of plots_scope:
  - READ (USING) allows all/supplier/assigned (a field officer can SEE the
    cycles of a plot they're assigned to).
  - WRITE (WITH CHECK) allows only all/supplier — a field officer
    (scope='assigned') must NOT create/close cycles at the DB level, as
    defense-in-depth on top of whatever permission the 7.2B write endpoints
    add. (If 7.2B needs assigned-write, it must add it in a NEW migration.)
  - The 'assigned' subquery casts app.user_id with NULLIF(...,'')::uuid, not a
    bare ::uuid. Postgres hoists that uncorrelated subquery into an InitPlan
    that runs once regardless of the active CASE branch (see
    app/api/deps/scope.py's _NO_USER_ID note), so a bare cast crashes on an
    empty app.user_id even under scope='all'. NULLIF turns '' into NULL →
    no rows, no crash — belt-and-suspenders over the app-layer sentinel.

Revision ID: 0035_plot_cycles_rls
Revises: 0034_plot_cycles
Create Date: 2026-07-14 00:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0035_plot_cycles_rls"
down_revision = "0034_plot_cycles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0016's ALTER DEFAULT PRIVILEGES already grants DML on tables created
    # after it (plot_cycles is one), but be explicit + idempotent so RLS can
    # never silently deny a correctly-scoped srm_app just for a missing grant.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON plot_cycles TO srm_app;")

    # Enable + FORCE (applies even to the table owner), same as plots/records.
    op.execute("ALTER TABLE plot_cycles ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE plot_cycles FORCE ROW LEVEL SECURITY;")

    op.execute("""
    CREATE POLICY plot_cycles_scope ON plot_cycles
        FOR ALL
        TO srm_app
        USING (
            CASE current_setting('app.scope', true)
                WHEN 'all'      THEN true
                WHEN 'supplier' THEN plot_id IN (
                    SELECT id FROM plots
                    WHERE supplier_id::text = current_setting('app.supplier_id', true)
                )
                WHEN 'assigned' THEN plot_id IN (
                    SELECT plot_id FROM plot_assignments
                    WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                )
                ELSE false
            END
        )
        WITH CHECK (
            CASE current_setting('app.scope', true)
                WHEN 'all'      THEN true
                WHEN 'supplier' THEN plot_id IN (
                    SELECT id FROM plots
                    WHERE supplier_id::text = current_setting('app.supplier_id', true)
                )
                ELSE false
            END
        );
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS plot_cycles_scope ON plot_cycles;")
    op.execute("ALTER TABLE plot_cycles DISABLE ROW LEVEL SECURITY;")
    op.execute("REVOKE ALL ON plot_cycles FROM srm_app;")

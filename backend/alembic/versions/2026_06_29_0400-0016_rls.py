"""RLS — Row Level Security on records + plots (Step 8).

Creates srm_app runtime role, grants DML, enables RLS with scope-based
policies on records and plots.

Revision ID: 0016_rls
Revises: 0015_records
Create Date: 2026-06-29 04:00:00
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

revision = "0016_rls"
down_revision = "0015_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fail-fast: DB_APP_PASSWORD MUST be set (non-blank) in the environment
    # this migration runs in. No hardcoded fallback — a missing/blank value
    # used to silently create/reset the srm_app runtime role to a fixed,
    # source-visible password. Never log or echo the value itself.
    app_password = os.environ.get("DB_APP_PASSWORD", "").strip()
    if not app_password:
        raise RuntimeError(
            "DB_APP_PASSWORD is not set (or blank). This migration creates/"
            "resets the srm_app runtime role's password and refuses to run "
            "with no value — set DB_APP_PASSWORD in the environment before "
            "upgrading."
        )

    # 1. Create limited runtime role (no superuser, no BYPASSRLS). Postgres's
    # own grammar for CREATE/ALTER ROLE's PASSWORD clause only accepts a
    # string literal (not a query bind parameter — `PASSWORD $1` is a syntax
    # error on every Postgres version), so the password can't be bound the
    # normal parameterized way here. Instead it is passed as an ordinary
    # bind parameter to `SELECT quote_literal(:pw)` — Postgres itself then
    # produces a correctly quoted/escaped SQL string literal for that exact
    # value (handles embedded quotes, backslashes, everything; this is the
    # standard, documented idiom Postgres itself uses for dynamic SQL, e.g.
    # inside plpgsql functions) — so the raw password is still never
    # string-formatted/f-string-concatenated by our own code, and never
    # visible in this file's own source or in any query log that only
    # records the parameterized SELECT's statement text. A DO $$ ... $$
    # block's body is a separate, opaque string to the outer SQL parser —
    # parameters bound on the outer statement cannot reach inside it — so the
    # exists-check is done as an ordinary (parameterizable) top-level
    # statement instead of inside PL/pgSQL.
    bind = op.get_bind()
    role_exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = 'srm_app'")
    ).scalar()
    quoted_password = bind.execute(
        sa.text("SELECT quote_literal(:pw)"), {"pw": app_password}
    ).scalar()
    if role_exists:
        op.execute(f"ALTER ROLE srm_app WITH PASSWORD {quoted_password}")
    else:
        op.execute(f"CREATE ROLE srm_app LOGIN PASSWORD {quoted_password} NOINHERIT")

    # 2. Grant schema access + DML on existing tables
    op.execute("GRANT USAGE ON SCHEMA public TO srm_app;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO srm_app;"
    )
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO srm_app;")
    # Future tables/sequences (runs in schema owner's context = srm_fieldinspect)
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO srm_app;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO srm_app;"
    )

    # 3. Enable RLS on records — FORCE applies even to table owner
    op.execute("ALTER TABLE records ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE records FORCE ROW LEVEL SECURITY;")

    # 4. Records scope policy (SELECT / UPDATE / DELETE via USING,
    #    INSERT via WITH CHECK)
    op.execute("""
    CREATE POLICY records_scope ON records
        FOR ALL
        TO srm_app
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

    # 5. Enable RLS on plots
    op.execute("ALTER TABLE plots ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE plots FORCE ROW LEVEL SECURITY;")

    # 6. Plots scope policy
    op.execute("""
    CREATE POLICY plots_scope ON plots
        FOR ALL
        TO srm_app
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


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS plots_scope ON plots;")
    op.execute("ALTER TABLE plots DISABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS records_scope ON records;")
    op.execute("ALTER TABLE records DISABLE ROW LEVEL SECURITY;")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM srm_app;")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM srm_app;")
    op.execute("REVOKE USAGE ON SCHEMA public FROM srm_app;")
    op.execute("DROP ROLE IF EXISTS srm_app;")

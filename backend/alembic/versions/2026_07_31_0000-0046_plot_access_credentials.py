"""plot_access_credentials — per-Plot inspection password ("รหัสยืนยันแปลง")
storage + RLS (round 8-9A).

Backend security FOUNDATION only. This round adds the storage; the public
inspection flow (/public/inspection-access/*) is UNCHANGED and does not consult
these rows yet. Enforcement lands in round 8-9C behind
PUBLIC_PLOT_PASSWORD_ENFORCEMENT (still false).

  1. CREATE plot_access_credentials — at most ONE credential per plot
     (UNIQUE(plot_id)), holding:
       - password_hash: bcrypt of the 6-digit PIN. NEVER plaintext.
       - password_lookup_digest: HMAC-SHA256(pepper, PIN), lowercase hex(64).
         A BLIND INDEX so round 8-9C can resolve "phone + password" to the
         matching plots with ONE indexed lookup instead of bcrypt-verifying
         every candidate plot.
       - credential_version: bumped on every real password change, so a future
         enforcement round can invalidate anything minted against the old one.
     Deliberately NO unique constraint on password_lookup_digest or
     password_hash: several plots MAY intentionally share the same password,
     and changing one plot's password must never affect another's.
  2. RLS on plot_access_credentials — scope resolved THROUGH the parent plot,
     reusing the exact app.scope/app.supplier_id/app.user_id vocabulary and the
     NULLIF(...,'')::uuid guard from migrations 0016/0035/0037/0039. USING and
     WITH CHECK are identical (all/supplier/assigned), mirroring the parent
     plots_scope policy — this neither broadens nor narrows Plot's own scope.

Additive only: new table, zero rows on creation. No backfill, no data mutation,
no reseed, no change to plots/records/plot_cycles/plot_access_phones data, no
plaintext password column anywhere. Transactional DDL — any failure rolls the
whole migration back.

Revision ID: 0046_plot_access_credentials
Revises: 0045_relax_yield_pct_cap
Create Date: 2026-07-31 00:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0046_plot_access_credentials"
down_revision = "0045_relax_yield_pct_cap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. plot_access_credentials table. Constraint/index names match the ORM
    #    naming convention (app/db/base.py) so model metadata and this
    #    migration agree exactly.
    op.execute(
        """
        CREATE TABLE plot_access_credentials (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            plot_id UUID NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            password_lookup_digest CHAR(64) NOT NULL,
            credential_version INTEGER NOT NULL DEFAULT 1,
            is_active BOOLEAN NOT NULL DEFAULT true,
            updated_by_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_plot_access_credentials_plot_id UNIQUE (plot_id),
            CONSTRAINT ck_plot_access_credentials_credential_version_positive
                CHECK (credential_version >= 1),
            -- Blind index must always be lowercase hex(64) — the helper emits
            -- hexdigest(), this is the backstop against a mixed-case/short
            -- value ever landing and silently missing on lookup.
            CONSTRAINT ck_plot_access_credentials_password_lookup_digest_format
                CHECK (password_lookup_digest ~ '^[0-9a-f]{64}$'),
            CONSTRAINT fk_plot_access_credentials_plot_id_plots
                FOREIGN KEY (plot_id) REFERENCES plots(id) ON DELETE CASCADE,
            CONSTRAINT fk_plot_access_credentials_updated_by_id_users
                FOREIGN KEY (updated_by_id) REFERENCES users(id) ON DELETE SET NULL
        );
        -- Blind-index lookup for round 8-9C (phone + password → plots). NOT
        -- unique: several plots may deliberately share one password.
        CREATE INDEX ix_plot_access_credentials_password_lookup_digest
            ON plot_access_credentials (password_lookup_digest);
        CREATE INDEX ix_plot_access_credentials_updated_by_id
            ON plot_access_credentials (updated_by_id);
        """
    )

    # 2. RLS — scope THROUGH the parent plot (plot_access_credentials has no
    #    supplier_id of its own), same vocabulary + NULLIF guard as 0016/0035/
    #    0037/0039. USING == WITH CHECK (all/supplier/assigned), mirroring
    #    plots_scope — no broadening/narrowing of Plot's own scope. 0016's ALTER
    #    DEFAULT PRIVILEGES already grants DML on tables created after it, but
    #    be explicit + idempotent so RLS can't silently deny a correctly-scoped
    #    srm_app just for a missing grant.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON plot_access_credentials TO srm_app;"
    )
    op.execute("ALTER TABLE plot_access_credentials ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE plot_access_credentials FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY plot_access_credentials_scope ON plot_access_credentials
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
                    WHEN 'assigned' THEN plot_id IN (
                        SELECT plot_id FROM plot_assignments
                        WHERE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                    )
                    ELSE false
                END
            );
        """
    )


def downgrade() -> None:
    # Drop ONLY what this migration created. Nothing here touches plots,
    # records, plot_cycles or plot_access_phones — their data and policies are
    # untouched in both directions.
    op.execute(
        "DROP POLICY IF EXISTS plot_access_credentials_scope ON plot_access_credentials;"
    )
    op.execute("ALTER TABLE plot_access_credentials DISABLE ROW LEVEL SECURITY;")
    op.execute("REVOKE ALL ON plot_access_credentials FROM srm_app;")
    op.execute(
        """
        DROP INDEX IF EXISTS ix_plot_access_credentials_updated_by_id;
        DROP INDEX IF EXISTS ix_plot_access_credentials_password_lookup_digest;
        DROP TABLE IF EXISTS plot_access_credentials;
        """
    )

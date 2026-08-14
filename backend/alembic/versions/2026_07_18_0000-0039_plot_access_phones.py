"""plot_access_phones — per-plot phone access keys + Record attribution + RLS
(round 8-3A).

Foundation for "a phone number authorized to inspect a Plot". This round adds
the storage + backend contract only; the public inspection flow is unchanged
and does not consult these rows yet (that lands in round 8-3B).

  1. CREATE plot_access_phones (one row per authorized phone of a plot), with:
       - CHECK access_type IN ('primary','additional')
       - CHECK phone_normalized is a canonical Thai mobile (^0[689][0-9]{8}$)
       - partial unique: at most ONE active 'primary' per plot
       - partial unique: no duplicate ACTIVE phone within a plot
       - a plain index on phone_normalized for exact cross-plot lookup (8-3B)
     The same phone on a DIFFERENT plot is always allowed (uniqueness is
     per-plot and only among active rows).
  2. ADD four nullable columns to records (phone-access attribution). Nullable
     so existing records read WITHOUT any backfill, and CHECKs that only bind
     non-NULL values.
  3. RLS on plot_access_phones — scope resolved THROUGH the parent plot, reusing
     the exact app.scope/app.supplier_id/app.user_id vocabulary and the
     NULLIF(...,'')::uuid guard from migrations 0016/0035/0037. USING and WITH
     CHECK are identical (all/supplier/assigned), matching the parent
     plots_scope policy — this neither broadens nor narrows Plot's own scope.

Additive only: no backfill, no data mutation, no reseed, no drop of existing
plots/records/plot_cycles data. Transactional DDL — any failure rolls the whole
migration back.

Revision ID: 0039_plot_access_phones
Revises: 0038_cycle_final_estimate
Create Date: 2026-07-18 00:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

from alembic import op

revision = "0039_plot_access_phones"
down_revision = "0038_cycle_final_estimate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. plot_access_phones table. Constraint/index names match the ORM naming
    #    convention (app/db/base.py) so model metadata and this migration agree.
    op.execute(
        """
        CREATE TABLE plot_access_phones (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            plot_id UUID NOT NULL,
            phone_normalized VARCHAR(20) NOT NULL,
            access_type VARCHAR(20) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_plot_access_phones_access_type_allowed
                CHECK (access_type IN ('primary', 'additional')),
            CONSTRAINT ck_plot_access_phones_phone_normalized_format
                CHECK (phone_normalized ~ '^0[689][0-9]{8}$'),
            CONSTRAINT fk_plot_access_phones_plot_id_plots
                FOREIGN KEY (plot_id) REFERENCES plots(id) ON DELETE CASCADE
        );
        CREATE INDEX ix_plot_access_phones_plot_id
            ON plot_access_phones (plot_id);
        CREATE INDEX ix_plot_access_phones_phone_normalized
            ON plot_access_phones (phone_normalized);
        -- At most ONE active primary phone per plot.
        CREATE UNIQUE INDEX uq_plot_access_phones_active_primary_per_plot
            ON plot_access_phones (plot_id)
            WHERE is_active = true AND access_type = 'primary';
        -- No duplicate ACTIVE phone within the same plot (a number can't be
        -- both primary and additional, nor listed twice). Same phone on a
        -- different plot is unaffected.
        CREATE UNIQUE INDEX uq_plot_access_phones_active_phone_per_plot
            ON plot_access_phones (plot_id, phone_normalized)
            WHERE is_active = true;
        """
    )

    # 2. records phone-access attribution columns — all nullable (existing
    #    records read without backfill; the public flow populates them in 8-3B).
    #    CHECKs bind only non-NULL values. FK ON DELETE SET NULL so deactivating
    #    an access phone never deletes a record's history.
    op.execute(
        """
        ALTER TABLE records ADD COLUMN plot_access_phone_id UUID;
        ALTER TABLE records ADD COLUMN submitted_phone_snapshot VARCHAR(20);
        ALTER TABLE records ADD COLUMN submitted_phone_type VARCHAR(20);
        ALTER TABLE records ADD COLUMN inspector_type VARCHAR(20);
        ALTER TABLE records ADD CONSTRAINT
            fk_records_plot_access_phone_id_plot_access_phones
            FOREIGN KEY (plot_access_phone_id)
            REFERENCES plot_access_phones(id) ON DELETE SET NULL;
        ALTER TABLE records ADD CONSTRAINT ck_records_submitted_phone_type_allowed
            CHECK (submitted_phone_type IS NULL
                   OR submitted_phone_type IN ('primary', 'additional'));
        ALTER TABLE records ADD CONSTRAINT ck_records_inspector_type_allowed
            CHECK (inspector_type IS NULL
                   OR inspector_type IN ('farmer', 'supplier', 'extension'));
        CREATE INDEX ix_records_plot_access_phone_id
            ON records (plot_access_phone_id);
        """
    )

    # 3. RLS — scope THROUGH the parent plot (plot_access_phones has no
    #    supplier_id of its own), same vocabulary + NULLIF guard as 0016/0035/
    #    0037. USING == WITH CHECK (all/supplier/assigned), mirroring plots_scope
    #    — no broadening/narrowing of Plot's own scope. 0016's ALTER DEFAULT
    #    PRIVILEGES already grants DML on tables created after it, but be
    #    explicit + idempotent so RLS can't silently deny a correctly-scoped
    #    srm_app just for a missing grant.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON plot_access_phones TO srm_app;")
    op.execute("ALTER TABLE plot_access_phones ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE plot_access_phones FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY plot_access_phones_scope ON plot_access_phones
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
    # Drop only what this migration created, in reverse dependency order (the
    # records FK must go before the table it references).
    op.execute("DROP POLICY IF EXISTS plot_access_phones_scope ON plot_access_phones;")
    op.execute("ALTER TABLE plot_access_phones DISABLE ROW LEVEL SECURITY;")
    op.execute("REVOKE ALL ON plot_access_phones FROM srm_app;")
    op.execute(
        """
        ALTER TABLE records DROP CONSTRAINT IF EXISTS ck_records_inspector_type_allowed;
        ALTER TABLE records DROP CONSTRAINT IF EXISTS ck_records_submitted_phone_type_allowed;
        ALTER TABLE records DROP CONSTRAINT IF EXISTS
            fk_records_plot_access_phone_id_plot_access_phones;
        DROP INDEX IF EXISTS ix_records_plot_access_phone_id;
        ALTER TABLE records DROP COLUMN IF EXISTS inspector_type;
        ALTER TABLE records DROP COLUMN IF EXISTS submitted_phone_type;
        ALTER TABLE records DROP COLUMN IF EXISTS submitted_phone_snapshot;
        ALTER TABLE records DROP COLUMN IF EXISTS plot_access_phone_id;
        DROP TABLE IF EXISTS plot_access_phones;
        """
    )
